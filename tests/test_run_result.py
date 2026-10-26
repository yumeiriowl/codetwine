"""process_all_files() returns what it analyzed and what failed.

A file whose dependency extraction fails is listed in dependency_fail_list, the
output a previous run left for it is removed, and the consolidated outputs leave it
out. A file left without a complete design document is listed in doc_fail_list. The
project directory is named by its absolute path, and one that is no directory stops the
run, as does an output directory that is the project directory or its parent, also
through a symbolic link; main.py logs such a stop as an error and exits with status 1.
Stub LLM clients stand in for the real API.
"""

import importlib.util
import json
import os
import pathlib
import sqlite3
import sys

import pytest

from codetwine import doc_creator, knowledge_db, pipeline
from codetwine.utils.file_utils import resolve_file_output_dir


class StubLLMClient:
    """Stand-in for LLMClient that answers every prompt with fixed text."""

    async def generate(self, prompt: str, max_tokens: int | None = None) -> str:
        return "OK"


class FailLLMClient:
    """Stand-in for LLMClient that fails every prompt."""

    async def generate(self, prompt: str, max_tokens: int | None = None) -> str:
        raise RuntimeError("401 Unauthorized")


def _write_template(tmp_path) -> str:
    """Write a one-section template and return its path."""
    template_path = tmp_path / "template.json"
    template_path.write_text(json.dumps({
        "sections": [{"id": "overview", "title": "Overview", "prompt": "Describe it."}],
        "summary_prompt": "Summarize it.",
    }))
    return str(template_path)


async def test_a_failed_file_is_reported_and_its_previous_output_is_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", False)
    monkeypatch.setattr(pipeline, "KNOWLEDGE_FORMAT", "sqlite")
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text("def old_name():\n    return 1\n")
    (project / "b.py").write_text("def other():\n    return 1\n")
    out = tmp_path / "out"

    first_result = await pipeline.process_all_files(str(project), str(out), None, max_workers=1)
    assert first_result == {
        "file_count": 2, "dependency_fail_list": [], "doc_count": 0, "doc_fail_list": [],
        "detected_encoding_dict": {},
    }

    # The source changes and its extraction fails in the next run
    (project / "a.py").write_text("def new_name():\n    return 2\n")
    real_get_file_dependencies = pipeline.get_file_dependencies

    def fail_on_a(file_abs, *args, **kwargs):
        if file_abs.endswith("a.py"):
            raise RuntimeError("parse failed")
        return real_get_file_dependencies(file_abs, *args, **kwargs)

    monkeypatch.setattr(pipeline, "get_file_dependencies", fail_on_a)
    second_result = await pipeline.process_all_files(str(project), str(out), None, max_workers=1)

    assert second_result["file_count"] == 2
    assert second_result["dependency_fail_list"] == ["a.py"]
    assert os.listdir(resolve_file_output_dir(str(out / "proj"), "a.py")) == []
    connection = sqlite3.connect(out / "proj" / "project_knowledge.sqlite")
    assert [row[0] for row in connection.execute("SELECT name FROM definitions")] == ["other"]


async def test_files_without_a_complete_document_are_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", True)
    monkeypatch.setattr(pipeline, "KNOWLEDGE_FORMAT", "json")
    monkeypatch.setattr(doc_creator, "DOC_TEMPLATE_PATH", _write_template(tmp_path))
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text("def f():\n    return 1\n")
    (project / "notes.txt").write_text("hello\n")
    out = tmp_path / "out"

    fail_result = await pipeline.process_all_files(str(project), str(out), FailLLMClient(), max_workers=1)
    assert fail_result["doc_count"] == 0
    assert fail_result["doc_fail_list"] == ["a.py"]

    ok_result = await pipeline.process_all_files(str(project), str(out), StubLLMClient(), max_workers=1)
    assert ok_result["file_count"] == 2
    assert ok_result["doc_count"] == 1
    assert ok_result["doc_fail_list"] == []


async def test_a_document_without_a_summary_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(doc_creator, "DOC_TEMPLATE_PATH", _write_template(tmp_path))
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", True)
    monkeypatch.setattr(pipeline, "KNOWLEDGE_FORMAT", "json")
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text("def f():\n    return 1\n")

    class SummaryFailLLMClient:
        """Stand-in for LLMClient that fails the summary prompt only."""

        async def generate(self, prompt: str, max_tokens: int | None = None) -> str:
            if "Summarize it." in prompt:
                raise RuntimeError("timeout")
            return "OK"

    run_result = await pipeline.process_all_files(
        str(project), str(tmp_path / "out"), SummaryFailLLMClient(), max_workers=1,
    )

    assert run_result["doc_count"] == 0
    assert run_result["doc_fail_list"] == ["a.py"]


async def test_a_project_directory_with_a_trailing_separator_or_dot_keeps_its_name(
        tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", False)
    monkeypatch.setattr(pipeline, "KNOWLEDGE_FORMAT", "json")
    project = tmp_path / "proj"
    (project / "pkg").mkdir(parents=True)
    (project / "app.py").write_text("from pkg.util import helper\n\nhelper()\n")
    (project / "pkg" / "util.py").write_text("def helper():\n    return 1\n")

    monkeypatch.chdir(project)
    for project_arg, out in ((str(project) + os.sep, tmp_path / "out1"), (".", tmp_path / "out2")):
        await pipeline.process_all_files(project_arg, str(out), None, max_workers=1)
        assert sorted(os.listdir(out)) == ["proj"]
        knowledge = json.loads((out / "proj" / "project_knowledge.json").read_text())
        assert knowledge["project_name"] == "proj"
        dep_dict = {dep["file"]: dep["callees"] for dep in knowledge["project_dependencies"]}
        assert dep_dict["proj/app_py/app.py"] == ["proj/pkg/util_py/util.py"]


async def test_a_project_directory_that_is_no_directory_stops_the_run(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", False)
    (tmp_path / "single.py").write_text("x = 1\n")
    out = tmp_path / "out"

    for project_arg in (str(tmp_path / "missing"), str(tmp_path / "single.py")):
        with pytest.raises(ValueError, match="is not a directory"):
            await pipeline.process_all_files(project_arg, str(out), None, max_workers=1)
    assert not out.exists()


def test_main_logs_a_stop_of_the_run_as_an_error_and_exits_with_status_1(
        tmp_path, monkeypatch, caplog):
    spec = importlib.util.spec_from_file_location(
        "codetwine_main", pathlib.Path(__file__).resolve().parent.parent / "main.py",
    )
    main = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(main)
    monkeypatch.setattr(main, "setup_logging", lambda: None)
    monkeypatch.setattr(main, "ENABLE_LLM_DOC", False)
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", False)
    missing = tmp_path / "missing"
    monkeypatch.setattr(sys, "argv", [
        "main.py", "--project-dir", str(missing), "--output-dir", str(tmp_path / "out"),
    ])

    with pytest.raises(SystemExit) as exit_info:
        main.main()

    assert exit_info.value.code == 1
    assert [(record.levelname, record.getMessage()) for record in caplog.records] == [(
        "ERROR",
        f"The project directory {missing} does not exist or is not a directory. Pass the "
        f"root directory of the project to analyze (--project-dir or DEFAULT_PROJECT_DIR).",
    )]
    assert not (tmp_path / "out").exists()


async def test_the_parent_of_the_project_as_output_directory_stops_the_run(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", False)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text("x = 1\n")

    with pytest.raises(ValueError, match="into the project directory itself"):
        await pipeline.process_all_files(str(project), str(tmp_path), None, max_workers=1)
    assert os.listdir(project) == ["a.py"]


async def test_the_parent_of_the_project_through_a_link_as_output_directory_stops_the_run(
        tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", False)
    project = tmp_path / "real" / "proj"
    project.mkdir(parents=True)
    (project / "a.py").write_text("x = 1\n")
    (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)

    for project_arg, output_dir in (
        (tmp_path / "link" / "proj", tmp_path / "real"),
        (project, tmp_path / "link"),
    ):
        with pytest.raises(ValueError, match="into the project directory itself"):
            await pipeline.process_all_files(str(project_arg), str(output_dir), None, max_workers=1)
    assert os.listdir(project) == ["a.py"]


@pytest.mark.parametrize("form", ["same", "trailing_separator", "dot", "link"])
async def test_the_project_as_output_directory_stops_the_run(tmp_path, monkeypatch, form):
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", False)
    # A project holding a package of its own name
    project = tmp_path / "requests"
    (project / "requests").mkdir(parents=True)
    (project / "setup.py").write_text("from requests.api import get\n")
    (project / "requests" / "api.py").write_text("def get():\n    return 1\n")
    (tmp_path / "link").symlink_to(project, target_is_directory=True)
    monkeypatch.chdir(project)
    output_dir = {
        "same": str(project),
        "trailing_separator": str(project) + os.sep,
        "dot": ".",
        "link": str(tmp_path / "link"),
    }[form]

    with pytest.raises(ValueError, match="is the project directory"):
        await pipeline.process_all_files(str(project), output_dir, None, max_workers=1)
    assert sorted(os.listdir(project)) == ["requests", "setup.py"]
    assert os.listdir(project / "requests") == ["api.py"]


async def test_a_failed_file_is_left_out_of_every_consolidated_output(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", False)
    monkeypatch.setattr(pipeline, "KNOWLEDGE_FORMAT", "both")
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text("from b import g\n\ng()\n")
    (project / "b.py").write_text("def g():\n    return 1\n")
    (project / "c.py").write_text("from a import *\n")
    out = tmp_path / "out"
    real_get_file_dependencies = pipeline.get_file_dependencies

    def fail_on_b(file_abs, *args, **kwargs):
        if os.path.basename(file_abs) == "b.py":
            raise RuntimeError("parse failed")
        return real_get_file_dependencies(file_abs, *args, **kwargs)

    monkeypatch.setattr(pipeline, "get_file_dependencies", fail_on_b)
    run_result = await pipeline.process_all_files(str(project), str(out), None, max_workers=1)
    assert run_result["dependency_fail_list"] == ["b.py"]

    base = out / "proj"
    knowledge = json.loads((base / "project_knowledge.json").read_text())
    summary = json.loads((base / "project_dependency_summary.json").read_text())
    b_path = "proj/b_py/b.py"
    json_dep_list = sorted(knowledge["project_dependencies"], key=lambda dep: dep["file"])
    assert [dep["file"] for dep in json_dep_list] == ["proj/a_py/a.py", "proj/c_py/c.py"]
    assert all(b_path not in dep["callers"] + dep["callees"] for dep in json_dep_list)
    assert sorted(summary["files"], key=lambda dep: dep["file"]) == json_dep_list
    assert sorted(entry["file"] for entry in knowledge["files"]) == ["proj/a_py/a.py", "proj/c_py/c.py"]
    assert "b.py" not in (base / "dependency_graph.md").read_text()

    connection = knowledge_db.open_knowledge(str(base / "project_knowledge.sqlite"))
    db_dep_list = sorted(knowledge_db.iter_dependencies(connection), key=lambda dep: dep["file"])
    assert db_dep_list == json_dep_list
    assert knowledge_db.callees_of(connection, "proj/a_py/a.py") == []
    connection.close()
