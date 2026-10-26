"""Files of the languages with import statements depend on each other through the names those bind.

Python, JavaScript / TypeScript, Java / Kotlin and C / C++ go through one path: the
names the import statements of a file bind are followed to the file that defines them
(ImportBinder), each usage of such a name is a reference (import_reference_target_list),
and callee_usages, caller_usages and the edges of the dependency graph are read from
those references.
"""

import logging
import os

from codetwine import import_binding
from codetwine.extractors.definitions import extract_definitions
from codetwine.extractors.definition_source import source_definition
from codetwine.extractors.dependency_graph import build_project_dependencies
from codetwine.config.settings import (
    EXT_TO_DEFINITION_DICT, EXT_TO_IMPORT_RESOLVE_DICT, language_ext,
)
from codetwine.file_analyzer import get_file_dependencies
from codetwine.import_binding import clear_import_binder_cache
from codetwine.import_reference import clear_import_reference_cache, import_reference_target_list
from codetwine.parsers.ts_parser import parse_file
from codetwine.reference_target import reference_target_list
from codetwine.utils.file_utils import copy_path_to_rel


def _make_files(tmp_path, file_dict: dict[str, str], name: str = "case") -> str:
    project_dir = tmp_path / name
    for file_rel, text in file_dict.items():
        path = project_dir / file_rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return str(project_dir)


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


def _analyze(project_dir: str) -> dict[str, dict]:
    """Run get_file_dependencies for every file of the project."""
    graph = _graph(project_dir)
    project_file_set = {f for f in graph if language_ext(os.path.join(project_dir, f))}
    caller_map = {f: sorted(graph[f]["callers"]) for f in project_file_set}
    return {
        file_rel: get_file_dependencies(
            os.path.join(project_dir, file_rel), project_dir, project_file_set, caller_map,
        )
        for file_rel in graph
    }


def _callee_dict(result: dict) -> dict[str, tuple[str, list[int]]]:
    """Map each usage name to (file, lines); of a name linked to several files, the first."""
    callee_dict: dict[str, tuple[str, list[int]]] = {}
    for usage in result["callee_usages"]:
        callee_dict.setdefault(usage["name"], (usage["from"], usage["lines"]))
    return callee_dict


def _callee_file_dict(result: dict) -> dict[str, list[str]]:
    """Map each usage name to the files it is linked to."""
    file_dict: dict[str, list[str]] = {}
    for usage in result["callee_usages"]:
        file_dict.setdefault(usage["name"], []).append(usage["from"])
    return file_dict


def _same_dict(result: dict) -> dict[str, list[int]]:
    return {u["name"]: u["lines"] for u in result["same_file_usages"]}


def _definition_name_list(project_dir: str, file_rel: str) -> list[tuple[str, str]]:
    file_abs = os.path.join(project_dir, file_rel)
    root = parse_file(file_abs)[0]
    return [
        (d.name, d.type)
        for d in extract_definitions(root, EXT_TO_DEFINITION_DICT[language_ext(file_abs)])
    ]


def _assert_callers_mirror_callees(result_dict: dict[str, dict]) -> None:
    """Every callee usage of a file is a caller usage of the file it leads to, and back."""
    callee_set = {
        (file_rel, u["from"], u["name"], tuple(u["lines"]))
        for file_rel, result in result_dict.items() for u in result["callee_usages"]
    }
    caller_set = {
        (u["file"], file_rel, u["name"], tuple(u["lines"]))
        for file_rel, result in result_dict.items() for u in result["caller_usages"]
    }
    assert callee_set == caller_set


# == Python ====================================================================

PYTHON_FILE_DICT = {
    "pkg/__init__.py": "from .core import Engine, make_engine\nVERSION = '1.0'\n",
    "pkg/core.py": """\
from .helpers import clamp, DEFAULT_LIMIT as LIMIT
from . import helpers as h
import pkg.helpers

COUNT = 0
total = limit = 3
(a, b) = (1, 2)
type Alias = list[int]


class Engine:
    size: int = 3

    def run(self, v):
        return clamp(v, LIMIT) + h.offset() + pkg.helpers.offset() + self.size

    def name(self):
        return self.run(1)


def make_engine(clamp=None):
    LIMIT = 5
    return clamp, LIMIT


def other(values):
    return [clamp for clamp in values]
""",
    "pkg/helpers.py": """\
from dataclasses import dataclass

DEFAULT_LIMIT = 10


def clamp(v, limit):
    return min(v, limit)


def offset():
    return 1


@dataclass
class Shape:
    w: int

    def area(self):
        return self.w
""",
    "pkg/sub/__init__.py": "",
    "pkg/sub/deep.py": "NAME = 'deep'\n",
    "app/main.py": """\
import pkg
from pkg import Engine, make_engine as mk
from pkg.core import *
from pkg.sub import deep as deep_mod
import pkg.core

e = pkg.Engine()
print(COUNT, deep_mod.NAME, pkg.core.COUNT, mk())


def run(Engine=None):
    return Engine.run()
""",
}


def test_python_names_are_followed_to_the_file_that_defines_them(tmp_path):
    project_dir = _make_files(tmp_path, PYTHON_FILE_DICT)
    result_dict = _analyze(project_dir)
    callee = _callee_dict(result_dict["app/main.py"])

    # A name __init__.py imports leads to the file that defines it
    assert callee["pkg.Engine"] == ("pkg/core.py", [7])
    assert callee["mk"] == ("pkg/core.py", [8])
    # from m import * binds the names of m
    assert callee["COUNT"] == ("pkg/core.py", [8])
    # A module of a package whose __init__.py is empty
    assert callee["deep_mod.NAME"] == ("pkg/sub/deep.py", [8])
    # import a.b after import a: each name leads to its own file
    assert callee["pkg.core.COUNT"] == ("pkg/core.py", [8])
    # A parameter named like an imported name is not a usage of it
    assert "Engine.run" not in callee
    context_dict = {u["name"]: u["target_context"] for u in result_dict["app/main.py"]["callee_usages"]}
    assert context_dict["pkg.Engine"].startswith("class Engine:")
    assert context_dict["mk"].startswith("def make_engine(")
    _assert_callers_mirror_callees(result_dict)


def test_python_local_names_and_members(tmp_path):
    project_dir = _make_files(tmp_path, PYTHON_FILE_DICT)
    result = _analyze(project_dir)["pkg/core.py"]
    callee = _callee_dict(result)

    assert callee["clamp"] == ("pkg/helpers.py", [15])
    assert callee["LIMIT"] == ("pkg/helpers.py", [15])
    # from . import module / import a.b bind the module
    assert callee["h.offset"] == ("pkg/helpers.py", [15])
    assert callee["pkg.helpers.offset"] == ("pkg/helpers.py", [15])
    # A member written after self is a usage of the file's own name
    assert _same_dict(result) == {"size": [15], "run": [18]}


def test_python_definitions(tmp_path):
    project_dir = _make_files(tmp_path, PYTHON_FILE_DICT)
    name_list = [name for name, _ in _definition_name_list(project_dir, "pkg/core.py")]
    assert name_list == [
        "COUNT", "total", "limit", "a", "b", "Alias", "Engine", "size", "run", "name",
        "make_engine", "other",
    ]
    # The members of a decorated class are listed
    assert _definition_name_list(project_dir, "pkg/helpers.py")[3:] == [
        ("Shape", "class_definition"), ("w", "expression_statement"),
        ("area", "function_definition"),
    ]


def test_a_field_named_like_an_import_is_no_usage_of_it(tmp_path):
    project_dir = _make_files(tmp_path, {
        "rules.py": "def check(x):\n    return x\n\n\ndef tidy(x):\n    return x\n",
        "form.py": (
            "from rules import check, tidy\n\n\n"
            "class Form:\n    check: bool = True\n    tidy = False\n\n"
            "    def run(self, v):\n        return check(v) if self.check else v\n\n\n"
            "cache = None\n\n\n"
            "def load():\n    global cache\n    cache = 1\n"
        ),
        "acme/util.kt": "package acme\n\nfun helper(): Int = 1\n",
        "acme2/car.kt": (
            "package acme2\n\nimport acme.helper\n\n"
            "class Car {\n    val helper = 2\n    fun go() = helper()\n}\n"
        ),
    })
    result_dict = _analyze(project_dir)
    # The name a field declaration binds is no usage of the import of that name
    assert _callee_dict(result_dict["form.py"]) == {"check": ("rules.py", [9])}
    # A variable of the module written in a function stays a usage of it, and the
    # field written after self is a usage of the field
    assert _same_dict(result_dict["form.py"]) == {"check": [9], "cache": [16, 17]}
    assert _callee_dict(result_dict["acme2/car.kt"]) == {"helper": ("acme/util.kt", [7])}


def test_python_import_inside_a_package_does_not_take_a_sibling_file(tmp_path):
    project_dir = _make_files(tmp_path, {
        "pkg/__init__.py": "X = 1\n",
        "pkg/types.py": "class Local:\n    pass\n",
        "pkg/use.py": "from types import TracebackType\nimport types\nt = types.ModuleType\n",
        "script/util.py": "def helper():\n    return 1\n",
        "script/run.py": "from util import helper\nhelper()\n",
    })
    graph = _graph(project_dir)
    assert graph["pkg/use.py"]["callees"] == set()
    assert graph["script/run.py"]["callees"] == {"script/util.py"}


def test_python_module_under_a_directory_is_found_by_the_end_of_its_path(tmp_path):
    project_dir = _make_files(tmp_path, {
        "backend/app/__init__.py": "X = 1\n",
        "backend/app/models.py": "class User:\n    pass\n",
        "backend/check/check_models.py": "from app.models import User\nu = User()\n",
    })
    result = _analyze(project_dir)["backend/check/check_models.py"]
    assert _callee_dict(result) == {"User": ("backend/app/models.py", [2])}


# == JavaScript / TypeScript ===================================================

TS_FILE_DICT = {
    "src/lib/math.ts": """\
export const PI = 3.14;
export let counter = 0, other = 1;
export function add(a: number, b: number): number { return a + b; }
export default function mul(a: number, b: number) { return a * b; }
export class Vec {
  x = 0;
  len(): number { return this.x; }
}
export abstract class Base { abstract run(): void; }
export declare function declared(x: number): void;
export namespace Geo { export const ORIGIN = 0; }
function internal() {}
export { internal as renamed };
""",
    "src/lib/util.ts": "export function helper() { return 1; }\nexport const CONFIG = { a: 1 };\n",
    "src/lib/index.ts": """\
export * from './math';
import { helper } from './util.js';
export { helper };
""",
    "src/lib/anon.ts": "export default { name: 'anon' };\n",
    "src/app.tsx": """\
import product, { add, PI as pi, Vec } from './lib/math';
import * as M from './lib/math';
import { helper, renamed } from './lib';
import settings from './lib/anon';
const { CONFIG } = require('./lib/util');
const util = require('./lib/util');
const lazy = () => import('./lazy');

export function App() {
  const v: Vec = new Vec();
  const add = 3;
  const z = M.add(1, 2) + M.PI + product(2, 3) + pi + util.helper() + CONFIG.a;
  helper(); renamed();
  return { settings, add, z, v };
}
""",
    "src/lazy.tsx": "export default function Lazy() { return null; }\n",
}


def test_typescript_imports(tmp_path):
    project_dir = _make_files(tmp_path, TS_FILE_DICT)
    result_dict = _analyze(project_dir)
    callee = _callee_dict(result_dict["src/app.tsx"])

    # A default import under another name leads to the default export
    assert callee["product"] == ("src/lib/math.ts", [12])
    # A namespace import binds the module
    assert callee["M.add"] == ("src/lib/math.ts", [12])
    assert callee["M.PI"] == ("src/lib/math.ts", [12])
    assert callee["pi"] == ("src/lib/math.ts", [12])
    # require() of a module and of names of it
    assert callee["util.helper"] == ("src/lib/util.ts", [12])
    assert callee["CONFIG.a"] == ("src/lib/util.ts", [12])
    # A name re-exported by an index file leads to the file that defines it; "./util.js"
    # written for util.ts, and export * with a renamed export, are followed
    assert callee["helper"] == ("src/lib/util.ts", [13])
    assert callee["renamed"] == ("src/lib/math.ts", [13])
    # The default export of an unnamed value
    assert callee["settings"] == ("src/lib/anon.ts", [14])
    # A local constant named like an imported name is not a usage of it
    assert "add" not in callee
    context_dict = {u["name"]: u["target_context"] for u in result_dict["src/app.tsx"]["callee_usages"]}
    assert context_dict["product"].startswith("function mul(")
    assert context_dict["renamed"] == "function internal() {}"
    assert context_dict["settings"] == "export default { name: 'anon' };"
    # A dynamic import of a string adds an edge
    assert "src/lazy.tsx" in _graph(project_dir)["src/app.tsx"]["callees"]
    _assert_callers_mirror_callees(result_dict)


def test_typescript_definitions(tmp_path):
    project_dir = _make_files(tmp_path, TS_FILE_DICT)
    assert [name for name, _ in _definition_name_list(project_dir, "src/lib/math.ts")] == [
        "PI", "counter", "other", "add", "mul", "Vec", "x", "len", "Base", "run",
        "declared", "Geo", "ORIGIN", "internal",
    ]
    # A name bound by require() is a name of the import, not a definition
    assert _definition_name_list(project_dir, "src/app.tsx") == [
        ("lazy", "lexical_declaration"), ("App", "function_declaration"),
    ]
    assert _definition_name_list(project_dir, "src/lib/anon.ts") == [
        ("default", "export_statement"), ("name", "pair"),
    ]
    # this.x is a usage of the member; a parameter of a signature is not
    assert _same_dict(_analyze(project_dir)["src/lib/math.ts"]) == {"x": [7], "internal": [13]}


def test_commonjs_module(tmp_path):
    project_dir = _make_files(tmp_path, {
        "index.js": "module.exports = require('./lib/express');\n",
        "lib/express.js": """\
exports = module.exports = createApplication;

function createApplication() { return {}; }

exports.version = '1';
""",
        "examples/demo/index.js": """\
var express = require('../..');
var direct = require('../../lib/express');
var app = express();
console.log(direct.version);
""",
    })
    result_dict = _analyze(project_dir)
    assert ("version", "expression_statement") in _definition_name_list(project_dir, "lib/express.js")
    # require("../..") is the index file of the project root; the value of a module is
    # what it assigns to module.exports
    callee = _callee_dict(result_dict["examples/demo/index.js"])
    assert callee == {
        "express": ("lib/express.js", [3]),
        "direct.version": ("lib/express.js", [4]),
    }
    context_dict = {
        u["name"]: u["target_context"] for u in result_dict["examples/demo/index.js"]["callee_usages"]
    }
    assert context_dict["express"] == "function createApplication() { return {}; }"
    assert context_dict["direct.version"] == "exports.version = '1';"


def test_exported_object_entries_and_member_functions(tmp_path):
    project_dir = _make_files(tmp_path, {
        "lib/app.js": (
            "var app = exports = module.exports = {};\n"
            "app.init = function init() { this.cache = {}; };\n"
            "app.handle = (req) => req;\n"
        ),
        "lib/tools.js": (
            "function impl() { return 1; }\n"
            "function plain() { return 2; }\n"
            "module.exports = {\n"
            "  plain,\n"
            "  renamed: impl,\n"
            "  inline: function () { return 3; },\n"
            "  method() { return 4; },\n"
            "};\n"
        ),
        "main.js": (
            "var app = require('./lib/app');\n"
            "var tools = require('./lib/tools');\n"
            "app.init();\n"
            "tools.plain(); tools.renamed(); tools.inline(); tools.method();\n"
        ),
        "color.ts": "export enum Color { Red, Green = 2 }\nexport default { run: () => 1 };\n",
        "use.ts": "import conf, { Color } from './color';\nconst c = Color.Red; conf.run();\n",
    })
    result_dict = _analyze(project_dir)

    # A function assigned to a member at the top level of a file is a definition
    assert _definition_name_list(project_dir, "lib/app.js") == [
        ("app", "variable_declaration"), ("init", "expression_statement"),
        ("handle", "expression_statement"),
    ]
    # The entries of an exported object: a function is a definition, key: name leads to the name
    assert _definition_name_list(project_dir, "lib/tools.js") == [
        ("impl", "function_declaration"), ("plain", "function_declaration"),
        ("default", "expression_statement"), ("inline", "pair"), ("method", "method_definition"),
    ]
    context_dict = {u["name"]: u["target_context"] for u in result_dict["main.js"]["callee_usages"]}
    assert context_dict == {
        "app.init": "app.init = function init() { this.cache = {}; };",
        "tools.plain": "function plain() { return 2; }",
        "tools.renamed": "function impl() { return 1; }",
        "tools.inline": "inline: function () { return 3; }",
        "tools.method": "method() { return 4; }",
    }
    # The members of a TypeScript enum, and an entry of a default-exported object
    context_dict = {u["name"]: u["target_context"] for u in result_dict["use.ts"]["callee_usages"]}
    assert context_dict == {"Color.Red": "Red", "conf.run": "run: () => 1"}
    _assert_callers_mirror_callees(result_dict)


def test_a_typed_variable_counts_for_its_function_only(tmp_path):
    project_dir = _make_files(tmp_path, {
        "com/acme/User.java": (
            "package com.acme;\npublic class User {\n    public String getName() { return \"\"; }\n}\n"
        ),
        "com/acme/Use.java": (
            "package com.acme;\npublic class Use {\n"
            "    int a(User u) { return u.getName().length(); }\n"
            "    int b(String u) { return u.length(); }\n}\n"
        ),
    })
    callee = _callee_dict(_analyze(project_dir)["com/acme/Use.java"])
    # u of b() is a String: its u.length() is no usage of User
    assert callee == {
        "User": ("com/acme/User.java", [3]),
        "User.getName": ("com/acme/User.java", [3]),
    }


def test_names_written_apart_on_one_line_are_each_a_usage(tmp_path):
    project_dir = _make_files(tmp_path, {
        "com/acme/Conf.java": (
            "package com.acme;\npublic class Conf {\n"
            "    public Conf copy() { return this; }\n"
            "    public void merge(Conf c) {}\n"
            "    public static class Maker { public Conf make() { return new Conf(); } }\n}\n"
        ),
        "com/acme/Use.java": (
            "package com.acme;\npublic class Use {\n"
            "    private Conf conf;\n"
            "    Use(Conf conf) { this.conf = conf.copy(); }\n"
            "    void put(Conf conf) { this.conf.merge(conf); }\n"
            "    Conf get(Conf.Maker m) { Conf c = m.make(); return c; }\n}\n"
        ),
    })
    result = _analyze(project_dir)["com/acme/Use.java"]
    callee = _callee_dict(result)
    # A member, a parameter and a type written apart from a longer name of their line stay usages
    assert callee["Conf"] == ("com/acme/Conf.java", [3, 4, 5, 6])
    assert callee["Conf.copy"] == ("com/acme/Conf.java", [4])
    assert callee["Conf.merge"] == ("com/acme/Conf.java", [5])
    assert callee["Conf.Maker.make"] == ("com/acme/Conf.java", [6])
    assert _same_dict(result)["conf"] == [4]


def test_names_written_apart_in_one_annotation_string_are_each_a_usage(tmp_path):
    project_dir = _make_files(tmp_path, {
        "m.py": "class Engine:\n    pass\n",
        "a.py": (
            "import m\n\n\n"
            'def run(e: "m.Engine | m") -> None:\n'                 # 4
            "    return e\n\n\n"
            'def only(e: "m.Engine") -> None:\n'                   # 8
            "    return e\n"
        ),
    })
    callee = _callee_dict(_analyze(project_dir)["a.py"])
    # The module written by itself in the string of a longer name of it is a usage
    assert callee["m"] == ("m.py", [4])
    assert callee["m.Engine"] == ("m.py", [4, 8])


# == Java / Kotlin =============================================================

JAVA_ROOT = "core/src/main/java/com/acme"
JAVA_FILE_DICT = {
    f"{JAVA_ROOT}/model/User.java": """\
package com.acme.model;

public class User {
    public static final int MAX = 10;
    private String name, nick;

    public User(String name) { this.name = name; }
    public String getName() { return name; }
    public static User of(String n) { return new User(n); }

    public enum Kind { ADMIN, GUEST }
    public static class Builder {
        public User build() { return new User("x"); }
    }
}
""",
    f"{JAVA_ROOT}/model/Role.java": """\
package com.acme.model;

public record Role(String id) {
    public static Role admin() { return new Role("a"); }
}
""",
    f"{JAVA_ROOT}/model/Audited.java": (
        "package com.acme.model;\n\npublic @interface Audited { String value() default \"\"; }\n"
    ),
    f"{JAVA_ROOT}/util/Strings.java": """\
package com.acme.util;

public final class Strings {
    public static final String EMPTY = "";
    public static String upper(String s) { return s.toUpperCase(); }
}
""",
    "app/src/main/java/com/acme/svc/UserService.java": """\
package com.acme.svc;

import com.acme.model.User;
import com.acme.model.User.Builder;
import com.acme.model.*;
import static com.acme.util.Strings.upper;
import static com.acme.util.Strings.*;

@Audited("svc")
public class UserService {
    private User current;

    public User find(String upper) {
        User u = User.of(upper(upper));
        Role r = Role.admin();
        Builder b = new Builder();
        String e = EMPTY;
        return current.getName() == null ? u : b.build();
    }
}
""",
}


def test_java_imports(tmp_path):
    project_dir = _make_files(tmp_path, JAVA_FILE_DICT)
    result_dict = _analyze(project_dir)
    service = "app/src/main/java/com/acme/svc/UserService.java"
    callee = _callee_dict(result_dict[service])
    user_file = f"{JAVA_ROOT}/model/User.java"

    # A package is found through the package statements, under any source directory
    assert callee["User.of"] == (user_file, [14])
    # A wildcard import binds the types of the package
    assert callee["Audited"] == (f"{JAVA_ROOT}/model/Audited.java", [9])
    assert callee["Role.admin"] == (f"{JAVA_ROOT}/model/Role.java", [15])
    # A nested class and static members are imported through their class
    assert callee["Builder"] == (user_file, [16])
    assert callee["upper"] == (f"{JAVA_ROOT}/util/Strings.java", [14])
    assert callee["EMPTY"] == (f"{JAVA_ROOT}/util/Strings.java", [17])
    # A method called on a variable of an imported type is named after the type
    assert callee["User.getName"] == (user_file, [18])
    assert callee["Builder.build"] == (user_file, [18])
    assert _graph(project_dir)[service]["callees"] == {
        user_file, f"{JAVA_ROOT}/model/Role.java", f"{JAVA_ROOT}/model/Audited.java",
        f"{JAVA_ROOT}/util/Strings.java",
    }
    context_dict = {u["name"]: u["target_context"] for u in result_dict[service]["callee_usages"]}
    assert context_dict["User.of"].startswith("public static User of(")
    assert context_dict["Builder"].startswith("public static class Builder")
    _assert_callers_mirror_callees(result_dict)


def test_java_definitions_and_members(tmp_path):
    project_dir = _make_files(tmp_path, JAVA_FILE_DICT)
    assert _definition_name_list(project_dir, f"{JAVA_ROOT}/model/User.java") == [
        ("User", "class_declaration"), ("MAX", "field_declaration"),
        ("name", "field_declaration"), ("nick", "field_declaration"),
        ("User", "constructor_declaration"), ("getName", "method_declaration"),
        ("of", "method_declaration"), ("Kind", "enum_declaration"),
        ("ADMIN", "enum_constant"), ("GUEST", "enum_constant"),
        ("Builder", "class_declaration"), ("build", "method_declaration"),
    ]
    assert _definition_name_list(project_dir, f"{JAVA_ROOT}/model/Role.java")[0] == (
        "Role", "record_declaration",
    )
    assert _definition_name_list(project_dir, f"{JAVA_ROOT}/model/Audited.java")[0] == (
        "Audited", "annotation_type_declaration",
    )
    # this.name is the field; the parameter name on the same line is not
    assert _same_dict(_analyze(project_dir)[f"{JAVA_ROOT}/model/User.java"])["name"] == [7, 8]


KOTLIN_ROOT = "src/main/kotlin/com/acme"
KOTLIN_FILE_DICT = {
    f"{KOTLIN_ROOT}/model/Models.kt": """\
package com.acme.model

const val MAX = 10
val (first, second) = Pair(1, 2)
typealias Users = List<User>

data class User(val name: String) {
    companion object {
        fun of(n: String) = User(n)
    }
    enum class Kind {
        ADMIN, GUEST
    }
}

object Registry {
    fun add(u: User) {}
}
fun topLevel(x: Int): Int = x + MAX
""",
    "src/main/java/com/acme/model/Legacy.java": (
        "package com.acme.model;\n\npublic class Legacy {\n    public static int count() { return 1; }\n}\n"
    ),
    f"{KOTLIN_ROOT}/svc/Service.kt": """\
package com.acme.svc

import com.acme.model.User
import com.acme.model.User.Kind
import com.acme.model.Registry
import com.acme.model.topLevel as tl
import com.acme.model.Users
import com.acme.model.Legacy

class Service {
    fun find(name: String): User {
        val u = User.of(name)
        val k: Kind = Kind.ADMIN
        Registry.add(u)
        val users: Users = listOf(u)
        return if (tl(Legacy.count()) > 0) u else User(name)
    }
}
""",
}


def test_kotlin_imports(tmp_path):
    project_dir = _make_files(tmp_path, KOTLIN_FILE_DICT)
    result_dict = _analyze(project_dir)
    callee = _callee_dict(result_dict[f"{KOTLIN_ROOT}/svc/Service.kt"])
    models = f"{KOTLIN_ROOT}/model/Models.kt"

    # A name of a package is found in the file that defines it, whatever the file is named
    assert callee["User.of"] == (models, [12])
    assert callee["Registry.add"] == (models, [14])
    assert callee["Users"] == (models, [15])
    # A nested class, an import under another name, and a Java class of the package
    assert callee["Kind.ADMIN"] == (models, [13])
    assert callee["tl"] == (models, [16])
    assert callee["Legacy.count"] == ("src/main/java/com/acme/model/Legacy.java", [16])
    _assert_callers_mirror_callees(result_dict)

    assert [name for name, _ in _definition_name_list(project_dir, models)] == [
        "MAX", "first", "second", "Users", "User", "of", "Kind", "ADMIN", "GUEST",
        "Registry", "add", "topLevel",
    ]


# == C / C++ ===================================================================

C_FILE_DICT = {
    "include/list.h": """\
#ifndef LIST_H
#define LIST_H
#define LIST_MAX 16
#define SQUARE(x) ((x) * (x))
typedef struct node {
    int value;
    struct node *next;
} node_t;
typedef int (*cmp_fn)(const void *, const void *);
enum color { RED, GREEN = 2 };
union data { int i; float f; };
extern int list_count;
node_t *list_push(node_t *head, int value);
int list_len(const node_t *head);
#endif
""",
    "src/list.c": """\
#include "list.h"

int list_count = 0;
static int hidden = 1, hidden2, *cursor;

node_t *list_push(node_t *head, int value) {
    int local = SQUARE(value);
    list_count++;
    return head;
}

int list_len(const node_t *head) {
    int list_count = 0;
    for (; head; head = head->next) list_count++;
    return list_count + hidden;
}
""",
    "src/main.c": """\
#include "list.h"

int main(void) {
    node_t *head = 0;
    struct node other;
    enum color c = GREEN;
    union data d;
    head = list_push(head, LIST_MAX);
    return list_len(head) + list_count + head->value;
}
""",
}


def test_c_include_and_definitions(tmp_path):
    project_dir = _make_files(tmp_path, C_FILE_DICT)
    result_dict = _analyze(project_dir)

    assert _definition_name_list(project_dir, "include/list.h") == [
        ("LIST_MAX", "preproc_def"), ("SQUARE", "preproc_function_def"),
        ("node_t", "type_definition"), ("node", "struct_specifier"),
        ("value", "field_declaration"), ("next", "field_declaration"),
        ("cmp_fn", "type_definition"), ("color", "enum_specifier"),
        ("RED", "enumerator"), ("GREEN", "enumerator"), ("data", "union_specifier"),
        ("i", "field_declaration"), ("f", "field_declaration"),
        ("list_count", "declaration"), ("list_push", "function_declarator"),
        ("list_len", "function_declarator"),
    ]
    # A function returning a pointer is one definition over its whole body, and its
    # local variables are no definitions
    list_c = result_dict["src/list.c"]
    assert [(d["name"], d["start_line"], d["end_line"]) for d in list_c["definitions"]] == [
        ("list_count", 3, 3), ("hidden", 4, 4), ("hidden2", 4, 4), ("cursor", 4, 4),
        ("list_push", 6, 10), ("list_len", 12, 16),
    ]
    # A #define ends on its own line
    assert result_dict["include/list.h"]["definitions"][0]["end_line"] == 3

    # "list.h" is found under include/ by the end of its path
    callee = _callee_dict(result_dict["src/main.c"])
    assert callee["list_push"] == ("include/list.h", [8])
    assert callee["GREEN"] == ("include/list.h", [6])
    assert callee["node"] == ("include/list.h", [5])
    assert callee["data"] == ("include/list.h", [7])
    assert callee["node_t.value"] == ("include/list.h", [9])
    context_dict = {
        (u["name"], u["from"]): u["target_context"]
        for u in result_dict["src/main.c"]["callee_usages"]
    }
    assert context_dict["list_push", "include/list.h"] == "node_t *list_push(node_t *head, int value);"
    assert context_dict["node_t.value", "include/list.h"] == "int value;"

    # A function the header only declares is linked to the file that defines it as well
    assert _callee_file_dict(result_dict["src/main.c"])["list_push"] == ["include/list.h", "src/list.c"]
    assert context_dict["list_push", "src/list.c"].startswith("node_t *list_push(node_t *head, int value) {")
    assert _graph(project_dir)["src/main.c"]["callees"] == {"include/list.h", "src/list.c"}
    assert "list_count" not in {
        u["name"] for u in result_dict["src/main.c"]["callee_usages"] if u["from"] == "src/list.c"
    }

    # A name the file defines itself is its own; a local variable of that name is not a usage
    assert _same_dict(list_c) == {"list_count": [8], "hidden": [15]}
    assert _callee_dict(list_c)["SQUARE"] == ("include/list.h", [7])
    _assert_callers_mirror_callees(result_dict)


CPP_FILE_DICT = {
    "include/geo/shape.hpp": """\
#pragma once

namespace geo {

constexpr double PI = 3.14;

class Shape {
public:
    virtual double area() const = 0;
    static int count();
    bool operator==(const Shape& o) const;
protected:
    static int count_;
};

class Circle final : public Shape {
public:
    Circle(double r);
    double area() const override;
};

enum class Mode { Fast, Slow };
struct Point { double x, y; };
double dist(const Point& a, const Point& b);

namespace detail { int helper(); }

}  // namespace geo
""",
    "src/shape.cpp": """\
#include "geo/shape.hpp"

namespace geo {

int Shape::count() { return count_; }
double Circle::area() const { return PI; }
double dist(const Point& a, const Point& b) { return a.x - b.x; }

}
""",
    "src/main.cpp": """\
#include "geo/shape.hpp"

int main() {
    geo::Circle c(1.0);
    geo::Shape* s = &c;
    geo::Mode m = geo::Mode::Fast;
    geo::Point a{0, 0};
    return s->area() + geo::Shape::count() + geo::detail::helper() + geo::dist(a, a) + geo::PI;
}
""",
}


def test_cpp_namespaces_and_qualified_names(tmp_path):
    project_dir = _make_files(tmp_path, CPP_FILE_DICT)
    result_dict = _analyze(project_dir)
    header = "include/geo/shape.hpp"

    # A name written with its namespaces is named from the first part that is a definition
    callee = _callee_dict(result_dict["src/main.cpp"])
    # c is declared with Circle: the variable is named after its type
    assert callee["Circle"] == (header, [4, 5])
    assert callee["Shape"] == (header, [5])
    assert callee["Mode::Fast"] == (header, [6])
    assert callee["Point"] == (header, [7, 8])
    assert callee["Shape::count"] == (header, [8])
    assert callee["helper"] == (header, [8])
    assert callee["dist"] == (header, [8])
    assert callee["PI"] == (header, [8])
    assert callee["Shape.area"] == (header, [8])
    assert "geo" not in callee
    context_dict = {
        (u["name"], u["from"]): u["target_context"]
        for u in result_dict["src/main.cpp"]["callee_usages"]
    }
    assert context_dict["Shape::count", header] == "static int count();"
    assert context_dict["Mode::Fast", header] == "Fast"
    # A member the class only declares is linked to the file that defines it as well
    assert context_dict["Shape::count", "src/shape.cpp"] == "int Shape::count() { return count_; }"
    assert _callee_file_dict(result_dict["src/main.cpp"])["dist"] == [header, "src/shape.cpp"]
    assert _callee_file_dict(result_dict["src/main.cpp"])["Shape.area"] == [header]

    # A member defined outside its class is named with its class and leads to its declaration
    assert _definition_name_list(project_dir, "src/shape.cpp") == [
        ("geo", "namespace_definition"), ("Shape::count", "function_definition"),
        ("Circle::area", "function_definition"), ("dist", "function_definition"),
    ]
    callee = _callee_dict(result_dict["src/shape.cpp"])
    assert callee["Shape::count"] == (header, [5])
    assert callee["Circle::area"] == (header, [6])
    assert callee["Point.x"] == (header, [7])
    assert [name for name, _ in _definition_name_list(project_dir, header)] == [
        "geo", "PI", "Shape", "area", "count", "operator==", "count_", "Circle", "Circle",
        "area", "Mode", "Fast", "Slow", "Point", "x", "y", "dist", "detail", "helper",
    ]
    _assert_callers_mirror_callees(result_dict)


def test_graph_edges_are_listed_in_path_order(tmp_path):
    project_dir = _make_files(tmp_path, {
        "a.py": "import b\nimport c\nimport d\nb.f(); c.f(); d.f()\n",
        "b.py": "def f():\n    pass\n",
        "c.py": "def f():\n    pass\n",
        "d.py": "def f():\n    pass\n",
    })
    entry = next(
        dep for dep in build_project_dependencies(project_dir) if dep["file"].endswith("a.py")
    )
    assert entry["callees"] == sorted(entry["callees"])
    assert len(entry["callees"]) == 3


def test_the_declarations_inside_a_callback_are_no_definitions(tmp_path):
    project_dir = _make_files(tmp_path, {
        "a.js": (
            "describe('x', function () {\n"
            "  var local = 1;\n"
            "  it('y', () => { const inner = 2; });\n"
            "});\n"
            "(function () {\n"
            "  var wrapped = 1;\n"
            "  function wrappedFn() {}\n"
            "})();\n"
            "class K { static { const s = 1; } m() {} }\n"
        ),
    })
    assert _definition_name_list(project_dir, "a.js") == [
        ("wrapped", "variable_declaration"), ("wrappedFn", "function_declaration"),
        ("K", "class_declaration"), ("m", "method_definition"),
    ]


def _target_set(project_dir: str, file_rel: str, first_file_list: list[str]) -> set[tuple[str, str]]:
    """Resolve the references of a file after the references of other files, from empty caches."""
    graph = _graph(project_dir)
    project_file_set = {f for f in graph if language_ext(os.path.join(project_dir, f))}
    clear_import_reference_cache()
    clear_import_binder_cache()
    for first_file in first_file_list:
        reference_target_list(first_file, project_file_set, project_dir)
    return {
        (target.name, target.file_rel)
        for target in reference_target_list(file_rel, project_file_set, project_dir)
    }


def test_headers_that_include_each_other_give_the_same_names_in_any_order(tmp_path):
    project_dir = _make_files(tmp_path, {
        "a.h": '#include "b.h"\n#include "c.h"\nint a_fn(void);\n',
        "b.h": '#include "a.h"\nint b_fn(void);\n',
        "c.h": "int c_fn(void);\n",
        "main.c": '#include "b.h"\nint main(void) { return c_fn() + a_fn() + b_fn(); }\n',
    })
    expected_set = {("a_fn", "a.h"), ("b_fn", "b.h"), ("c_fn", "c.h")}
    for first_file_list in ([], ["a.h"], ["b.h"], ["c.h", "b.h", "a.h"]):
        assert _target_set(project_dir, "main.c", first_file_list) == expected_set


def test_python_modules_that_import_each_other_give_the_same_names_in_any_order(tmp_path):
    project_dir = _make_files(tmp_path, {
        "pkg/__init__.py": "from .core import Engine\nfrom .helpers import helper_fn\n",
        "pkg/core.py": (
            "from pkg import helper_fn\n\n\n"
            "class Engine:\n    def run(self):\n        return helper_fn()\n"
        ),
        "pkg/helpers.py": "def helper_fn():\n    return 1\n",
        "third.py": "from pkg.core import helper_fn, Engine\n\nhelper_fn()\nEngine().run()\n",
    })
    expected_set = {("helper_fn", "pkg/helpers.py"), ("Engine", "pkg/core.py")}
    for first_file_list in ([], ["pkg/core.py"], ["pkg/__init__.py"], ["pkg/helpers.py"]):
        assert _target_set(project_dir, "third.py", first_file_list) == expected_set


def test_python_import_inside_a_function(tmp_path):
    project_dir = _make_files(tmp_path, {
        "m.py": "def helper():\n    return 1\n\n\ndef other():\n    return 2\n\n\nclass Tool:\n    pass\n",
        "n.py": "def helper():\n    return 3\n",
        "a.py": (
            "def f():\n"                                # 1
            "    from m import helper\n"                # 2
            "    return helper()\n"                     # 3
            "\n\n"
            "def g():\n"                                # 6
            "    import m\n"                            # 7
            "    return m.other()\n"                    # 8
            "\n\n"
            "def h():\n"                                # 11
            "    from n import helper\n"                # 12
            "    from m import Tool as T\n"             # 13
            "    return helper(), T()\n"                # 14
            "\n\n"
            "def helper(value):\n"                      # 17
            "    return value\n"                        # 18
            "\n\n"
            "def k(m):\n"                               # 21
            "    import json\n"                         # 22
            "    return helper(m.other), json\n"        # 23
        ),
    })
    result_dict = _analyze(project_dir)
    result = result_dict["a.py"]
    usage_set = {(u["name"], u["from"], tuple(u["lines"])) for u in result["callee_usages"]}
    assert usage_set == {
        ("helper", "m.py", (3,)),
        ("m.other", "m.py", (8,)),
        ("helper", "n.py", (14,)),
        ("T", "m.py", (14,)),
    }
    # Outside the functions that import it, helper is the function of the file itself
    assert _same_dict(result) == {"helper": [23]}
    assert _graph(project_dir)["a.py"]["callees"] == {"m.py", "n.py"}
    _assert_callers_mirror_callees(result_dict)


def test_function_that_imports_a_name_like_its_own_stays_a_definition_of_the_file(tmp_path):
    project_dir = _make_files(tmp_path, {
        "impl.py": "def open_file(path):\n    return path\n",
        "api.py": (
            "def open_file(path):\n"                    # 1
            "    from impl import open_file\n"          # 2
            "    return open_file(path)\n"              # 3
            "\n\n"
            "def run():\n"                              # 6
            "    return open_file('x')\n"               # 7
        ),
    })
    result = _analyze(project_dir)["api.py"]
    assert _callee_dict(result) == {"open_file": ("impl.py", [3])}
    assert _same_dict(result) == {"open_file": [7]}


def test_javascript_require_inside_a_function(tmp_path):
    project_dir = _make_files(tmp_path, {
        "m.js": "function helper() { return 1; }\nfunction other() { return 2; }\nmodule.exports = { helper, other };\n",
        "a.js": (
            "function f() {\n"                                  # 1
            "  const m = require('./m');\n"                     # 2
            "  return m.helper();\n"                            # 3
            "}\n"
            "function g() {\n"                                  # 5
            "  const { other } = require('./m');\n"             # 6
            "  return other();\n"                               # 7
            "}\n"
            "const run = () => {\n"                             # 9
            "  const { helper: hp } = require('./m');\n"        # 10
            "  return hp();\n"                                  # 11
            "};\n"
            "function k(other) {\n"                             # 13
            "  const fs = require('fs');\n"                     # 14
            "  return other(fs);\n"                             # 15
            "}\n"
        ),
    })
    result_dict = _analyze(project_dir)
    result = result_dict["a.js"]
    assert _callee_dict(result) == {
        "m.helper": ("m.js", [3]),
        "other": ("m.js", [7]),
        "hp": ("m.js", [11]),
    }
    _assert_callers_mirror_callees(result_dict)


def test_javascript_name_only_passed_on_is_no_name_of_the_file(tmp_path):
    project_dir = _make_files(tmp_path, {
        "m.js": "export function a() { return 1; }\nexport function c() { return 3; }\n",
        "idx.js": (
            "export { a } from './m';\n"                        # 1
            "import { c as p } from './m';\n"                   # 2
            "function a() { return 2; }\n"                      # 3
            "const q = 5;\n"                                    # 4
            "export { p as q2 };\n"                             # 5
            "export function run() { return a() + q + p(); }\n" # 6
        ),
        "use.js": "import { a, q2, run } from './idx';\na(); q2(); run();\n",
    })
    result_dict = _analyze(project_dir)
    # The export statement is a usage of the name it passes on; the function a of the
    # file itself is not the a of m
    assert _callee_dict(result_dict["idx.js"]) == {"a": ("m.js", [1]), "p": ("m.js", [5, 6])}
    assert _same_dict(result_dict["idx.js"]) == {"a": [6], "q": [6]}
    assert _callee_dict(result_dict["use.js"]) == {
        "a": ("m.js", [2]), "q2": ("m.js", [2]), "run": ("idx.js", [2]),
    }
    _assert_callers_mirror_callees(result_dict)


def test_python_default_value_and_annotation_are_read_outside_the_function(tmp_path):
    project_dir = _make_files(tmp_path, {
        "m.py": "def helper():\n    return 1\n\n\nclass Tool:\n    pass\n",
        "a.py": (
            "from m import helper, Tool\n"              # 1
            "\n\n"
            "def f(helper=helper):\n"                   # 4
            "    return helper()\n"                     # 5
            "\n\n"
            "def g(Tool: Tool) -> Tool:\n"              # 8
            "    return Tool\n"                         # 9
            "\n\n"
            "h = lambda helper=helper: helper\n"        # 12
            "k = lambda helper: [Tool for Tool in helper]\n"   # 13
        ),
    })
    result = _analyze(project_dir)["a.py"]
    assert _callee_dict(result) == {"helper": ("m.py", [4, 12]), "Tool": ("m.py", [8])}


def test_javascript_member_of_require_and_awaited_import(tmp_path):
    project_dir = _make_files(tmp_path, {
        "m.js": "function helper() { return 1; }\nfunction other() { return 2; }\nmodule.exports = { helper, other };\n",
        "n.mjs": "export function deep() { return 3; }\nexport default function main() { return 4; }\n",
        "a.js": (
            "var hp = require('./m').helper;\n"                             # 1
            "var other = require('./m').other;\n"                           # 2
            "var join = require('node:path').join;\n"                       # 3
            "hp(); other(); join();\n"                                      # 4
            "async function h() {\n"                                        # 5
            "  const n = await import('./n.mjs');\n"                        # 6
            "  const { deep, default: main } = await import('./n.mjs');\n"  # 7
            "  return n.deep() + deep() + main();\n"                        # 8
            "}\n"
        ),
    })
    result_dict = _analyze(project_dir)
    result = result_dict["a.js"]
    assert _callee_dict(result) == {
        "hp": ("m.js", [4]),
        "other": ("m.js", [4]),
        "n.deep": ("n.mjs", [8]),
        "deep": ("n.mjs", [8]),
        "main": ("n.mjs", [8]),
    }
    context_dict = {u["name"]: u["target_context"] for u in result["callee_usages"]}
    assert context_dict["hp"].startswith("function helper()")
    assert context_dict["main"].startswith("function main()")
    # A member of a module outside the project is no name of the file itself
    assert _same_dict(result) == {}
    _assert_callers_mirror_callees(result_dict)


def test_typescript_modules_are_resolved_through_the_config_file(tmp_path):
    project_dir = _make_files(tmp_path, {
        "tsconfig.base.json": '{"compilerOptions": {"baseUrl": "./lib"}}\n',
        "tsconfig.json": (
            "{\n"
            '  // the paths of the package\n'
            '  "extends": "./tsconfig.base",\n'
            '  "compilerOptions": {\n'
            '    "strict": true /* a comment */,\n'
            '    "paths": {\n'
            '      "app": ["../src/index.ts"],\n'
            '      "app/*": ["../src/*.ts"],\n'
            '      "@/*": ["../src/deep/*"],\n'
            "    },\n"
            "  },\n"
            "}\n"
        ),
        "src/index.ts": "export { create } from './store';\n",
        "src/store.ts": "export function create() { return 1; }\n",
        "src/react.ts": "export function useStore() { return 2; }\n",
        "src/deep/tool/index.ts": "export function tool() { return 3; }\n",
        "lib/shared/util.ts": "export function util() { return 4; }\n",
        "spec/a.test.ts": (
            "import { create } from 'app';\n"                   # 1
            "import { useStore } from 'app/react';\n"           # 2
            "import { tool } from '@/tool';\n"                  # 3
            "import { util } from 'shared/util';\n"             # 4
            "import { useState } from 'react';\n"               # 5
            "create(); useStore(); tool(); util(); useState();\n"   # 6
        ),
        "examples/tsconfig.json": '{"compilerOptions": {"strict": true}}\n',
        "examples/demo.ts": "import { create } from 'app';\nimport { useState } from 'react';\ncreate(); useState();\n",
    })
    result_dict = _analyze(project_dir)
    assert _callee_dict(result_dict["spec/a.test.ts"]) == {
        "create": ("src/store.ts", [6]),
        "useStore": ("src/react.ts", [6]),
        "tool": ("src/deep/tool/index.ts", [6]),
        "util": ("lib/shared/util.ts", [6]),
    }
    # The config file of a directory counts for the files under it: without "paths"
    # and "baseUrl" a module that is not relative is no file of the project
    assert _callee_dict(result_dict["examples/demo.ts"]) == {}
    assert _graph(project_dir)["examples/demo.ts"]["callees"] == set()
    _assert_callers_mirror_callees(result_dict)


def test_javascript_module_without_a_config_file_is_looked_up_from_the_project_root(tmp_path):
    project_dir = _make_files(tmp_path, {
        "lib/util.js": "export function util() { return 1; }\n",
        "app/main.js": "import { util } from 'lib/util';\nutil();\n",
    })
    result_dict = _analyze(project_dir)
    assert _callee_dict(result_dict["app/main.js"]) == {"util": ("lib/util.js", [2])}


def test_a_usage_carries_the_definition_it_leads_to(tmp_path):
    project_dir = _make_files(tmp_path, {
        "m.js": (
            "function helper() { return 1; }\n"            # 1
            "function other() { return 2; }\n"             # 2
            "module.exports = { helper, other };\n"        # 3
        ),
        "a.js": (
            "const { helper: hp } = require('./m');\n"     # 1
            "const m = require('./m');\n"                  # 2
            "function run() { return hp() + m.other(); }\n"    # 3
            "run();\n"                                     # 4
            "m;\n"                                         # 5
        ),
        "n.mjs": "export function deep() { return 3; }\n",
        "b.mjs": "import * as n from './n.mjs';\nexport const all = n;\n",
    })
    result_dict = _analyze(project_dir)
    result = result_dict["a.js"]
    target_dict = {
        u["name"]: (u["target_name"], u["target_start_line"]) for u in result["callee_usages"]
    }
    # The name of the usage is the one the file writes; the target is the definition.
    # A module used as a value is its default export
    assert target_dict == {"hp": ("helper", 1), "m.other": ("other", 2), "m": ("default", 3)}
    # A module without a default export used as a value leads to no definition
    assert [
        (u["name"], u["target_context"], u["target_name"], u["target_start_line"])
        for u in result_dict["b.mjs"]["callee_usages"]
    ] == [("n", None, None, None)]
    assert result["same_file_usages"] == [
        {"lines": [4], "name": "run", "target_name": "run", "target_start_line": 3},
    ]


def test_a_destructuring_target_with_a_default_value_or_a_rest_is_a_definition(tmp_path):
    project_dir = _make_files(tmp_path, {
        "m.js": "function helper() { return 1; }\nmodule.exports = { helper };\n",
        "a.js": (
            "const cfg = {};\n"                                           # 1
            "const { hp: inner = 1, plain = 2, ...rest } = cfg;\n"        # 2
            "const [first = 0, , ...tail] = [];\n"                        # 3
            "const { helper: hp = null, other = 3 } = require('./m');\n"  # 4
            "hp();\n"                                                     # 5
        ),
        "p.py": "first, *rest = [1, 2, 3]\n[head, (mid, *tail)] = [1, (2, 3)]\n",
    })
    assert [name for name, _ in _definition_name_list(project_dir, "a.js")] == [
        "cfg", "inner", "plain", "rest", "first", "tail",
    ]
    assert [name for name, _ in _definition_name_list(project_dir, "p.py")] == [
        "first", "rest", "head", "mid", "tail",
    ]
    result_dict = _analyze(project_dir)
    assert _callee_dict(result_dict["a.js"]) == {"hp": ("m.js", [5])}
    _assert_callers_mirror_callees(result_dict)


def test_a_class_declared_with_a_macro_in_front_of_its_name_is_a_class(tmp_path):
    project_dir = _make_files(tmp_path, {
        "xml.h": (
            "#define XML_LIB\n"                                    # 1
            "class XML_LIB Node {\n"                               # 2
            "public:\n"                                            # 3
            "    int Depth();\n"                                   # 4
            "};\n"                                                 # 5
            "class XML_LIB Document : public Node {\n"             # 6
            "public:\n"                                            # 7
            "    int Parse(const char* text);\n"                   # 8
            "    friend class Node;\n"                             # 9
            "    struct XML_LIB Options final { int depth; };\n"   # 10
            "};\n"                                                 # 11
            "struct stat_like { int size; };\n"                    # 12
            "struct stat_like global_stat{};\n"                    # 13
        ),
        "main.cpp": (
            '#include "xml.h"\n'                                   # 1
            "int main() {\n"                                       # 2
            "    Document doc;\n"                                  # 3
            "    return doc.Parse(\"<a/>\");\n"                    # 4
            "}\n"
        ),
    })
    file_abs = os.path.join(project_dir, "xml.h")
    root_node, content = parse_file(file_abs)
    assert content.decode("utf-8").splitlines()[5] == "class XML_LIB Document : public Node {"
    definition_list = extract_definitions(root_node, EXT_TO_DEFINITION_DICT[language_ext(file_abs)])
    assert [(d.name, d.type, d.start_line, d.end_line) for d in definition_list] == [
        ("XML_LIB", "preproc_def", 1, 1),
        ("Node", "class_specifier", 2, 5),
        ("Depth", "function_declarator", 4, 4),
        ("Document", "class_specifier", 6, 11),
        ("Parse", "function_declarator", 8, 8),
        ("Options", "struct_specifier", 10, 10),
        ("depth", "field_declaration", 10, 10),
        ("stat_like", "struct_specifier", 12, 12),
        ("size", "field_declaration", 12, 12),
        ("global_stat", "declaration", 13, 13),
    ]
    document = next(d for d in definition_list if d.name == "Document")
    assert content[document.start_byte:document.end_byte].decode("utf-8").startswith(
        "class XML_LIB Document : public Node {"
    )
    result_dict = _analyze(project_dir)
    assert _callee_dict(result_dict["main.cpp"]) == {
        "Document": ("xml.h", [3]),
        "Document.Parse": ("xml.h", [4]),
    }
    # The macro name is a usage of the macro on the lines it is written on
    assert _same_dict(result_dict["xml.h"])["XML_LIB"] == [2, 6, 10]
    _assert_callers_mirror_callees(result_dict)


def test_a_member_written_by_its_name_inside_a_member_defined_outside_its_class(tmp_path):
    project_dir = _make_files(tmp_path, {
        "xml.h": (
            "class Node {\n"                                       # 1
            "public:\n"
            "    int Depth();\n"                                   # 3
            "protected:\n"
            "    int depth_;\n"                                    # 5
            "};\n"
            "class Document : public Node {\n"                     # 7
            "public:\n"
            "    int Parse(const char* text);\n"                   # 9
            "    int Count();\n"                                   # 10
            "private:\n"
            "    int count_;\n"                                    # 12
            "};\n"
            "int helper();\n"                                      # 14
        ),
        "xml.cpp": (
            '#include "xml.h"\n'                                   # 1
            "int Node::Depth() { return depth_; }\n"               # 2
            "int Document::Count() { return count_; }\n"           # 3
            "int Document::Parse(const char* text) {\n"            # 4
            "    int local = Depth();\n"                           # 5
            "    count_ = local + Count() + helper();\n"           # 6
            "    return count_;\n"                                 # 7
            "}\n"
            "int helper() { int depth_ = 0; return count_ + depth_; }\n"  # 9
        ),
        "main.cpp": (
            '#include "xml.h"\n'                                   # 1
            "class Local : public Document {\n"                    # 2
            "public:\n"
            "    int Run() { return Count() + depth_; }\n"         # 4
            "};\n"
            "int run() {\n"                                        # 6
            "    Document doc;\n"                                  # 7
            "    return doc.Depth();\n"                            # 8
            "}\n"
        ),
    })
    result_dict = _analyze(project_dir)
    target_dict = {
        (usage["name"], usage["target_name"], usage["target_start_line"]): usage["lines"]
        for usage in result_dict["xml.cpp"]["callee_usages"]
    }
    # A member of the class and of its base class; outside a member, the name is no member
    assert target_dict == {
        ("Node::Depth", "Depth", 3): [2],
        ("depth_", "depth_", 5): [2],
        ("Document::Count", "Count", 10): [3],
        ("count_", "count_", 12): [3, 6, 7],
        ("Document::Parse", "Parse", 9): [4],
        ("Depth", "Depth", 3): [5],
        ("Count", "Count", 10): [6],
    }
    # The member also leads to its definition in the file itself
    assert _same_dict(result_dict["xml.cpp"]) == {"Depth": [5], "helper": [6], "Count": [6]}
    # Inside a class, a member of a base class; a member only a base class has leads to
    # the base class and to the file that defines it
    assert {
        (usage["name"], usage["from"], usage["target_name"], usage["target_start_line"],
         tuple(usage["lines"]))
        for usage in result_dict["main.cpp"]["callee_usages"]
    } == {
        ("Document", "xml.h", "Document", 7, (2, 7)),
        ("Count", "xml.h", "Count", 10, (4,)),
        ("Count", "xml.cpp", "Document::Count", 3, (4,)),
        ("depth_", "xml.h", "depth_", 5, (4,)),
        ("Document.Depth", "xml.h", "Depth", 3, (8,)),
        ("Document.Depth", "xml.cpp", "Node::Depth", 2, (8,)),
    }
    _assert_callers_mirror_callees(result_dict)


def test_javascript_member_read_where_the_module_is_required_and_import_callback(tmp_path):
    project_dir = _make_files(tmp_path, {
        "m.js": (
            "function helper() { return 1; }\n"
            "function other() { return 2; }\n"
            "function run() { return 3; }\n"
            "module.exports = { helper, other, run };\n"
        ),
        "a.js": (
            "require('./m').run();\n"                                          # 1
            "const v = require('./m').helper;\n"                               # 2
            "function f() { return require('./m').other(1); }\n"               # 3
            "import('./m').then((m) => m.run());\n"                            # 4
            "import('./m').then(({ helper, other: o }) => helper() + o());\n"  # 5
            "import('./m').then(function (mod) { return mod.other(); });\n"    # 6
            "require('fs').readFileSync('x');\n"                               # 7
            "v();\n"                                                           # 8
            "function run() { return 0; }\n"                                   # 9
            "run();\n"                                                         # 10
        ),
    })
    result_dict = _analyze(project_dir)
    result = result_dict["a.js"]
    assert {
        (usage["name"], usage["target_name"], tuple(usage["lines"]))
        for usage in result["callee_usages"]
    } == {
        ("run", "run", (1,)),
        ("other", "other", (3,)),
        ("m.run", "run", (4,)),
        ("helper", "helper", (5,)),
        ("o", "other", (5,)),
        ("mod.other", "other", (6,)),
        ("v", "helper", (8,)),
    }
    # The name of the file itself stays its own outside the line of the access
    assert _same_dict(result) == {"run": [10]}
    _assert_callers_mirror_callees(result_dict)


def test_javascript_method_named_like_a_member_read_where_a_module_is_required(tmp_path):
    project_dir = _make_files(tmp_path, {
        "m.js": "function init() { return 1; }\nmodule.exports = { init };\n",
        "a.js": (
            "require('./m').init();\n"                       # 1
            "class App {\n"                                  # 2
            "  init() { return 1; }\n"                       # 3
            "  start() { return this.init(); }\n"            # 4
            "}\n"
            "module.exports = App;\n"                        # 6
        ),
    })
    result_dict = _analyze(project_dir)
    result = result_dict["a.js"]
    assert [
        (usage["name"], usage["target_name"], usage["lines"]) for usage in result["callee_usages"]
    ] == [("init", "init", [1])]
    assert _same_dict(result) == {"init": [4], "App": [6]}
    _assert_callers_mirror_callees(result_dict)


def test_a_variable_given_something_else_no_longer_stands_for_its_class(tmp_path):
    project_dir = _make_files(tmp_path, {
        "eng.py": "class Engine:\n    def run(self): return 1\ndef make(): return 1\n",
        "use.py": (
            "from eng import Engine, make\n"
            "def a():\n"
            "    e = Engine()\n"          # 3
            "    e.run()\n"               # 4
            "    e = make()\n"            # 5
            "    e.run()\n"               # 6
            "def b():\n"
            "    e = Engine()\n"          # 8
            "    for e in [1]:\n"         # 9
            "        e.run()\n"           # 10
            "def c():\n"
            "    e = Engine()\n"          # 12
            "    with open('x') as e:\n"  # 13
            "        e.run()\n"           # 14
            "def d():\n"
            "    e = Engine()\n"          # 16
            "    e, f = 1, 2\n"           # 17
            "    e.run()\n"               # 18
            "def g():\n"
            "    e = Engine()\n"          # 20
            "    if (e := make()):\n"     # 21
            "        e.run()\n"           # 22
            "def h():\n"
            "    if (e := Engine()):\n"   # 24
            "        e.run()\n"           # 25
        ),
        "eng.js": (
            "class Engine { run() { return 1; } }\n"
            "function make() { return 1; }\n"
            "module.exports = { Engine, make };\n"
        ),
        "use.js": (
            "const { Engine, make } = require('./eng');\n"
            "function a() {\n"
            "  let e = new Engine();\n"                    # 3
            "  e.run();\n"                                 # 4
            "  e = make();\n"                              # 5
            "  e.run();\n"                                 # 6
            "}\n"
            "function b() {\n"
            "  let e = new Engine();\n"                    # 9
            "  for (e of [1]) { e.run(); }\n"              # 10
            "}\n"
            "function c() {\n"
            "  let e = new Engine();\n"                    # 13
            "  [e] = [1];\n"                               # 14
            "  e.run();\n"                                 # 15
            "}\n"
            "function d() {\n"
            "  let e;\n"                                   # 18
            "  e = new Engine();\n"                        # 19
            "  try { e.run(); }\n"                         # 20
            "  catch (e) { e.run(); }\n"                   # 21
            "}\n"
            "function f(pick) {\n"
            "  var Engine = pick();\n"                     # 24
            "  const e = new Engine();\n"                  # 25
            "  return e.run();\n"                          # 26
            "}\n"
        ),
        "use.ts": (
            "import { Engine, Base } from './eng2';\n"
            "export function a(flag: boolean): Base {\n"
            "  let e: Base;\n"                             # 3
            "  if (flag) { e = new Engine(); }\n"          # 4
            "  else { e = new Base(); }\n"                 # 5
            "  e.run();\n"                                 # 6
            "  return e;\n"                                # 7
            "}\n"
        ),
        "eng2.ts": (
            "export class Base { run(): number { return 0; } }\n"
            "export class Engine extends Base { run(): number { return 1; } }\n"
        ),
    })
    result_dict = _analyze(project_dir)
    assert _callee_dict(result_dict["use.py"]) == {
        "Engine": ("eng.py", [3, 8, 12, 16, 20, 24]),
        "Engine.run": ("eng.py", [4, 25]),
        "make": ("eng.py", [5, 21]),
    }
    assert _callee_dict(result_dict["use.js"]) == {
        "Engine": ("eng.js", [3, 9, 13, 19]),
        "Engine.run": ("eng.js", [4, 20]),
        "make": ("eng.js", [5]),
    }
    assert _callee_dict(result_dict["use.ts"]) == {
        "Base": ("eng2.ts", [2, 3, 4, 5, 7]),
        "Engine": ("eng2.ts", [4]),
        "Base.run": ("eng2.ts", [6]),
    }
    _assert_callers_mirror_callees(result_dict)


def test_python_star_import_takes_the_names_all_lists(tmp_path):
    project_dir = _make_files(tmp_path, {
        "listed.py": (
            '__all__ = ["shown", "_kept"]\n'
            '__all__ += ("added",)\n'
            '__all__.append("sub")\n'
            "def shown(): return 1\n"
            "def hidden(): return 2\n"
            "def _kept(): return 3\n"
            "def added(): return 4\n"
        ),
        "plain.py": "def open_name(): return 1\ndef _private(): return 2\n",
        "computed.py": "__all__ = [name for name in ('a',)]\ndef a(): return 1\ndef b(): return 2\n",
        "chain.py": "from listed import *\ndef own(): return 5\n",
        "pkg/__init__.py": '__all__ = ["part"]\n',
        "pkg/part.py": "VALUE = 1\n",
        "main.py": (
            "from listed import *\n"                              # 1
            "from plain import *\n"                               # 2
            "from computed import *\n"                            # 3
            "from pkg import *\n"                                 # 4
            "from plain import _private as direct\n"              # 5
            "shown(); hidden(); _kept(); added()\n"               # 6
            "open_name(); _private(); direct()\n"                 # 7
            "a(); b()\n"                                          # 8
            "part.VALUE\n"                                        # 9
        ),
        "second.py": (
            "from chain import *\n"                               # 1
            "shown(); hidden(); own()\n"                          # 2
        ),
    })
    result_dict = _analyze(project_dir)
    assert _callee_dict(result_dict["main.py"]) == {
        "shown": ("listed.py", [6]),
        "_kept": ("listed.py", [6]),
        "added": ("listed.py", [6]),
        "open_name": ("plain.py", [7]),
        "direct": ("plain.py", [7]),
        "a": ("computed.py", [8]),
        "b": ("computed.py", [8]),
        "part.VALUE": ("pkg/part.py", [9]),
    }
    # A file without __all__ passes on the names it took with * itself
    assert _callee_dict(result_dict["second.py"]) == {
        "shown": ("listed.py", [2]),
        "own": ("chain.py", [2]),
    }
    _assert_callers_mirror_callees(result_dict)


def test_a_variable_given_an_object_is_named_after_its_class(tmp_path):
    project_dir = _make_files(tmp_path, {
        "engine.py": (
            "class Engine:\n"
            "    def run(self): return 1\n"
            "    def stop(self): return 2\n"
            "def make(): return Engine()\n"
        ),
        "main.py": (
            "from engine import Engine, make\n"        # 1
            "import engine as eng\n"                   # 2
            "def use():\n"                             # 3
            "    e = Engine()\n"                       # 4
            "    e.run()\n"                            # 5
            "    e = make()\n"                         # 6
            "    e.stop()\n"                           # 7
            "def annotated(e: Engine, other):\n"       # 8
            "    e.stop()\n"                           # 9
            "    other.run()\n"                        # 10
            "def qualified():\n"                       # 11
            "    q = eng.Engine()\n"                   # 12
            "    q.run()\n"                            # 13
            "    with Engine() as w:\n"                # 14
            "        w.stop()\n"                       # 15
            "    x: Engine = make()\n"                 # 16
            "    x.run()\n"                            # 17
            "def not_a_class():\n"                     # 18
            "    m = make()\n"                         # 19
            "    m.run()\n"                            # 20
        ),
        "local.py": (
            "class Box:\n"                             # 1
            "    def open(self): return 1\n"           # 2
            "def use(e):\n"                            # 3
            "    b = Box()\n"                          # 4
            "    return b.open() + e.run()\n"          # 5
        ),
        "engine.js": "class Engine { run() { return 1; } }\nmodule.exports = { Engine };\n",
        "a.js": (
            "const { Engine } = require('./engine');\n"   # 1
            "function use() {\n"                          # 2
            "  const e = new Engine();\n"                 # 3
            "  return e.run();\n"                         # 4
            "}\n"
        ),
        "kit.ts": "export class Kit { open() { return 1; } }\n",
        "b.ts": (
            "import { Kit } from './kit';\n"              # 1
            "function use(k: Kit, n: number) {\n"         # 2
            "  const made: Kit = k;\n"                    # 3
            "  return k.open() + made.open() + n;\n"      # 4
            "}\n"
        ),
    })
    result_dict = _analyze(project_dir)
    assert _callee_dict(result_dict["main.py"]) == {
        "Engine": ("engine.py", [4, 8, 14, 16]),
        "Engine.run": ("engine.py", [5, 17]),
        "Engine.stop": ("engine.py", [9, 15]),
        "make": ("engine.py", [6, 16, 19]),
        "eng.Engine": ("engine.py", [12]),
        "eng.Engine.run": ("engine.py", [13]),
    }
    target_dict = {
        usage["name"]: (usage["target_name"], usage["target_start_line"])
        for usage in result_dict["main.py"]["callee_usages"]
    }
    assert target_dict["Engine.run"] == ("run", 2)
    assert target_dict["eng.Engine.run"] == ("run", 2)
    # A class of the file itself
    assert _same_dict(result_dict["local.py"]) == {"Box": [4], "Box.open": [5]}
    assert _callee_dict(result_dict["a.js"]) == {
        "Engine": ("engine.js", [3]),
        "Engine.run": ("engine.js", [4]),
    }
    assert _callee_dict(result_dict["b.ts"]) == {
        "Kit": ("kit.ts", [2, 3]),
        "Kit.open": ("kit.ts", [4]),
    }
    _assert_callers_mirror_callees(result_dict)


def test_typescript_modules_are_resolved_through_package_files_references_and_aliases(tmp_path):
    project_dir = _make_files(tmp_path, {
        # A workspace package entered through its build output, and a path under it
        "packages/ui/package.json": (
            '{"name": "@acme/ui", "main": "./dist/index.js",'
            ' "exports": {".": {"types": "./dist/index.d.ts", "default": "./dist/index.js"},'
            ' "./button": "./dist/button.js"}}'
        ),
        "packages/ui/src/index.ts": "export function render() { return 1; }\n",
        "packages/ui/src/button.ts": "export function button() { return 2; }\n",
        # The "imports" of the package the file is in
        "app/package.json": (
            '{"name": "app", "imports": {"#db": "./src/db.js", "#internal/*": "./src/internal/*.js"}}'
        ),
        "app/src/db.ts": "export function connect() { return 3; }\n",
        "app/src/internal/log.ts": "export function log() { return 4; }\n",
        # A config file that covers no file itself and refers to the one that does
        "app/tsconfig.json": '{"files": [], "references": [{"path": "./tsconfig.app.json"}]}',
        "app/tsconfig.app.json": (
            '{"include": ["src"], "exclude": ["src/**/*.spec.ts"],'
            ' "compilerOptions": {"baseUrl": ".", "paths": {"~/*": ["./src/*"]}}}'
        ),
        "app/src/store.ts": "export function store() { return 5; }\n",
        # An alias of the bundler config
        "app/vite.config.ts": (
            "import path from 'path';\n"
            "export default {\n"
            "  resolve: { alias: {\n"
            "    '@': path.resolve(__dirname, 'src'),\n"
            "    lib: fileURLToPath(new URL('./lib', import.meta.url)),\n"
            "  } },\n"
            "};\n"
        ),
        "app/lib/tool.ts": "export function tool() { return 6; }\n",
        "app/src/main.ts": (
            "import { render } from '@acme/ui';\n"             # 1
            "import { button } from '@acme/ui/button';\n"      # 2
            "import { connect } from '#db';\n"                 # 3
            "import { log } from '#internal/log';\n"           # 4
            "import { store } from '~/store';\n"               # 5
            "import { store as aliased } from '@/store';\n"    # 6
            "import { tool } from 'lib/tool';\n"               # 7
            "import { useState } from 'react';\n"              # 8
            "render(); button(); connect(); log(); store(); aliased(); tool(); useState();\n"  # 9
        ),
    })
    result_dict = _analyze(project_dir)
    assert _callee_dict(result_dict["app/src/main.ts"]) == {
        "render": ("packages/ui/src/index.ts", [9]),
        "button": ("packages/ui/src/button.ts", [9]),
        "connect": ("app/src/db.ts", [9]),
        "log": ("app/src/internal/log.ts", [9]),
        "store": ("app/src/store.ts", [9]),
        "aliased": ("app/src/store.ts", [9]),
        "tool": ("app/lib/tool.ts", [9]),
    }
    _assert_callers_mirror_callees(result_dict)



def test_javascript_relative_module_naming_a_directory(tmp_path):
    project_dir = _make_files(tmp_path, {
        # "." and ".." without a trailing "/"
        "lib/index.ts": "export function lib() { return 1; }\n",
        "lib/near.ts": "import { lib } from '.';\nlib();\n",
        "lib/sub/far.ts": "import { lib } from '..';\nlib();\n",
        # A directory entered through the "main" of its package.json
        "pkg/package.json": '{"main": "./out/entry.js"}',
        "pkg/out/entry.js": "module.exports = function entry() { return 2; };\n",
        "pkg/index.js": "module.exports = function other() { return 3; };\n",
        "test/t.js": "const entry = require('../pkg');\nentry();\n",
    })
    result_dict = _analyze(project_dir)
    assert _callee_dict(result_dict["lib/near.ts"]) == {"lib": ("lib/index.ts", [2])}
    assert _callee_dict(result_dict["lib/sub/far.ts"]) == {"lib": ("lib/index.ts", [2])}
    assert {usage["from"] for usage in result_dict["test/t.js"]["callee_usages"]} == {
        "pkg/out/entry.js",
    }
    _assert_callers_mirror_callees(result_dict)


def test_typescript_import_require_template_string_and_declaration_file(tmp_path):
    project_dir = _make_files(tmp_path, {
        "src/eq.ts": "export function eq() { return 1; }\n",
        "src/tpl.js": "exports.tpl = function () { return 2; };\n",
        "src/types.d.ts": "export interface User { id: number }\n",
        "src/api/index.d.ts": "export declare function get(): number;\n",
        "src/main.ts": (
            "import eq = require('./eq');\n"          # 1
            "const tpl = require(`./tpl`);\n"         # 2
            "const any = require(`./${name}`);\n"     # 3
            "import type { User } from './types';\n"  # 4
            "import { get } from './api';\n"          # 5
            "export function main(u: User) { return eq.eq() + tpl.tpl() + get(); }\n"  # 6
        ),
    })
    result_dict = _analyze(project_dir)
    assert _callee_dict(result_dict["src/main.ts"]) == {
        "eq.eq": ("src/eq.ts", [6]),
        "tpl.tpl": ("src/tpl.js", [6]),
        "User": ("src/types.d.ts", [6]),
        "get": ("src/api/index.d.ts", [6]),
    }
    _assert_callers_mirror_callees(result_dict)


def test_vite_alias_written_from_the_config_directory(tmp_path):
    project_dir = _make_files(tmp_path, {
        "web/vite.config.ts": "export default { resolve: { alias: { '@': '/src' } } };\n",
        "web/src/store/user.ts": "export function user() { return 1; }\n",
        "web/src/views/Home.ts": "import { user } from '@/store/user';\nuser();\n",
    })
    result_dict = _analyze(project_dir)
    assert _callee_dict(result_dict["web/src/views/Home.ts"]) == {
        "user": ("web/src/store/user.ts", [2]),
    }


def test_an_include_and_a_script_import_look_in_the_directory_of_the_file_first(tmp_path):
    project_dir = _make_files(tmp_path, {
        "common.h": "int root_common(void);\n",
        "lib/net/common.h": "int net_common(void);\n",
        "lib/net/sock.c": '#include "common.h"\nint sock(void) { return net_common(); }\n',
        "main.c": '#include "common.h"\nint main(void) { return root_common(); }\n',
        # A script directory that is no package
        "utils.py": "def root_util():\n    return 1\n",
        "scripts/utils.py": "def script_util():\n    return 2\n",
        "scripts/run.py": "import utils\nutils.script_util()\n",
    })
    graph = _graph(project_dir)
    assert graph["lib/net/sock.c"]["callees"] == {"lib/net/common.h"}
    assert graph["main.c"]["callees"] == {"common.h"}
    assert graph["scripts/run.py"]["callees"] == {"scripts/utils.py"}


def test_a_dependency_or_standard_module_is_not_a_file_under_another_src(tmp_path):
    project_dir = _make_files(tmp_path, {
        # A package the package.json depends on, and a file of that name under src/
        "package.json": '{"dependencies": {"conf": "^1.0"}, "devDependencies": {"@scope/kit": "*"}}',
        "src/conf.js": "module.exports = { conf: 1 };\n",
        "src/@scope/kit.js": "module.exports = { kit: 1 };\n",
        "src/own.js": "export function own() { return 1; }\n",
        "src/components/App.js": (
            "import conf from 'conf';\nimport kit from '@scope/kit';\nimport { own } from 'own';\n"
            "conf.x; kit.y; own();\n"
        ),
        # A module of the Python standard library and a file of that name elsewhere
        "app/main.py": "import logging\nfrom email.utils import formatdate\nlogging.info(formatdate())\n",
        "tools/lint/src/logging.py": "def info(text):\n    return text\n",
        "notify/__init__.py": "",
        "notify/email/__init__.py": "",
        "notify/email/utils.py": "def formatdate():\n    return ''\n",
    })
    graph = _graph(project_dir)
    assert graph["src/components/App.js"]["callees"] == {"src/own.js"}
    assert graph["app/main.py"]["callees"] == set()


def test_the_entry_of_a_package_follows_the_language_of_the_importing_file(tmp_path):
    project_dir = _make_files(tmp_path, {
        # A package with a hand-written declaration file next to its main file
        "mylib/package.json": '{"name": "mylib", "main": "index.js", "types": "index.d.ts"}',
        "mylib/index.js": "function createApp() { return 1; }\nmodule.exports = createApp;\n",
        "mylib/index.d.ts": "declare function createApp(): number;\nexport = createApp;\n",
        "mylib/test/a.test.js": "var app = require('../');\napp();\n",
        "mylib/test/b.test.js": "var app = require('..');\napp();\n",
        "mylib/test/c.test.js": "var app = require('mylib');\napp();\n",
        "mylib/test/d.test.ts": "import app = require('..');\napp();\n",
        # "exports" with a "types" condition
        "packages/kit/package.json": (
            '{"name": "kit", "exports": {".": {"types": "./types/index.d.ts",'
            ' "require": "./lib/index.js"}}}'
        ),
        "packages/kit/lib/index.js": "exports.run = function () { return 1; };\n",
        "packages/kit/types/index.d.ts": "export declare function run(): number;\n",
        "app/x.js": "const { run } = require('kit');\nrun();\n",
        "app/y.ts": "import { run } from 'kit';\nrun();\n",
        # "types" without "main": Node.js reads the index file
        "pkg/package.json": '{"types": "index.d.ts"}',
        "pkg/index.js": "exports.run = function () { return 1; };\n",
        "pkg/index.d.ts": "export declare function run(): number;\n",
        "test/t.js": "const { run } = require('../pkg');\nrun();\n",
        "test/u.ts": "import { run } from '../pkg';\nrun();\n",
        # "typings" and "module"
        "esm/package.json": '{"typings": "dist/index.d.ts", "module": "dist/index.mjs"}',
        "esm/dist/index.mjs": "export function run() { return 1; }\n",
        "esm/dist/index.d.ts": "export declare function run(): number;\n",
        "web/m.mjs": "import { run } from '../esm';\nrun();\n",
        "web/n.ts": "import { run } from '../esm';\nrun();\n",
    })
    graph = _graph(project_dir)
    for test_rel in ("mylib/test/a.test.js", "mylib/test/b.test.js", "mylib/test/c.test.js"):
        assert graph[test_rel]["callees"] == {"mylib/index.js"}
    assert graph["mylib/test/d.test.ts"]["callees"] == {"mylib/index.d.ts"}
    assert graph["app/x.js"]["callees"] == {"packages/kit/lib/index.js"}
    assert graph["app/y.ts"]["callees"] == {"packages/kit/types/index.d.ts"}
    assert graph["test/t.js"]["callees"] == {"pkg/index.js"}
    assert graph["test/u.ts"]["callees"] == {"pkg/index.d.ts"}
    assert graph["web/m.mjs"]["callees"] == {"esm/dist/index.mjs"}
    assert graph["web/n.ts"]["callees"] == {"esm/dist/index.d.ts"}


def test_a_package_named_like_a_standard_module_above_the_file(tmp_path):
    project_dir = _make_files(tmp_path, {
        # Apps under src/ and under a directory that is no package
        "src/manage.py": "import accounts.views\n",
        "src/profile/__init__.py": "",
        "src/profile/models.py": "class Profile:\n    pass\n",
        "src/calendar/__init__.py": "",
        "src/calendar/events.py": "def next_event():\n    return 1\n",
        "src/accounts/__init__.py": "",
        "src/accounts/views.py": (
            "from profile.models import Profile\nfrom calendar.events import next_event\n"
            "Profile()\nnext_event()\n"
        ),
        "backend/profile/__init__.py": "",
        "backend/profile/models.py": "class Profile:\n    pass\n",
        "backend/shop/__init__.py": "",
        "backend/shop/views.py": "from profile.models import Profile\nProfile()\n",
        # A package of that name inside another package, and under another part's src/
        "pkg/__init__.py": "",
        "pkg/email/__init__.py": "",
        "pkg/email/utils.py": "def formatdate():\n    return 1\n",
        "pkg/sub/__init__.py": "",
        "pkg/sub/mod.py": "from email.utils import formatdate\nformatdate()\n",
        "tool/setup.py": "from distutils.core import setup\nsetup()\n",
        "vendor/src/distutils/__init__.py": "",
        "vendor/src/distutils/core.py": "def setup():\n    return 1\n",
    })
    graph = _graph(project_dir)
    assert graph["src/accounts/views.py"]["callees"] == {
        "src/profile/models.py", "src/calendar/events.py",
    }
    assert graph["backend/shop/views.py"]["callees"] == {"backend/profile/models.py"}
    assert graph["pkg/sub/mod.py"]["callees"] == set()
    assert graph["tool/setup.py"]["callees"] == set()


def test_the_standard_modules_of_every_python_version_from_3_11():
    standard_module_set = EXT_TO_IMPORT_RESOLVE_DICT["py"]["standard_module_set"]
    # Removed in 3.12, removed in 3.13, added in 3.14, and in every version
    assert {"distutils", "imp", "asyncore", "smtpd"} <= standard_module_set
    assert {"cgi", "telnetlib", "lib2to3", "imghdr"} <= standard_module_set
    assert {"annotationlib", "compression"} <= standard_module_set
    assert {"logging", "email", "profile", "calendar"} <= standard_module_set


def test_a_relative_module_is_looked_up_only_from_the_file(tmp_path):
    project_dir = _make_files(tmp_path, {
        # A path that repeats the directory of the file
        "pkg/__init__.py": "",
        "pkg/b.py": "def helper():\n    return 1\n",
        "pkg/a.py": "from .b import helper\nhelper()\n",
        "pkg/pkg/__init__.py": "",
        "pkg/pkg/b.py": "def helper():\n    return 2\n",
        "pkg/sub/__init__.py": "",
        "pkg/sub/c.py": "from ..b import helper\nhelper()\n",
        "pkg/sub/pkg/__init__.py": "",
        "pkg/sub/pkg/b.py": "def helper():\n    return 3\n",
        "lib/util.h": "int util(void);\n",
        "lib/net/lib/util.h": "int util2(void);\n",
        "lib/net/sock.c": '#include "../util.h"\nint sock(void) { return util(); }\n',
        "lib/net/x.c": '#include "./common.h"\nint x(void) { return common(); }\n',
        "lib/net/common.h": "int common(void);\n",
        "lib/net/lib/net/common.h": "int common2(void);\n",
        # A relative module whose file is missing, and the same path under src/
        "app/main.js": "const { util } = require('./util');\nutil();\n",
        "src/app/util.js": "exports.util = function () { return 1; };\n",
        "mod/__init__.py": "",
        "mod/a.py": "from .c import helper\nhelper()\n",
        "src/mod/__init__.py": "",
        "src/mod/c.py": "def helper():\n    return 1\n",
    })
    graph = _graph(project_dir)
    assert graph["pkg/a.py"]["callees"] == {"pkg/b.py"}
    assert graph["pkg/sub/c.py"]["callees"] == {"pkg/b.py"}
    assert graph["lib/net/sock.c"]["callees"] == {"lib/util.h"}
    assert graph["lib/net/x.c"]["callees"] == {"lib/net/common.h"}
    assert graph["app/main.js"]["callees"] == set()
    assert graph["mod/a.py"]["callees"] == set()


def test_an_angle_include_is_not_looked_up_in_the_directory_of_the_file(tmp_path):
    project_dir = _make_files(tmp_path, {
        "config.h": "int root_config(void);\n",
        "lib/net/config.h": "int net_config(void);\n",
        "lib/net/a.c": "#include <config.h>\nint a(void) { return root_config(); }\n",
        "lib/net/b.c": '#include "config.h"\nint b(void) { return net_config(); }\n',
        "src/x.h": "int src_x(void);\n",
        "lib/net/x.h": "int net_x(void);\n",
        "lib/net/c.c": "#include <x.h>\nint c(void) { return src_x(); }\n",
        "sub/y.hpp": "int root_y();\n",
        "lib/net/sub/y.hpp": "int net_y();\n",
        "lib/net/d.cpp": "#include <sub/y.hpp>\nint d() { return root_y(); }\n",
    })
    graph = _graph(project_dir)
    assert graph["lib/net/a.c"]["callees"] == {"config.h"}
    assert graph["lib/net/b.c"]["callees"] == {"lib/net/config.h"}
    assert graph["lib/net/c.c"]["callees"] == {"src/x.h"}
    assert graph["lib/net/d.cpp"]["callees"] == {"sub/y.hpp"}


def test_a_member_of_the_class_comes_before_an_import_of_its_name(tmp_path):
    project_dir = _make_files(tmp_path, {
        "rules.py": "def validate(x):\n    return x\n",
        "form.py": (
            "from rules import validate\n"                 # 1
            "from os import path\n\n\n"                    # 2
            "class Form:\n"                                # 5
            "    path: str = ''\n\n"                       # 6
            "    def validate(self):\n"                    # 8
            "        return validate(self.path)\n\n"       # 9
            "    def save(self):\n"                        # 11
            "        return self.validate()\n"             # 12
        ),
        "rules.js": "export function validate(x) { return x; }\n",
        "form.js": (
            'import { validate } from "./rules";\n'        # 1
            "export class Form {\n"                        # 2
            "  validate() { return validate(this.data); }\n"   # 3
            "  save() { return this.validate(); }\n"       # 4
            "}\n"
        ),
        "com/acme/util/Util.java": (
            "package com.acme.util;\n"
            "public class Util {\n"
            "    public static final int LIMIT = 1;\n"         # 3
            "    public static boolean validate(int x) { return x > 0; }\n"   # 4
            "    public static class Inner { public static int secret(int x) { return x; } }\n"
            "}\n"
        ),
        "com/acme/app/Form.java": (
            "package com.acme.app;\n"                          # 1
            "import static com.acme.util.Util.*;\n"            # 2
            "public class Form {\n"                            # 3
            "    private int LIMIT = 2;\n"                     # 4
            "    boolean validate() { return LIMIT > 0; }\n"   # 5
            "    boolean save() { return validate() && this.validate() && secret(1) > 0; }\n"  # 6
            "}\n"
        ),
        "com/acme/kt/Helpers.kt": (
            "package com.acme.kt\n\nfun parse(s: String): Int = s.length\n\nfun helper(): Int = 1\n"
        ),
        "com/acme/kt/Reader.kt": (
            "package com.acme.kt\n\n"                      # 1
            "class Reader {\n"                             # 3
            "    val helper = 2\n"                         # 4
            "    fun parse(s: String): Int = s.length + 1\n"   # 5
            '    fun read(): Int = parse("x") + this.parse("y")\n'   # 6
            "    fun other(): Int = helper() + helper\n"   # 7
            "}\n"
        ),
        "util.h": "int clamp(int v);\n",
        "box.h": (
            '#include "util.h"\n'                          # 1
            "class Box {\n"                                # 2
            "public:\n"
            "    int clamp(int v) const { return v; }\n"   # 4
            "    int fit(int v) const { return clamp(v) + this->clamp(v); }\n"   # 5
            "};\n"
        ),
    })
    result_dict = _analyze(project_dir)
    # Python, JS: the member written after self / this; the name by itself stays the import
    assert _callee_dict(result_dict["form.py"]) == {"validate": ("rules.py", [9])}
    assert _same_dict(result_dict["form.py"]) == {"path": [9], "validate": [12]}
    assert _callee_dict(result_dict["form.js"]) == {"validate": ("rules.js", [3])}
    assert _same_dict(result_dict["form.js"]) == {"validate": [4]}
    # Java, Kotlin, C++: the member by its name alone too; a call takes a function only,
    # and a member of a nested class is not imported with its outer class
    assert _callee_dict(result_dict["com/acme/app/Form.java"]) == {}
    assert _same_dict(result_dict["com/acme/app/Form.java"]) == {"LIMIT": [5], "validate": [6]}
    assert _callee_dict(result_dict["com/acme/kt/Reader.kt"]) == {
        "helper": ("com/acme/kt/Helpers.kt", [7]),
    }
    assert _same_dict(result_dict["com/acme/kt/Reader.kt"]) == {"parse": [6], "helper": [7]}
    assert _callee_dict(result_dict["box.h"]) == {}
    assert _same_dict(result_dict["box.h"]) == {"clamp": [5]}
    _assert_callers_mirror_callees(result_dict)


def test_a_member_written_after_this_inside_a_member_defined_outside_its_class(tmp_path):
    project_dir = _make_files(tmp_path, {
        "box.h": (
            "class Box {\n"                                # 1
            "public:\n"
            "    void setWidth(int width);\n"              # 3
            "    int area() const;\n"                      # 4
            "    int width;\n"                             # 5
            "    int height;\n"                            # 6
            "    int scale(int v) const { return v * height; }\n"   # 7
            "};\n"
        ),
        "box.cpp": (
            '#include "box.h"\n'                           # 1
            "void Box::setWidth(int width) { this->width = width; }\n"     # 2
            "int Box::area() const {\n"                    # 3
            "    int height = 2;\n"                        # 4
            "    return this->scale(this->height) * height;\n"   # 5
            "}\n"
        ),
    })
    result_dict = _analyze(project_dir)
    assert {
        (usage["name"], usage["target_name"], usage["target_start_line"]): usage["lines"]
        for usage in result_dict["box.cpp"]["callee_usages"]
    } == {
        ("Box::setWidth", "setWidth", 3): [2],
        ("width", "width", 5): [2],
        ("Box::area", "area", 4): [3],
        ("scale", "scale", 7): [5],
        ("height", "height", 6): [5],
    }
    _assert_callers_mirror_callees(result_dict)


def test_a_declaration_leads_to_the_definitions_outside_a_class_and_with_their_namespaces(tmp_path):
    project_dir = _make_files(tmp_path, {
        "util.h": "int size(int v);\n",
        "util.cpp": '#include "util.h"\nint size(int v) { return v; }\n',
        "box.h": '#include "util.h"\nclass Box {\npublic:\n    int size() const { return 1; }\n};\n',
        "geo.h": (
            "namespace geo {\nint area(int w, int h);\n"
            "class Table {\npublic:\n    struct Cmp { static int make(); };\n};\n}\n"
        ),
        "geo.cpp": (
            '#include "geo.h"\n'                           # 1
            "int geo::area(int w, int h) { return w * h; }\n"   # 2
            "int geo::Table::Cmp::make() { return area(1, 2); }\n"   # 3
        ),
        "main.cpp": (
            '#include "util.h"\n#include "geo.h"\n'        # 1, 2
            "int run() { return size(1) + geo::area(1, 2) + geo::Table::Cmp::make(); }\n"   # 3
        ),
    })
    result_dict = _analyze(project_dir)
    target_set = {
        (usage["name"], usage["from"], usage["target_name"], usage["target_start_line"])
        for usage in result_dict["main.cpp"]["callee_usages"]
    }
    # A member function written inside its class is no definition of a function a header
    # declares; a definition written with its namespace or classes is found under its name
    assert target_set == {
        ("size", "util.h", "size", 1),
        ("size", "util.cpp", "size", 2),
        ("area", "geo.h", "area", 2),
        ("area", "geo.cpp", "geo::area", 2),
        ("Table::Cmp::make", "geo.h", "make", 5),
        ("Table::Cmp::make", "geo.cpp", "geo::Table::Cmp::make", 3),
    }
    assert _graph(project_dir)["main.cpp"]["callees"] == {"util.h", "util.cpp", "geo.h", "geo.cpp"}
    # The definition in the file itself
    assert _same_dict(result_dict["geo.cpp"]) == {"area": [3]}
    _assert_callers_mirror_callees(result_dict)


def test_a_variable_of_a_type_written_with_its_class_or_namespace(tmp_path):
    project_dir = _make_files(tmp_path, {
        "table.h": (
            "namespace db {\nclass Table {\npublic:\n"
            "    struct Cmp { int compare(int a, int b) const; };\n"     # 4
            "};\n"
            "class Row { public: int size() const; };\n"               # 6
            "}\n"
        ),
        "main.cpp": (
            '#include "table.h"\n'                                       # 1
            "int run(const Table::Cmp& c, db::Row* r) {\n"               # 2
            "    return c.compare(1, 2) + r->size();\n"                  # 3
            "}\n"
        ),
    })
    target_set = {
        (usage["name"], usage["target_name"], tuple(usage["lines"]))
        for usage in _analyze(project_dir)["main.cpp"]["callee_usages"]
    }
    # A class written with the class it is a member of, and one written with its namespace
    assert ("Table::Cmp.compare", "compare", (3,)) in target_set
    assert ("Row.size", "size", (3,)) in target_set


def test_a_file_whose_tree_cannot_be_read_is_reported_once(tmp_path, monkeypatch, caplog):
    project_dir = _make_files(tmp_path, {
        "x.js": "module.exports = { foo() {}, bar() {}, baz() {} };\n",
        "a.js": 'import util from "./x";\nutil.foo();\nutil.bar();\nutil.baz();\n',
    })
    real_parse_file = import_binding.parse_file

    def fail_on_x(file_abs, *args, **kwargs):
        if os.path.basename(file_abs) == "x.js":
            raise OSError("cannot read")
        return real_parse_file(file_abs, *args, **kwargs)

    monkeypatch.setattr(import_binding, "parse_file", fail_on_x)
    clear_import_binder_cache()
    clear_import_reference_cache()
    with caplog.at_level(logging.WARNING, logger="codetwine.import_binding"):
        import_reference_target_list("a.js", {"a.js", "x.js"}, project_dir)
    message_list = [record.getMessage() for record in caplog.records if "x.js" in record.getMessage()]
    assert message_list
    assert len(message_list) == len(set(message_list))
    clear_import_binder_cache()
    clear_import_reference_cache()


def test_java_sees_a_kotlin_file_through_the_class_of_the_file(tmp_path):
    project_dir = _make_files(tmp_path, {
        "com/acme/util/Strings.kt": "package com.acme.util\n\nfun shout(s: String): String = s\n",
        "com/acme/util/Names.kt": (
            '@file:JvmName("NameUtils")\npackage com.acme.util\n\n'
            "fun clean(s: String): String = s\n\nclass Name(val v: String)\n"
        ),
        "com/acme/util/Tool.java": (
            "package com.acme.util;\n"                     # 1
            "public class Tool {\n"                        # 2
            "    static final int shout = 1;\n"            # 3
            "    int run() { return shout + Name.class.hashCode(); }\n"   # 4
            "}\n"
        ),
        "com/acme/app/Main.java": (
            "package com.acme.app;\n"                      # 1
            "import com.acme.util.StringsKt;\n"            # 2
            "import com.acme.util.NameUtils;\n"            # 3
            "import static com.acme.util.StringsKt.shout;\n"   # 4
            "public class Main {\n"                        # 5
            "    String run() {\n"
            '        return StringsKt.shout("a") + NameUtils.clean("b") + shout("c");\n'   # 7
            "    }\n}\n"
        ),
    })
    result_dict = _analyze(project_dir)
    assert {
        (usage["name"], usage["from"], usage["target_name"], tuple(usage["lines"]))
        for usage in result_dict["com/acme/app/Main.java"]["callee_usages"]
    } == {
        ("StringsKt.shout", "com/acme/util/Strings.kt", "shout", (7,)),
        ("NameUtils.clean", "com/acme/util/Names.kt", "clean", (7,)),
        ("shout", "com/acme/util/Strings.kt", "shout", (7,)),
    }
    # A Java file of the package sees the classes of a Kotlin file, not its functions
    assert _callee_dict(result_dict["com/acme/util/Tool.java"]) == {
        "Name": ("com/acme/util/Names.kt", [4]),
    }
    assert _same_dict(result_dict["com/acme/util/Tool.java"]) == {"shout": [4]}
    _assert_callers_mirror_callees(result_dict)


def test_a_name_written_with_its_package(tmp_path):
    project_dir = _make_files(tmp_path, {
        "com/acme/model/Shape.java": (
            "package com.acme.model;\npublic class Shape {\n"
            "    public static final int MAX = 3;\n"           # 3
            "    public static Shape of(int n) { return new Shape(); }\n"   # 4
            "}\n"
        ),
        "com/acme/app/Main.java": (
            "package com.acme.app;\n"                      # 1
            "public class Main {\n"                        # 2
            "    private int out = 0;\n"                   # 3
            "    int run() {\n"                            # 4
            "        com.acme.model.Shape a = new com.acme.model.Shape();\n"   # 5
            "        System.out.println(1);\n"             # 6
            "        return com.acme.model.Shape.of(1).hashCode() + com.acme.model.Shape.MAX;\n"  # 7
            "    }\n}\n"
        ),
        "acme/model/Util.kt": (
            "package acme.model\n\nfun helper(): Int = 1\n\nobject Registry {\n    fun all(): Int = 2\n}\n"
        ),
        "acme/app/Main.kt": (
            "package acme.app\n\nfun use(): Int = acme.model.helper() + acme.model.Registry.all()\n"
        ),
    })
    result_dict = _analyze(project_dir)
    assert {
        (usage["name"], usage["target_name"], tuple(usage["lines"]))
        for usage in result_dict["com/acme/app/Main.java"]["callee_usages"]
    } == {
        ("com.acme.model.Shape", "Shape", (5,)),
        ("com.acme.model.Shape.of", "of", (7,)),
        ("com.acme.model.Shape.MAX", "MAX", (7,)),
    }
    # A name of the file in a chain that starts from no name of the project is no usage
    assert _same_dict(result_dict["com/acme/app/Main.java"]) == {}
    assert {
        (usage["name"], usage["target_name"])
        for usage in result_dict["acme/app/Main.kt"]["callee_usages"]
    } == {("acme.model.helper", "helper"), ("acme.model.Registry.all", "all")}
    _assert_callers_mirror_callees(result_dict)


def test_a_comment_in_the_head_of_a_class_names_no_class_it_is_made_from(tmp_path):
    project_dir = _make_files(tmp_path, {
        "shop.py": (
            "class Store:\n    def flush(self):\n        return 1\n\n\n"     # 1-3
            "class Cache:  # pylint: disable=too-few-public-methods\n"       # 6
            '    """A cache in front of a Store."""\n\n'                     # 7
            "    def get(self):\n        return self.flush()\n\n\n"          # 9-10
            "class Child(Store):  # made from Store\n"                       # 13
            "    def get(self):\n        return self.flush()\n"              # 14-15
        ),
        "Shop.java": (
            "class Store {\n    void flush() {}\n}\n"                        # 1-3
            "class Cache /* not a Store */ extends java.util.ArrayList<String> { // a Store\n"   # 4
            "    private int Store_count = 0;\n"                             # 5
            "    void get() { this.flush(); }\n"                             # 6
            "}\n"
        ),
    })
    result_dict = _analyze(project_dir)
    # Only the class written in the head itself is a class the class is made from
    assert _same_dict(result_dict["shop.py"]) == {"Store": [13], "flush": [15]}
    assert "flush" not in _same_dict(result_dict["Shop.java"])


def test_a_class_written_after_another_on_its_line_is_not_inside_it(tmp_path):
    project_dir = _make_files(tmp_path, {
        "b.js": "class A { x() { return 1; } }; class B { y() { return this.x(); } }\n",
        "c.cpp": "struct A { int x; }; struct B { int y() { return this->x; } };\n",
    })
    clear_import_binder_cache()
    clear_import_reference_cache()
    file_set = {"b.js", "c.cpp"}
    # this.x inside B names a member of B, which has none
    for file_rel in sorted(file_set):
        assert [
            target.name for target in import_reference_target_list(file_rel, file_set, project_dir)
            if target.name == "x"
        ] == []
    clear_import_binder_cache()
    clear_import_reference_cache()


def test_a_call_with_arguments_is_no_call_of_a_member_without_parameters(tmp_path):
    project_dir = _make_files(tmp_path, {
        "acme/Hash.kt": "package acme\n\nobject Hash {\n    fun sha1(s: String): Int = 1\n}\n",
        "acme/test/HashTest.kt": (
            "package acme.test\n\n"                        # 1
            "import acme.Hash.sha1\n\n"                    # 3
            "class HashTest {\n"                           # 5
            '    fun sha1() { val h = sha1("abc") }\n'     # 6
            "    fun run() = sha1 { }\n"                   # 7
            "    fun again() = sha1()\n"                   # 8
            "}\n"
        ),
    })
    result_dict = _analyze(project_dir)
    # The test method takes no arguments: a call with arguments is the imported function
    assert _callee_dict(result_dict["acme/test/HashTest.kt"]) == {"sha1": ("acme/Hash.kt", [6, 7])}
    assert _same_dict(result_dict["acme/test/HashTest.kt"]) == {"sha1": [8]}


def test_a_call_written_with_its_namespace_is_no_call_of_a_member(tmp_path):
    project_dir = _make_files(tmp_path, {
        "db.h": "namespace store {\nint Repair(int x);\n}\n",
        "repair_test.cc": (
            '#include "db.h"\n'                            # 1
            "class RepairTest {\n"                         # 2
            "  void Repair() {\n"                          # 3
            "    ::store::Repair(1);\n"                    # 4
            "  }\n"
            "  void Run() { Repair(); }\n"                 # 6
            "};\n"
        ),
    })
    result_dict = _analyze(project_dir)
    assert _callee_dict(result_dict["repair_test.cc"]) == {"Repair": ("db.h", [4])}
    assert _same_dict(result_dict["repair_test.cc"]) == {"Repair": [6]}


def test_a_field_written_by_itself_and_with_a_call_on_one_line(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Conf.java": (
            "public class Conf {\n"                                        # 1
            "    Conf next;\n"                                             # 2
            "    boolean ok() { return next != null && next.ok(); }\n"     # 3
            "}\n"
        ),
    })
    # The field by itself, and the field with the call written after it
    assert _same_dict(_analyze(project_dir)["Conf.java"]) == {"next": [3], "next.ok": [3]}


def test_a_member_named_after_this_in_the_first_of_two_classes_on_a_line(tmp_path):
    js_text = (
        "class A { run() { return this.x(); } } class B {\n"    # 1
        "  x() { return 2; }\n"                                  # 2
        "}\n"
        "class C { x() { return 3; } } class D { x() { return 4; } y() { return this.x(); } }\n"
    )
    project_dir = _make_files(tmp_path, {
        "b.js": js_text,
        "c.cpp": "struct A { int run() { return this->x; } }; struct B {\n  int x;\n};\n",
        "k.kt": "class A { fun run(): Int = this.x } class B {\n    val x = 1\n}\n",
        "e.js": "class A { run() { return this.x(); } }\nclass B {\n  x() { return 2; }\n}\n",
    })
    clear_import_binder_cache()
    clear_import_reference_cache()
    file_set = {"b.js", "c.cpp", "k.kt", "e.js"}
    target_dict = {
        file_rel: [
            target for target in import_reference_target_list(file_rel, file_set, project_dir)
            if target.name == "x"
        ]
        for file_rel in sorted(file_set)
    }
    # this.x inside A names a member of A, which has none, on its line or another
    assert [target.line for target in target_dict["b.js"]] == [4]
    assert target_dict["c.cpp"] == []
    assert target_dict["k.kt"] == []
    assert target_dict["e.js"] == []
    # this.x inside D names the x of D, written on the line of the x of C
    d_target = target_dict["b.js"][0]
    assert d_target.definition_start_byte == js_text.index("x() { return 4; }")
    definition, source_text = source_definition(
        "b.js", d_target.definition_name, project_dir, d_target.definition_line,
        d_target.definition_start_byte,
    )
    assert source_text == "x() { return 4; }"
    clear_import_binder_cache()
    clear_import_reference_cache()


def test_a_variable_comes_before_a_package_of_its_name(tmp_path):
    project_dir = _make_files(tmp_path, {
        "app/Settings.kt": "package app\n\nval timeout: Int = 30\nfun start(): Int = 1\n",
        "db/Query.kt": "package db\n\nfun query(): Int = 2\n",
        "net/Client.kt": (
            "package net\n\n"                                          # 1
            "class Server(val timeout: Int) { fun start(): Int = 3 }\n"    # 3
            "class Conn { fun query(): Int = 4 }\n\n"                  # 4
            "class Client(val app: Server) {\n"                        # 6
            "    fun a(): Int = app.timeout + app.start()\n"           # 7
            "    fun b(app: Server): Int = app.start()\n"              # 8
            "    fun c(): Int { val db: Conn = Conn(); return db.query() }\n"   # 9
            "}\n\n"
            "fun d(): Int = db.query()\n"                              # 12
        ),
    })
    result_dict = _analyze(project_dir)
    # A property, a parameter and a local variable are read before a package of their name
    assert _same_dict(result_dict["net/Client.kt"]) == {
        "Server": [6, 8], "Server.timeout": [7], "Server.start": [7, 8],
        "Conn": [9], "Conn.query": [9],
    }
    # Where no variable of the name counts, the name is the package
    assert _callee_dict(result_dict["net/Client.kt"]) == {"db.query": ("db/Query.kt", [12])}
    _assert_callers_mirror_callees(result_dict)


def test_a_string_in_an_annotation_of_a_method_is_no_parameter_list(tmp_path):
    project_dir = _make_files(tmp_path, {
        "acme/util/Io.kt": (
            "package acme.util\n\n"
            "fun sink(path: String): Int = path.length\n"      # 3
            "fun source(path: String): Int = path.length\n"    # 4
            "fun pipe(path: String): Int = path.length\n"      # 5
            "fun gen(path: String): Int = path.length\n"       # 6
        ),
        "acme/app/Files.kt": (
            "package acme.app\n\n"                             # 1
            "import acme.util.sink\nimport acme.util.source\n"     # 3, 4
            "import acme.util.pipe\nimport acme.util.gen\n\n"      # 5, 6
            "class Files {\n"                                  # 8
            '    @Deprecated(message = "moved", replaceWith = ReplaceWith(expression = "file.sink()"))\n'
            "    fun sink(file: String): Int = 1\n"            # 10
            '    @Deprecated(message = "moved", replaceWith = ReplaceWith(expression = "source(file)"))\n'
            "    fun source(): Int = 2\n"                      # 12
            "    fun pipe /* none */ (): Int = 3\n"            # 13
            "    fun <T> gen(): Int = 4\n"                     # 14
            "    fun log(path: String): Int = 5\n"             # 15
            '    fun go(): Int = sink("a") + source("b") + pipe("c") + gen("d") + log("e")\n'   # 16
            "}\n"
        ),
    })
    result_dict = _analyze(project_dir)
    # A call with arguments skips the methods declared without parameters only
    assert _same_dict(result_dict["acme/app/Files.kt"]) == {"sink": [16], "log": [16]}
    assert {
        name: file_rel for name, (file_rel, _) in _callee_dict(result_dict["acme/app/Files.kt"]).items()
    } == {"source": "acme/util/Io.kt", "pipe": "acme/util/Io.kt", "gen": "acme/util/Io.kt"}


def test_a_nested_class_comes_before_a_class_of_its_name_in_another_file(tmp_path):
    project_dir = _make_files(tmp_path, {
        "acme/Entry.kt": (
            "package acme\n\n"
            "class Entry(val key: String) {\n    fun size(): Int = 1\n}\n"       # 3, 4
            "class Leaf(val key: String) {\n    fun size(): Int = 1\n}\n"        # 6, 7
        ),
        "acme/Cache.kt": (
            "package acme\n\n"                                         # 1
            "class Cache {\n"                                          # 3
            "    data class Entry(val key: String, val value: Int) {\n"    # 4
            "        fun size(): Int = value\n"                        # 5
            "    }\n"
            "    inner class Leaf(val key: String) {\n"                # 7
            "        fun size(): Int = 2\n"                            # 8
            "    }\n"
            '    private val head: Entry = Entry("a", 1)\n'            # 10
            "    fun total(e: Entry): Int = e.size() + head.value\n"   # 11
            '    fun leaf(): Leaf = Leaf("b")\n'                       # 12
            "}\n\n"
            "class Other {\n"                                          # 15
            '    fun make(e: Entry): Entry = Entry("c" + e.size())\n'  # 16
            "}\n"
        ),
        "acme/util/Node.java": (
            "package acme.util;\n\npublic class Node {\n"
            "    public int v;\n"                                      # 4
            "    public int size() { return 1; }\n"                    # 5
            "}\n"
        ),
        "acme/app/Tree.java": (
            "package acme.app;\n\n"                                    # 1
            "import acme.util.Node;\n\n"                               # 3
            "public class Tree {\n"                                    # 5
            "    static class Node {\n"                                # 6
            "        int v;\n"                                         # 7
            "        int size() { return 2; }\n"                       # 8
            "    }\n"
            "    Node root;\n"                                         # 10
            "    int count(Node other) {\n"                            # 11
            "        acme.util.Node far = null;\n"                     # 12
            "        return root.v + other.size() + far.size();\n"     # 13
            "    }\n"
            "}\n\n"
            "class Forest {\n"                                         # 17
            "    int total(Node n) { return n.size(); }\n"             # 18
            "}\n"
        ),
        "node.h": "struct Node {\n  int v;\n  int size() const;\n};\n",
        "tree.h": (
            '#include "node.h"\n\n'                                    # 1
            "class Tree {\n"                                           # 3
            "  struct Node {\n"                                        # 4
            "    int v;\n"                                             # 5
            "    int size() const { return v; }\n"                     # 6
            "  };\n"
            "  int count(const Node& other) const { return other.size(); }\n"   # 8
            "};\n"
        ),
    })
    result_dict = _analyze(project_dir)
    kotlin_result = result_dict["acme/Cache.kt"]
    # Inside the class the nested class is the name, a call that makes an object as well;
    # the property head is a usage under its own name too
    assert _same_dict(kotlin_result) == {
        "Entry": [10, 11], "Entry.size": [11], "Entry.value": [11], "head.value": [11],
        "Leaf": [12],
    }
    assert {
        usage["name"]: usage["target_start_line"] for usage in kotlin_result["same_file_usages"]
    } == {"Entry": 4, "Entry.size": 5, "Entry.value": 4, "head.value": 10, "Leaf": 7}
    # Outside it the name is the class of the package
    assert _callee_dict(kotlin_result) == {
        "Entry": ("acme/Entry.kt", [16]), "Entry.size": ("acme/Entry.kt", [16]),
    }
    java_result = result_dict["acme/app/Tree.java"]
    assert {
        usage["name"]: usage["target_start_line"] for usage in java_result["same_file_usages"]
        if usage["name"].startswith("Node")
    } == {"Node": 6, "Node.v": 7, "Node.size": 8}
    assert {
        (usage["name"], usage["from"], tuple(usage["lines"])) for usage in java_result["callee_usages"]
    } == {
        ("acme.util.Node", "acme/util/Node.java", (12,)),
        ("acme.util.Node.size", "acme/util/Node.java", (13,)),
        ("Node", "acme/util/Node.java", (18,)),
        ("Node.size", "acme/util/Node.java", (18,)),
    }
    # C++: the member of the nested class by its name alone, and a variable of its type
    assert _callee_dict(result_dict["tree.h"]) == {}
    assert {
        usage["name"]: usage["target_start_line"] for usage in result_dict["tree.h"]["same_file_usages"]
    } == {"v": 5, "Node": 4, "Node.size": 6}
    _assert_callers_mirror_callees(result_dict)


def test_a_member_written_alone_in_a_function_defined_outside_its_class_of_the_file(tmp_path):
    project_dir = _make_files(tmp_path, {
        "util.h": "int clamp(int v);\nint twice(int v);\n",
        "node.h": "struct Base {\n  int w;\n};\n",
        "box.cpp": (
            '#include "util.h"\n'                              # 1
            '#include "node.h"\n\n'                            # 2
            "template <class T>\n"                             # 4
            "class Box {\n"                                    # 5
            " public:\n"
            "  int clamp(int v) const { return v; }\n"         # 7
            "  int fit(int v) const;\n"                        # 8
            "  struct Leaf : Base {\n"                         # 9
            "    int v;\n"                                     # 10
            "    int get() const;\n"                           # 11
            "  };\n"
            "};\n\n"
            "template <class T>\n"                             # 15
            "int Box<T>::fit(int v) const {\n"                 # 16
            "  return clamp(v) + twice(v);\n"                  # 17
            "}\n\n"
            "template <class T>\n"                             # 20
            "int Box<T>::Leaf::get() const {\n"                # 21
            "  return v + w;\n"                                # 22
            "}\n\n"
            "int free_fn(int v) {\n"                           # 25
            "  return clamp(v);\n"                             # 26
            "}\n"
        ),
    })
    result_dict = _analyze(project_dir)
    # The members of the class come before the names of the headers; other names do not
    assert {
        usage["name"]: (usage["lines"], usage["target_start_line"])
        for usage in result_dict["box.cpp"]["same_file_usages"]
    } == {"clamp": ([17], 7), "v": ([22], 10)}
    assert {
        name: (file_rel, lines) for name, (file_rel, lines) in _callee_dict(result_dict["box.cpp"]).items()
    } == {
        "Base": ("node.h", [9]), "twice": ("util.h", [17]), "w": ("node.h", [22]),
        "clamp": ("util.h", [26]),
    }
    _assert_callers_mirror_callees(result_dict)


def test_java_sees_the_class_of_a_kotlin_file_named_with_other_characters(tmp_path):
    project_dir = _make_files(tmp_path, {
        "acme/string-utils.kt": "package acme\n\nfun shout(s: String): String = s\n",
        "acme/1st.file.kt": "package acme\n\nfun first(): Int = 1\n",
        "acme/Strings.kt": "package acme\n\nfun upper(s: String): String = s\n",
        "acme/Main.java": (
            "package acme;\n\n"                                        # 1
            "public class Main {\n"                                    # 3
            "    String run() {\n"
            '        return String_utilsKt.shout("a") + _1st_fileKt.first() + StringsKt.upper("b");\n'   # 5
            "    }\n}\n"
        ),
    })
    result_dict = _analyze(project_dir)
    # The class name has "_" for each character of the file name that is no letter or digit,
    # and "_" before a first digit
    assert {
        name: file_rel for name, (file_rel, _) in _callee_dict(result_dict["acme/Main.java"]).items()
    } == {
        "String_utilsKt.shout": "acme/string-utils.kt",
        "_1st_fileKt.first": "acme/1st.file.kt",
        "StringsKt.upper": "acme/Strings.kt",
    }
