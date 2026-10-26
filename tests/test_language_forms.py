"""Forms of each language that are read as definitions and references.

Each case writes a small project and checks the definitions and the references of
get_file_dependencies(): qualified type names, members written with private names,
re-exports through require(), names in patterns and string annotations, extension
functions, and the forms a grammar does not read as they are written (a macro on a
line of its own, a Kotlin constructor on the line after its class name).
"""

import os

from codetwine.config.settings import EXT_TO_DEFINITION_DICT, language_ext
from codetwine.extractors.definition_source import source_definition
from codetwine.extractors.definitions import (
    extract_definitions,
    member_owner_dict,
    select_top_level_definitions,
)
from codetwine.extractors.dependency_graph import build_project_dependencies
from codetwine.file_analyzer import get_file_dependencies
from codetwine.parsers.ts_parser import parse_file
from codetwine.utils.file_utils import copy_path_to_rel


def _analyze(tmp_path, file_dict: dict[str, str]) -> dict[str, dict]:
    """Write the files as a project and run get_file_dependencies for each of them."""
    project_dir = str(tmp_path / "case")
    for file_rel, text in file_dict.items():
        path = os.path.join(project_dir, file_rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    graph = {}
    for dep in build_project_dependencies(project_dir):
        rel = copy_path_to_rel(dep["file"][len("case") + 1:])
        graph[rel] = sorted(copy_path_to_rel(p[len("case") + 1:]) for p in dep["callers"])
    project_file_set = {f for f in graph if language_ext(os.path.join(project_dir, f))}
    caller_map = {f: graph[f] for f in project_file_set}
    return {
        file_rel: get_file_dependencies(
            os.path.join(project_dir, file_rel), project_dir, project_file_set, caller_map,
        )
        for file_rel in graph
    }


def _callee_set(result: dict) -> set[tuple[str, str, str | None, int]]:
    """Return (name, file, target name, line) of each callee reference of a file."""
    return {
        (usage["name"], usage["from"], usage["target_name"], line)
        for usage in result["callee_usages"] for line in usage["lines"]
    }


def _same_set(result: dict) -> set[tuple[str, str | None, int]]:
    """Return (name, target name, line) of each same-file reference of a file."""
    return {
        (usage["name"], usage["target_name"], line)
        for usage in result["same_file_usages"] for line in usage["lines"]
    }


def _definition_list(result: dict) -> list[tuple[str, str, int, int]]:
    return [(d["name"], d["type"], d["start_line"], d["end_line"]) for d in result["definitions"]]


def _definition_name_list(result: dict) -> list[str]:
    return [d["name"] for d in result["definitions"]]


# == C / C++ ===================================================================

def test_cpp_member_defined_outside_its_class_leads_to_its_declaration(tmp_path):
    result_dict = _analyze(tmp_path, {
        "shape.hpp": (
            "class Shape {\n"                       # 1
            " public:\n"
            "  Shape(int n);\n"                     # 3
            "  static int count();\n"               # 4
            " private:\n"
            "  int n_;\n"                           # 6
            "};\n"
            "class Circle : public Shape {\n"       # 8
            " public:\n"
            "  Circle();\n"                         # 10
            "  int twice() { return Shape::count() * 2; }\n"   # 11
            "};\n"
        ),
        "shape.cpp": (
            '#include "shape.hpp"\n'
            "Shape::Shape(int n) : n_(n) {}\n"      # 2
            "int Shape::count() { return 1; }\n"    # 3
            "Circle::Circle() : Shape(1) {}\n"      # 4
        ),
    })
    callee_set = _callee_set(result_dict["shape.cpp"])
    # The name of a member leads to the member, not to the constructor of its class
    assert ("Shape::count", "shape.hpp", "count", 3) in callee_set
    assert ("Shape::Shape", "shape.hpp", "Shape", 2) in callee_set
    # The member and the base class a constructor initializes
    assert ("n_", "shape.hpp", "n_", 2) in callee_set
    assert ("Shape", "shape.hpp", "Shape", 4) in callee_set
    # Inside a class with a base class, the base class is the class
    same_set = _same_set(result_dict["shape.hpp"])
    assert ("Shape", "Shape", 8) in same_set
    assert ("Shape::count", "count", 11) in same_set


def test_c_macro_on_a_line_of_its_own_and_next_to_a_type(tmp_path):
    result_dict = _analyze(tmp_path, {
        "node.h": (
            "#define NODE_FIELDS int kind; int size;\n"   # 1
            "#define NODE_PRIVATE int flag;\n"            # 2
            "#define API\n"                               # 3
            "typedef int handle_t;\n"                     # 4
            "struct node_s {\n"                           # 5
            "  NODE_FIELDS\n"
            "  int own;\n"                                # 7
            "  NODE_PRIVATE\n"
            "};\n"                                        # 9
            "enum only { ONLY_ONE\n"                      # 10
            "};\n"
            "API handle_t node_open(struct node_s* node);\n"   # 12
            "API int node_close(handle_t handle);\n"           # 13
        ),
    })
    result = result_dict["node.h"]
    definition_list = _definition_list(result)
    assert ("node_s", "struct_specifier", 5, 9) in definition_list
    assert ("own", "field_declaration", 7, 7) in definition_list
    assert ("ONLY_ONE", "enumerator", 10, 10) in definition_list
    assert ("node_open", "function_declarator", 12, 12) in definition_list
    assert ("node_close", "function_declarator", 13, 13) in definition_list
    assert not {"int", "NODE_PRIVATE", "API"} & {
        name for name, kind, _, _ in definition_list if kind != "preproc_def"
    }
    # A macro read as spaces is a usage of the macro
    same_set = _same_set(result)
    assert ("NODE_FIELDS", "NODE_FIELDS", 6) in same_set
    assert ("API", "API", 12) in same_set
    assert ("handle_t", "handle_t", 12) in same_set


def test_c_function_written_with_a_macro_keeps_its_local_variables_out(tmp_path):
    result_dict = _analyze(tmp_path, {
        "test.c": (
            "static int count;\n"                    # 1
            "TEST_IMPL(ping_pong) {\n"               # 2
            "  int i;\n"
            "  struct local { int x; } value;\n"
            "  count = i;\n"
            "  return 0;\n"
            "}\n"                                    # 7
        ),
        "conv.hpp": (
            "struct Flag {\n"                        # 1
            "  operator bool() const {\n"            # 2
            "    int local = 1;\n"
            "    return local;\n"
            "  }\n"
            "};\n"
        ),
    })
    assert _definition_list(result_dict["test.c"]) == [
        ("count", "declaration", 1, 1),
        ("ping_pong", "function_definition", 2, 7),
    ]
    assert _definition_name_list(result_dict["conv.hpp"]) == ["Flag", "operator bool"]


def test_c_typedef_named_like_its_struct_is_listed_once(tmp_path):
    result_dict = _analyze(tmp_path, {
        "node.h": (
            "typedef struct node {\n"            # 1
            "  int value;\n"
            "} node;\n"                          # 3
            "typedef struct item {\n"            # 4
            "  int id;\n"
            "} item_t, *item_p;\n"               # 6
            "node *first(item_t *it);\n"         # 7
        ),
    })
    result = result_dict["node.h"]
    assert [(name, kind) for name, kind, _, _ in _definition_list(result)] == [
        ("node", "struct_specifier"), ("value", "field_declaration"),
        ("item_t", "type_definition"), ("item_p", "type_definition"),
        ("item", "struct_specifier"), ("id", "field_declaration"),
        ("first", "function_declarator"),
    ]
    assert ("node", "node", 7) in _same_set(result)


def test_c_function_pointers_are_definitions(tmp_path):
    result_dict = _analyze(tmp_path, {
        "ops.h": (
            "int (*handler)(int);\n"                    # 1
            "static void (*callbacks[4])(void);\n"      # 2
            "struct ops {\n"                            # 3
            "    int (*open)(const char *path);\n"      # 4
            "    void (*close)(int fd);\n"              # 5
            "    int count;\n"                          # 6
            "};\n"
            "void (*signal_like(int sig, void (*func)(int)))(int);\n"   # 8
        ),
        "main.c": (
            '#include "ops.h"\n'
            "void (*handler_of(int sig))(int) { return handler; }\n"   # 2
            'int run(struct ops *o) { o->count = 1; return o->open("x"); }\n'   # 3
        ),
    })
    assert _definition_list(result_dict["ops.h"]) == [
        ("handler", "declaration", 1, 1), ("callbacks", "declaration", 2, 2),
        ("ops", "struct_specifier", 3, 7), ("open", "field_declaration", 4, 4),
        ("close", "field_declaration", 5, 5), ("count", "field_declaration", 6, 6),
        ("signal_like", "function_declarator", 8, 8),
    ]
    # A function that returns a function pointer is a function definition
    assert _definition_list(result_dict["main.c"]) == [
        ("handler_of", "function_definition", 2, 2), ("run", "function_definition", 3, 3),
    ]
    # A member that is a function pointer leads to the member, not to its struct
    callee_set = _callee_set(result_dict["main.c"])
    assert ("ops.open", "ops.h", "open", 3) in callee_set
    assert ("handler", "ops.h", "handler", 2) in callee_set


def test_cpp_static_members_defined_outside_their_class_and_conversion_declarations(tmp_path):
    result_dict = _analyze(tmp_path, {
        "shape.hpp": (
            "class Shape {\n"                           # 1
            " public:\n"
            "  static int count;\n"                     # 3
            "  static const char* const kName;\n"       # 4
            "  explicit operator bool() const;\n"       # 5
            "};\n"
            "template <typename T> struct Box { static int total; };\n"   # 7
            "struct Table { static int sizes[]; };\n"   # 8
        ),
        "shape.cpp": (
            '#include "shape.hpp"\n'
            "int Shape::count = 0;\n"                   # 2
            'const char* const Shape::kName = "shape";\n'   # 3
            "template <typename T> int Box<T>::total = 0;\n"   # 4
            "int Table::sizes[] = {1, 2};\n"            # 5
            "Shape::operator bool() const { return Shape::count > 0; }\n"   # 6
        ),
    })
    assert ("operator bool", "declaration", 5, 5) in _definition_list(result_dict["shape.hpp"])
    assert _definition_list(result_dict["shape.cpp"]) == [
        ("Shape::count", "declaration", 2, 2), ("Shape::kName", "declaration", 3, 3),
        ("Box::total", "declaration", 4, 4), ("Table::sizes", "declaration", 5, 5),
        ("Shape::operator bool", "function_definition", 6, 6),
    ]
    # The name of such a definition leads to the declaration in the class
    callee_set = _callee_set(result_dict["shape.cpp"])
    assert ("Shape::count", "shape.hpp", "count", 2) in callee_set
    assert ("Table::sizes", "shape.hpp", "sizes", 5) in callee_set


def test_c_function_declared_with_its_name_in_parentheses(tmp_path):
    result_dict = _analyze(tmp_path, {
        "lib.h": (
            "int (paren_fn)(int x);\n"            # 1
            "int plain_fn(int x);\n"              # 2
            "int (*fp)(int);\n"                   # 3
            "int ((twice_fn))(int x);\n"          # 4
        ),
        "lib.c": (
            '#include "lib.h"\n'
            "int (paren_fn)(int x) { return x; }\n"   # 2
            "int plain_fn(int x) { return x; }\n"     # 3
        ),
        "main.c": '#include "lib.h"\nint run(void) { return paren_fn(1) + plain_fn(2) + fp(3); }\n',
    })
    # A name in parentheses declares the function itself; a pointer in parentheses
    # declares a variable
    assert _definition_list(result_dict["lib.h"]) == [
        ("paren_fn", "function_declarator", 1, 1), ("plain_fn", "function_declarator", 2, 2),
        ("fp", "declaration", 3, 3), ("twice_fn", "function_declarator", 4, 4),
    ]
    assert _definition_list(result_dict["lib.c"]) == [
        ("paren_fn", "function_definition", 2, 2), ("plain_fn", "function_definition", 3, 3),
    ]
    callee_set = _callee_set(result_dict["main.c"])
    for name in ("paren_fn", "plain_fn"):
        assert (name, "lib.h", name, 2) in callee_set
        assert (name, "lib.c", name, 2) in callee_set
    assert ("fp", "lib.h", "fp", 2) in callee_set


def test_cpp_class_macro_whatever_its_base_classes_are_written_as(tmp_path):
    result_dict = _analyze(tmp_path, {
        "widget.h": (
            "template <typename T>\n"
            "class Base {};\n"                                                       # 2
            "class UI_EXPORT Widget : public Base<Widget> {\n"                       # 3
            " public:\n"
            "  int Size();\n"                                                        # 5
            "};\n"                                                                   # 6
            "class UI_EXPORT Stream : virtual public Base<int> {\n"                  # 7
            "  int Read();\n"                                                        # 8
            "};\n"                                                                   # 9
            "template <typename T>\n"
            "class UI_EXPORT Holder : public Base<T> {\n"                            # 11
            "  T Get();\n"                                                           # 12
            "};\n"                                                                   # 13
            "class UI_EXPORT Pair : public Base<int>, private Base<char> { int k; };\n"  # 14
            "class UI_EXPORT UI_DEPRECATED Old final : public ns::Base<int> {\n"     # 15
            "  int Age();\n"                                                         # 16
            "};\n"                                                                   # 17
            "class Panel {\n"                                                        # 18
            "  struct UI_EXPORT Item : Base<int> { int id; };\n"                     # 19
            "};\n"                                                                   # 20
            "class Plain bare;\n"                                                    # 21
            "template <>\n"
            "class UI_EXPORT Holder<char> : public Base<char> { char Get(); };\n"      # 23
        ),
        "use.cc": (
            '#include "widget.h"\n'
            "int Count(Widget* widget, Stream* stream) {\n"                           # 2
            "  return widget->Size() + stream->Read();\n"                             # 3
            "}\n"
            "int Sum(Items& list) {\n"                                                # 5
            "  int total = 0;\n"
            "  for (class Item item : list) total += item.id;\n"                      # 7
            "  return total;\n"
            "}\n"
        ),
    })
    assert _definition_list(result_dict["widget.h"]) == [
        ("Base", "class_specifier", 2, 2),
        ("Widget", "class_specifier", 3, 6),
        ("Size", "function_declarator", 5, 5),
        ("Stream", "class_specifier", 7, 9),
        ("Read", "function_declarator", 8, 8),
        ("Holder", "class_specifier", 11, 13),
        ("Get", "function_declarator", 12, 12),
        ("Pair", "class_specifier", 14, 14),
        ("k", "field_declaration", 14, 14),
        ("Old", "class_specifier", 15, 17),
        ("Age", "function_declarator", 16, 16),
        ("Panel", "class_specifier", 18, 20),
        ("Item", "struct_specifier", 19, 19),
        ("id", "field_declaration", 19, 19),
        ("bare", "declaration", 21, 21),
        ("Holder<char>", "class_specifier", 23, 23),
        ("Get", "function_declarator", 23, 23),
    ]
    project_dir = tmp_path / "case"
    definition_list = extract_definitions(
        parse_file(str(project_dir / "widget.h"))[0], EXT_TO_DEFINITION_DICT["h"],
    )
    owner_dict = member_owner_dict(definition_list)
    assert {
        d.name: owner_dict[id(d)].name
        for d in definition_list if id(d) in owner_dict and d.start_line != 23
    } == {
        "Size": "Widget", "Read": "Stream", "Get": "Holder", "k": "Pair", "Age": "Old",
        "Item": "Panel", "id": "Item",
    }
    assert [owner_dict[id(d)].name for d in definition_list if d.name == "Get"] == [
        "Holder", "Holder<char>",
    ]
    callee_set = _callee_set(result_dict["use.cc"])
    assert ("Widget.Size", "widget.h", "Size", 3) in callee_set
    assert ("Stream.Read", "widget.h", "Read", 3) in callee_set
    # The class of a variable declared in a range-based for loop is no macro
    assert _definition_name_list(result_dict["use.cc"]) == ["Count", "Sum"]


def test_c_function_declared_with_a_macro_and_its_name_in_parentheses(tmp_path):
    for header_rel, source_rel in (("lua.h", "lapi.c"), ("lua.hpp", "lapi.cpp")):
        result_dict = _analyze(tmp_path / header_rel.replace(".", "_"), {
            header_rel: (
                "typedef struct lua_State lua_State;\n"                               # 1
                "typedef double lua_Number;\n"                                        # 2
                "LUA_API int (lua_absindex) (lua_State *L, int idx);\n"               # 3
                "LUA_API lua_Number (lua_tonumberx) (lua_State *L, int idx, int *isnum);\n"  # 4
                "LUA_API void (lua_settop) (lua_State *L, int idx);\n"                # 5
                "LUA_API const char *(lua_typename) (lua_State *L, int tp);\n"        # 6
                "LUA_API int lua_gettop (lua_State *L);\n"                            # 7
                "typedef int (*lua_CFunction) (lua_State *L);\n"                      # 8
                "int (plain_paren) (int x);\n"                                        # 9
            ),
            source_rel: (
                f'#include "{header_rel}"\n'
                "LUA_API int (lua_absindex) (lua_State *L, int idx) { return idx; }\n"  # 2
                "LUA_API int lua_gettop (lua_State *L) { return 0; }\n"                 # 3
            ),
            "main.c": (
                f'#include "{header_rel}"\n'
                "int run(lua_State *L) { return lua_absindex(L, 1) + lua_gettop(L); }\n"  # 2
            ),
        })
        assert _definition_list(result_dict[header_rel]) == [
            ("lua_State", "type_definition", 1, 1),
            ("lua_Number", "type_definition", 2, 2),
            ("lua_absindex", "function_declarator", 3, 3),
            ("lua_tonumberx", "function_declarator", 4, 4),
            ("lua_settop", "function_declarator", 5, 5),
            ("lua_typename", "function_declarator", 6, 6),
            ("lua_gettop", "function_declarator", 7, 7),
            ("lua_CFunction", "type_definition", 8, 8),
            ("plain_paren", "function_declarator", 9, 9),
        ]
        assert _definition_list(result_dict[source_rel]) == [
            ("lua_absindex", "function_definition", 2, 2),
            ("lua_gettop", "function_definition", 3, 3),
        ]
        callee_set = _callee_set(result_dict["main.c"])
        for name in ("lua_absindex", "lua_gettop"):
            assert (name, header_rel, name, 2) in callee_set
            assert (name, source_rel, name, 2) in callee_set


def test_cpp_member_of_a_class_defined_outside_its_class_is_looked_up_inside_it(tmp_path):
    result_dict = _analyze(tmp_path, {
        "slice.h": (
            "class Slice {\n"                                  # 1
            " public:\n"
            "  int compare(const Slice& b) const;\n"           # 3
            "  class Iter;\n"
            "};\n"
            "inline int Slice::compare(const Slice& b) const { return 0; }\n"   # 6
            "class Slice::Iter {\n"                            # 7
            " public:\n"
            "  int next() { return 1; }\n"                     # 9
            "};\n"
        ),
        "main.cc": (
            '#include "slice.h"\n'
            "int run(Slice a, Slice b) { return a.compare(b); }\n"   # 2
            "int step(Slice::Iter it) { return it.next(); }\n"       # 3
        ),
    })
    target_dict = {
        usage["name"]: (usage["target_name"], usage["target_start_line"])
        for usage in result_dict["main.cc"]["callee_usages"]
    }
    # A member written after a value of its class leads to its declaration in the class
    assert target_dict["Slice.compare"] == ("compare", 3)
    # A member of a nested class leads to the member in the body of that class
    assert target_dict["Slice::Iter.next"] == ("next", 9)


def test_c_struct_written_as_the_return_type_of_a_prototype_is_listed_by_itself(tmp_path):
    project_dir = tmp_path / "case"
    project_dir.mkdir()
    file_abs = str(project_dir / "t.h")
    text = (
        "struct T { int y; } make_t(void);\n"     # 1
        "struct S { int x; } s;\n"                # 2
        "struct P { int z; };\n"                  # 3
        "int area(struct P p);\n"                 # 4
    )
    (project_dir / "t.h").write_text(text, encoding="utf-8")
    definition_list = extract_definitions(parse_file(file_abs)[0], EXT_TO_DEFINITION_DICT["c"])
    text_dict = {
        d.name: text.encode()[d.start_byte:d.end_byte].decode() for d in definition_list
    }
    assert text_dict["T"] == "struct T { int y; }"
    assert text_dict["make_t"] == "struct T { int y; } make_t(void);"
    assert text_dict["S"] == "struct S { int x; }"
    assert text_dict["s"] == "struct S { int x; } s;"
    assert text_dict["P"] == "struct P { int z; }"
    assert text_dict["area"] == "int area(struct P p);"
    assert [d.name for d in select_top_level_definitions(definition_list)] == [
        "make_t", "T", "s", "S", "P", "area",
    ]
    owner_dict = member_owner_dict(definition_list)
    assert {d.name: owner_dict[id(d)].name for d in definition_list if id(d) in owner_dict} == {
        "y": "T", "x": "S", "z": "P",
    }


# == Java ======================================================================

def test_java_qualified_types_annotations_and_method_references(tmp_path):
    result_dict = _analyze(tmp_path, {
        "src/main/java/com/acme/model/Shape.java": (
            "package com.acme.model;\n"
            "public interface Shape {\n"                        # 2
            "    int SIDES = 0;\n"                              # 3
            "    double area();\n"                              # 4
            "    @interface Marker { String value(); }\n"      # 5
            "    class Builder { public Builder size(int s) { return this; } }\n"   # 6
            "    static int twice(int x) { return x * 2; }\n"  # 7
            "}\n"
        ),
        "src/main/java/com/acme/app/Main.java": (
            "package com.acme.app;\n"
            "import com.acme.model.Shape;\n"
            "import java.util.function.Function;\n"
            '@Shape.Marker("m")\n'                                         # 4
            "public class Main {\n"
            "    private Shape.Builder builder = new Shape.Builder();\n"   # 6
            "    int run(Shape s) {\n"
            "        Function<Integer, Integer> f = Shape::twice;\n"       # 8
            "        Function<Shape, Double> g = Shape::area;\n"           # 9
            "        com.acme.model.Shape other = s;\n"                    # 10
            "        builder.size(Shape.SIDES);\n"                         # 11
            "        return f.apply(1);\n"
            "    }\n"
            "}\n"
        ),
    })
    shape = "src/main/java/com/acme/model/Shape.java"
    assert ("SIDES", "constant_declaration", 3, 3) in _definition_list(result_dict[shape])
    callee_set = _callee_set(result_dict["src/main/java/com/acme/app/Main.java"])
    assert ("Shape.Marker", shape, "Marker", 4) in callee_set
    assert ("Shape.Builder", shape, "Builder", 6) in callee_set
    assert ("Shape.twice", shape, "twice", 8) in callee_set
    assert ("Shape.area", shape, "area", 9) in callee_set
    assert ("Shape", shape, "Shape", 10) in callee_set
    assert ("Shape.Builder.size", shape, "size", 11) in callee_set
    assert ("Shape.SIDES", shape, "SIDES", 11) in callee_set


# == JavaScript / TypeScript ===================================================

def test_js_private_members_and_modules_passed_on_as_members(tmp_path):
    result_dict = _analyze(tmp_path, {
        "lib/store.js": (
            "class Store {\n"                                   # 1
            "  #items = [];\n"                                  # 2
            "  add(x) { this.#items.push(x); return this.#check(); }\n"   # 3
            "  #check() { return true; }\n"                     # 4
            "}\n"
            "function helper() {}\n"                            # 6
            "module.exports = { Store, helper };\n"             # 7
        ),
        "lib/engine.js": (
            "class Engine { static of() { return new Engine(); } }\n"   # 1
            "module.exports = Engine;\n"
        ),
        "lib/index.js": (
            "exports.store = require('./store');\n"
            "module.exports.Engine = require('./engine');\n"
        ),
        "lib/all.js": "module.exports = { store: require('./store') };\n",
        "esm.mjs": "export default function () {}\nexport const named = 1;\n",
        "app.js": (
            "const lib = require('./lib');\n"
            "const { store: s2, Engine: E2 } = require('./lib');\n"
            "const all = require('./lib/all');\n"
            "lib.store.helper();\n"                             # 4
            "lib.Engine.of();\n"                                # 5
            "s2.helper(); E2.of();\n"                           # 6
            "all.store.helper();\n"                             # 7
        ),
        "app.mjs": (
            "import * as ns from './esm.mjs';\n"
            "import store from './lib/store.js';\n"
            "console.log(ns, ns.named);\n"                      # 3
            "export { ns };\n"                                  # 4
            "store.helper(); new store.Store();\n"              # 5
        ),
    })
    assert _definition_name_list(result_dict["lib/store.js"])[:5] == [
        "Store", "#items", "add", "#check", "helper",
    ]
    assert {("#items.push", "#items", 3), ("#check", "#check", 3)} <= _same_set(
        result_dict["lib/store.js"]
    )
    callee_set = _callee_set(result_dict["app.js"])
    assert ("lib.store.helper", "lib/store.js", "helper", 4) in callee_set
    assert ("lib.Engine.of", "lib/engine.js", "of", 5) in callee_set
    assert ("s2.helper", "lib/store.js", "helper", 6) in callee_set
    assert ("E2.of", "lib/engine.js", "of", 6) in callee_set
    assert ("all.store.helper", "lib/store.js", "helper", 7) in callee_set
    module_callee_set = _callee_set(result_dict["app.mjs"])
    # A namespace import written by itself leads to the file, not to its default export
    assert ("ns", "esm.mjs", None, 4) in module_callee_set
    assert ("ns.named", "esm.mjs", "named", 3) in module_callee_set
    # A default import of a CommonJS module names what the module exports
    assert ("store.helper", "lib/store.js", "helper", 5) in module_callee_set
    assert ("store.Store", "lib/store.js", "Store", 5) in module_callee_set


def test_js_name_bound_by_require_is_no_definition(tmp_path):
    result_dict = _analyze(tmp_path, {
        "lib/helper.js": "function run() {}\nmodule.exports = { run };\n",
        "lib/index.js": (
            "const helper = require('./helper');\n"                 # 1
            "const { run: go } = require('./helper');\n"            # 2
            "const first = require('./helper').run, own = 1;\n"     # 3
            "const made = require('./helper')(own);\n"              # 4
            "module.exports = { helper, go, first };\n"             # 5
        ),
        "app.js": (
            "const lib = require('./lib');\n"
            "lib.helper.run();\n"                                   # 2
        ),
    })
    assert _definition_name_list(result_dict["lib/index.js"]) == ["own", "made", "default"]
    # The name a file passes on leads to the file the import brings it from
    assert ("lib.helper.run", "lib/helper.js", "run", 2) in _callee_set(result_dict["app.js"])


def test_js_method_named_by_a_key_keeps_its_local_variables_out(tmp_path):
    result_dict = _analyze(tmp_path, {
        "bag.js": (
            "const key = 'k';\n"                              # 1
            "class Bag {\n"                                   # 2
            "  [key]() {\n"
            "    const inside = 1;\n"
            "    return inside;\n"
            "  }\n"
            "  'quoted name'() {\n"
            "    let other = 2;\n"
            "    return other;\n"
            "  }\n"
            "}\n"
        ),
    })
    assert _definition_name_list(result_dict["bag.js"]) == ["key", "Bag"]


def test_ts_type_written_with_its_namespace(tmp_path):
    result_dict = _analyze(tmp_path, {
        "model.ts": "export interface Options { mode: string }\nexport class Circle {}\n",
        "view.ts": (
            'import type * as T from "./model";\n'
            'import * as models from "./model";\n'
            "export function draw(o: T.Options, c: models.Circle): T.Options { return o; }\n"   # 3
        ),
    })
    callee_set = _callee_set(result_dict["view.ts"])
    assert ("T.Options", "model.ts", "Options", 3) in callee_set
    assert ("models.Circle", "model.ts", "Circle", 3) in callee_set


def test_js_method_of_an_object_is_a_definition_only_where_the_module_exports_the_object(tmp_path):
    result_dict = _analyze(tmp_path, {
        "vue.js": (
            "export default defineComponent({\n"    # 1
            "  data() { return { count: 0 }; },\n"  # 2
            "  methods: { increment() { this.count++; } },\n"   # 3
            "});\n"
            "Object.assign(Store.prototype, { get(key) { return key; } });\n"   # 5
            "app.use({ handler() {} });\n"          # 6
        ),
        "api.js": (
            "module.exports = {\n"                  # 1
            "  run() { return 1; },\n"              # 2
            "  get size() { return 2; },\n"         # 3
            "};\n"
        ),
        "obj.ts": "export default { load() {} };\n",
    })
    assert _definition_list(result_dict["vue.js"]) == [("default", "export_statement", 1, 4)]
    assert _definition_list(result_dict["api.js"]) == [
        ("default", "expression_statement", 1, 4), ("run", "method_definition", 2, 2),
        ("size", "method_definition", 3, 3),
    ]
    assert _definition_list(result_dict["obj.ts"]) == [
        ("default", "export_statement", 1, 1), ("load", "method_definition", 1, 1),
    ]


def test_js_function_called_with_call_or_apply_where_it_is_written_wraps_the_file(tmp_path):
    result_dict = _analyze(tmp_path, {
        "lib.js": (
            "(function () {\n"                      # 1
            "  var root = this;\n"                  # 2
            "  function helper() {}\n"              # 3
            "}).call(this);\n"
            "(function () { var other = 1; }.apply(this, arguments));\n"   # 5
            "(function () { var bound = 1; }).bind(this);\n"   # 6
        ),
    })
    assert _definition_list(result_dict["lib.js"]) == [
        ("root", "variable_declaration", 2, 2), ("helper", "function_declaration", 3, 3),
        ("other", "variable_declaration", 5, 5),
    ]


def test_ts_namespace_of_a_dotted_name_module_and_enum_members_named_by_strings(tmp_path):
    result_dict = _analyze(tmp_path, {
        "geo.ts": (
            "namespace Geo.Shapes {\n"              # 1
            "  export const unit = 1;\n"            # 2
            "}\n"
            "module Legacy { export function old() {} }\n"   # 4
            'declare module "ext" { export const inExt: number; }\n'   # 5
            "enum Dir { Up, \"up-left\" = 2, 'down' }\n"   # 6
        ),
    })
    assert _definition_list(result_dict["geo.ts"]) == [
        ("Geo.Shapes", "internal_module", 1, 3), ("unit", "lexical_declaration", 2, 2),
        ("Legacy", "module", 4, 4), ("old", "function_declaration", 4, 4),
        ("inExt", "lexical_declaration", 5, 5), ("Dir", "enum_declaration", 6, 6),
        ("Up", "property_identifier", 6, 6), ("up-left", "enum_assignment", 6, 6),
        ("down", "string", 6, 6),
    ]


def test_js_object_a_module_exports_through_parentheses_type_assertions_and_assignment_chains(tmp_path):
    result_dict = _analyze(tmp_path, {
        "a.ts": "export default { m1() {}, p1: () => 1 } satisfies Config;\n",
        "b.ts": "export default { m2() {}, p2: () => 1 } as const;\n",
        "c.ts": "export = { m3() {}, p3: () => 1 };\n",
        "d.js": "export default ({ m4() {}, p4: () => 1 });\n",
        "e.js": "module.exports = ({ m5() {}, p5: () => 1 });\n",
        "chain.js": (
            "exports = module.exports = { c1() {}, q1: function () {} };\n"   # 1
        ),
        "plain.js": "module.exports = { run() {}, size: () => 1 };\n",
        "h.ts": "function helper() {}\nexport default (helper);\n",
        "other.ts": (
            "foo({ n1() {} }) as X;\n"                     # 1
            "const x = ({ n2() {} });\n"                    # 2
            "export const cfg = { n3() {} } satisfies C;\n"  # 3
            "app.use(({ n4() {} }));\n"                    # 4
        ),
        "main.ts": (
            'import a from "./a";\n'
            'import chain from "./chain";\n'
            'import h from "./h";\n'
            "a.m1(); a.p1(); chain.c1(); h();\n"        # 4
        ),
    })
    for file_rel, number, statement_type in (
        ("a.ts", 1, "export_statement"), ("b.ts", 2, "export_statement"),
        ("c.ts", 3, "export_statement"), ("d.js", 4, "export_statement"),
        ("e.js", 5, "expression_statement"),
    ):
        assert _definition_list(result_dict[file_rel]) == [
            ("default", statement_type, 1, 1), (f"m{number}", "method_definition", 1, 1),
            (f"p{number}", "pair", 1, 1),
        ]
    assert _definition_list(result_dict["chain.js"]) == [
        ("default", "expression_statement", 1, 1), ("c1", "method_definition", 1, 1),
        ("q1", "pair", 1, 1),
    ]
    assert _definition_list(result_dict["plain.js"]) == [
        ("default", "expression_statement", 1, 1), ("run", "method_definition", 1, 1),
        ("size", "pair", 1, 1),
    ]
    # A default export of a name in parentheses is the name
    assert _definition_list(result_dict["h.ts"]) == [("helper", "function_declaration", 1, 1)]
    assert _definition_list(result_dict["other.ts"]) == [
        ("x", "lexical_declaration", 2, 2), ("cfg", "lexical_declaration", 3, 3),
    ]
    callee_set = _callee_set(result_dict["main.ts"])
    assert {
        ("a.m1", "a.ts", "m1", 4), ("a.p1", "a.ts", "p1", 4),
        ("chain.c1", "chain.js", "c1", 4), ("h", "h.ts", "helper", 4),
    } <= callee_set


def test_js_objects_a_module_exports_through_a_conditional_or_a_default_value(tmp_path):
    result_dict = _analyze(tmp_path, {
        "cookies.js": (
            "export default platform.hasStandardBrowserEnv ?\n"   # 1
            "  {\n"
            "    write(name, value) { document.cookie = name + value; },\n"   # 3
            "    read(name) { return document.cookie; },\n"        # 4
            "  }\n"
            "  :\n"
            "  {\n"
            "    write() {},\n"                                     # 8
            "    read() { return null; },\n"                       # 9
            "  };\n"
        ),
        "nested.ts": "export default a ? { e1() {} } : b ? ({ e2() {} } as T) : { e3() {} };\n",
        "fallback.js": "module.exports = window.api || { c1() {}, q1: function () {} };\n",
        "nullish.ts": "export default globalThis.cfg ?? { d1() {} };\n",
        "member.js": "exports.api = isNode ? { f1() {} } : { f2() {} };\n",
        "other.js": (
            "const x = c ? { n1() {} } : { n2() {} };\n"   # 1
            "foo(c ? { n3() {} } : {});\n"                 # 2
            "export const y = a || { n4() {} };\n"         # 3
            "export default { n5() {} }.ok ? 1 : 2;\n"     # 4
        ),
        "main.js": (
            'import cookies from "./cookies";\n'
            'const api = require("./fallback");\n'
            "cookies.read(); api.c1();\n"                  # 3
        ),
    })
    assert _definition_list(result_dict["cookies.js"]) == [
        ("default", "export_statement", 1, 10), ("write", "method_definition", 3, 3),
        ("read", "method_definition", 4, 4), ("write", "method_definition", 8, 8),
        ("read", "method_definition", 9, 9),
    ]
    assert _definition_name_list(result_dict["nested.ts"]) == ["default", "e1", "e2", "e3"]
    assert _definition_name_list(result_dict["fallback.js"]) == ["default", "c1", "q1"]
    assert _definition_name_list(result_dict["nullish.ts"]) == ["default", "d1"]
    assert _definition_name_list(result_dict["member.js"]) == ["api", "f1", "f2"]
    # A choice of a value that the module does not export holds no definitions
    assert _definition_name_list(result_dict["other.js"]) == ["x", "y", "default"]
    callee_set = _callee_set(result_dict["main.js"])
    assert {
        ("cookies.read", "cookies.js", "read", 3), ("api.c1", "fallback.js", "c1", 3),
    } <= callee_set


def test_ts_name_through_namespaces_of_dotted_names_leads_to_the_member_of_each(tmp_path):
    result_dict = _analyze(tmp_path, {
        "models.ts": (
            "export namespace App.Models { export class User { a = 1; } }\n"     # 1
            "export namespace Admin.Models { export class User { b = 2; } }\n"   # 2
            "export namespace Nest { export namespace Other {\n"                 # 3
            "  export class User { c = 3; } } }\n"                               # 4
            "export namespace A.B { export namespace C.D {\n"                    # 5
            "  export class User { d = 4; } } }\n"                               # 6
        ),
        "main.ts": (
            'import { App, Admin, Nest, A } from "./models";\n'
            "const u1 = new App.Models.User();\n"      # 2
            "const u2 = new Admin.Models.User();\n"    # 3
            "const u3 = new Nest.Other.User();\n"      # 4
            "const u4 = new A.B.C.D.User();\n"         # 5
            "const m = Admin.Models;\n"                # 6
        ),
    })
    target_dict = {
        usage["name"]: (usage["target_name"], usage["target_start_line"])
        for usage in result_dict["main.ts"]["callee_usages"]
    }
    assert target_dict == {
        "App.Models.User": ("User", 1), "Admin.Models.User": ("User", 2),
        "Nest.Other.User": ("User", 4), "A.B.C.D.User": ("User", 6),
        "Admin.Models": ("Admin.Models", 2),
    }


# == Python ====================================================================

def test_python_patterns_type_aliases_and_string_annotations(tmp_path):
    result_dict = _analyze(tmp_path, {
        "m.py": (
            "class Engine:\n    pass\n"            # 1
            "class Config:\n    pass\n"            # 3
            "type Pair[T] = tuple[T, T]\n"         # 5
            "LIMIT = 1\n"                          # 6
        ),
        "a.py": (
            "from typing import Annotated, Literal\n"
            "import m\n"
            "from m import Config, Engine, LIMIT\n"
            "\n"
            "def match_it(obj, LIMIT=LIMIT):\n"                     # 5
            "    match obj:\n"
            "        case Config(name=found):\n"                    # 7
            "            return found\n"
            "        case m.Engine() as e:\n"                       # 9
            "            return e\n"
            "        case Engine:\n"                                # 11
            "            return None\n"
            "\n"
            'def typed(e: "Engine", c: "list[m.Config] | None") -> "Config":\n'   # 14
            "    return c\n"
            "\n"
            'def plain(mode: Literal["Engine"], doc: Annotated[int, "Config doc"]):\n'   # 17
            '    return "Engine"\n'
        ),
    })
    assert ("Pair", "type_alias_statement", 5, 5) in _definition_list(result_dict["m.py"])
    callee_set = _callee_set(result_dict["a.py"])
    assert ("Config", "m.py", "Config", 7) in callee_set
    assert ("m.Engine", "m.py", "Engine", 9) in callee_set
    # A name a pattern binds is no usage
    assert not {line for name, _, _, line in callee_set if name == "Engine"} & {11}
    assert ("Engine", "m.py", "Engine", 14) in callee_set
    assert ("m.Config", "m.py", "Config", 14) in callee_set
    assert ("Config", "m.py", "Config", 14) in callee_set
    assert not {line for _, _, _, line in callee_set} & {17, 18}


# == Kotlin ====================================================================

KOTLIN_MODEL = (
    "package com.acme.model\n"                                    # 1
    "\n"
    'val greeting = "hi"\n'                                       # 3
    "\n"
    "open class Shape(val name: String) {\n"                      # 5
    "    open fun area(): Double = 0.0\n"                         # 6
    "    fun size(): Int = 1\n"                                   # 7
    "    object Empty : Shape(\"empty\")\n"                       # 8
    "    companion object {\n"                                    # 9
    "        fun unit() = Shape(\"unit\")\n"                      # 10
    "    }\n"
    "}\n"
    "\n"
    "class Reader\n"                                              # 14
    "@Deprecated(\"x\")\n"                                        # 15
    "internal constructor(\n"                                     # 16
    "    internal val source: String,\n"
    ") {\n"
    "    fun read(): String = source\n"                           # 19
    "}\n"
    "\n"
    "class Box(val item: Int) { fun get(): Int = item; inner class In { fun peek() = item } }\n"   # 22
    "\n"
    "fun Shape.describe(): String = name\n"                       # 24
    "fun Shape.size(): Int = 2\n"                                 # 25
    "class After\n"                                               # 26
)


def test_kotlin_forms_the_grammar_does_not_read_as_written(tmp_path):
    result_dict = _analyze(tmp_path, {"src/main/kotlin/com/acme/model/Model.kt": KOTLIN_MODEL})
    definition_list = _definition_list(result_dict["src/main/kotlin/com/acme/model/Model.kt"])
    # A constructor written on the lines after the class name
    assert ("Reader", "class_declaration", 14, 20) in definition_list
    assert ("read", "function_declaration", 19, 19) in definition_list
    # A body written on one line
    assert ("Box", "class_declaration", 22, 22) in definition_list
    assert ("In", "class_declaration", 22, 22) in definition_list
    assert ("peek", "function_declaration", 22, 22) in definition_list
    assert ("describe", "function_declaration", 24, 24) in definition_list
    assert ("After", "class_declaration", 26, 26) in definition_list
    # A companion object written without a name is no definition
    assert "Companion" not in [name for name, _, _, _ in definition_list]


def test_kotlin_declarations_after_annotations_the_grammar_reads_as_an_expression(tmp_path):
    forms = "src/main/kotlin/com/acme/api/Forms.kt"
    box = "src/main/kotlin/com/acme/api/Box.kt"
    after_box = "src/main/kotlin/com/acme/api/AfterBox.kt"
    result_dict = _analyze(tmp_path, {
        forms: (
            "package com.acme.api\n"                                                   # 1
            "\n"
            "import kotlin.annotation.AnnotationRetention.RUNTIME\n"                   # 3
            "import kotlin.annotation.AnnotationTarget.FUNCTION\n"
            "\n"
            "@Retention(RUNTIME) @Target(FUNCTION) public annotation class ToJson\n"   # 6
            "@Target(AnnotationTarget.TYPE)\n"                                        # 7
            "@Retention(AnnotationRetention.RUNTIME)\n"
            "annotation class TypeMark\n"                                             # 9
            '@Suppress("unused") enum class Level { LOW, HIGH }\n'                   # 10
            '@Suppress("unused") data object Empty\n'                                # 11
            '@Suppress("unused") fun top() = 3\n'                                    # 12
            "class Plain\n"                                                           # 13
            "@Qualifier @Retention(AnnotationRetention.RUNTIME) annotation class Named\n"   # 14
            "@AutoService(Provider::class)\n"                                         # 15
            "public class Service : Provider {\n"
            "    override fun create(): Int = 1\n"
            "}\n"
            "@OptIn(Experimental::class)\n"                                           # 19
            "internal fun opt(): Int {\n"
            "    return 1\n"
            "}\n"
        ),
        "src/main/kotlin/com/acme/api/Qualifier.kt": "package com.acme.api\n\nannotation class Qualifier\n",
        box: (
            "package com.acme.api\n"
            "\n"
            '@Suppress("unused") class Box { fun size() = 1 }\n'                     # 3
            '@Suppress("unused") fun interface Handler { fun handle(): Int }\n'      # 4
            "class After\n"                                                           # 5
        ),
        after_box: (
            "package com.acme.api\n"
            "\n"
            "import com.acme.other.Experimental\n"
            "\n"
            '@Suppress("unused") class OneLine { fun size() = 1 }\n'                 # 5
            "@OptIn(Experimental::class)\n"                                           # 6
            "internal fun later(): Int {\n"
            "    return 1\n"
            "}\n"
        ),
        "src/main/kotlin/com/acme/app/Main.kt": (
            "package com.acme.app\n"
            "\n"
            "import com.acme.api.*\n"
            "\n"
            "class Adapter(val level: Level, val box: Box) {\n"                      # 5
            "    @ToJson fun write(): @TypeMark Int = 1\n"                           # 6
            "}\n"
        ),
        "build.gradle.kts": (
            'plugins { id("java") }\n'
            '@Suppress("unused")\n'
            "val flag = true\n"                                                       # 3
            'tasks.register("hello") { }\n'
        ),
    })
    definition_list = _definition_list(result_dict[forms])
    assert ("ToJson", "class_declaration", 6, 6) in definition_list
    assert ("TypeMark", "class_declaration", 9, 9) in definition_list
    assert ("Level", "class_declaration", 10, 10) in definition_list
    assert ("LOW", "enum_entry", 10, 10) in definition_list
    assert ("Empty", "object_declaration", 11, 11) in definition_list
    assert ("top", "function_declaration", 12, 12) in definition_list
    assert ("Plain", "class_declaration", 13, 13) in definition_list
    assert ("Named", "class_declaration", 14, 14) in definition_list
    # A declaration the grammar reads as one starts at its annotations
    assert ("Service", "class_declaration", 15, 18) in definition_list
    assert ("opt", "function_declaration", 19, 22) in definition_list
    # The names written in the annotations are linked
    assert ("Qualifier", "src/main/kotlin/com/acme/api/Qualifier.kt", "Qualifier", 14) in (
        _callee_set(result_dict[forms])
    )
    assert _definition_list(result_dict[box]) == [
        ("Box", "class_declaration", 3, 3), ("size", "function_declaration", 3, 3),
        ("Handler", "class_declaration", 4, 4), ("handle", "function_declaration", 4, 4),
        ("After", "class_declaration", 5, 5),
    ]
    # A declaration the grammar reads as one keeps its start at its annotations
    assert ("later", "function_declaration", 6, 9) in _definition_list(result_dict[after_box])
    callee_set = _callee_set(result_dict["src/main/kotlin/com/acme/app/Main.kt"])
    assert {
        ("Level", forms, "Level", 5), ("Box", box, "Box", 5),
        ("ToJson", forms, "ToJson", 6), ("TypeMark", forms, "TypeMark", 6),
    } <= callee_set
    assert _definition_list(result_dict["build.gradle.kts"]) == [
        ("flag", "property_declaration", 2, 3),
    ]


def test_kotlin_extension_calls_templates_and_nested_types(tmp_path):
    model = "src/main/kotlin/com/acme/model/Model.kt"
    result_dict = _analyze(tmp_path, {
        model: KOTLIN_MODEL,
        "src/main/kotlin/com/acme/app/Main.kt": (
            "package com.acme.app\n"
            "\n"
            "import com.acme.model.*\n"
            "import com.acme.model.describe as desc\n"
            "import com.acme.model.Shape.Companion.unit\n"
            "\n"
            "fun run(s: Shape, other: Any): String {\n"               # 7
            "    val made = unit()\n"                                 # 8
            "    if (other is Shape.Empty) return made.describe()\n"  # 9
            "    val n = made.size()\n"                               # 10
            '    return "$greeting ${made.desc()} $n"\n'              # 11
            "}\n"
        ),
    })
    callee_set = _callee_set(result_dict["src/main/kotlin/com/acme/app/Main.kt"])
    assert ("unit", model, "unit", 8) in callee_set
    assert ("Shape.Empty", model, "Empty", 9) in callee_set
    # An extension function is linked by its name, whatever it is called on
    assert ("describe", model, "describe", 9) in callee_set
    assert ("desc", model, "describe", 11) in callee_set
    # A name that is also a member of a class is not
    assert not {name for name, _, _, line in callee_set if line == 10}
    assert ("greeting", model, "greeting", 11) in callee_set


def test_kotlin_class_read_as_an_error_node_keeps_its_members(tmp_path):
    result_dict = _analyze(tmp_path, {
        "src/main/kotlin/com/acme/io/Source.kt": (
            "package com.acme.io\n"
            "\n"
            "class Source(val name: String) {\n"          # 3
            "    private var final = false\n"             # 4
            "    fun refill() {\n"                        # 5
            "        final = true\n"                      # 6
            "    }\n"
            "    fun close() {}\n"                        # 8
            "}\n"                                         # 9
        ),
        "src/main/kotlin/com/acme/io/Other.kt": (
            "package com.acme.io\n"
            "\n"
            "fun use(refill: Int, s: Source) = refill\n"  # 3
        ),
    })
    definition_list = _definition_list(result_dict["src/main/kotlin/com/acme/io/Source.kt"])
    assert [(name, start) for name, kind, start, _ in definition_list if kind == "class_declaration"] == [
        ("Source", 3),
    ]
    assert next(end for name, _, _, end in definition_list if name == "Source") >= 8
    # The members of the class are no names of the package
    other_callee_set = _callee_set(result_dict["src/main/kotlin/com/acme/io/Other.kt"])
    assert {name for name, _, _, _ in other_callee_set} == {"Source"}


def test_kotlin_annotated_accessor_of_a_property_declared_without_a_type(tmp_path):
    result_dict = _analyze(tmp_path, {
        "src/main/kotlin/com/acme/io/Counter.kt": (
            "package com.acme.io\n"
            "\n"
            "class Counter {\n"                                                   # 3
            "    val size\n"                                                      # 4
            '        @JvmName("size")\n'
            "        get() = count()\n"                                           # 6
            "    var label\n"                                                     # 7
            '        get() = "x"\n'
            "        @Synchronized set(value) {}\n"                               # 9
            "    val total\n"                                                     # 10
            '        @Deprecated("old", ReplaceWith("size")) inline get() = 1\n'  # 11
            "    fun count() = 2\n"                                               # 12
            "    fun fill(): Int {\n"                                             # 13
            "        val copy = count()\n"
            "        get(copy)\n"                                                 # 15
            "        return copy\n"
            "    }\n"                                                             # 17
            "}\n"                                                                 # 18
            "fun after() = 3\n"                                                   # 19
        ),
    })
    # The getter is read with the call of get written after it in the file
    assert _definition_list(result_dict["src/main/kotlin/com/acme/io/Counter.kt"]) == [
        ("Counter", "class_declaration", 3, 18),
        ("size", "property_declaration", 4, 6),
        ("label", "property_declaration", 7, 9),
        ("total", "property_declaration", 10, 11),
        ("count", "function_declaration", 12, 12),
        ("fill", "function_declaration", 13, 17),
        ("after", "function_declaration", 19, 19),
    ]


def test_kotlin_constructor_on_a_later_line_of_a_file_with_crlf_line_breaks(tmp_path):
    result_dict = _analyze(tmp_path, {
        "src/main/kotlin/com/acme/io/Reader.kt": (
            "package com.acme.io\r\n"
            "\r\n"
            "class Reader\r\n"                        # 3
            "@Inject\r\n"
            "internal constructor(val x: Int) {\r\n"
            "    fun read() = x\r\n"                  # 6
            "}\r\n"                                   # 7
            "class Writer<T>\r\n"                     # 8
            "@Inject constructor(val y: T) {\r\n"
            "    fun write() = y\r\n"                 # 10
            "}\r\n"                                   # 11
            "fun after() = 1\r\n"                     # 12
        ),
    })
    assert _definition_list(result_dict["src/main/kotlin/com/acme/io/Reader.kt"]) == [
        ("Reader", "class_declaration", 3, 7),
        ("read", "function_declaration", 6, 6),
        ("Writer", "class_declaration", 8, 11),
        ("write", "function_declaration", 10, 10),
        ("after", "function_declaration", 12, 12),
    ]


def test_kotlin_secondary_constructor_keeps_its_local_variables_out(tmp_path):
    result_dict = _analyze(tmp_path, {
        "src/main/kotlin/com/acme/io/Stack.kt": (
            "package com.acme.io\n"
            "\n"
            "class Stack(val size: Int) {\n"            # 3
            "    constructor(other: Stack) : this(other.size) {\n"
            "        val element = other.size\n"        # 5
            "        println(element)\n"
            "    }\n"
            "    fun top(): Int = size\n"               # 8
            "}\n"
        ),
    })
    assert _definition_name_list(result_dict["src/main/kotlin/com/acme/io/Stack.kt"]) == [
        "Stack", "top",
    ]


def test_kotlin_property_in_front_of_a_secondary_constructor_in_a_file_read_again(tmp_path):
    result_dict = _analyze(tmp_path, {
        "src/main/kotlin/com/acme/ui/NoticeView.kt": (
            "package com.acme.ui\n"
            "\n"
            "class NoticeView : View {\n"                                           # 3
            "    private val paint = Paint()\n"                                      # 4
            "    protected open lateinit var subscriptions: Binding\n"               # 5
            "    constructor(context: Context) : super(context)\n"
            "    constructor(context: Context, attrs: AttributeSet) : super(context, attrs)\n"
            "\n"
            "    fun draw() = paint\n"                                               # 9
            "}\n"                                                                    # 10
            "\n"
            "class Badge { val count = 1 }\n"                                        # 12
        ),
    })
    assert _definition_list(result_dict["src/main/kotlin/com/acme/ui/NoticeView.kt"]) == [
        ("NoticeView", "class_declaration", 3, 10),
        ("paint", "property_declaration", 4, 4),
        ("subscriptions", "property_declaration", 5, 5),
        ("draw", "function_declaration", 9, 9),
        ("Badge", "class_declaration", 12, 12),
        ("count", "property_declaration", 12, 12),
    ]


def test_kotlin_constructor_of_a_class_after_a_nested_class_without_a_body(tmp_path):
    result_dict = _analyze(tmp_path, {
        "src/main/kotlin/com/acme/geo/Point.kt": (
            "package com.acme.geo\n"
            "\n"
            "class Point(val x: Int) {\n"                         # 3
            "    private class Inner\n"                           # 4
            "    constructor(y: Int, z: Int) : this(y) {}\n"      # 5
            "    private object Origin\n"                         # 6
            "    constructor(s: String) : this(s.length)\n"       # 7
            "    fun area() = x\n"                                # 8
            "}\n"                                                 # 9
            "\n"
            "class Counter {\n"                                   # 11
            "    val size\n"                                      # 12
            '        @JvmName("size")\n'
            "        get() = 1\n"                                 # 14
            "}\n"                                                 # 15
            "\n"
            "class Reader\n"                                      # 17
            "@Inject\n"
            "internal constructor(val r: Int) {\n"
            "    fun read() = r\n"                                # 20
            "}\n"                                                 # 21
            "\n"
            "class Box { val q = 1 }\n"                           # 23
        ),
        "src/main/kotlin/com/acme/geo/Line.kt": (
            "package com.acme.geo\n"
            "\n"
            "class Line(val a: Int) {\n"                          # 3
            "    private class Part\n"                            # 4
            "    constructor(b: Int, c: Int) : this(b) {}\n"      # 5
            "    fun length() = a\n"                              # 6
            "}\n"                                                 # 7
            "\n"
            "class Tick { val t = 1 }\n"                          # 9
        ),
    })
    # The constructor of the outer class is no constructor of the nested class; a
    # constructor on the line after a top-level class name is still read with it
    assert _definition_list(result_dict["src/main/kotlin/com/acme/geo/Point.kt"]) == [
        ("Point", "class_declaration", 3, 9),
        ("Inner", "class_declaration", 4, 4),
        ("Origin", "object_declaration", 6, 6),
        ("area", "function_declaration", 8, 8),
        ("Counter", "class_declaration", 11, 15),
        ("size", "property_declaration", 12, 14),
        ("Reader", "class_declaration", 17, 21),
        ("read", "function_declaration", 20, 20),
        ("Box", "class_declaration", 23, 23),
        ("q", "property_declaration", 23, 23),
    ]
    assert _definition_list(result_dict["src/main/kotlin/com/acme/geo/Line.kt"]) == [
        ("Line", "class_declaration", 3, 7),
        ("Part", "class_declaration", 4, 4),
        ("length", "function_declaration", 6, 6),
        ("Tick", "class_declaration", 9, 9),
        ("t", "property_declaration", 9, 9),
    ]


# == Rust ======================================================================

def test_rust_method_called_on_a_typed_parameter(tmp_path):
    result_dict = _analyze(tmp_path, {
        "Cargo.toml": '[package]\nname = "p"\nedition = "2021"\n',
        "src/lib.rs": "pub mod shape;\npub mod app;\n",
        "src/shape.rs": (
            "pub struct Circle { pub r: f64 }\n"                              # 1
            "impl Circle { pub fn area(&self) -> f64 { self.r } }\n"          # 2
            "pub trait Shape { fn name(&self) -> String; }\n"                 # 3
            "fn own(c: &Circle) -> f64 { c.area() }\n"                        # 4
        ),
        "src/app.rs": (
            "use crate::shape::{Circle, Shape};\n"
            "\n"
            "fn total(c: &Circle, s: &dyn Shape, m: &mut Circle) -> f64 {\n"  # 3
            "    let k: Circle = Circle { r: 1.0 };\n"                        # 4
            "    s.name();\n"                                                 # 5
            "    c.area() + k.area() + m.r\n"                                 # 6
            "}\n"
        ),
    })
    callee_set = _callee_set(result_dict["src/app.rs"])
    assert ("Circle.area", "src/shape.rs", "area", 6) in callee_set
    assert ("Circle.r", "src/shape.rs", "Circle", 6) in callee_set
    assert ("Shape.name", "src/shape.rs", "name", 5) in callee_set
    assert ("Circle.area", "area", 4) in _same_set(result_dict["src/shape.rs"])


# == Members, blocks and typed variables =======================================

def _same_line_set(result: dict) -> set[tuple[str, int | None, int]]:
    """Return (name, first line of the target, line) of each same-file reference of a file."""
    return {
        (usage["name"], usage["target_start_line"], line)
        for usage in result["same_file_usages"] for line in usage["lines"]
    }


def test_member_leads_to_the_class_it_is_written_in(tmp_path):
    result_dict = _analyze(tmp_path, {
        "a.py": (
            "class A:\n"                            # 1
            "    def run(self):\n"                  # 2
            "        return 1\n"
            "\n"
            "class B:\n"                            # 5
            "    def run(self):\n"                  # 6
            "        return 2\n"
            "\n"
            "    def go(self):\n"                   # 9
            "        return self.run()\n"           # 10
        ),
        "A.java": (
            "class A {\n"
            "  int size;\n"                         # 2
            "  void run() {}\n"                     # 3
            "}\n"
            "class B {\n"
            "  int size;\n"                         # 6
            "  void run() {}\n"                     # 7
            "  void go() { this.run(); size = 2; }\n"   # 8
            "}\n"
        ),
        "a.rs": (
            "struct A;\n"
            "struct B;\n"
            "impl A {\n"
            "    fn run(&self) {}\n"                # 4
            "}\n"
            "impl B {\n"
            "    fn go(&self) { self.run(); }\n"    # 7
            "}\n"
            "impl B {\n"
            "    fn run(&self) {}\n"                # 10
            "}\n"
        ),
        "a.cpp": (
            "class A {\n"
            " public:\n"
            "  void run() {}\n"                     # 3
            "};\n"
            "class B {\n"
            " public:\n"
            "  void run() {}\n"                     # 7
            "  void go() { this->run(); run(); }\n" # 8
            "};\n"
        ),
    })
    assert _same_line_set(result_dict["a.py"]) == {("run", 6, 10)}
    assert _same_line_set(result_dict["A.java"]) == {("run", 7, 8), ("size", 6, 8)}
    # The type an impl block is written for is a usage of the struct
    assert _same_line_set(result_dict["a.rs"]) == {
        ("run", 10, 7), ("A", 1, 3), ("B", 2, 6), ("B", 2, 9),
    }
    assert _same_line_set(result_dict["a.cpp"]) == {("run", 7, 8)}


def test_name_qualified_with_its_class_leads_to_the_member_not_the_constructor(tmp_path):
    result_dict = _analyze(tmp_path, {
        "Handler.java": (
            "class Handler {\n"                     # 1
            "  int count = 0;\n"                    # 2
            "  Handler() {}\n"                      # 3
            "  static class User {\n"               # 4
            "    static final String EMAIL = \"e\";\n"   # 5
            "    User() {}\n"                       # 6
            "  }\n"
            "  void run(Handler other) {\n"         # 8
            "    other.count++;\n"                  # 9
            "    String e = User.EMAIL;\n"          # 10
            "  }\n"
            "}\n"
        ),
    })
    assert ("Handler.count", 2, 9) in _same_line_set(result_dict["Handler.java"])
    assert ("User.EMAIL", 5, 10) in _same_line_set(result_dict["Handler.java"])


def test_name_written_by_itself_is_no_member_of_a_class(tmp_path):
    result_dict = _analyze(tmp_path, {
        "a.py": (
            "class F:\n"                            # 1
            "    limit = 3\n"                       # 2
            "    other = limit + 1\n"               # 3
            "\n"
            "    def open(self):\n"                 # 5
            "        return limit\n"                # 6
            "\n"
            "def g():\n"
            "    return open(\"x\")\n"              # 9
        ),
        "a.ts": (
            "class F {\n"
            "  size = 1;\n"
            "  open() { return 1; }\n"
            "}\n"
            "function g() {\n"
            "  return open(\"x\") + size;\n"
            "}\n"
        ),
        "a.rs": (
            "struct F;\n"
            "impl F {\n"
            "    fn len(&self) -> usize { 0 }\n"    # 3
            "    fn twice(&self) -> usize { assert!(self.len() == 0); 0 }\n"   # 4
            "}\n"
            "fn g(v: Vec<u8>) {\n"
            "    assert_eq!(v.len(), 1);\n"
            "    len();\n"
            "}\n"
        ),
    })
    assert _same_line_set(result_dict["a.py"]) == {("limit", 2, 3)}
    assert _same_line_set(result_dict["a.ts"]) == set()
    assert _same_line_set(result_dict["a.rs"]) == {("len", 3, 4), ("F", 1, 2)}


def test_typed_variable_of_the_file_is_a_usage_under_its_own_name(tmp_path):
    result_dict = _analyze(tmp_path, {
        "engine.py": "class Engine:\n    def run(self):\n        return 1\n",
        "a.py": (
            "from engine import Engine\n"
            "\n"
            "engine: Engine = Engine()\n"           # 3
            "\n"
            "def f(engine: Engine):\n"              # 5
            "    return engine.run()\n"             # 6
            "\n"
            "def g():\n"
            "    return engine.run()\n"             # 9
        ),
        "Engine.java": "public class Engine { public int run() { return 1; } }\n",
        "A.java": (
            "public class A {\n"
            "  private final Engine engine = new Engine();\n"   # 2
            "  int f(Other engine) { return this.engine.run(); }\n"   # 3
            "  int g() { return engine.run(); }\n"  # 4
            "}\n"
        ),
        "Other.java": "public class Other {}\n",
    })
    assert ("Engine.run", "engine.py", "run", 6) in _callee_set(result_dict["a.py"])
    assert ("Engine.run", "engine.py", "run", 9) in _callee_set(result_dict["a.py"])
    assert _same_line_set(result_dict["a.py"]) == {("engine.run", 3, 9)}
    assert {
        (name, line) for name, file_rel, _, line in _callee_set(result_dict["A.java"])
        if file_rel == "Engine.java" and name == "Engine.run"
    } == {("Engine.run", 3), ("Engine.run", 4)}
    assert _same_line_set(result_dict["A.java"]) == {("engine.run", 2, 3), ("engine.run", 2, 4)}


def test_variable_of_a_block_counts_for_the_block(tmp_path):
    result_dict = _analyze(tmp_path, {
        "k.js": "exports.fresh = function () {};\nexports.local = function () {};\n",
        "app.js": (
            "const k = require('./k');\n"           # 1
            "function d() {\n"
            "  try {\n"
            "    k.fresh();\n"                      # 4
            "  } catch (k) {\n"
            "    k.message;\n"                      # 6
            "  }\n"
            "  for (let k = 0; k < 1; k++) { k; }\n"   # 8
            "  k.local();\n"                        # 9
            "}\n"
            "function e() {\n"
            "  if (d) { var k = 1; }\n"             # 12
            "  return k.local;\n"                   # 13
            "}\n"
        ),
        "a.c": (
            "int total;\n"                          # 1
            "int f(int n) {\n"
            "  if (n) {\n"
            "    int total = n;\n"                  # 4
            "    return total;\n"                   # 5
            "  }\n"
            "  return total;\n"                     # 7
            "}\n"
        ),
    })
    assert {line for name, _, _, line in _callee_set(result_dict["app.js"])} == {4, 9}
    assert _same_line_set(result_dict["a.c"]) == {("total", 1, 7)}


def test_js_class_written_as_a_value_and_export_of_a_name(tmp_path):
    result_dict = _analyze(tmp_path, {
        "k.js": (
            "module.exports = class Klass {\n"      # 1
            "  run() { return 1; }\n"               # 2
            "  static of() { return new Klass(); }\n"   # 3
            "};\n"
        ),
        "m.js": (
            "const Foo = class {\n"                 # 1
            "  go() {}\n"                           # 2
            "};\n"
            "function local() {}\n"                 # 4
            "exports.Foo = Foo;\n"
            "exports.local = local;\n"
        ),
        "app.js": (
            "const Klass = require('./k');\n"
            "const { local } = require('./m');\n"
            "Klass.of();\n"                         # 3
            "local();\n"                            # 4
        ),
    })
    assert _definition_list(result_dict["k.js"]) == [
        ("default", "class", 1, 4), ("run", "method_definition", 2, 2), ("of", "method_definition", 3, 3),
    ]
    assert _definition_list(result_dict["m.js"]) == [
        ("Foo", "class", 1, 3), ("go", "method_definition", 2, 2),
        ("local", "function_declaration", 4, 4),
    ]
    assert _callee_set(result_dict["app.js"]) == {
        ("Klass.of", "k.js", "of", 3), ("local", "m.js", "local", 4),
    }


def test_java_method_reference_and_variables_of_loops_and_patterns(tmp_path):
    result_dict = _analyze(tmp_path, {
        "Shape.java": (
            "public class Shape {\n"
            "  public double area() { return 0; }\n"    # 2
            "  public String describe() { return \"\"; }\n"   # 3
            "}\n"
        ),
        "Main.java": (
            "import java.util.List;\n"
            "import java.util.function.Function;\n"
            "public class Main {\n"
            "  double area() { return 1; }\n"       # 4
            "  void run(List<Shape> shapes, Object o) {\n"
            "    Function<Shape, Double> f = Shape::area;\n"   # 6
            "    Object made = shapes.stream().map(Shape::new);\n"   # 7
            "    for (Shape each : shapes) { each.describe(); }\n"   # 8
            "    if (o instanceof Shape s) { s.area(); }\n"   # 9
            "  }\n"
            "}\n"
        ),
    })
    callee_set = _callee_set(result_dict["Main.java"])
    assert ("Shape.area", "Shape.java", "area", 6) in callee_set
    assert ("Shape", "Shape.java", "Shape", 7) in callee_set
    assert ("Shape.describe", "Shape.java", "describe", 8) in callee_set
    assert ("Shape.area", "Shape.java", "area", 9) in callee_set
    assert _same_line_set(result_dict["Main.java"]) == set()


def test_kotlin_get_call_after_a_property_and_class_parameters(tmp_path):
    result_dict = _analyze(tmp_path, {
        "a.kt": (
            "package a\n"
            "\n"
            "const val DEFAULT = 1\n"               # 3
            "class Q { val b = 2 }\n"               # 4
            "class P(var a: Int, val b: Int = DEFAULT)\n"   # 5
            "enum class Kind { ROUND, FLAT }\n"     # 6
            "\n"
            "fun route() {\n"                       # 8
            "    val service by lazy { P(1) }\n"
            "    get(\"/a\") {\n"                   # 10
            "        service.a\n"
            "    }\n"
            "}\n"                                   # 13
        ),
    })
    assert _definition_list(result_dict["a.kt"]) == [
        ("DEFAULT", "property_declaration", 3, 3),
        ("Q", "class_declaration", 4, 4), ("b", "property_declaration", 4, 4),
        ("P", "class_declaration", 5, 5),
        ("Kind", "class_declaration", 6, 6), ("ROUND", "enum_entry", 6, 6), ("FLAT", "enum_entry", 6, 6),
        ("route", "function_declaration", 8, 13),
    ]
    assert _same_line_set(result_dict["a.kt"]) == {("DEFAULT", 3, 5), ("P", 5, 9)}


def test_c_macros_written_after_a_declarator_and_as_a_statement(tmp_path):
    result_dict = _analyze(tmp_path, {
        "m.h": (
            "#define GUARDED_BY(x)\n"               # 1
            "#define LOCKS(x)\n"                    # 2
            "#define NOEXCEPT_MACRO noexcept\n"     # 3
            "#define DISALLOW_COPY(T) T(const T&) = delete\n"   # 4
            "#define MODULE_INIT(f)\n"              # 5
        ),
        "a.h": (
            "#include \"m.h\"\n"
            "class Mutex {\n"                       # 2
            " public:\n"
            "  explicit Mutex(int n) LOCKS(n) : n_(n) {}\n"   # 4
            "  void Lock() LOCKS(mu_) { n_++; }\n"  # 5
            "  void Plain() NOEXCEPT_MACRO { n_--; }\n"   # 6
            "  int Read(int* out)\n"                # 7
            "      LOCKS(mu_);\n"                   # 8
            "  DISALLOW_COPY(Mutex);\n"             # 9
            " private:\n"
            "  int n_ GUARDED_BY(mu_);\n"           # 11
            "  int mu_;\n"                          # 12
            "};\n"
            "int count GUARDED_BY(global_mu);\n"    # 14
            "MODULE_INIT(setup);\n"                 # 15
        ),
    })
    assert _definition_list(result_dict["a.h"]) == [
        ("Mutex", "class_specifier", 2, 13),
        ("Mutex", "function_definition", 4, 4),
        ("Lock", "function_definition", 5, 5),
        ("Plain", "function_definition", 6, 6),
        ("Read", "function_declarator", 7, 8),
        ("n_", "field_declaration", 11, 11),
        ("mu_", "field_declaration", 12, 12),
        ("count", "declaration", 14, 14),
    ]
    callee_set = _callee_set(result_dict["a.h"])
    assert {line for name, _, _, line in callee_set if name == "LOCKS"} == {4, 5, 8}
    assert ("NOEXCEPT_MACRO", "m.h", "NOEXCEPT_MACRO", 6) in callee_set
    assert ("DISALLOW_COPY", "m.h", "DISALLOW_COPY", 9) in callee_set
    assert {line for name, _, _, line in callee_set if name == "GUARDED_BY"} == {11, 14}
    assert ("MODULE_INIT", "m.h", "MODULE_INIT", 15) in callee_set
    assert ("mu_", 12, 5) in _same_line_set(result_dict["a.h"])
    assert ("mu_", 12, 11) in _same_line_set(result_dict["a.h"])


def test_cpp_class_macro_inside_a_class_and_in_a_forward_declaration(tmp_path):
    result_dict = _analyze(tmp_path, {
        "store.h": (
            "class PUBLIC_API Store;\n"                                   # 1
            "class PUBLIC_API Store {\n"                                  # 2
            " public:\n"
            "  class PUBLIC_API Visitor {\n"                              # 4
            "   public:\n"
            "    virtual void Visit(int key);\n"                          # 6
            "  };\n"                                                      # 7
            "  struct PUBLIC_API Entry : public Visitor { int key; };\n"  # 8
            "  class PUBLIC_API Ref;\n"                                   # 9
            "  class PUBLIC_API Tag final {};\n"                          # 10
            "  int Size();\n"                                             # 11
            "};\n"                                                        # 12
            "class PUBLIC_API Error : public std::exception {};\n"        # 13
            "class PUBLIC_API Empty {};\n"                                # 14
            "class Plain bare;\n"                                         # 15
        ),
        "use.cc": (
            '#include "store.h"\n'
            "int Count(Store* store) { return store->Size(); }\n"        # 2
        ),
    })
    store_list = _definition_list(result_dict["store.h"])
    assert store_list == [
        ("Store", "class_specifier", 2, 12),
        ("Visitor", "class_specifier", 4, 7),
        ("Visit", "function_declarator", 6, 6),
        ("Entry", "struct_specifier", 8, 8),
        ("key", "field_declaration", 8, 8),
        ("Tag", "class_specifier", 10, 10),
        ("Size", "function_declarator", 11, 11),
        ("Error", "class_specifier", 13, 13),
        ("Empty", "class_specifier", 14, 14),
        ("bare", "declaration", 15, 15),
    ]
    project_dir = tmp_path / "case"
    definition_list = extract_definitions(
        parse_file(str(project_dir / "store.h"))[0], EXT_TO_DEFINITION_DICT["h"],
    )
    owner_dict = member_owner_dict(definition_list)
    assert {d.name: owner_dict[id(d)].name for d in definition_list if id(d) in owner_dict} == {
        "Visitor": "Store", "Visit": "Visitor", "Entry": "Store", "key": "Entry", "Tag": "Store",
        "Size": "Store",
    }
    callee_set = _callee_set(result_dict["use.cc"])
    assert ("Store.Size", "store.h", "Size", 2) in callee_set


def test_c_upper_case_name_declared_after_a_macro(tmp_path):
    for file_rel in ("api.h", "api.c"):
        result_dict = _analyze(tmp_path / file_rel.replace(".", "_"), {
            file_rel: (
                "LIB_API int RESET(void);\n"                  # 1
                "int CDECL_CALL START(int a);\n"              # 2
                "LIB_API extern const int LEVEL_MAX;\n"      # 3
                "LIB_API int TOTAL;\n"                        # 4
                "LIB_API int lower(void);\n"                  # 5
                "LIB_API int ONE(void) LOCKS(mu);\n"          # 6
            ),
        })
        assert _definition_list(result_dict[file_rel]) == [
            ("RESET", "function_declarator", 1, 1),
            ("START", "function_declarator", 2, 2),
            ("LEVEL_MAX", "declaration", 3, 3),
            ("TOTAL", "declaration", 4, 4),
            ("lower", "function_declarator", 5, 5),
            ("ONE", "function_declarator", 6, 6),
        ]


def test_c_declarations_read_outside_a_function_are_no_definitions(tmp_path):
    result_dict = _analyze(tmp_path, {
        "a.cc": (
            "int answer;\n"                         # 1
            "TEST(printf_test, enum) {\n"           # 2
            "  volatile int local = answer;\n"      # 3
            "  use(local);\n"                       # 4
            "}\n"
            "namespace geo {\n"                     # 6
            "int scale;\n"                          # 7
            "}\n"
        ),
    })
    assert "local" not in _definition_name_list(result_dict["a.cc"])
    assert {"answer", "scale"} <= set(_definition_name_list(result_dict["a.cc"]))


def test_cpp_typed_variables_made_with_arguments_and_in_a_range_loop(tmp_path):
    result_dict = _analyze(tmp_path, {
        "log.h": (
            "namespace log {\n"
            "class Writer {\n"                      # 2
            " public:\n"
            "  explicit Writer(int f);\n"
            "  void Add(int v);\n"                  # 5
            "};\n"
            "}\n"
        ),
        "a.cc": (
            "#include \"log.h\"\n"
            "#include <vector>\n"
            "void run(std::vector<log::Writer>& all) {\n"   # 3
            "  log::Writer log(1);\n"               # 4
            "  log.Add(2);\n"                       # 5
            "  for (log::Writer& each : all) {\n"   # 6
            "    each.Add(3);\n"                    # 7
            "  }\n"
            "}\n"
            "log::Writer* make() { return new log::Writer(4); }\n"   # 10
        ),
    })
    callee_set = _callee_set(result_dict["a.cc"])
    assert ("Writer.Add", "log.h", "Add", 5) in callee_set
    assert ("Writer.Add", "log.h", "Add", 7) in callee_set
    assert ("Writer", "log.h", "Writer", 10) in callee_set


def test_rust_associated_type_of_a_trait(tmp_path):
    result_dict = _analyze(tmp_path, {
        "Cargo.toml": "[package]\nname = \"t\"\nversion = \"0.1.0\"\n",
        "src/lib.rs": (
            "pub trait Runner {\n"                  # 1
            "    type Output;\n"                    # 2
            "    fn run(&self) -> Self::Output;\n"  # 3
            "}\n"
        ),
    })
    assert ("Output", "associated_type", 2, 2) in _definition_list(result_dict["src/lib.rs"])
    assert _same_line_set(result_dict["src/lib.rs"]) == {("Runner::Output", 2, 3)}


def test_member_leads_to_a_base_class_of_the_file_and_to_no_other_class(tmp_path):
    result_dict = _analyze(tmp_path, {
        "a.py": (
            "class Base:\n"                         # 1
            "    kind = \"b\"\n"                    # 2
            "    def show(self):\n"                 # 3
            "        return self.render()\n"        # 4
            "\n"
            "class Child(Base):\n"                  # 6
            "    def render(self):\n"               # 7
            "        return self.show() + self.kind\n"   # 8
            "\n"
            "def kind():\n"                         # 10
            "    return 1\n"
            "\n"
            "class Other:\n"                        # 13
            "    limit = kind\n"                    # 14
            "    def get(self):\n"                  # 15
            "        return self.kind, self.show(), kind()\n"   # 16
        ),
        "a.ts": (
            "class Node { size = 1; }\n"            # 1
            "const obj = {\n"
            "  grow() { return this.size; },\n"     # 3
            "};\n"
            "class Leaf {\n"
            "  weight() { return this.size; }\n"    # 6
            "}\n"
        ),
        "a.rs": (
            "pub struct Config;\n"
            "pub trait Runner { fn run(&self) -> usize; fn twice(&self) -> usize { 2 } }\n"   # 2
            "impl Runner for Config { fn run(&self) -> usize { 1 } }\n"   # 3
            "impl Config {\n"
            "    pub fn go(&self) -> usize { self.twice() + self.run() }\n"   # 5
            "}\n"
            "pub struct Other;\n"
            "impl Other {\n"
            "    pub fn go(&self) -> usize { self.twice() }\n"   # 9
            "}\n"
        ),
    })
    assert _same_line_set(result_dict["a.py"]) == {
        ("render", 7, 4), ("Base", 1, 6), ("show", 3, 8), ("kind", 2, 8),
        ("kind", 10, 14), ("kind", 10, 16),
    }
    assert _same_line_set(result_dict["a.ts"]) == {("size", 1, 3)}
    assert _same_line_set(result_dict["a.rs"]) == {
        ("Runner", 2, 3), ("twice", 2, 5), ("run", 3, 5),
        ("Config", 1, 3), ("Config", 1, 4), ("Other", 7, 8),
    }


def test_name_written_by_itself_leads_to_a_definition_that_is_no_member(tmp_path):
    result_dict = _analyze(tmp_path, {
        "Cargo.toml": "[package]\nname = \"t\"\nversion = \"0.1.0\"\n",
        "src/lib.rs": (
            "pub struct Glob;\n"
            "impl Glob {\n"
            "    pub fn literal(&self) -> usize { 0 }\n"   # 3
            "}\n"
            "#[cfg(test)]\n"
            "mod tests {\n"
            "    macro_rules! literal {\n"          # 7
            "        ($name:ident) => { fn $name() {} };\n"
            "    }\n"
            "    literal!(first);\n"                # 10
            "}\n"
        ),
    })
    assert ("literal", 7, 10) in _same_line_set(result_dict["src/lib.rs"])


def test_kotlin_constructor_property_declared_with_a_type(tmp_path):
    result_dict = _analyze(tmp_path, {
        "a.kt": (
            "package a\n"
            "class Engine(val power: Int) { fun run() = power }\n"   # 2
            "class Car(private val engine: Engine, val spare: Engine = Engine(1)) {\n"   # 3
            "    fun drive(): Int = engine.run() + spare.run()\n"   # 4
            "}\n"
        ),
    })
    assert ("Engine.run", 2, 4) in _same_line_set(result_dict["a.kt"])


def test_c_block_after_a_macro_line_is_the_body_of_a_function(tmp_path):
    result_dict = _analyze(tmp_path, {
        "a.c": (
            "#define YY_DECL int yylex(void)\n"     # 1
            "#define YY_SETUP \\\n"
            "    setup()\n"
            "YY_DECL\n"                             # 4
            "{\n"
            "    int current;\n"                    # 6
            "    return current;\n"
            "}\n"
        ),
    })
    assert "current" not in _definition_name_list(result_dict["a.c"])


def test_class_head_arguments_are_no_base_classes(tmp_path):
    result_dict = _analyze(tmp_path, {
        "a.kt": (
            "package a\n"
            "open class Vehicle { fun stop() = 0 }\n"   # 2
            "class Engine { fun run() = 1 }\n"      # 3
            "class Car(val engine: Engine) : Vehicle() {\n"   # 4
            "    fun drive(): Int = run() + stop()\n"   # 5
            "}\n"
        ),
        "a.cpp": (
            "class day {};\n"                       # 1
            "class calendar {\n"
            " public:\n"
            "  int day() const { return 1; }\n"     # 4
            "};\n"
            "template <typename T> struct formatter {};\n"   # 6
            "template <> struct formatter<calendar> {};\n"   # 7
            "template <> struct formatter<day> {\n" # 8
            "  int format(day d) { return 0; }\n"   # 9
            "};\n"
            "void take(const day& value);\n"        # 11
        ),
    })
    assert ("stop", 2, 5) in _same_line_set(result_dict["a.kt"])
    assert ("day", 1, 9) in _same_line_set(result_dict["a.cpp"])
    assert ("day", 4, 9) not in _same_line_set(result_dict["a.cpp"])
    assert {name for name, _, line in _same_line_set(result_dict["a.cpp"]) if line == 11} == {"day"}


def test_c_variable_declared_with_a_typedef_name_of_the_file(tmp_path):
    result_dict = _analyze(tmp_path, {
        "a.c": (
            "typedef struct node { int value; struct node *next; } node_t;\n"   # 1
            "static node_t head;\n"                 # 2
            "static int sum(node_t *n) {\n"         # 3
            "  int total = 0;\n"
            "  for (node_t *p = n; p; p = p->next) { total += p->value; }\n"   # 5
            "  return total + head.value;\n"        # 6
            "}\n"
        ),
    })
    same_set = _same_set(result_dict["a.c"])
    assert ("node_t.value", "value", 5) in same_set
    assert ("node_t.next", "next", 5) in same_set
    assert ("node_t.value", "value", 6) in same_set
    assert ("head.value", "head", 6) in same_set


def test_containers_written_on_one_line_keep_their_members(tmp_path):
    project_dir = tmp_path / "case"
    project_dir.mkdir()
    file_dict = {
        "shape.h": "struct Point { int x; int y; }; struct Size { int w; int h; };\nint area(struct Size s);\n",
        "e.kt": "sealed class E { class A { val a = 1 }; class B { val b = 2 } }\n",
        "two.ts": "class A { x = 1; } class B { x = 2; }\n",
    }
    for file_rel, text in file_dict.items():
        (project_dir / file_rel).write_text(text, encoding="utf-8")

    def definitions_of(file_rel: str) -> list:
        file_abs = str(project_dir / file_rel)
        return extract_definitions(parse_file(file_abs)[0], EXT_TO_DEFINITION_DICT[language_ext(file_abs)])

    shape_list = definitions_of("shape.h")
    assert [d.name for d in shape_list] == ["Point", "x", "y", "Size", "w", "h", "area"]
    assert [d.name for d in select_top_level_definitions(shape_list)] == ["Point", "Size", "area"]
    owner_dict = member_owner_dict(shape_list)
    assert {d.name: owner_dict[id(d)].name for d in shape_list if id(d) in owner_dict} == {
        "x": "Point", "y": "Point", "w": "Size", "h": "Size",
    }
    kotlin_list = definitions_of("e.kt")
    owner_dict = member_owner_dict(kotlin_list)
    assert {d.name: owner_dict[id(d)].name for d in kotlin_list if id(d) in owner_dict} == {
        "A": "E", "a": "A", "B": "E", "b": "B",
    }
    # A member is looked up inside the class it is written in, also on the same line
    assert source_definition("two.ts", "B.x", str(project_dir))[1] == "x = 2"
