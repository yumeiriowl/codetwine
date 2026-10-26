"""A second build_project_dependencies() call in one process reads the project files again.

The indexes built from files that have no language (.csproj, Cargo.toml) are not kept
from the first call, and process_all_files() clears the caches of its analysis when it
ends.
"""

import os

from codetwine import import_reference, pipeline
from codetwine.extractors import definition_source
from codetwine.extractors.dependency_graph import build_project_dependencies
from codetwine.parsers.ts_parser import parse_cache_dict
from codetwine.utils.file_utils import copy_path_to_rel


def _make_files(tmp_path, file_dict: dict[str, str], name: str = "twice") -> str:
    """Create a project directory holding the given files."""
    project = tmp_path / name
    for file_rel, text in file_dict.items():
        path = project / file_rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    parse_cache_dict.clear()
    return str(project)


def _callee_set(project_dir: str, file_rel: str) -> set[str]:
    """Build the dependency graph and return the callees of one file."""
    project_name = os.path.basename(project_dir)
    for dep in build_project_dependencies(project_dir):
        if copy_path_to_rel(dep["file"][len(project_name) + 1:]) == file_rel:
            return {copy_path_to_rel(p[len(project_name) + 1:]) for p in dep["callees"]}
    raise AssertionError(f"{file_rel} is not in the graph")


def test_csproj_added_between_two_analyses(tmp_path):
    project_dir = _make_files(tmp_path, {
        "src/App/GlobalUsings.cs": "global using App.Text;\n",
        "src/App/Text/Formatter.cs": (
            "namespace App.Text;\n\n"
            "public static class Formatter { public static string Format(int x) => \"\"; }\n"
        ),
        "src/Tool/Program.cs": (
            "namespace Tool;\n\npublic class Program { public string Run() => Formatter.Format(1); }\n"
        ),
    })
    # Without a .csproj file the global using directive counts for the whole project
    assert _callee_set(project_dir, "src/Tool/Program.cs") == {"src/App/Text/Formatter.cs"}

    with open(os.path.join(project_dir, "src/App/App.csproj"), "w") as project_file:
        project_file.write("<Project Sdk=\"Microsoft.NET.Sdk\"></Project>\n")
    assert _callee_set(project_dir, "src/Tool/Program.cs") == set()


def test_cargo_toml_added_between_two_analyses(tmp_path):
    project_dir = _make_files(tmp_path, {
        "b/Cargo.toml": "[package]\nname = \"b\"\n",
        "b/src/main.rs": "use a::hello;\n\nfn main() { hello(); }\n",
        "a/src/lib.rs": "pub fn hello() {}\n",
    })
    assert _callee_set(project_dir, "b/src/main.rs") == set()

    with open(os.path.join(project_dir, "a/Cargo.toml"), "w") as cargo_file:
        cargo_file.write("[package]\nname = \"a\"\n")
    assert _callee_set(project_dir, "b/src/main.rs") == {"a/src/lib.rs"}


def test_copybook_document_that_gains_an_r_chunk(tmp_path):
    project_dir = _make_files(tmp_path, {
        "main.cbl": (
            "       IDENTIFICATION DIVISION.\n       PROGRAM-ID. MAIN.\n"
            "       DATA DIVISION.\n       WORKING-STORAGE SECTION.\n       COPY NOTES.\n"
            "       PROCEDURE DIVISION.\n           MOVE 1 TO WS-A.\n           STOP RUN.\n"
        ),
        "NOTES.Rmd": "       01 WS-A PIC 9.\n",
    })
    assert _callee_set(project_dir, "main.cbl") == {"NOTES.Rmd"}

    with open(os.path.join(project_dir, "NOTES.Rmd"), "a") as document_file:
        document_file.write("\n```{r}\nx <- 1\n```\n")
    parse_cache_dict.clear()
    assert _callee_set(project_dir, "main.cbl") == set()


async def test_a_run_leaves_no_analysis_cache_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", False)
    monkeypatch.setattr(pipeline, "KNOWLEDGE_FORMAT", "json")
    project = _make_files(tmp_path, {
        "a.py": "from b import g\n\ng()\n",
        "b.py": "def g():\n    return 1\n",
    })

    await pipeline.process_all_files(project, str(tmp_path / "out"), None, max_workers=1)

    assert parse_cache_dict == {}
    assert definition_source._definition_list_cache_dict == {}
    assert definition_source._definition_cache_dict == {}
    assert import_reference._import_target_cache_dict == {}
