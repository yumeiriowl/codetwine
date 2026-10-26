"""Other extensions of C++, JavaScript and TypeScript are analyzed with that language.

.cc, .cxx, .hpp, .hh and .hxx are read as C++, .mjs and .cjs as JavaScript, and .mts and
.cts as TypeScript; an include or import written with such an extension resolves to the file.
"""

import pytest

from codetwine.config.settings import EXT_TO_DEFINITION_DICT, EXT_TO_LANGUAGE_DICT, language_ext
from codetwine.extractors.dependency_graph import build_project_dependencies


@pytest.mark.parametrize("alias, canonical", [
    ("cc", "cpp"), ("cxx", "cpp"), ("hpp", "cpp"), ("hh", "cpp"), ("hxx", "cpp"),
    ("mjs", "js"), ("cjs", "js"), ("mts", "ts"), ("cts", "ts"),
])
def test_an_alias_extension_has_the_settings_of_its_language(alias, canonical):
    assert language_ext(f"src/a.{alias}") == alias
    assert EXT_TO_LANGUAGE_DICT[alias] is EXT_TO_LANGUAGE_DICT[canonical]
    assert EXT_TO_DEFINITION_DICT[alias] is EXT_TO_DEFINITION_DICT[canonical]


def _callee_dict(dep_list: list[dict]) -> dict[str, list[str]]:
    """Key the callees of a build_project_dependencies() result by file."""
    return {dep["file"]: sorted(dep["callees"]) for dep in dep_list}


def test_a_cpp_include_of_an_hpp_file_resolves(tmp_path):
    (tmp_path / "geo.hpp").write_text("int area(int w, int h);\n")
    (tmp_path / "geo.cc").write_text('#include "geo.hpp"\nint area(int w, int h) { return w * h; }\n')
    (tmp_path / "main.cxx").write_text('#include "geo.hpp"\nint run() { return area(2, 3); }\n')

    callee_dict = _callee_dict(build_project_dependencies(str(tmp_path)))

    name = tmp_path.name
    # The caller depends on the header and on the file that defines the function it declares
    assert callee_dict[f"{name}/main_cxx/main.cxx"] == [
        f"{name}/geo_cc/geo.cc", f"{name}/geo_hpp/geo.hpp",
    ]
    assert callee_dict[f"{name}/geo_cc/geo.cc"] == [f"{name}/geo_hpp/geo.hpp"]


def test_an_es_module_import_with_its_extension_resolves(tmp_path):
    (tmp_path / "util.mjs").write_text("export function twice(x) { return x * 2; }\n")
    (tmp_path / "main.mjs").write_text("import { twice } from './util.mjs';\nexport const v = twice(2);\n")
    (tmp_path / "lib.mts").write_text("export function half(x: number): number { return x / 2; }\n")
    (tmp_path / "app.cts").write_text("import { half } from './lib.mts';\nexport const w = half(4);\n")

    callee_dict = _callee_dict(build_project_dependencies(str(tmp_path)))

    name = tmp_path.name
    assert callee_dict[f"{name}/main_mjs/main.mjs"] == [f"{name}/util_mjs/util.mjs"]
    assert callee_dict[f"{name}/app_cts/app.cts"] == [f"{name}/lib_mts/lib.mts"]
