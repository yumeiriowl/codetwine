"""C# files depend on each other through the types and members they name.

A name is resolved through the namespaces around it, the using directives written for
them (namespace, static, alias, global) and the members of the types it is written in.
build_project_dependencies() adds an edge for each name that leads into another file,
and get_file_dependencies() lists each usage with the definition it names.
"""

import os

from codetwine.extractors.definition_source import extract_callee_source
from codetwine.extractors.dependency_graph import (
    build_project_dependencies,
)
from codetwine.extractors.definitions import extract_definitions
from codetwine.config.settings import EXT_TO_DEFINITION_DICT
from codetwine.csharp_namespace_index import csharp_target_cache_dict, namespace_index_cache_dict
from codetwine.file_analyzer import get_file_dependencies
from codetwine.parsers.ts_parser import parse_cache_dict, parse_file
from codetwine.utils.file_utils import copy_path_to_rel

APP_FILE_DICT = {
    "src/App/App.csproj": "<Project Sdk=\"Microsoft.NET.Sdk\"></Project>\n",
    "src/App/GlobalUsings.cs": "global using App.Text;\n",
    "src/App/Models/Order.cs": """\
namespace App.Models;

public class Order
{
    public int Id { get; set; }
    public Customer Customer { get; set; }
    public OrderStatus Status;

    public static Order Create(int id) => new Order { Id = id };

    public string Owner() => Customer.Name;
}

public enum OrderStatus { Open, Closed }
""",
    "src/App/Models/Customer.cs": """\
namespace App.Models
{
    public class Customer
    {
        public string Name { get; set; }

        public class Address
        {
            public string City;
        }
    }

}
""",
    "src/App/Models/Result.cs": "namespace App.Models;\n\npublic class Result { }\n",
    "src/App/Models/Result{T}.cs": "namespace App.Models;\n\npublic class Result<T> { }\n",
    "src/App/Text/Formatter.cs": """\
namespace App.Text;

public static class Formatter
{
    public static string Format(string text) => text.Trim();
}

public static class TextExtension
{
    public static string Shout(this string text) => text.ToUpper();
}
""",
    "src/App/Util/MathEx.cs": """\
namespace App.Util;

public static class MathEx
{
    public static int Sq(int v) => v * v;
}

[System.AttributeUsage(System.AttributeTargets.Class)]
public class AuditAttribute : System.Attribute { }
""",
    "src/App/Services/OrderService.cs": """\
using System;
using System.Collections.Generic;
using App.Models;
using App.Util;
using static App.Util.MathEx;
using Fmt = App.Text.Formatter;

namespace App.Services;

[Audit]
public partial class OrderService : ServiceBase
{
    private readonly List<Order> cache = new();

    public Order Find(int id)
    {
        var order = Order.Create(Sq(id));
        if (order.Status == OrderStatus.Open)
        {
            Console.WriteLine(Fmt.Format("open").Shout());
        }
        Customer.Address address = new Customer.Address();
        var text = Formatter.Format(address.City);
        Log(text);
        cache.Add(order);
        return order;
    }

    public Result<Order> Wrap(Order order) => new Result<Order>();
}
""",
    "src/App/Services/OrderService.Log.cs": """\
namespace App.Services;

public partial class OrderService
{
    private void Log(string text)
    {
        System.Console.WriteLine(text);
    }
}
""",
    "src/App/Services/ServiceBase.cs": """\
namespace App.Services
{
    public abstract class ServiceBase { }
}
""",
    "src/App/Program.cs": """\
using App.Services;

var service = new OrderService();
var order = service.Find(1);
App.Models.Customer customer = order.Customer;
global::App.Text.Formatter.Format(customer.Name);

public partial class Program { }
""",
    "src/Tool/Tool.csproj": "<Project Sdk=\"Microsoft.NET.Sdk\"></Project>\n",
    "src/Tool/Program.cs": """\
using App.Models;

namespace Tool
{
    public class Program
    {
        public static void Main()
        {
            var order = Order.Create(1);
            var program = new Program();
            Formatter.Format("x");
        }
    }
}
""",
}


def _make_files(tmp_path, file_dict: dict[str, str], name: str = "csapp") -> str:
    """Create a project directory holding the given files."""
    project = tmp_path / name
    for file_rel, text in file_dict.items():
        path = project / file_rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    parse_cache_dict.clear()
    namespace_index_cache_dict.clear()
    csharp_target_cache_dict.clear()
    return str(project)


def _graph(project_dir: str) -> dict[str, dict]:
    """Build the dependency graph keyed by the original relative path."""
    project_name = os.path.basename(project_dir)
    graph = {}
    for dep in build_project_dependencies(project_dir):
        rel = copy_path_to_rel(dep["file"][len(project_name) + 1:])
        graph[rel] = {
            key: {copy_path_to_rel(p[len(project_name) + 1:]) for p in dep[key]}
            for key in ("callers", "callees")
        }
    return graph


def _file_dependencies(project_dir: str, file_rel: str) -> dict:
    """Run get_file_dependencies for one file of the project."""
    graph = _graph(project_dir)
    project_file_set = {f for f in graph if f.endswith(".cs")}
    caller_map = {f: sorted(graph[f]["callers"]) for f in project_file_set}
    return get_file_dependencies(
        os.path.join(project_dir, file_rel), project_dir,
        project_file_set, caller_map,
    )


def _usage_dict(result: dict, key: str = "callee_usages") -> dict[tuple[str, str], list[int]]:
    """Return {(name, file): lines} of a usage list of a get_file_dependencies result."""
    file_key = "from" if key == "callee_usages" else "file"
    return {(usage["name"], usage[file_key]): usage["lines"] for usage in result[key]}


def test_edges(tmp_path):
    graph = _graph(_make_files(tmp_path, APP_FILE_DICT))

    assert graph["src/App/Services/OrderService.cs"]["callees"] == {
        "src/App/Models/Order.cs",
        "src/App/Models/Customer.cs",
        "src/App/Text/Formatter.cs",
        "src/App/Util/MathEx.cs",
        "src/App/Services/OrderService.Log.cs",
        "src/App/Services/ServiceBase.cs",
        "src/App/Models/Result{T}.cs",
    }
    # A property named like its type, and a type of the same namespace
    assert graph["src/App/Models/Order.cs"]["callees"] == {"src/App/Models/Customer.cs"}
    # Top-level statements, a name written with its namespaces, and global::
    assert graph["src/App/Program.cs"]["callees"] == {
        "src/App/Services/OrderService.cs",
        "src/App/Services/OrderService.Log.cs",
        "src/App/Models/Customer.cs",
        "src/App/Text/Formatter.cs",
    }
    # The global using directive of another .csproj directory does not count, and
    # Program is the one of the file itself
    assert graph["src/Tool/Program.cs"]["callees"] == {"src/App/Models/Order.cs"}
    assert graph["src/App/Services/ServiceBase.cs"]["callers"] == {
        "src/App/Services/OrderService.cs",
    }
    assert "src/App/App.csproj" in graph
    assert graph["src/App/App.csproj"]["callees"] == set()


def test_definitions(tmp_path):
    project_dir = _make_files(tmp_path, APP_FILE_DICT)
    root = parse_file(os.path.join(project_dir, "src/App/Models/Order.cs"))[0]
    definition_list = [
        (d.name, d.type) for d in extract_definitions(root, EXT_TO_DEFINITION_DICT["cs"])
    ]

    assert definition_list == [
        ("Order", "class_declaration"),
        ("Id", "property_declaration"),
        ("Customer", "property_declaration"),
        ("Status", "field_declaration"),
        ("Create", "method_declaration"),
        ("Owner", "method_declaration"),
        ("OrderStatus", "enum_declaration"),
        ("Open", "enum_member_declaration"),
        ("Closed", "enum_member_declaration"),
    ]


def test_callee_usages(tmp_path):
    project_dir = _make_files(tmp_path, APP_FILE_DICT)
    result = _file_dependencies(project_dir, "src/App/Services/OrderService.cs")
    usage_dict = _usage_dict(result)

    assert result["language"] == "cs"
    assert usage_dict == {
        ("AuditAttribute", "src/App/Util/MathEx.cs"): [10],
        ("ServiceBase", "src/App/Services/ServiceBase.cs"): [11],
        ("Order", "src/App/Models/Order.cs"): [13, 15, 29],
        ("Order.Create", "src/App/Models/Order.cs"): [17],
        ("Sq", "src/App/Util/MathEx.cs"): [17],
        ("OrderStatus.Open", "src/App/Models/Order.cs"): [18],
        ("Formatter.Format", "src/App/Text/Formatter.cs"): [20, 23],
        ("Shout", "src/App/Text/Formatter.cs"): [20],
        ("Customer.Address", "src/App/Models/Customer.cs"): [22],
        ("Log", "src/App/Services/OrderService.Log.cs"): [24],
        ("Result", "src/App/Models/Result{T}.cs"): [29],
    }

    context_dict = {usage["name"]: usage["target_context"] for usage in result["callee_usages"]}
    assert context_dict["Order.Create"].lstrip().startswith("public static Order Create(int id)")
    assert context_dict["Sq"].lstrip().startswith("public static int Sq(int v)")
    assert context_dict["Formatter.Format"].lstrip().startswith("public static string Format(string text)")
    assert context_dict["Shout"].lstrip().startswith("public static string Shout(this string text)")
    assert context_dict["Log"].lstrip().startswith("private void Log(string text)")
    assert context_dict["AuditAttribute"].lstrip().startswith("[System.AttributeUsage")
    assert context_dict["Customer.Address"].lstrip().startswith("public class Address")
    assert context_dict["Result"] == "public class Result<T> { }"


def test_a_type_written_with_an_alias_is_named_with_the_type(tmp_path):
    file_dict = {
        "src/App/Models/Customer.cs": APP_FILE_DICT["src/App/Models/Customer.cs"],
        "src/App/Use.cs": """\
using Cust = App.Models.Customer;
using Models = App.Models;

namespace App;

public class Use
{
    public Cust Make() => new Cust();

    public Models.Customer.Address Where() => new Models.Customer.Address();
}
""",
    }
    project_dir = _make_files(tmp_path, file_dict)
    result = _file_dependencies(project_dir, "src/App/Use.cs")

    assert _usage_dict(result) == {
        ("Customer", "src/App/Models/Customer.cs"): [8],
        ("Customer.Address", "src/App/Models/Customer.cs"): [10],
    }


def test_caller_usages(tmp_path):
    project_dir = _make_files(tmp_path, APP_FILE_DICT)
    result = _file_dependencies(project_dir, "src/App/Models/Order.cs")
    usage_dict = _usage_dict(result, "caller_usages")

    assert usage_dict == {
        ("Order", "src/App/Services/OrderService.cs"): [13, 15, 29],
        ("Order.Create", "src/App/Services/OrderService.cs"): [17],
        ("OrderStatus.Open", "src/App/Services/OrderService.cs"): [18],
        ("Order.Create", "src/Tool/Program.cs"): [9],
    }
    context = next(
        usage["usage_context"] for usage in result["caller_usages"]
        if usage["file"] == "src/Tool/Program.cs"
    )
    assert "var order = Order.Create(1);" in context


def test_same_file_usages(tmp_path):
    project_dir = _make_files(tmp_path, APP_FILE_DICT)

    result = _file_dependencies(project_dir, "src/App/Services/OrderService.cs")
    assert {usage["name"]: usage["lines"] for usage in result["same_file_usages"]} == {
        "cache": [25],
    }

    # A property named like its type is the property in an expression and the type
    # where a type is written
    result = _file_dependencies(project_dir, "src/App/Models/Order.cs")
    assert {usage["name"]: usage["lines"] for usage in result["same_file_usages"]} == {
        "OrderStatus": [7],
        "Customer": [11],
    }
    assert _usage_dict(result) == {("Customer", "src/App/Models/Customer.cs"): [6]}


def test_member_lookup(tmp_path):
    project_dir = _make_files(tmp_path, APP_FILE_DICT)

    source = extract_callee_source("src/App/Models/Customer.cs", "Customer.Address", project_dir)
    assert source.lstrip().startswith("public class Address")
    source = extract_callee_source("src/App/Models/Order.cs", "OrderStatus.Closed", project_dir)
    assert source == "Closed"


def test_namespace_inside_preprocessor_and_nested(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Lib/Shape.cs": """\
#region License
// text
#endregion
#if !NET20
namespace Lib.Geometry
{
    public struct Point { public int X; }

    namespace Deep
    {
        public interface IShape { Point Origin { get; } }
    }
}
#endif
""",
        "Lib/Circle.cs": """\
namespace Lib
{
    namespace Drawing
    {
        using Geometry.Deep;

        public record Circle(Geometry.Point Center) : IShape
        {
            public Geometry.Point Origin => Center;
        }
    }
}
""",
    })
    graph = _graph(project_dir)

    assert graph["Lib/Circle.cs"]["callees"] == {"Lib/Shape.cs"}
    result = _file_dependencies(project_dir, "Lib/Circle.cs")
    assert _usage_dict(result) == {
        ("Point", "Lib/Shape.cs"): [7, 9],
        ("IShape", "Lib/Shape.cs"): [7],
    }
    # A type of the same file is a usage of the file itself
    result = _file_dependencies(project_dir, "Lib/Shape.cs")
    assert {usage["name"]: usage["lines"] for usage in result["same_file_usages"]} == {
        "Point": [11],
    }


def test_type_parameter_and_local_names_are_not_usages(tmp_path):
    project_dir = _make_files(tmp_path, {
        "T.cs": "namespace N;\npublic class T { }\npublic class Item { }\n",
        "Box.cs": """\
namespace N;

public class Box<T> where T : new()
{
    public T Value = new T();

    public TOut Map<TOut>(System.Func<T, TOut> f, int Item)
    {
        label: goto label;
        var pair = new { Item = 1 };
        return f(Value);
    }
}
""",
    })
    graph = _graph(project_dir)

    assert graph["Box.cs"]["callees"] == set()


def test_extension_method_of_a_namespace_out_of_sight(tmp_path):
    project_dir = _make_files(tmp_path, {
        "A/Ext.cs": """\
namespace A;
public static class Ext { public static int Twice(this int v) => v * 2; }
""",
        "B/Use.cs": """\
namespace B;
public class Use { int Run(int v) => v.Twice(); }
""",
        "C/Use.cs": """\
using A;
namespace C;
public class Use { int Run(int? v) => v?.Twice() ?? this.Get().Twice(); int Get() => 1; }
""",
    })
    graph = _graph(project_dir)

    assert graph["B/Use.cs"]["callees"] == set()
    assert graph["C/Use.cs"]["callees"] == {"A/Ext.cs"}
    result = _file_dependencies(project_dir, "C/Use.cs")
    assert _usage_dict(result) == {("Twice", "A/Ext.cs"): [3]}


def test_extension_method_by_argument_count(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Ext.cs": """\
namespace N;
public static class Ext
{
    public static string Trim(this string s, int start, int length) => s;
    public static int Parse(this object reader) => 0;
    public static string Pad(this string s, int width, char fill = ' ') => s;
    public static string Glue(this string s, params object[] rest) => s;
}
""",
        "Fit.cs": """\
namespace N;
public class Fit
{
    void Run(string s, object o)
    {
        s.Trim(1, 2);
        o.Parse();
        s.Pad(3);
        s.Pad(3, '0');
        s.Glue();
        s.Glue(1, 2, 3);
    }
}
""",
        "NoFit.cs": """\
namespace N;
public class NoFit
{
    void Run(string s)
    {
        s.Trim();
        s.Trim('x');
        int.Parse("1");
        System.Guid.Parse("x");
        s.Pad();
        s.Pad(1, '0', 2);
    }
}
""",
    })
    graph = _graph(project_dir)

    assert graph["NoFit.cs"]["callees"] == set()
    assert graph["Fit.cs"]["callees"] == {"Ext.cs"}
    result = _file_dependencies(project_dir, "Fit.cs")
    assert _usage_dict(result) == {
        ("Trim", "Ext.cs"): [6],
        ("Parse", "Ext.cs"): [7],
        ("Pad", "Ext.cs"): [8, 9],
        ("Glue", "Ext.cs"): [10, 11],
    }


def test_type_of_another_csproj_directory(tmp_path):
    project_dir = _make_files(tmp_path, {
        "A/A.csproj": "<Project />\n",
        "A/Extensions.cs": "public static class Extensions { public static void Add() { } }\n",
        "A/Use.cs": "public class UseA { void Run() { Extensions.Add(); } }\n",
        "B/B.csproj": "<Project />\n",
        "B/Extensions.cs": "public static class Extensions { public static void Add() { } }\n",
        "B/Use.cs": "public class UseB { void Run() { Extensions.Add(); } }\n",
        "C/C.csproj": "<Project />\n",
        "C/Use.cs": "public class UseC { void Run() { Extensions.Add(); } }\n",
    })
    graph = _graph(project_dir)

    assert graph["A/Use.cs"]["callees"] == {"A/Extensions.cs"}
    assert graph["B/Use.cs"]["callees"] == {"B/Extensions.cs"}
    assert graph["C/Use.cs"]["callees"] == {"A/Extensions.cs", "B/Extensions.cs"}


def test_source_with_syntax_the_grammar_does_not_read(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Base.cs": "namespace N;\npublic class Base { }\npublic interface IX { }\n",
        "Split.cs": """\
namespace N
{
    public class Split
#if HAVE_X
        : Base, IX
#else
        : Base
#endif
    {
        public Base Parent;
    }
}
""",
    })
    graph = _graph(project_dir)

    assert graph["Split.cs"]["callees"] == {"Base.cs"}
    result = _file_dependencies(project_dir, "Split.cs")
    assert [d["name"] for d in result["definitions"]] == ["Split", "Parent"]
    assert _usage_dict(result) == {
        ("Base", "Base.cs"): [5, 7, 10],
        ("IX", "Base.cs"): [5],
    }


def test_member_names_of_patterns_and_initializers_are_not_usages(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Order.cs": """\
namespace N;
public class Order { public int Status; public Customer Customer; public int X; }
public class Customer { public string Name; }
""",
        "Check.cs": """\
namespace N;
public class Check
{
    int Status, Other;
    int X;
    int @event;
    string Name;

    void Run(Order o)
    {
        if (o is { Status: 1, Customer.Name: "a" }) { }
        var copy = o with { X = 1 };
        var (first, Status2) = (1, 2);
        Other = @event;
    }
}
""",
    })
    result = _file_dependencies(project_dir, "Check.cs")

    assert _usage_dict(result) == {("Order", "Order.cs"): [9]}
    assert {usage["name"]: usage["lines"] for usage in result["same_file_usages"]} == {
        "Other": [14],
        "event": [14],
    }


def test_file_scoped_namespace_inside_preprocessor(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Lib/Point.cs": "namespace Lib.Geometry;\npublic struct Point { }\n",
        "Lib/Shape.cs": """\
#if NET8_0
namespace Lib.Geometry;
#endif

public class Shape
{
    public Point Origin;
}
""",
    })
    graph = _graph(project_dir)

    assert graph["Lib/Shape.cs"]["callees"] == {"Lib/Point.cs"}



def test_local_names_are_not_linked_to_members_of_the_same_name(tmp_path):
    project_dir = _make_files(tmp_path, {
        "App.csproj": "<Project />\n",
        "Job.cs": (
            "namespace App;\npublic partial class Job\n{\n"
            "    private int count;\n    private string name;\n    public void Reset() { }\n}\n"
        ),
        "Job.Run.cs": (
            "namespace App;\npublic partial class Job\n{\n"
            "    public Job(int count) { this.count = count; }\n"
            "    public static int Length(string name)\n    {\n"
            "        var count = name.Length;\n"
            "        void Reset() { }\n"
            "        Reset();\n"
            "        System.Func<int, int> f = name => name + count;\n"
            "        return count;\n    }\n}\n"
        ),
        "Defs.cs": (
            "namespace App;\npublic class Vec\n{\n"
            "    public int a, b = 2;\n"
            "    public int this[int i] => i;\n"
            "    public static Vec operator +(Vec x, Vec y) => x;\n"
            "    public static implicit operator int(Vec v) => 0;\n"
            "    ~Vec() { }\n}\n"
        ),
    })
    result = _file_dependencies(project_dir, "Job.Run.cs")
    # this.count is the field; the parameters, the local variable, the lambda parameter
    # and the local function are not
    assert [(u["name"], u["lines"]) for u in result["callee_usages"]] == [("count", [4])]

    definition_list = [
        d["name"] for d in _file_dependencies(project_dir, "Defs.cs")["definitions"]
    ]
    assert definition_list == ["Vec", "a", "b", "this[]", "operator +", "operator int", "~Vec"]


def test_member_named_like_its_type(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Types.cs": """\
namespace App;
public enum Status { Active, Closed }
public class Money
{
    public static Money Parse(string s) => null;
    public static Money Zero;
    public int Amount;
    public class Unit { }
}
""",
        "Order.cs": """\
namespace App;
public class Order
{
    public Status Status { get; set; }
    public Money Money { get; set; }

    public void Run()
    {
        Status = Status.Closed;
        Money = Money.Parse("1");
        Money = Money.Zero;
        var amount = Money.Amount;
        var text = Money.ToString();
        var unit = new Money.Unit();
    }
}
""",
    })
    result = _file_dependencies(project_dir, "Order.cs")

    # A static member, an enum member and a type inside the type are named through the
    # type; a member of a value and a name the type does not define are the property
    assert _usage_dict(result) == {
        ("Status", "Types.cs"): [4],
        ("Money", "Types.cs"): [5],
        ("Status.Closed", "Types.cs"): [9],
        ("Money.Parse", "Types.cs"): [10],
        ("Money.Zero", "Types.cs"): [11],
        ("Money.Unit", "Types.cs"): [14],
    }
    assert {usage["name"]: usage["lines"] for usage in result["same_file_usages"]} == {
        "Status": [9],
        "Money": [10, 11, 12, 13],
    }


def test_type_by_number_of_type_arguments(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Local.cs": "namespace App;\npublic class Result { }\n",
        "LibResult.cs": "namespace Lib.B;\npublic class Result<T> { }\n",
        "A.cs": "namespace Lib.A;\npublic class Node { }\n",
        "B.cs": "namespace Lib.B;\npublic class Node<T> { }\npublic class Only<T> { }\n",
        "Use.cs": """\
using Lib.A;
using Lib.B;
namespace App;
class Use
{
    Node<int> generic;
    Node plain;
    Result<int> r;
    Result p;
    object o = typeof(Only);
}
""",
    })
    result = _file_dependencies(project_dir, "Use.cs")

    # Only is written without type arguments and no type Only has none
    assert _usage_dict(result) == {
        ("Node", "B.cs"): [6],
        ("Node", "A.cs"): [7],
        ("Result", "LibResult.cs"): [8],
        ("Result", "Local.cs"): [9],
        ("Only", "B.cs"): [10],
    }


def test_alias_of_a_generic_type_and_types_inside_a_using_directive(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Result.cs": "namespace App;\npublic class Result { }\n",
        "ResultT.cs": "namespace App;\npublic class Result<T> { }\n",
        "Cust.cs": "namespace App;\npublic class Customer { }\npublic class Item { }\n",
        "Holder.cs": """\
namespace App;
public static class Holder { public static int Make() => 0; }
public static class Holder<T> { public static int Make() => 1; }
""",
        "Use.cs": """\
using IntResult = App.Result<int>;
using CustList = System.Collections.Generic.List<App.Customer>;
using Pair = (App.Item, int);
using static App.Holder<int>;
namespace App.Sub;
class Use
{
    IntResult a;
    CustList b;
    Pair c;
    int Run() => Make();
}
""",
    })
    result = _file_dependencies(project_dir, "Use.cs")

    assert _usage_dict(result) == {
        ("Customer", "Cust.cs"): [2],
        ("Item", "Cust.cs"): [3],
        ("Result", "ResultT.cs"): [8],
        ("Make", "Holder.cs"): [11],
    }
    context_dict = {usage["name"]: usage["target_context"] for usage in result["callee_usages"]}
    assert context_dict["Make"] == "public static int Make() => 1;"


def test_target_context_of_an_overload_and_of_a_generic_type(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Limits.cs": """\
namespace App;
public static class Limits
{
    public static int Twice(int a, int b) => a + b;
    public static string Twice(string s) => s + s;
    public static string Cut(this string s) => s;
    public static string Cut(this string s, int length) => s;
}
public class Gen<T>
{
    public static int Size() => 1;
}
public class Gen
{
    public static int Size() => 0;
}
""",
        "Two.cs": "namespace App;\nclass Two { int Run() => Limits.Twice(1, 2); }\n",
        "One.cs": "namespace App;\nclass One { string Run(string s) => Limits.Twice(s).Cut(3); }\n",
        "Group.cs": (
            "namespace App;\n"
            "class Group { object Run(string s) => (System.Func<string, string>)Limits.Twice; }\n"
        ),
        "Zero.cs": "namespace App;\nclass Zero { string Run(string s) => s.Cut(); Gen g; }\n",
        "Plain.cs": "namespace App;\nclass Plain { int Run() => Gen.Size(); }\n",
        "Typed.cs": "namespace App;\nclass Typed { int Run() => Gen<int>.Size(); Gen<int> g; }\n",
    })

    def context_dict(file_rel: str) -> dict[str, str]:
        result = _file_dependencies(project_dir, file_rel)
        return {usage["name"]: usage["target_context"] for usage in result["callee_usages"]}

    assert context_dict("Two.cs") == {
        "Limits.Twice": "public static int Twice(int a, int b) => a + b;",
    }
    assert context_dict("One.cs") == {
        "Limits.Twice": "public static string Twice(string s) => s + s;",
        "Cut": "public static string Cut(this string s, int length) => s;",
    }
    # A name that is not called is the first declaration
    assert context_dict("Group.cs") == {
        "Limits.Twice": "public static int Twice(int a, int b) => a + b;",
    }
    assert context_dict("Zero.cs") == {
        "Cut": "public static string Cut(this string s) => s;",
        "Gen": "public class Gen\n{\n    public static int Size() => 0;\n}",
    }
    assert context_dict("Plain.cs") == {"Gen.Size": "public static int Size() => 0;"}
    assert context_dict("Typed.cs") == {
        "Gen.Size": "public static int Size() => 1;",
        "Gen": "public class Gen<T>\n{\n    public static int Size() => 1;\n}",
    }


def test_member_written_with_its_type_inside_the_type(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Cfg.cs": """\
class Cfg
{
    const int Max = 3;
    static int Twice() => Cfg.Max * 2;
    static int Plain() => Max * 2;
}

class Made
{
    Made() { }
    const int Max = 3;
    static int Twice() => Made.Max * 2;
    static int Plain() => Max * 2;
}
""",
    })
    result = _file_dependencies(project_dir, "Cfg.cs")

    assert {
        (usage["name"], tuple(usage["lines"]), usage["target_start_line"])
        for usage in result["same_file_usages"]
    } == {
        ("Cfg.Max", (4,), 3),
        ("Made.Max", (12,), 11),
        ("Max", (5,), 3),
        ("Max", (13,), 11),
    }


def test_an_overload_is_chosen_by_the_types_of_the_arguments(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Fmt.cs": """\
class Box { }
static class Fmt
{
    public static string Show(int value) => "i";
    public static string Show(string value) => "s";
    public static string Show(Box value) => "b";
    public static string Show(double value, int width) => "d";
    public static string Show<T>(T value, string label) => "t";
}
""",
        "Use.cs": """\
class Use
{
    Box held = new Box();
    string Run(string text, long big)
    {
        var made = new Box();
        return Fmt.Show(1) + Fmt.Show("a") + Fmt.Show(text)
            + Fmt.Show(made) + Fmt.Show(held)
            + Fmt.Show(2, 3)
            + Fmt.Show(big, "label")
            + Fmt.Show(Unknown());
    }
    int Unknown() => 0;
}
""",
    })
    result = _file_dependencies(project_dir, "Use.cs")
    assert {
        (usage["target_start_line"], tuple(usage["lines"]))
        for usage in result["callee_usages"] if usage["name"] == "Fmt.Show"
    } == {
        # Show(int): the literal, and the call whose argument tells no type (the first)
        (4, (7, 11)),
        (5, (7,)),       # Show(string): the literal and the parameter, two calls of one line
        (6, (8,)),       # Show(Box): the local variable and the field
        (7, (9,)),       # Show(double, int): an int is passed as a double
        (8, (10,)),      # Show<T>(T, string)
    }


def test_an_overload_of_the_exact_argument_type_comes_before_a_wider_one(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Fmt.cs": """\
static class Fmt
{
    public static string Show(double value) => "d";
    public static string Show(int value) => "i";
    public static string Show(long value) => "l";
    public static string Pair(double left, double right) => "dd";
    public static string Pair(double left, int right) => "di";
}
""",
        "Use.cs": """\
class Use
{
    string A() => Fmt.Show(1);
    string B() => Fmt.Show(2.5);
    string C() => Fmt.Show(3L);
    string D() => Fmt.Pair(1, 2);
    string E() => Fmt.Pair(1, 2.5);
}
""",
    })
    result = _file_dependencies(project_dir, "Use.cs")
    assert {
        (usage["name"], usage["target_start_line"], tuple(usage["lines"]))
        for usage in result["callee_usages"]
    } == {
        ("Fmt.Show", 4, (3,)),
        ("Fmt.Show", 3, (4,)),
        ("Fmt.Show", 5, (5,)),
        ("Fmt.Pair", 7, (6,)),
        ("Fmt.Pair", 6, (7,)),
    }


def test_a_member_written_with_its_type_inside_a_type_with_a_constructor(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Made.cs": """\
class Made
{
    Made() { }
    int Name = 3;
    string Label() => nameof(Made.Name);
}
""",
    })
    result = _file_dependencies(project_dir, "Made.cs")
    assert {
        (usage["name"], tuple(usage["lines"]), usage["target_start_line"])
        for usage in result["same_file_usages"]
    } == {("Made.Name", (5,), 4)}



def test_a_checked_operator_and_a_conversion_to_a_type_with_blanks_are_named_by_the_operator(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Num.cs": (
            "namespace App;\npublic struct Num\n{\n"
            "    public static Num operator checked -(Num a, Num b) => a;\n"
            "    public static explicit operator checked int(Num n) => 0;\n"
            "    public static implicit operator System.Collections.Generic.Dictionary<int,   string>(Num n)\n"
            "        => null;\n"
            "    public static implicit operator System.Tuple<int,\n        long>(Num n) => null;\n}\n"
        ),
    })
    definition_list = [
        d["name"] for d in _file_dependencies(project_dir, "Num.cs")["definitions"]
    ]
    assert definition_list == [
        "Num", "operator -", "operator int",
        "operator System.Collections.Generic.Dictionary<int, string>",
        "operator System.Tuple<int, long>",
    ]


def _call_line_dict(project_dir: str, file_rel: str, name: str) -> dict[int, set[tuple[str, int]]]:
    """Return {line: {(file, target_start_line)}} of the usages of a name in a file."""
    result = _file_dependencies(project_dir, file_rel)
    line_dict: dict[int, set[tuple[str, int]]] = {}
    for key in ("callee_usages", "same_file_usages"):
        for usage in result[key]:
            if usage["name"] != name:
                continue
            for line in usage["lines"]:
                line_dict.setdefault(line, set()).add(
                    (usage.get("from", file_rel), usage["target_start_line"]),
                )
    return line_dict


def test_the_type_of_an_argument_is_read_from_the_scope_that_declares_the_name(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Printer.cs": """\
namespace App
{
    public class Box { }
    public class Label { }
    public static class Printer
    {
        public static void Show(Label l) { }
        public static void Show(Box b) { }
        public static void Show(string s) { }
    }
}
""",
        "BaseUser.cs": (
            "namespace App { public class BaseUser { public Label Value { get; set; } "
            "public Label Item { get; set; } } }\n"
        ),
        "Use.cs": """\
namespace App
{
    public class Other { public Box Value { get; set; } }
    public class User : BaseUser
    {
        void M() { Printer.Show(Value); }
        void N()
        {
            { Label x = null; Printer.Show(x); }
            { string x = ""; }
        }
        class Nested { public Box Item; }
        void P() { Printer.Show(Item); }
        public Label Own;
        void Q() { Printer.Show(Own); }
        class Nested2 { public Box Own; }
        void R(Box b) { System.Action a = () => { Printer.Show(b); }; }
        void S() { foreach (string t in new string[0]) { Printer.Show(t); } }
    }
}
""",
    })
    assert _call_line_dict(project_dir, "Use.cs", "Printer.Show") == {
        # A member of a base type is not read: the first declaration
        6: {("Printer.cs", 7)},
        # The variable of the block the call is in
        9: {("Printer.cs", 7)},
        # The members of a nested type are not the members of the type around it
        13: {("Printer.cs", 7)},
        15: {("Printer.cs", 7)},
        # A parameter of the method around a lambda, a typed foreach variable
        17: {("Printer.cs", 8)},
        18: {("Printer.cs", 9)},
    }


def test_the_type_of_an_argument_is_read_from_a_primary_constructor(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Printer.cs": """\
namespace App
{
    public class Box { }
    public class Label { }
    public static class Printer
    {
        public static void Show(Label l) { }
        public static void Show(Box b) { }
        public static void Show(string s) { }
    }
}
""",
        "Use.cs": """\
namespace App
{
    public record Rec(Box B)
    {
        void M() { Printer.Show(B); }
    }
    public class Prim(Box pc)
    {
        void M() { Printer.Show(pc); }
    }
    public struct Point(string s) { void M() { Printer.Show(s); } }
    public record struct Pair(Box First, string Second)
    {
        void M() { Printer.Show(Second); }
    }
    public class Shadow(Box v)
    {
        private Label v = null;
        void M() { Printer.Show(v); }
    }
    public class Plain { Box f; void M() { Printer.Show(f); } }
}
""",
    })
    assert _call_line_dict(project_dir, "Use.cs", "Printer.Show") == {
        5: {("Printer.cs", 8)},
        9: {("Printer.cs", 8)},
        11: {("Printer.cs", 9)},
        14: {("Printer.cs", 9)},
        # A field of the name comes before the parameter
        19: {("Printer.cs", 7)},
        21: {("Printer.cs", 8)},
    }


def test_a_call_of_a_partial_type_leads_to_the_file_of_the_overload_it_takes(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Svc.A.cs": """\
namespace App
{
    public partial class Svc
    {
        public void Run(string s) { }
        void M()
        {
            Run(1);
            Run("a");
            Run(Make());
        }
        static int Make() => 0;
    }
}
""",
        "Svc.B.cs": """\
namespace App
{
    public partial class Svc
    {
        public void Run(int n) { }
        public static void Stat(int n) { }
    }
}
""",
        "Svc.C.cs": "namespace App { public partial class Svc { public static void Stat(string n) { } } }\n",
        "Third.cs": (
            "namespace App { public class Third {\n"
            "    void M() { Svc.Stat(1); }\n"
            "    void N() { Svc.Stat(Svc.Make()); }\n"
            "} }\n"
        ),
        "Fourth.cs": "namespace App { public class Fourth { void M() { Svc.Stat(\"a\"); } } }\n",
    })
    assert _call_line_dict(project_dir, "Svc.A.cs", "Run") == {
        8: {("Svc.B.cs", 5)},
        9: {("Svc.A.cs", 5)},
        # A call that fits several declarations leads to the file itself
        10: {("Svc.A.cs", 5)},
    }
    assert _call_line_dict(project_dir, "Third.cs", "Svc.Stat") == {
        2: {("Svc.B.cs", 6)},
        # A call that fits several declarations leads to every file that declares one
        3: {("Svc.B.cs", 6), ("Svc.C.cs", 1)},
    }
    graph = _graph(project_dir)
    assert graph["Fourth.cs"]["callees"] == {"Svc.C.cs"}


def test_an_overload_is_chosen_with_the_types_of_system_written_by_name(tmp_path):
    project_dir = _make_files(tmp_path, {
        "P.cs": """\
using System;
public static class P
{
    public static void Show(Int32 n) { }
    public static void Show(String s) { }
    public static void Put(int n) { }
    public static void Put(Object o) { }
    public static void Hex(int n) { }
    public static void Hex(System.Int64 n) { }
    public static void Bin(int n) { }
    public static void Bin(uint n) { }
    public static void Wide(string s) { }
    public static void Wide(int n) { }
}
""",
        "U.cs": """\
class U
{
    void M(short s, String t)
    {
        P.Show("a");
        P.Put("a");
        P.Hex(0x10L);
        P.Bin(0b1u);
        P.Wide(s);
        P.Show(t);
    }
}
""",
    })
    result = _file_dependencies(project_dir, "U.cs")
    assert {
        (usage["name"], usage["target_start_line"], tuple(usage["lines"]))
        for usage in result["callee_usages"] if usage["name"].startswith("P.")
    } == {
        ("P.Show", 5, (5, 10)),
        ("P.Put", 7, (6,)),
        ("P.Hex", 9, (7,)),
        ("P.Bin", 11, (8,)),
        ("P.Wide", 13, (9,)),
    }


def test_the_type_of_a_parameter_of_a_generic_type_fits_any_argument(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Box.cs": """\
public class Box<T>
{
    public static void Put(string s) { }
    public static void Put(T v) { }
}
""",
        "U.cs": "class U { void M() { Box<int>.Put(1); } }\n",
    })
    result = _file_dependencies(project_dir, "U.cs")
    assert {
        usage["target_start_line"]
        for usage in result["callee_usages"] if usage["name"] == "Box.Put"
    } == {4}


def test_names_are_looked_up_once_per_scope(tmp_path, monkeypatch):
    from codetwine.extractors import csharp_source

    count_list = [0]
    for reader_name in ("_local_declare_dict", "_member_declare_dict"):
        reader = getattr(csharp_source, reader_name)

        def count_reader(node, reader=reader):
            count_list[0] += 1
            return reader(node)

        monkeypatch.setattr(csharp_source, reader_name, count_reader)

    def read_count(method_count: int) -> int:
        line_list = ["namespace App {", "class C {", "void Local() {"]
        line_list += [f"Box v{index} = null; Show(v{index});" for index in range(method_count)]
        line_list.append("}")
        line_list += [f"void M{index}() {{ Show(field{index}); }}" for index in range(method_count)]
        line_list += ["}", "}"]
        path = tmp_path / f"C{method_count}.cs"
        path.write_text("\n".join(line_list))
        count_list[0] = 0
        csharp_source.csharp_reference_list(parse_file(str(path))[0])
        return count_list[0]

    assert read_count(20) < 3 * read_count(10)


def test_the_declarations_of_a_file_are_read_in_time_linear_in_its_members(tmp_path):
    import time

    from codetwine.extractors.csharp_source import read_csharp_declaration

    def read_time(shape: str, count: int) -> float:
        if shape == "types":
            line_list = ["namespace App {"]
            line_list += [f"class C{index} {{ void M(int n) {{ }} }}" for index in range(count)]
            line_list.append("}")
        else:
            line_list = ["namespace App;", "class C {"]
            line_list += [f"void M{index}(int n) {{ }}" for index in range(count)]
            line_list.append("}")
        path = tmp_path / f"{shape}{count}.cs"
        path.write_text("\n".join(line_list))
        root_node = parse_file(str(path))[0]
        time_list = []
        for _ in range(3):
            start = time.perf_counter()
            read_csharp_declaration(root_node)
            time_list.append(time.perf_counter() - start)
        return min(time_list)

    for shape in ("types", "methods"):
        assert read_time(shape, 4000) < 8 * read_time(shape, 1000)


def test_a_params_parameter_is_a_local_name(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Bag.cs": """\
namespace App
{
    public class Item { }
    public class Bag
    {
        private Item items;
        private string name;
        public Bag(string name, params Item[] items)
        {
            Use(name, items);
            Show(items);
        }
        void Use(string s, Item[] a) { }
        void Show(object o) { }
        void Show(Item i) { }
        public static void Count(params string[] name) { var n = name.Length; }
    }
}
""",
    })
    result = _file_dependencies(project_dir, "Bag.cs")
    assert {
        (usage["name"], tuple(usage["lines"]), usage["target_start_line"])
        for usage in result["same_file_usages"]
    } == {
        ("Item", (6, 8, 13, 15), 3),
        ("Use", (10,), 13),
        # The type of a params parameter is an array: the first declaration
        ("Show", (11,), 14),
    }


def test_a_static_call_after_a_namespace_is_not_an_extension_method(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Ext.cs": """\
using System;
using IO = System.IO;
namespace App
{
    public static class SeqExt
    {
        public static bool Exists(this string dir, string name) => true;
    }
    public class U
    {
        void M(string d)
        {
            System.IO.File.Exists("a");
            global::System.IO.File.Exists("b");
            IO.File.Exists("c");
            App.Other.Exists("d");
            File.Exists("e");
            d.Exists("f");
        }
    }
}
""",
    })
    assert _call_line_dict(project_dir, "Ext.cs", "Exists") == {
        # A name the project does not define is taken as a value
        17: {("Ext.cs", 7)},
        18: {("Ext.cs", 7)},
    }


def test_a_call_after_a_name_with_one_part_after_it_is_an_extension_method(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Ext.cs": """\
using Microsoft.EntityFrameworkCore;
using Conv = System.Convert;
namespace App
{
    public static class DbExt
    {
        public static void Seed(this object db) { }
        public static void ToInt32(this object o) { }
    }
    public class ShopContext : DbContext
    {
        public void Init()
        {
            Database.Seed();
            Data.Seed();
            Microsoft.Seed();
            Conv.ToInt32("1");
            Database.Facade.Seed();
            Microsoft.EntityFrameworkCore.Seed();
        }
    }
}
""",
        "Repo.cs": "namespace App.Database { public class Repo { } }\n",
        "Data.cs": "namespace Data { public class Row { } }\n",
    })
    assert _call_line_dict(project_dir, "Ext.cs", "Seed") == {
        # A namespace or the first name of a using directive with one part after it is
        # taken as a value (a member of a base type)
        14: {("Ext.cs", 7)},
        15: {("Ext.cs", 7)},
        16: {("Ext.cs", 7)},
    }
    # An alias with one part after it is not a value
    assert _call_line_dict(project_dir, "Ext.cs", "ToInt32") == {}


def test_a_member_of_one_of_two_types_written_on_one_line_leads_to_that_type(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Two.cs": (
            "namespace N { public class A { public static int X = 1; } "
            "public class B { public static int X = 2; } }\n"
        ),
        "Main.cs": "namespace N { public class C { int v = B.X; int w = A.X; } }\n",
    })
    result = _file_dependencies(project_dir, "Main.cs")

    assert {usage["name"]: usage["target_context"] for usage in result["callee_usages"]} == {
        "A.X": "public static int X = 1;", "B.X": "public static int X = 2;",
    }
