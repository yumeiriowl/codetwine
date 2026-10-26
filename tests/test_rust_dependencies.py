"""Rust files depend on each other through use declarations, mod declarations and paths.

A path is resolved through the tree the mod declarations form (#[path] included),
through use declarations of the reached module (re-exports) and through the crates of a
Cargo workspace. build_project_dependencies() adds an edge for each path that leads into
another file, and get_file_dependencies() lists each usage with the definition it names.
"""

import os

from codetwine.extractors.definition_source import extract_callee_source
from codetwine.extractors.dependency_graph import (
    build_project_dependencies,
)
from codetwine.extractors.definitions import extract_definitions
from codetwine.config.settings import EXT_TO_DEFINITION_DICT
from codetwine.file_analyzer import get_file_dependencies
from codetwine.parsers.ts_parser import parse_file
from codetwine.rust_module_tree import module_tree_cache_dict
from codetwine.utils.file_utils import copy_path_to_rel

APP_FILE_DICT = {
    "Cargo.toml": '[package]\nname = "my-app"\nedition = "2021"\n',
    "src/lib.rs": """\
pub mod config;
pub mod geometry;
mod util;
#[cfg(unix)]
#[path = "sys/unix.rs"]
mod sys;

pub use crate::util::text::shout as loud;
pub use geometry::shape::*;
""",
    "src/main.rs": """\
use my_app::config::{self, Settings, DEFAULT_NAME as NAME};
use my_app::loud;

fn main() {
    let config = Settings::new(NAME);
    let mode = config::Mode::Fast;
    println!("{}", loud(&config.name));
    let c = my_app::Circle::unit();
    my_app::geometry::area(&c);
}
""",
    "src/config.rs": """\
use std::fmt;

pub const DEFAULT_NAME: &str = "app";

pub struct Settings {
    pub name: String,
}

impl Settings {
    pub fn new(name: &str) -> Self {
        Settings { name: name.to_string() }
    }
}

impl fmt::Display for Settings {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        write!(f, "{}", self.name)
    }
}

pub enum Mode {
    Fast,
    Slow,
}

pub fn new() -> u8 {
    0
}

pub type Res = fmt::Result;

pub fn default_mode() -> Mode {
    Mode::Fast
}
""",
    "src/geometry/mod.rs": """\
pub mod shape;

use self::shape::Shape;

pub fn area<T: Shape>(s: &T) -> f64 {
    s.area()
}

mod tests {
    use super::*;
    use super::super::config::Settings;

    fn check() {
        let _ = Settings::new("x");
    }
}
""",
    "src/geometry/shape.rs": """\
pub trait Shape {
    fn area(&self) -> f64;
}

pub struct Circle {
    pub r: f64,
}

impl Circle {
    pub fn unit() -> Self {
        Circle { r: 1.0 }
    }
}

impl Shape for Circle {
    fn area(&self) -> f64 {
        3.14 * self.r * self.r
    }
}
""",
    "src/util.rs": "pub mod text;\n",
    "src/util/text.rs": "pub fn shout(s: &str) -> String {\n    s.to_uppercase()\n}\n",
    "src/sys/unix.rs": "pub fn page_size() -> usize {\n    crate::util::text::shout(\"x\").len()\n}\n",
    "examples/demo.rs": """\
mod common;

use my_app::config::Settings;

fn it_works() {
    common::setup();
    let _ = Settings::new("t");
}
""",
    "examples/common/mod.rs": "pub fn setup() {}\n",
}


def _name_line_list(usage_list: list[dict]) -> list[dict]:
    """Return the usages as {"lines", "name"} dicts, without the fields of the target definition."""
    return [{"lines": usage["lines"], "name": usage["name"]} for usage in usage_list]


def _make_rust_project(tmp_path) -> str:
    """Create a Cargo package with a library, a binary, and an example."""
    project = tmp_path / "rustapp"
    for file_rel, text in APP_FILE_DICT.items():
        path = project / file_rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    module_tree_cache_dict.clear()
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
    project_file_set = {f for f in graph if f.endswith(".rs")}
    caller_map = {f: sorted(graph[f]["callers"]) for f in project_file_set}
    return get_file_dependencies(
        os.path.join(project_dir, file_rel), project_dir,
        project_file_set, caller_map,
    )


def test_import_edges(tmp_path):
    graph = _graph(_make_rust_project(tmp_path))

    assert graph["src/lib.rs"]["callees"] == {
        "src/config.rs", "src/geometry/mod.rs", "src/geometry/shape.rs",
        "src/util.rs", "src/util/text.rs", "src/sys/unix.rs",
    }
    # The crate name from Cargo.toml, a re-export and a glob re-export
    assert graph["src/main.rs"]["callees"] == {
        "src/config.rs", "src/util/text.rs", "src/geometry/shape.rs", "src/geometry/mod.rs",
    }
    # super inside an inline module starts from the file's own module
    assert graph["src/geometry/mod.rs"]["callees"] == {"src/geometry/shape.rs", "src/config.rs"}
    # A crate::path without a use declaration
    assert graph["src/sys/unix.rs"]["callees"] == {"src/util/text.rs"}
    # A crate root other than lib.rs / main.rs
    assert graph["examples/demo.rs"]["callees"] == {"examples/common/mod.rs", "src/config.rs"}
    assert graph["src/util.rs"]["callees"] == {"src/util/text.rs"}


def test_definitions(tmp_path):
    project_dir = _make_rust_project(tmp_path)
    root = parse_file(os.path.join(project_dir, "src/config.rs"))[0]
    definition_list = [
        (d.name, d.type) for d in extract_definitions(root, EXT_TO_DEFINITION_DICT["rs"])
    ]
    assert definition_list == [
        ("DEFAULT_NAME", "const_item"),
        ("Settings", "struct_item"),
        ("Settings", "impl_item"),
        ("new", "function_item"),
        ("Settings", "impl_item"),
        ("fmt", "function_item"),
        ("Mode", "enum_item"),
        ("new", "function_item"),
        ("Res", "type_item"),
        ("default_mode", "function_item"),
    ]

    root = parse_file(os.path.join(project_dir, "src/lib.rs"))[0]
    assert extract_definitions(root, EXT_TO_DEFINITION_DICT["rs"]) == []


def test_callee_usages(tmp_path):
    project_dir = _make_rust_project(tmp_path)
    result = _file_dependencies(project_dir, "src/main.rs")
    usage_dict = {
        (u["name"], u["from"]): (u["lines"], u["target_context"].splitlines()[0].strip())
        for u in result["callee_usages"]
    }
    assert usage_dict == {
        ("Settings::new", "src/config.rs"): ([5], "pub fn new(name: &str) -> Self {"),
        ("NAME", "src/config.rs"): ([5], 'pub const DEFAULT_NAME: &str = "app";'),
        ("config::Mode::Fast", "src/config.rs"): ([6], "pub enum Mode {"),
        ("loud", "src/util/text.rs"): ([7], "pub fn shout(s: &str) -> String {"),
        ("my_app::Circle::unit", "src/geometry/shape.rs"): ([8], "pub fn unit() -> Self {"),
        ("my_app::geometry::area", "src/geometry/mod.rs"): ([9], "pub fn area<T: Shape>(s: &T) -> f64 {"),
    }


def test_caller_usages(tmp_path):
    project_dir = _make_rust_project(tmp_path)
    result = _file_dependencies(project_dir, "src/config.rs")
    usage_set = {(u["file"], u["name"], tuple(u["lines"])) for u in result["caller_usages"]}
    assert usage_set == {
        ("src/main.rs", "Settings::new", (5,)),
        ("src/main.rs", "NAME", (5,)),
        ("src/main.rs", "config::Mode::Fast", (6,)),
        ("src/geometry/mod.rs", "Settings::new", (14,)),
        ("examples/demo.rs", "Settings::new", (7,)),
    }


def test_same_file_usages(tmp_path):
    project_dir = _make_rust_project(tmp_path)
    result = _file_dependencies(project_dir, "src/config.rs")
    # fmt::Result refers to the imported std module, not to the fmt method
    # The struct named in the impl blocks of the type and inside them is a usage of it
    assert _name_line_list(result["same_file_usages"]) == [
        {"lines": [9, 11, 15], "name": "Settings"},
        {"lines": [32], "name": "Mode"},
        {"lines": [33], "name": "Mode::Fast"},
    ]


def test_member_lookup(tmp_path):
    project_dir = _make_rust_project(tmp_path)
    assert extract_callee_source("src/config.rs", "Settings::new", project_dir).lstrip().startswith(
        "pub fn new(name: &str) -> Self {"
    )
    assert extract_callee_source("src/config.rs", "new", project_dir).lstrip().startswith(
        "pub fn new() -> u8 {"
    )
    assert extract_callee_source("src/config.rs", "Mode::Slow", project_dir).lstrip().startswith(
        "pub enum Mode {"
    )


def _make_files(tmp_path, file_dict: dict[str, str], name: str = "case") -> str:
    """Write a small project and clear the module tree cache."""
    project = tmp_path / name
    for file_rel, text in file_dict.items():
        path = project / file_rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    module_tree_cache_dict.clear()
    return str(project)


def _usage_dict(result: dict) -> dict[str, str]:
    """Map each callee usage name to its source file."""
    return {u["name"]: u["from"] for u in result["callee_usages"]}


def test_glob_inside_inline_module_does_not_crash(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Cargo.toml": '[package]\nname = "g"\n',
        "src/lib.rs": "mod m {\n    use *;\n}\n",
    })
    assert _graph(project_dir)["src/lib.rs"]["callees"] == set()


def test_impl_block_does_not_define_the_type_name(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Cargo.toml": '[package]\nname = "app"\nedition = "2021"\n',
        "src/lib.rs": "mod types;\npub use types::Error;\n"
                      "impl std::fmt::Display for Error {\n"
                      "    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result { Ok(()) }\n}\n",
        "src/types.rs": "pub struct Error;\nimpl Error {\n    pub fn new() -> Self { Error }\n}\n",
        "src/main.rs": "use app::Error;\nfn main() {\n    let _ = Error::new();\n}\n",
        "src/ext.rs": "pub trait Shout { fn shout(&self); }\nimpl Shout for String {\n    fn shout(&self) {}\n}\n",
        "src/app.rs": "use crate::ext::*;\nfn f() -> String {\n    String::from(\"x\")\n}\n",
    })
    assert _graph(project_dir)["src/main.rs"]["callees"] == {"src/types.rs"}
    result = _file_dependencies(project_dir, "src/main.rs")
    assert result["callee_usages"][0]["target_context"].lstrip().startswith("pub fn new() -> Self")
    assert "String" not in _usage_dict(_file_dependencies(project_dir, "src/app.rs"))


def test_use_super_in_tests_module_keeps_same_file_usages(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Cargo.toml": '[package]\nname = "p"\nedition = "2021"\n',
        "src/lib.rs": "pub fn parse() {}\n\npub fn run() {\n    parse();\n}\n\n"
                      "#[cfg(test)]\nmod tests {\n    use super::parse;\n\n    fn t() {\n        parse();\n    }\n}\n",
    })
    result = _file_dependencies(project_dir, "src/lib.rs")
    assert _name_line_list(result["same_file_usages"]) == [{"lines": [4, 12], "name": "parse"}]


def test_enum_glob_and_local_names(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Cargo.toml": '[package]\nname = "s"\nedition = "2021"\n',
        "src/lib.rs": "mod shape;\nmod app;\nmod log;\nmod worker;\nmod util;\nmod a;\n",
        "src/shape.rs": "pub enum Kind { Round, Square }\npub fn area() -> u8 { 0 }\n",
        "src/app.rs": "use crate::shape::Kind::*;\nfn area() -> u8 { 1 }\nfn f() -> u8 {\n    area()\n}\n",
        "src/log.rs": "pub fn info() {}\n",
        "src/worker.rs": "fn w() {\n    log::info!(\"x\");\n}\n",
        "src/util.rs": "pub fn helper() {}\n",
        "src/a.rs": "mod util {\n    pub fn helper() {}\n}\nfn f() {\n    util::helper();\n}\n",
    })
    graph = _graph(project_dir)
    # An external crate path and a local inline module are not taken for crate-root modules
    assert graph["src/worker.rs"]["callees"] == set()
    assert graph["src/a.rs"]["callees"] == set()
    # Enum variants imported by a glob do not bring in the other definitions of the file
    result = _file_dependencies(project_dir, "src/app.rs")
    assert "area" not in _usage_dict(result)
    assert {"lines": [4], "name": "area"} in _name_line_list(result["same_file_usages"])


def test_glob_from_parent_and_own_definition(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Cargo.toml": '[package]\nname = "j"\nedition = "2021"\n',
        "src/lib.rs": "mod function;\nmod color;\nfn env_var() {}\n",
        "src/color.rs": "pub struct Color;\nimpl Color {\n    pub fn new() -> Self { Color }\n}\n",
        "src/function.rs": "use super::*;\nfn env_var() {}\nfn f() {\n    env_var();\n    let _ = color::Color::new();\n}\n",
    })
    result = _file_dependencies(project_dir, "src/function.rs")
    usage_dict = _usage_dict(result)
    # A child module reached through use super::*
    assert usage_dict["color::Color::new"] == "src/color.rs"
    # The file's own env_var hides the one brought in by the glob import
    assert "env_var" not in usage_dict
    assert {"lines": [4], "name": "env_var"} in _name_line_list(result["same_file_usages"])


def test_raw_identifier_module(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Cargo.toml": '[package]\nname = "r"\nedition = "2021"\n',
        "src/lib.rs": "mod r#type;\nfn f() {\n    r#type::check();\n}\n",
        "src/type.rs": "pub fn check() {}\n",
    })
    assert _graph(project_dir)["src/lib.rs"]["callees"] == {"src/type.rs"}


def _callee_dict(result: dict) -> dict[tuple[str, str], tuple[list[int], str | None]]:
    """Map (usage name, source file) to (lines, first line of target_context)."""
    return {
        (u["name"], u["from"]): (
            u["lines"],
            u["target_context"].splitlines()[0].strip() if u["target_context"] else None,
        )
        for u in result["callee_usages"]
    }


def test_use_inside_function_and_inline_module_is_scoped(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Cargo.toml": '[package]\nname = "s"\nedition = "2021"\n',
        "src/lib.rs": "mod fmt;\nmod config;\nmod other;\nmod two;\nmod app;\n\n"
                      "pub mod private {\n    use std::fmt;\n    pub fn f() -> String {\n"
                      "        fmt::format(format_args!(\"x\"))\n    }\n}\n\n"
                      "pub fn h() {\n    use std::env as config;\n    let _ = config::args();\n}\n\n"
                      "pub fn k() {\n    let _ = config::load();\n    let _ = fmt::render();\n}\n",
        "src/fmt.rs": "pub fn render() -> u8 { 0 }\npub fn format() -> u8 { 0 }\n",
        "src/config.rs": "pub fn load() -> u8 { 0 }\npub fn args() -> u8 { 0 }\npub fn save() -> u8 { 0 }\n",
        "src/other.rs": "pub fn load() -> u8 { 1 }\n",
        "src/two.rs": "pub fn item() -> u8 { 2 }\npub struct Thing;\n",
        "src/app.rs": "fn load() -> u8 {\n    9\n}\n\n"
                      "fn a() -> u8 {\n    use crate::config;\n    {\n        use config::load;\n        load()\n    }\n}\n\n"
                      "fn b() -> u8 {\n    use crate::config::*;\n    save() + load()\n}\n\n"
                      "fn c() -> u8 {\n    save();\n    load()\n}\n\n"
                      "fn d() -> u8 {\n    use crate::config;\n    assert_eq!(config::load(), 0);\n    config::load()\n}\n\n"
                      "fn e() -> u8 {\n    use crate::other as config;\n    config::load()\n}\n\n"
                      "mod deep {\n    fn g() -> u8 {\n        use crate::config;\n        config::save()\n    }\n}\n\n"
                      "pub fn one() -> u8 {\n    use crate::two::item;\n    item()\n}\n\n"
                      "pub fn two(item: u8) -> u8 {\n    item + 1\n}\n\n"
                      "pub fn three() -> u8 {\n    let v = item;\n    v\n}\n\n"
                      "fn four() {\n    use crate::two::Thing;\n    let _ = Thing;\n}\n\n"
                      "fn five() {\n    let _ = Thing;\n}\n",
    })
    # A use declaration of an inline module or a function hides the module of that name
    assert _callee_dict(_file_dependencies(project_dir, "src/lib.rs")) == {
        ("config::load", "src/config.rs"): ([20], "pub fn load() -> u8 { 0 }"),
        ("fmt::render", "src/fmt.rs"): ([21], "pub fn render() -> u8 { 0 }"),
    }
    # A use declaration of a block counts for the paths and names of that block only
    result = _file_dependencies(project_dir, "src/app.rs")
    assert _callee_dict(result) == {
        ("load", "src/config.rs"): ([9, 15], "pub fn load() -> u8 { 0 }"),
        ("save", "src/config.rs"): ([15], "pub fn save() -> u8 { 0 }"),
        ("config::load", "src/config.rs"): ([25, 26], "pub fn load() -> u8 { 0 }"),
        ("config::load", "src/other.rs"): ([31], "pub fn load() -> u8 { 1 }"),
        ("config::save", "src/config.rs"): ([37], "pub fn save() -> u8 { 0 }"),
        ("item", "src/two.rs"): ([43], "pub fn item() -> u8 { 2 }"),
        ("Thing", "src/two.rs"): ([57], "pub struct Thing;"),
    }
    assert {"lines": [20, 25], "name": "load"} in _name_line_list(result["same_file_usages"])
    assert _graph(project_dir)["src/app.rs"]["callees"] == {
        "src/config.rs", "src/other.rs", "src/two.rs",
    }


def test_same_file_paths(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Cargo.toml": '[package]\nname = "p"\nedition = "2021"\n',
        "src/lib.rs": "mod parser;\n\npub struct P {\n    v: u8,\n}\n\n"
                      "impl P {\n    pub fn new(v: u8) -> Self {\n        P { v }\n    }\n\n"
                      "    pub fn make() -> Self {\n        Self::new(1)\n    }\n\n"
                      "    pub fn again() -> Self {\n        Self::again()\n    }\n}\n\n"
                      "pub fn parse() -> u8 {\n    0\n}\n\n"
                      "pub fn run() -> u8 {\n    self::parse() + crate::parse()\n}\n\n"
                      "#[cfg(test)]\nmod tests {\n    fn helper() {}\n\n    fn t() {\n"
                      "        super::run();\n        let _ = super::P::new(1);\n        self::helper();\n    }\n}\n",
        "src/parser.rs": "use crate::P;\n\npub fn parse() -> u8 {\n    0\n}\n\n"
                         "pub fn go() -> u8 {\n    crate::parser::parse() + self::parse()\n}\n\n"
                         "pub fn rec() -> u8 {\n    crate::parser::rec()\n}\n\n"
                         "impl Default for P {\n    fn default() -> Self {\n        Self::new(0)\n    }\n}\n",
    })
    result = _file_dependencies(project_dir, "src/lib.rs")
    assert _name_line_list(result["same_file_usages"]) == [
        {"lines": [7, 9], "name": "P"},
        {"lines": [13], "name": "P::new"},
        {"lines": [26], "name": "crate::parse"},
        {"lines": [26], "name": "self::parse"},
        {"lines": [34], "name": "super::run"},
        {"lines": [35], "name": "super::P::new"},
        {"lines": [36], "name": "self::helper"},
    ]
    assert _graph(project_dir)["src/lib.rs"]["callees"] == {"src/parser.rs"}
    # A path written inside the definition it names is left out
    result = _file_dependencies(project_dir, "src/parser.rs")
    assert _name_line_list(result["same_file_usages"]) == [
        {"lines": [8], "name": "self::parse"},
        {"lines": [8], "name": "crate::parser::parse"},
    ]
    # Self in an impl block of a type of another file is named after the type
    assert _callee_dict(result)[("P::new", "src/lib.rs")] == ([17], "pub fn new(v: u8) -> Self {")


def test_impl_in_another_file(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Cargo.toml": '[package]\nname = "i"\nedition = "2021"\n',
        "src/lib.rs": "mod shape;\nmod shape_ext;\nmod user;\n",
        "src/shape.rs": "pub struct Circle;\n\nimpl Circle {\n    pub fn base() -> u8 { 1 }\n}\n",
        "src/shape_ext.rs": "use crate::shape::Circle;\n\nimpl Circle {\n    pub fn extra() -> u8 { 7 }\n}\n",
        "src/user.rs": "use crate::shape::Circle;\n\nfn f() -> u8 {\n    Circle::extra() + Circle::base()\n}\n\n"
                       "fn g() -> u8 {\n    crate::shape::Circle::extra()\n}\n",
    })
    assert _graph(project_dir)["src/user.rs"]["callees"] == {"src/shape.rs", "src/shape_ext.rs"}
    assert _callee_dict(_file_dependencies(project_dir, "src/user.rs")) == {
        ("Circle::base", "src/shape.rs"): ([4], "pub fn base() -> u8 { 1 }"),
        ("Circle::extra", "src/shape.rs"): ([4], "pub struct Circle;"),
        ("Circle::extra", "src/shape_ext.rs"): ([4], "pub fn extra() -> u8 { 7 }"),
        ("crate::shape::Circle::extra", "src/shape.rs"): ([8], "pub struct Circle;"),
        ("crate::shape::Circle::extra", "src/shape_ext.rs"): ([8], "pub fn extra() -> u8 { 7 }"),
    }
    result = _file_dependencies(project_dir, "src/shape_ext.rs")
    assert {(u["file"], u["name"], tuple(u["lines"])) for u in result["caller_usages"]} == {
        ("src/user.rs", "Circle::extra", (4,)),
        ("src/user.rs", "crate::shape::Circle::extra", (8,)),
    }


def test_path_with_leading_colons(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Cargo.toml": '[package]\nname = "app"\nedition = "2021"\n',
        "src/lib.rs": "use ::core_lib::Level;\n\npub fn v() -> u8 {\n    ::core_lib::version()\n}\n\n"
                      "pub fn m(_level: Level) -> u8 {\n    assert_eq!(::core_lib::version(), 1);\n"
                      "    let _ = Vec::<u8>::new();\n    0\n}\n",
        "core_lib/Cargo.toml": '[package]\nname = "core_lib"\nedition = "2021"\n',
        "core_lib/src/lib.rs": "pub fn version() -> u8 { 1 }\npub struct Level;\n",
    })
    assert _graph(project_dir)["src/lib.rs"]["callees"] == {"core_lib/src/lib.rs"}
    assert _callee_dict(_file_dependencies(project_dir, "src/lib.rs")) == {
        ("::core_lib::version", "core_lib/src/lib.rs"): ([4, 8], "pub fn version() -> u8 { 1 }"),
        ("Level", "core_lib/src/lib.rs"): ([7], "pub struct Level;"),
    }


def test_enum_variants_of_glob_import(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Cargo.toml": '[package]\nname = "e"\nedition = "2021"\n',
        "src/lib.rs": "mod shape;\nmod app;\nmod local;\nmod tone;\n",
        "src/shape.rs": "pub enum Kind { Round, Square }\npub enum Size { Small, Large }\n",
        "src/tone.rs": "use self::Tone::*;\n\npub enum Tone { Dark, Light }\n\n"
                       "fn f(t: Tone) -> u8 {\n    match t {\n        Dark => 1,\n        Light => 2,\n    }\n}\n",
        "src/app.rs": "use crate::shape::Kind::*;\nuse crate::shape::Kind;\n\nstruct Square;\n\n"
                      "fn f(k: Kind) -> u8 {\n    match k {\n        Round => 1,\n        _ => 2,\n    }\n}\n\n"
                      "fn g() -> Square {\n    Square\n}\n",
        "src/local.rs": "use crate::shape::Size;\n\nfn f(s: Size) -> u8 {\n    use crate::shape::Size::*;\n"
                        "    match s {\n        Small => 1,\n        Large => 2,\n    }\n}\n\n"
                        "fn g() -> u8 {\n    let _ = Small;\n    0\n}\n",
    })
    # A variant gives way to a definition of the file of the same name
    result = _file_dependencies(project_dir, "src/app.rs")
    assert _callee_dict(result) == {
        ("Kind", "src/shape.rs"): ([6, 7], "pub enum Kind { Round, Square }"),
        ("Round", "src/shape.rs"): ([8], "pub enum Kind { Round, Square }"),
    }
    assert {"lines": [13, 14], "name": "Square"} in _name_line_list(result["same_file_usages"])
    # The variants of an enum of the file itself
    tone_result = _file_dependencies(project_dir, "src/tone.rs")
    assert _name_line_list(tone_result["same_file_usages"]) == [
        {"lines": [5, 6], "name": "Tone"},
        {"lines": [7], "name": "Dark"},
        {"lines": [8], "name": "Light"},
    ]
    # A glob import written in a function counts for that function
    assert _callee_dict(_file_dependencies(project_dir, "src/local.rs")) == {
        ("Size", "src/shape.rs"): ([3, 5], "pub enum Size { Small, Large }"),
        ("Small", "src/shape.rs"): ([6], "pub enum Size { Small, Large }"),
        ("Large", "src/shape.rs"): ([7], "pub enum Size { Small, Large }"),
    }


def test_reexport_of_outside_name_gives_no_usage(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Cargo.toml": '[package]\nname = "x"\nedition = "2021"\n',
        "src/lib.rs": "mod reexp;\nmod glob_user;\nmod name_user;\n",
        "src/reexp.rs": "pub use std::collections::HashMap;\npub fn own() {}\n",
        "src/glob_user.rs": "use crate::reexp::*;\n\nfn f() {\n    let _m: HashMap<u8, u8> = HashMap::new();\n    own();\n}\n",
        "src/name_user.rs": "use crate::reexp::HashMap;\n\nfn f() {\n    let _m: HashMap<u8, u8> = HashMap::new();\n"
                            "    let _n = crate::reexp::HashMap::<u8, u8>::new();\n}\n",
    })
    assert _callee_dict(_file_dependencies(project_dir, "src/glob_user.rs")) == {
        ("own", "src/reexp.rs"): ([5], "pub fn own() {}"),
    }
    assert _file_dependencies(project_dir, "src/name_user.rs")["callee_usages"] == []
    assert _graph(project_dir)["src/name_user.rs"]["callees"] == {"src/reexp.rs"}


def test_a_name_a_pattern_binds_is_no_usage_and_a_constant_in_a_pattern_is(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Cargo.toml": '[package]\nname = "pat"\nedition = "2021"\n',
        "src/lib.rs": "mod shape;\nmod app;\n",
        "src/shape.rs": (
            "pub const MAX: u8 = 9;\n"
            "pub struct Unit;\n"
            "pub enum Kind { Round, Square }\n"
            "pub fn other() -> u8 { 0 }\n"
            "pub fn v() -> u8 { 1 }\n"
        ),
        "src/app.rs": (
            "use crate::shape::{MAX, Unit, Kind, other, v};\n"   # 1
            "use crate::shape::Kind::*;\n"                        # 2
            "\n"
            "fn f(k: Kind, n: u8, o: Option<u8>) -> u8 {\n"      # 4
            "    match n {\n"                                     # 5
            "        MAX => 1,\n"                                 # 6
            "        other => other + 1,\n"                       # 7
            "    };\n"
            "    match k {\n"                                     # 9
            "        Round => 1,\n"                               # 10
            "        _ => other(),\n"                             # 11
            "    };\n"
            "    if let Some(v) = o {\n"                          # 13
            "        return v;\n"                                 # 14
            "    }\n"
            "    let Unit = Unit;\n"                              # 16
            "    v()\n"                                           # 17
            "}\n"
        ),
    })
    result = _file_dependencies(project_dir, "src/app.rs")
    assert {name: lines for (name, _), (lines, _) in _callee_dict(result).items()} == {
        "Kind": [4, 9],
        "MAX": [6],
        "Round": [10],
        "other": [11],
        "Unit": [16],
        "v": [17],
    }


def test_self_inside_a_trait_is_named_after_the_trait(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Cargo.toml": '[package]\nname = "tr"\nedition = "2021"\n',
        "src/lib.rs": (
            "pub trait Shape {\n"                                 # 1
            "    const KIND: u8;\n"                               # 2
            "    fn helper() -> u8 { 0 }\n"                       # 3
            "    fn kind(&self) -> u8 { Self::KIND }\n"           # 4
            "    fn make() -> u8 { Self::helper() }\n"            # 5
            "}\n"
        ),
    })
    result = _file_dependencies(project_dir, "src/lib.rs")
    assert _name_line_list(result["same_file_usages"]) == [
        {"lines": [4], "name": "Shape::KIND"},
        {"lines": [5], "name": "Shape::helper"},
    ]


def test_an_inline_module_sees_the_names_around_it_only_with_use_super(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Cargo.toml": '[package]\nname = "im"\nedition = "2021"\n',
        "src/lib.rs": (
            "pub fn top() -> u8 { 1 }\n"                          # 1
            "pub fn shared() -> u8 { 2 }\n"                       # 2
            "mod closed {\n"                                      # 3
            "    fn inner() -> u8 { 3 }\n"                        # 4
            "    pub fn run() -> u8 {\n"                          # 5
            "        inner() + top() + super::shared()\n"         # 6
            "    }\n"
            "}\n"
            "mod open {\n"                                        # 9
            "    use super::*;\n"                                 # 10
            "    pub fn run() -> u8 { top() }\n"                  # 11
            "    mod deep {\n"                                    # 12
            "        pub fn run() -> u8 { top() }\n"              # 13
            "    }\n"
            "}\n"
        ),
    })
    result = _file_dependencies(project_dir, "src/lib.rs")
    assert sorted(
        (usage["name"], usage["lines"]) for usage in result["same_file_usages"]
    ) == [("inner", [6]), ("super::shared", [6]), ("top", [11])]


def test_an_inline_module_sees_a_macro_of_the_file_without_use_super(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Cargo.toml": '[package]\nname = "mm"\nedition = "2021"\n',
        "src/lib.rs": (
            "macro_rules! twice {\n"                              # 1
            "    ($x:expr) => { $x * 2 };\n"
            "}\n"
            "pub fn top() -> u8 { 1 }\n"                          # 4
            "mod closed {\n"                                      # 5
            "    pub fn run() -> u8 { twice!(2) + top() }\n"      # 6
            "}\n"
            "pub fn open() -> u8 { twice!(3) }\n"                 # 8
        ),
    })
    result = _file_dependencies(project_dir, "src/lib.rs")
    assert sorted(
        (usage["name"], usage["lines"]) for usage in result["same_file_usages"]
    ) == [("twice", [6, 8])]



def test_a_member_written_after_self_in_an_impl_block_of_another_file(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Cargo.toml": '[package]\nname = "m"\nedition = "2021"\n',
        "src/lib.rs": "mod model;\nmod ext;\nmod ext2;\n",
        "src/model.rs": (
            "pub struct Counter {\n    pub count: u32,\n}\n"
            "impl Counter {\n    pub fn bump(&mut self) { self.count += 1; }\n}\n"
        ),
        "src/ext.rs": (
            "use crate::model::Counter;\n\n"
            "impl Counter {\n    pub fn twice(&mut self) {\n        self.bump();\n    }\n}\n"
        ),
        "src/ext2.rs": (
            "use crate::model::Counter;\n\n"               # 1
            "impl Counter {\n"                             # 3
            "    fn three(&mut self) {\n"                  # 4
            "        self.twice();\n"                      # 5
            "        self.bump();\n"                       # 6
            "        bump();\n"                            # 7
            "    }\n}\n\n"
            "fn bump() {}\n"                               # 11
        ),
    })
    assert _callee_dict(_file_dependencies(project_dir, "src/ext.rs")) == {
        ("Counter", "src/model.rs"): ([3], "pub struct Counter {"),
        ("bump", "src/model.rs"): ([5], "pub fn bump(&mut self) { self.count += 1; }"),
    }
    # A member of an impl block of a third file; a name by itself is no member
    result = _file_dependencies(project_dir, "src/ext2.rs")
    assert _callee_dict(result) == {
        ("Counter", "src/model.rs"): ([3], "pub struct Counter {"),
        ("twice", "src/ext.rs"): ([5], "pub fn twice(&mut self) {"),
        ("bump", "src/model.rs"): ([6], "pub fn bump(&mut self) { self.count += 1; }"),
    }
    assert [(u["name"], u["lines"]) for u in result["same_file_usages"]] == [("bump", [7])]


def test_a_member_of_another_file_written_after_self_in_the_file_of_the_type(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Cargo.toml": '[package]\nname = "m"\nedition = "2021"\n',
        "src/lib.rs": "mod model;\nmod ext;\n",
        "src/model.rs": (
            "pub struct Counter {\n    pub count: u32,\n}\n"       # 1
            "impl Counter {\n"                                    # 4
            "    pub fn all(&mut self) {\n"                       # 5
            "        self.twice();\n"                             # 6
            "        self.count += 1;\n"                          # 7
            "    }\n}\n"
        ),
        "src/ext.rs": (
            "use crate::model::Counter;\n\n"
            "impl Counter {\n    pub fn twice(&mut self) {}\n}\n"
        ),
    })
    # A method an impl block of another file defines for the type of the file
    result = _file_dependencies(project_dir, "src/model.rs")
    assert _callee_dict(result) == {
        ("twice", "src/ext.rs"): ([6], "pub fn twice(&mut self) {}"),
    }


def test_a_parameter_of_a_type_written_with_its_module(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Cargo.toml": '[package]\nname = "t"\nedition = "2021"\n',
        "src/lib.rs": "pub mod shape;\npub mod app;\n",
        "src/shape.rs": (
            "pub struct Circle {\n    pub r: f64,\n}\n\n"
            "impl Circle {\n    pub fn area(&self) -> f64 { self.r }\n}\n"
        ),
        "src/app.rs": (
            "use crate::shape;\n\n"
            "pub fn typed(c: &shape::Circle) -> f64 {\n    c.area()\n}\n\n"   # 3-5
            "pub struct Circle;\n\n"                                       # 7
            "pub fn other(e: &outside::Circle) -> f64 {\n    e.area()\n}\n"   # 9-11
        ),
    })
    result = _file_dependencies(project_dir, "src/app.rs")
    assert _callee_dict(result) == {
        ("shape::Circle", "src/shape.rs"): ([3], "pub struct Circle {"),
        ("shape::Circle.area", "src/shape.rs"): ([4], "pub fn area(&self) -> f64 { self.r }"),
    }
    # A type of another crate is no type of the file of the same name
    assert [u["name"] for u in result["same_file_usages"]] == []


def test_crate_roots_that_declare_the_same_module_file(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Cargo.toml": '[package]\nname = "shared"\nedition = "2021"\n',
        "src/lib.rs": "mod sub;\nmod util;\n",
        "src/util.rs": "pub fn helper() {}\n",
        "src/bin/common.rs": "pub fn shared() {}\n",
        "src/bin/one.rs": "mod common;\nfn main() { common::shared(); }\n",
        "src/bin/two.rs": "mod common;\nfn main() { common::shared(); }\n",
        "examples/common/mod.rs": "pub fn setup() {}\n",
        "examples/a.rs": "mod common;\nfn main() { common::setup(); }\n",
        "examples/b.rs": "mod common;\nfn main() { common::setup(); }\n",
        "examples/c.rs": "mod common;\nuse common::setup;\nfn main() { setup(); }\n",
        "src/sub.rs": "mod util;\n",
    })
    graph = _graph(project_dir)
    assert graph["examples/common/mod.rs"]["callers"] == {
        "examples/a.rs", "examples/b.rs", "examples/c.rs",
    }
    assert graph["src/bin/common.rs"]["callers"] == {"src/bin/one.rs", "src/bin/two.rs"}
    for file_rel in ("examples/a.rs", "examples/b.rs"):
        assert _callee_dict(_file_dependencies(project_dir, file_rel)) == {
            ("common::setup", "examples/common/mod.rs"): ([2], "pub fn setup() {}"),
        }
    assert _callee_dict(_file_dependencies(project_dir, "examples/c.rs")) == {
        ("setup", "examples/common/mod.rs"): ([3], "pub fn setup() {}"),
    }
    assert _callee_dict(_file_dependencies(project_dir, "src/bin/two.rs")) == {
        ("common::shared", "src/bin/common.rs"): ([2], "pub fn shared() {}"),
    }
    # A file that is the module of another file declares its modules under its own name
    assert graph["src/sub.rs"]["callees"] == set()


def test_a_use_declaration_of_an_inline_module_is_followed(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Cargo.toml": '[package]\nname = "inl"\nedition = "2021"\n',
        "src/lib.rs": (
            "pub mod shape;\nmod app;\nmod glob;\nmod write;\n"     # 1-4
            "pub mod prelude {\n"                                 # 5
            "    pub use crate::shape::Circle;\n"                 # 6
            "    pub use super::shape::area as size;\n"           # 7
            "    pub use std::fmt::Write;\n"                      # 8
            "    pub fn own() {}\n"                               # 9
            "    pub mod inner {\n"                               # 10
            "        pub use crate::shape::*;\n"                  # 11
            "        pub enum Mode { Fast, Slow }\n"              # 12
            "    }\n"
            "}\n"
            "pub fn local() -> f64 { prelude::size() }\n"         # 15
        ),
        "src/shape.rs": (
            "pub struct Circle;\n"
            "impl Circle {\n    pub fn new() -> Self { Circle }\n}\n"
            "pub fn area() -> f64 { 0.0 }\n"                       # 5
            "pub fn perimeter() -> f64 { 0.0 }\n"                  # 6
        ),
        "src/app.rs": (
            "use crate::prelude::Circle;\n"                       # 1
            "use crate::prelude::size;\n"                         # 2
            "use crate::prelude::inner::Mode::*;\n"               # 3
            "fn f() -> f64 {\n"                                   # 4
            "    let _c = Circle::new();\n"                       # 5
            "    crate::prelude::own();\n"                        # 6
            "    let _m = Fast;\n"                                # 7
            "    crate::prelude::inner::perimeter() + size()\n"   # 8
            "}\n"
        ),
        "src/glob.rs": (
            "use crate::prelude::inner::*;\n"                     # 1
            "fn g() -> f64 { Circle::new(); area() }\n"           # 2
        ),
        "src/write.rs": "use crate::prelude::Write;\nfn h(w: &mut dyn Write) {}\n",
    })
    assert _callee_dict(_file_dependencies(project_dir, "src/app.rs")) == {
        ("Circle::new", "src/shape.rs"): ([5], "pub fn new() -> Self { Circle }"),
        ("size", "src/shape.rs"): ([8], "pub fn area() -> f64 { 0.0 }"),
        ("crate::prelude::own", "src/lib.rs"): ([6], "pub fn own() {}"),
        ("Fast", "src/lib.rs"): ([7], "pub enum Mode { Fast, Slow }"),
        ("crate::prelude::inner::perimeter", "src/shape.rs"): (
            [8], "pub fn perimeter() -> f64 { 0.0 }",
        ),
    }
    assert _callee_dict(_file_dependencies(project_dir, "src/lib.rs")) == {
        ("prelude::size", "src/shape.rs"): ([15], "pub fn area() -> f64 { 0.0 }"),
    }
    # A path through a glob import of an inline module
    assert _callee_dict(_file_dependencies(project_dir, "src/glob.rs"))[
        ("Circle::new", "src/shape.rs")
    ] == ([2], "pub fn new() -> Self { Circle }")
    # A name the inline module re-exports from outside the project leads nowhere
    assert _file_dependencies(project_dir, "src/write.rs")["callee_usages"] == []
    assert _graph(project_dir)["src/app.rs"]["callees"] == {"src/shape.rs", "src/lib.rs"}


def test_a_use_path_of_an_inline_module_starts_from_the_module(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Cargo.toml": '[package]\nname = "rel"\nedition = "2021"\n',
        "src/lib.rs": (
            "mod imp;\nmod shape;\n"                              # 1-2
            "pub mod o1 {\n"                                     # 3
            "    mod imp {\n"                                    # 4
            "        pub fn g() -> i32 { 1 }\n"                  # 5
            "    }\n"
            "    pub use imp::g;\n"                              # 7
            "}\n"
            "pub mod o2 {\n"                                     # 9
            "    pub mod deep {\n"                               # 10
            "        mod imp {\n"                                # 11
            "            pub fn h() -> i32 { 1 }\n"              # 12
            "        }\n"
            "        pub use imp::*;\n"                          # 14
            "    }\n"
            "    pub use deep::h;\n"                             # 16
            "}\n"
            "pub mod o3 {\n"                                     # 18
            "    use crate::shape;\n"                            # 19
            "    pub use shape::area;\n"                         # 20
            "    pub enum Mode { Fast, Slow }\n"                 # 21
            "    pub use Mode::Fast;\n"                          # 22
            "    pub use std::fmt::Write;\n"                     # 23
            "}\n"
            "mod imp5 {\n"                                       # 25
            "    pub fn g5() -> i32 { 1 }\n"                     # 26
            "}\n"
            "pub use imp5::g5;\n"                                # 28
        ),
        "src/imp.rs": "pub fn g() -> i32 { 2 }\n",
        "src/shape.rs": "pub fn area() -> f64 { 0.0 }\n",
        "src/main.rs": (
            "use std::fmt::Write;\n"                             # 1
            "fn main() {\n"                                      # 2
            "    rel::o1::g();\n"                                # 3
            "    rel::o2::h();\n"                                # 4
            "    rel::o3::area();\n"                             # 5
            "    let _m = rel::o3::Fast;\n"                      # 6
            "    rel::g5();\n"                                   # 7
            "    rel::o3::Write::write_str;\n"                   # 8
            "}\n"
        ),
    })
    assert _callee_dict(_file_dependencies(project_dir, "src/main.rs")) == {
        # the inline module imp of o1, not the file module imp of the crate root
        ("rel::o1::g", "src/lib.rs"): ([3], "pub fn g() -> i32 { 1 }"),
        ("rel::o2::h", "src/lib.rs"): ([4], "pub fn h() -> i32 { 1 }"),
        ("rel::o3::area", "src/shape.rs"): ([5], "pub fn area() -> f64 { 0.0 }"),
        ("rel::o3::Fast", "src/lib.rs"): ([6], "pub enum Mode { Fast, Slow }"),
        ("rel::g5", "src/lib.rs"): ([7], "pub fn g5() -> i32 { 1 }"),
    }


def _count_walk(monkeypatch, project_dir: str) -> int:
    """Build the dependencies of a project and count the module tree walks it takes."""
    from codetwine.rust_module_tree import RustModuleTree

    count_list = [0]
    walk = RustModuleTree._walk

    def walk_with_count(self, *arg_tuple):
        count_list[0] += 1
        return walk(self, *arg_tuple)

    monkeypatch.setattr(RustModuleTree, "_walk", walk_with_count)
    build_project_dependencies(project_dir)
    monkeypatch.setattr(RustModuleTree, "_walk", walk)
    return count_list[0]


def test_paths_through_a_glob_prelude_are_resolved_in_time_linear_in_the_modules(
    tmp_path, monkeypatch,
):
    count_dict = {}
    for module_count in (10, 20):
        file_dict = {
            "Cargo.toml": '[package]\nname = "pre"\nedition = "2021"\n',
            "src/lib.rs": "pub mod prelude;\n" + "".join(
                f"pub mod m{i};\n" for i in range(module_count)
            ),
            "src/prelude.rs": "".join(f"pub use crate::m{i}::*;\n" for i in range(module_count)),
        }
        for i in range(module_count):
            file_dict[f"src/m{i}.rs"] = (
                f"use crate::prelude::*;\npub fn f{i}() {{\n"
                + "".join(f"    ext{k}::call{k}();\n" for k in range(5))
                + f"    f{(i + 1) % module_count}();\n}}\n"
            )
        project_dir = _make_files(tmp_path, file_dict, name=f"pre{module_count}")
        count_dict[module_count] = _count_walk(monkeypatch, project_dir)
    assert count_dict[20] < 3 * count_dict[10]


def test_a_name_written_apart_from_a_path_in_a_macro_is_a_usage(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Cargo.toml": '[package]\nname = "mp"\nedition = "2021"\n',
        "src/lib.rs": (
            "mod config;\n"
            "use crate::config::Config;\n"                                    # 2
            "fn check() {\n"
            "    println!(\"{:?} {}\", Config { n: 1 }, Config::size());\n"   # 4
            "    let v = vec![Config::size(), Config::size()];\n"            # 5
            "    let w = (Config { n: 2 }, Config::size());\n"                # 6
            "}\n"
        ),
        "src/config.rs": (
            "#[derive(Debug)]\npub struct Config {\n    pub n: u8,\n}\n"
            "impl Config {\n    pub fn size() -> u8 { 1 }\n}\n"
        ),
    })
    callee_dict = _callee_dict(_file_dependencies(project_dir, "src/lib.rs"))
    # In a macro as outside one: Config by itself on 4 and 6, the path alone on 5
    assert callee_dict[("Config", "src/config.rs")][0] == [4, 6]
    assert callee_dict[("Config::size", "src/config.rs")][0] == [4, 5, 6]


def test_a_struct_of_one_cfg_named_like_an_import_of_another_is_no_usage_of_it(tmp_path):
    project_dir = _make_files(tmp_path, {
        "Cargo.toml": '[package]\nname = "cf"\nedition = "2021"\n',
        "src/lib.rs": (
            "mod sys;\n"
            "#[cfg(unix)]\nuse crate::sys::Handle;\n"
            "#[cfg(windows)]\npub struct Handle {\n    h: u64,\n}\n"     # 5
            "pub fn open() -> Handle {\n    todo!()\n}\n"               # 8
        ),
        "src/sys.rs": "pub struct Handle {\n    fd: i32,\n}\n",
    })
    callee_dict = _callee_dict(_file_dependencies(project_dir, "src/lib.rs"))
    assert callee_dict[("Handle", "src/sys.rs")][0] == [8]
