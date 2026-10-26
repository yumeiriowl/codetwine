"""Where KNOWLEDGE_FORMAT is checked.

The pipeline reads the setting from its own namespace, so a caller can replace it
before running. Checking the value while the settings module is imported would stop
such a caller over a value it never uses, so the check belongs in process_all_files().
"""

import os
import subprocess
import sys

import pytest

from codetwine import doc_creator, pipeline
from codetwine.config.settings import KNOWLEDGE_FORMAT_TUPLE


def _import_with(value: str) -> subprocess.CompletedProcess:
    """Import the pipeline in a child process with KNOWLEDGE_FORMAT set.

    Args:
        value: What to put in the environment variable.

    Returns:
        The finished process, its stderr captured.
    """
    env = dict(os.environ, KNOWLEDGE_FORMAT=value)
    return subprocess.run([sys.executable, "-c", "import codetwine.pipeline"],
                          env=env, capture_output=True, text=True, check=False)


@pytest.mark.parametrize("value", ["sqlite3", "", "yaml"])
def test_an_unusable_value_does_not_stop_the_import(value):
    """Importing must not fail over a setting the caller may be about to replace."""
    done = _import_with(value)
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize("value", list(KNOWLEDGE_FORMAT_TUPLE))
def test_every_accepted_value_imports_too(value):
    assert _import_with(value).returncode == 0


async def test_an_unusable_value_stops_the_run_before_anything_is_analysed(
        tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "KNOWLEDGE_FORMAT", "sqlite3")
    project = tmp_path / "proj"
    project.mkdir()
    out = tmp_path / "out"
    with pytest.raises(ValueError) as e:
        await pipeline.process_all_files(str(project), str(out), None, max_workers=1)
    assert "json / sqlite / both" in str(e.value)
    assert not out.exists()


async def test_the_value_the_caller_sets_is_the_one_that_is_checked(tmp_path,
                                                                    monkeypatch):
    """A caller replacing the setting is not held to what the environment holds."""
    monkeypatch.setenv("KNOWLEDGE_FORMAT", "sqlite3")
    monkeypatch.setattr(pipeline, "KNOWLEDGE_FORMAT", "sqlite")
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", False)
    project = tmp_path / "proj"
    project.mkdir()
    out = tmp_path / "out"
    await pipeline.process_all_files(str(project), str(out), None, max_workers=1)
    assert (out / "proj").is_dir()


async def test_an_unusable_piece_length_stops_the_run_before_anything_is_analysed(
        tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", True)
    monkeypatch.setattr(doc_creator, "ENABLE_CODE_SUMMARY", True)
    monkeypatch.setattr(doc_creator, "CODE_SUMMARY_PIECE_LINES", 0)
    project = tmp_path / "proj"
    project.mkdir()
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="CODE_SUMMARY_PIECE_LINES must be 1 or more"):
        await pipeline.process_all_files(str(project), str(out), None, max_workers=1)
    assert not out.exists()


def test_the_piece_length_is_not_checked_without_code_summaries(monkeypatch):
    monkeypatch.setattr(doc_creator, "ENABLE_CODE_SUMMARY", False)
    monkeypatch.setattr(doc_creator, "CODE_SUMMARY_PIECE_LINES", 0)
    doc_creator.check_code_summary_setting()


async def test_the_piece_length_is_not_checked_without_design_documents(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", False)
    monkeypatch.setattr(doc_creator, "ENABLE_CODE_SUMMARY", True)
    monkeypatch.setattr(doc_creator, "CODE_SUMMARY_PIECE_LINES", 0)
    project = tmp_path / "proj"
    project.mkdir()
    out = tmp_path / "out"
    await pipeline.process_all_files(str(project), str(out), None, max_workers=1)
    assert (out / "proj").is_dir()


@pytest.mark.parametrize("max_workers", [0, -1])
async def test_a_worker_count_below_one_stops_the_run_before_anything_is_analysed(
        tmp_path, monkeypatch, max_workers):
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", True)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text("def f():\n    return 1\n")
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="MAX_WORKERS must be 1 or more"):
        await pipeline.process_all_files(str(project), str(out), None, max_workers=max_workers)
    assert not out.exists()


async def test_the_worker_count_is_not_checked_without_design_documents(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", False)
    project = tmp_path / "proj"
    project.mkdir()
    out = tmp_path / "out"
    await pipeline.process_all_files(str(project), str(out), None, max_workers=0)
    assert (out / "proj").is_dir()


async def test_a_negative_parse_cache_size_stops_the_run_before_anything_is_analysed(
        tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", False)
    monkeypatch.setattr(pipeline, "PARSE_CACHE_MAX_FILES", -1)
    project = tmp_path / "proj"
    project.mkdir()
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="PARSE_CACHE_MAX_FILES must be 0 or more"):
        await pipeline.process_all_files(str(project), str(out), None, max_workers=1)
    assert not out.exists()


@pytest.mark.parametrize("max_workers", [0, -1])
async def test_generate_all_docs_refuses_a_worker_count_below_one(tmp_path, max_workers):
    with pytest.raises(ValueError, match="MAX_WORKERS must be 1 or more"):
        await doc_creator.generate_all_docs(str(tmp_path), [], None, max_workers=max_workers)


@pytest.mark.parametrize("template_text, message", [
    (None, "cannot be read"),
    ("{not json", "cannot be read"),
    ("[]", "is not a JSON object"),
    ('{"sections": []}', 'has no "sections" list'),
    ('{"sections": [{"id": "a", "title": "A"}]}', 'section (number 1) without a "prompt" string'),
    ('{"sections": [{"id": "a", "title": "A", "prompt": "p"}, {"id": "a", "title": "B", "prompt": "p"}]}',
     'two sections with the "id" a'),
    ('{"sections": [{"id": "a", "title": "A", "prompt": "p"}], "summary_prompt": 1}',
     '"summary_prompt" that is not a string'),
])
async def test_an_unusable_template_stops_the_run_before_anything_is_analysed(
        tmp_path, monkeypatch, template_text, message):
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", True)
    template_path = tmp_path / "template.json"
    if template_text is not None:
        template_path.write_text(template_text)
    monkeypatch.setattr(doc_creator, "DOC_TEMPLATE_PATH", str(template_path))
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text("def f():\n    return 1\n")
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="DOC_TEMPLATE_PATH") as e:
        await pipeline.process_all_files(str(project), str(out), None, max_workers=1)
    assert message in str(e.value)
    assert not out.exists()


async def test_no_llm_client_stops_the_run_before_anything_is_analysed(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", True)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text("def f():\n    return 1\n")
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="no LLM client") as e:
        await pipeline.process_all_files(str(project), str(out), None, max_workers=1)
    assert "ENABLE_LLM_DOC=False" in str(e.value)
    assert not out.exists()


async def test_no_llm_client_is_needed_without_design_documents(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", False)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text("def f():\n    return 1\n")
    out = tmp_path / "out"
    run_result = await pipeline.process_all_files(str(project), str(out), None, max_workers=1)
    assert run_result["file_count"] == 1
