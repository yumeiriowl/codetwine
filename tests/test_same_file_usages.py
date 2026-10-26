"""same_file_usages lists where a file uses the names it defines itself.

The entries stay out of callee_usages, so the file-level dependency graph holds no edge
from a file to itself.
"""

import json
import sqlite3

from codetwine import pipeline


async def _analyze(tmp_path, monkeypatch, source_by_name_dict: dict[str, str]) -> sqlite3.Connection:
    """Write the sources into a project, run the pipeline and open the database."""
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", False)
    monkeypatch.setattr(pipeline, "KNOWLEDGE_FORMAT", "sqlite")
    project = tmp_path / "proj"
    project.mkdir()
    for name, source in source_by_name_dict.items():
        (project / name).write_text(source)
    out = tmp_path / "out"
    await pipeline.process_all_files(str(project), str(out), None, max_workers=1)
    return sqlite3.connect(out / "proj" / "project_knowledge.sqlite")


def _name_line_list(usage_list: list[dict]) -> list[dict]:
    """Return the usages as {"lines", "name"} dicts, without the fields of the target definition."""
    return [{"lines": usage["lines"], "name": usage["name"]} for usage in usage_list]


def _file_dependencies(connection: sqlite3.Connection, file: str) -> dict:
    """Return the file_dependencies body of one file."""
    row = connection.execute("SELECT file_dependencies FROM files WHERE file = ?", (file,)).fetchone()
    return json.loads(row[0])


async def test_a_call_within_the_file_is_listed(tmp_path, monkeypatch):
    connection = await _analyze(tmp_path, monkeypatch, {
        "one.py": (
            "LIMIT = 3\n\n\n"
            "def helper(x):\n    return x + LIMIT\n\n\n"
            "def caller(x):\n    return helper(x) + helper(x + 1)\n\n\n"
            "def loop(x):\n    return loop(x - 1) if x else 0\n"
        ),
        "two.py": "from one import helper\n\n\ndef outside(x):\n    return helper(x)\n",
    })

    one = _file_dependencies(connection, "proj/one_py/one.py")
    assert one["callee_usages"] == []
    assert _name_line_list(one["same_file_usages"]) == [
        {"lines": [5], "name": "LIMIT"},
        {"lines": [9], "name": "helper"},
    ]

    two = _file_dependencies(connection, "proj/two_py/two.py")
    assert [usage["name"] for usage in two["callee_usages"]] == ["helper"]
    assert _name_line_list(two["same_file_usages"]) == []

    edge_list = list(connection.execute("SELECT file, direction, other FROM file_edges"))
    assert all(file != other for file, _, other in edge_list)


async def test_an_imported_name_is_not_listed_as_a_same_file_usage(tmp_path, monkeypatch):
    connection = await _analyze(tmp_path, monkeypatch, {
        "one.py": "def helper(x):\n    return x\n",
        "two.py": (
            "from one import helper\n\n\n"
            "def wrap(x):\n    return helper(x)\n\n\n"
            "def run(x):\n    return wrap(x)\n"
        ),
    })

    two = _file_dependencies(connection, "proj/two_py/two.py")
    assert [usage["name"] for usage in two["callee_usages"]] == ["helper"]
    assert _name_line_list(two["same_file_usages"]) == [{"lines": [9], "name": "wrap"}]


async def test_a_file_without_a_language_has_an_empty_list(tmp_path, monkeypatch):
    connection = await _analyze(tmp_path, monkeypatch, {"notes.txt": "hello\n"})

    assert _file_dependencies(connection, "proj/notes_txt/notes.txt")["same_file_usages"] == []


def _target_line_list(usage_list: list[dict]) -> list[tuple[str, int, list[int]]]:
    """Return the usages as (name, target_start_line, lines)."""
    return [(usage["name"], usage["target_start_line"], usage["lines"]) for usage in usage_list]


async def test_a_call_inside_a_definition_of_the_same_name_that_leads_elsewhere_is_listed(
    tmp_path, monkeypatch,
):
    connection = await _analyze(tmp_path, monkeypatch, {
        "wrap.py": (
            "def run():\n    return 1\n\n\n"
            "class Runner:\n    def run(self):\n        return run()\n\n"
            "    def go(self):\n        return self.run()\n"
        ),
        "view.js": (
            "function render(x) {\n  return x;\n}\n\n"
            "class View {\n  render() {\n    return render(this);\n  }\n}\n"
            "module.exports = View;\n"
        ),
        "Log.cs": (
            "public class Log\n{\n"
            "    public void Write(string m)\n    {\n        Write(m, 1);\n    }\n\n"
            "    public void Write(string m, int level)\n    {\n    }\n\n"
            "    public void Again(string m, int level)\n    {\n        Again(m, level);\n    }\n}\n"
        ),
    })

    # A reference is left out only inside the definition it leads to
    assert _target_line_list(_file_dependencies(connection, "proj/wrap_py/wrap.py")[
        "same_file_usages"
    ]) == [("run", 1, [7]), ("run", 6, [10])]
    assert ("render", 1, [7]) in _target_line_list(
        _file_dependencies(connection, "proj/view_js/view.js")["same_file_usages"]
    )
    assert _target_line_list(_file_dependencies(connection, "proj/Log_cs/Log.cs")[
        "same_file_usages"
    ]) == [("Write", 8, [5])]


async def test_the_name_of_a_second_definition_is_no_usage_of_the_first(tmp_path, monkeypatch):
    connection = await _analyze(tmp_path, monkeypatch, {
        "conf.h": (
            "#ifdef WIDE\n#define WIDTH 2\ntypedef long fd_t;\n"
            "#else\n#define WIDTH 1\ntypedef int fd_t;\n#endif\n"
            "fd_t width(void) { return WIDTH; }\n"
        ),
        "store.ts": (
            "export function use(a: string): string;\n"
            "export function use(a: number): number;\n"
            "export function use(a: any): any {\n  return a;\n}\n"
            "export const show = function use(b: any) { return b; };\n"
        ),
        "w.cpp": (
            "struct W {\n    W(int a);\n    W(double b);\n};\n"
            "W::W(int a) {}\n"
            "W::W(double b) {}\n"
        ),
        "p.rs": (
            "pub struct P {\n    v: u8,\n}\n\n"
            "impl P {\n    pub fn new() -> P {\n        P { v: 0 }\n    }\n}\n"
        ),
    })

    # The name of a second definition is no usage of the first
    assert _target_line_list(_file_dependencies(connection, "proj/conf_h/conf.h")[
        "same_file_usages"
    ]) == [("WIDTH", 2, [8]), ("fd_t", 3, [8])]
    assert _target_line_list(_file_dependencies(connection, "proj/store_ts/store.ts")[
        "same_file_usages"
    ]) == []
    assert _target_line_list(_file_dependencies(connection, "proj/w_cpp/w.cpp")[
        "same_file_usages"
    ]) == []
    # The struct an impl block is written for, and written inside it, leads to the struct
    assert _target_line_list(_file_dependencies(connection, "proj/p_rs/p.rs")[
        "same_file_usages"
    ]) == [("P", 1, [5, 6, 7])]


async def test_the_name_of_a_type_defined_again_is_no_usage_of_the_type(tmp_path, monkeypatch):
    connection = await _analyze(tmp_path, monkeypatch, {
        "lib.rs": (
            "#[cfg(unix)]\npub struct Handle {\n    fd: i32,\n}\n"
            "#[cfg(windows)]\npub struct Handle {\n    h: u64,\n}\n"
            "#[cfg(unix)]\npub type Raw = i32;\n#[cfg(windows)]\npub type Raw = u64;\n"
            "mod a {\n    pub enum Kind { A }\n}\nmod b {\n    pub enum Kind { B }\n}\n"
            "impl Handle {\n    pub fn raw(&self) -> Raw { 0 }\n}\n"
            "pub fn open() -> a::Kind { a::Kind::A }\n"
        ),
        "cfg.h": (
            "#ifdef BIG\nstruct Point { long x; };\nunion Val { long l; };\n"
            "#else\nstruct Point { int x; };\nunion Val { int i; };\n#endif\n"
            "struct Point make_point(void);\n"
        ),
        "box.hpp": (
            "class Node;\n"
            "namespace geo {\nstruct Size { int w; };\n}\n"
            "namespace ui {\nstruct Size { int h; };\n}\n"
            "class Node {\n  Node* next;\n};\n"
            "geo::Size total(Node n);\n"
        ),
        "m.ts": (
            "interface Options {\n  a: number;\n}\ninterface Options {\n  b: string;\n}\n"
            "class Box {}\ninterface Box { w: number }\n"
            "export namespace A {\n  export class User {}\n}\n"
            "export namespace B {\n  export class User {}\n}\n"
            "export function make(o: Options): Box { return new Box(); }\n"
        ),
        "Shapes.java": (
            "public class Shapes {\n"
            "    static class A { enum Mode { X } }\n"
            "    static class B { enum Mode { Y } }\n"
            "    A.Mode first() { return A.Mode.X; }\n"
            "}\n"
        ),
        "k.kt": (
            "fun size(a: Int, b: Int): Int = a + b\n"
            "fun size(a: Int): Int = size(a, 0)\n"
        ),
    })

    def same_file(file: str) -> list[tuple[str, int, list[int]]]:
        """Return the same_file_usages of a file as sorted (name, target_start_line, lines)."""
        return sorted(_target_line_list(_file_dependencies(connection, file)["same_file_usages"]))

    # A definition of a name already defined (another #[cfg], #if branch, module,
    # namespace, class or a merged declaration) is no usage of the first definition
    assert same_file("proj/lib_rs/lib.rs") == [
        ("Handle", 2, [19]), ("Raw", 10, [20]), ("a::Kind", 14, [22]), ("a::Kind::A", 14, [22]),
    ]
    assert same_file("proj/cfg_h/cfg.h") == [("Point", 2, [8])]
    # A declaration without a body is no definition: its name is a usage of the class
    assert same_file("proj/box_hpp/box.hpp") == [
        ("Node", 8, [1, 11]), ("Size", 3, [11]),
    ]
    assert same_file("proj/m_ts/m.ts") == [("Box", 7, [15]), ("Options", 1, [15])]
    assert same_file("proj/Shapes_java/Shapes.java") == [
        ("A.Mode", 2, [4]), ("A.Mode.X", 2, [4]),
    ]
    # A call of another overload written on the line of a definition's name is a usage
    assert same_file("proj/k_kt/k.kt") == [("size", 1, [2])]


async def test_a_reference_inside_a_definition_of_its_name_that_starts_on_its_line(
    tmp_path, monkeypatch,
):
    connection = await _analyze(tmp_path, monkeypatch, {
        "two.ts": (
            "class A { static run() { return 1; } } class B { static run() {\n"
            "  return A.run();\n"
            "} }\n"
            "class C { static run() { return C.run(); } }\n"
        ),
        "Two.cs": (
            "namespace Acme {\n"
            "  class A { public static int Run() { return 1; } } class B { public static int Run() {\n"
            "    return A.Run();\n"
            "  } }\n"
            "}\n"
        ),
    })

    def same_file(file: str) -> list[tuple[str, int, list[int]]]:
        """Return the same_file_usages of a file as sorted (name, target_start_line, lines)."""
        return sorted(_target_line_list(_file_dependencies(connection, file)["same_file_usages"]))

    # A.run of line 1 is no definition the reference is written inside; C.run calls itself
    assert same_file("proj/two_ts/two.ts") == [("A.run", 1, [2])]
    assert same_file("proj/Two_cs/Two.cs") == [("A.Run", 2, [3])]
