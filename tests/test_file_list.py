"""process_all_files() analyzes only the files of file_list when one is given.

EXCLUDE_PATTERNS and the text file check apply to the given files as they do to a walk
of the project; a given path that does not exist or leads outside the project is named
in a warning. Without file_list, the project is walked.
"""

import sqlite3

from codetwine import pipeline
from codetwine.extractors.dependency_graph import build_project_dependencies


def _make_project(tmp_path) -> str:
    """Create a project with two source files, a binary file and an excluded directory."""
    project = tmp_path / "proj"
    (project / "pkg").mkdir(parents=True)
    (project / "a.py").write_text("def f():\n    return 1\n")
    (project / "pkg" / "local.py").write_text("TOKEN = 'xyz'\n")
    (project / "blob.bin").write_bytes(b"\x89PNG\x00\x00binary")
    (project / "node_modules").mkdir()
    (project / "node_modules" / "x.js").write_text("module.exports = 1;\n")
    (tmp_path / "outside.py").write_text("def g():\n    return 1\n")
    return str(project)


def _file_list(dep_list: list[dict]) -> list[str]:
    """Return the "file" fields of a build_project_dependencies() result, sorted."""
    return sorted(dep["file"] for dep in dep_list)


def test_only_the_given_files_are_listed(tmp_path):
    project = _make_project(tmp_path)

    assert _file_list(build_project_dependencies(project, ["a.py"])) == ["proj/a_py/a.py"]
    assert _file_list(build_project_dependencies(project)) == [
        "proj/a_py/a.py", "proj/pkg/local_py/local.py",
    ]


def test_the_given_files_pass_the_same_checks_as_a_walk(tmp_path):
    project = _make_project(tmp_path)

    dep_list = build_project_dependencies(project, [
        "a.py", "a.py", "blob.bin", "node_modules/x.js", "missing.py", "../outside.py",
    ])

    assert _file_list(dep_list) == ["proj/a_py/a.py"]


async def test_the_pipeline_passes_the_file_list_on(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", False)
    monkeypatch.setattr(pipeline, "KNOWLEDGE_FORMAT", "sqlite")
    project = _make_project(tmp_path)
    out = tmp_path / "out"

    run_result = await pipeline.process_all_files(
        project, str(out), None, max_workers=1, file_list=["a.py"],
    )

    assert run_result["file_count"] == 1
    connection = sqlite3.connect(out / "proj" / "project_knowledge.sqlite")
    assert [row[0] for row in connection.execute("SELECT file FROM files")] == ["proj/a_py/a.py"]


def test_a_given_path_outside_the_project_or_missing_is_named_in_a_warning(tmp_path, caplog):
    project = _make_project(tmp_path)
    outside_abs = str(tmp_path / "outside.py")

    dep_list = build_project_dependencies(project, [
        "a.py", "deleted.py", "pkg/gone.py", "../outside.py", outside_abs, "pkg/../../outside.py",
        "blob.bin", "node_modules/x.js", "node_modules/gone.js",
    ])

    assert _file_list(dep_list) == ["proj/a_py/a.py"]
    assert [
        record.getMessage() for record in caplog.records if record.levelname == "WARNING"
    ] == [
        "Left out of file_list (outside the project directory): "
        f"../outside.py, {outside_abs}, pkg/../../outside.py",
        "Left out of file_list (no such file): deleted.py, pkg/gone.py",
    ]


def test_given_files_of_the_project_give_no_warning(tmp_path, caplog):
    project = _make_project(tmp_path)

    dep_list = build_project_dependencies(project, ["a.py", "./a.py", "pkg/../a.py", "pkg/local.py"])

    assert _file_list(dep_list) == ["proj/a_py/a.py", "proj/pkg/local_py/local.py"]
    assert [record for record in caplog.records if record.levelname == "WARNING"] == []
