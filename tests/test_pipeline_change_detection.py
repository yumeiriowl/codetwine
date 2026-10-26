"""Change detection is based on the source each design document was generated from.

doc.json records the SHA256 of its source (source_hash), and _detect_change_file_set()
compares the current source with that. The copy of the source in the output directory
is refreshed by every run, including runs that generate no documents, so it cannot
tell whether a document is current. The consolidated outputs take a design document
only while it is current, and leave out one that cannot be read. A stub LLM client
stands in for the real API.
"""

import json
import os

from codetwine import doc_creator, knowledge_db, pipeline
from codetwine.pipeline import _detect_change_file_set, _source_hash_dict
from codetwine.utils.file_utils import compute_file_hash, resolve_file_output_dir


class StubLLMClient:
    """Stand-in for LLMClient that answers every prompt with fixed text."""

    async def generate(self, prompt: str, max_tokens: int | None = None) -> str:
        return "OK"


def _write_template(tmp_path) -> str:
    """Write a one-section template and return its path."""
    template_path = tmp_path / "template.json"
    template_path.write_text(json.dumps({
        "sections": [{"id": "overview", "title": "Overview", "prompt": "Describe it."}],
        "summary_prompt": "Summarize it.",
    }))
    return str(template_path)


async def _run_without_docs(project: str, out: str) -> None:
    """Run the pipeline with design document generation turned off."""
    await pipeline.process_all_files(project, out, None, max_workers=1)


async def test_a_change_made_between_runs_without_docs_is_still_detected(tmp_path, monkeypatch):
    """The request's scenario: no-docs run, source edited, no-docs run, then detection."""
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", False)
    monkeypatch.setattr(pipeline, "KNOWLEDGE_FORMAT", "json")
    monkeypatch.setattr(doc_creator, "DOC_TEMPLATE_PATH", _write_template(tmp_path))

    project = tmp_path / "proj"
    project.mkdir()
    source = project / "a.py"
    source.write_text("def f():\n    return 1\n")
    out = tmp_path / "out"
    base_output_dir = str(out / "proj")

    # First run creates the copy and file_dependencies.json; then the document is generated
    await _run_without_docs(str(project), str(out))
    await doc_creator.generate_all_docs(
        base_output_dir, [{"file": "a.py", "callers": [], "callees": []}],
        StubLLMClient(), max_workers=1, changed_files={"a.py"},
    )
    assert _detect_change_file_set(["a.py"], _source_hash_dict(["a.py"], str(project)), base_output_dir) == set()

    # The source changes, and a run without documents refreshes the copy
    source.write_text("def f():\n    return 2\n")
    assert _detect_change_file_set(["a.py"], _source_hash_dict(["a.py"], str(project)), base_output_dir) == {"a.py"}
    await _run_without_docs(str(project), str(out))

    # The document is still out of date
    assert _detect_change_file_set(["a.py"], _source_hash_dict(["a.py"], str(project)), base_output_dir) == {"a.py"}


async def test_the_document_records_the_hash_of_its_source(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", False)
    monkeypatch.setattr(pipeline, "KNOWLEDGE_FORMAT", "json")
    monkeypatch.setattr(doc_creator, "DOC_TEMPLATE_PATH", _write_template(tmp_path))

    project = tmp_path / "proj"
    project.mkdir()
    source = project / "a.py"
    source.write_text("def f():\n    return 1\n")
    out = tmp_path / "out"
    base_output_dir = str(out / "proj")

    await _run_without_docs(str(project), str(out))
    await doc_creator.generate_all_docs(
        base_output_dir, [{"file": "a.py", "callers": [], "callees": []}],
        StubLLMClient(), max_workers=1, changed_files={"a.py"},
    )

    doc = doc_creator.load_doc(resolve_file_output_dir(base_output_dir, "a.py"))
    assert doc["source_hash"] == compute_file_hash(str(source))


def test_a_document_without_a_source_hash_counts_as_changed(tmp_path):
    """A document made before source_hash existed is regenerated once."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text("x = 1\n")
    base_output_dir = str(tmp_path / "out" / "proj")
    output_dir = resolve_file_output_dir(base_output_dir, "a.py")
    os.makedirs(output_dir)
    with open(os.path.join(output_dir, "doc.json"), "w", encoding="utf-8") as f:
        json.dump({"file": "a.py", "sections": [], "summary": "s"}, f)

    assert _detect_change_file_set(["a.py"], _source_hash_dict(["a.py"], str(project)), base_output_dir) == {"a.py"}


def test_a_missing_or_unreadable_document_counts_as_changed(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text("x = 1\n")
    base_output_dir = str(tmp_path / "out" / "proj")

    assert _detect_change_file_set(["a.py"], _source_hash_dict(["a.py"], str(project)), base_output_dir) == {"a.py"}

    output_dir = resolve_file_output_dir(base_output_dir, "a.py")
    os.makedirs(output_dir)
    with open(os.path.join(output_dir, "doc.json"), "w", encoding="utf-8") as f:
        f.write("{not json")
    assert _detect_change_file_set(["a.py"], _source_hash_dict(["a.py"], str(project)), base_output_dir) == {"a.py"}


def _knowledge_doc_dict(base_output_dir: str) -> tuple[dict, dict, dict]:
    """Return the summaries and the docs of project_knowledge.json and the docs of the SQLite database.

    Returns:
        ({file: summary}, {file: doc or None} of the JSON, {file: doc or None} of the
        database), each keyed by the output path of the file.
    """
    with open(os.path.join(base_output_dir, "project_knowledge.json"), encoding="utf-8") as f:
        knowledge = json.load(f)
    summary_dict = {dep["file"]: dep["summary"] for dep in knowledge["project_dependencies"]}
    json_doc_dict = {entry["file"]: entry.get("doc") for entry in knowledge["files"]}
    connection = knowledge_db.open_knowledge(os.path.join(base_output_dir, "project_knowledge.sqlite"))
    db_doc_dict = {entry["file"]: entry.get("doc") for entry in knowledge_db.iter_files(connection)}
    connection.close()
    return summary_dict, json_doc_dict, db_doc_dict


async def test_a_document_of_an_older_source_is_left_out_of_the_consolidated_outputs(
        tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "KNOWLEDGE_FORMAT", "both")
    monkeypatch.setattr(doc_creator, "DOC_TEMPLATE_PATH", _write_template(tmp_path))
    project = tmp_path / "proj"
    project.mkdir()
    source = project / "a.py"
    source.write_text("def f():\n    return 1\n")
    out = tmp_path / "out"
    base_output_dir = str(out / "proj")
    a_path = "proj/a_py/a.py"

    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", True)
    await pipeline.process_all_files(str(project), str(out), StubLLMClient(), max_workers=1)
    summary_dict, json_doc_dict, db_doc_dict = _knowledge_doc_dict(base_output_dir)
    assert summary_dict[a_path] == "OK"
    assert json_doc_dict[a_path] is not None and db_doc_dict[a_path] == json_doc_dict[a_path]

    # The source changes and the next run makes no design documents
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", False)
    source.write_text("def renamed():\n    return 2\n")
    await _run_without_docs(str(project), str(out))
    assert _knowledge_doc_dict(base_output_dir) == ({a_path: None}, {a_path: None}, {a_path: None})
    assert os.path.isfile(os.path.join(resolve_file_output_dir(base_output_dir, "a.py"), "doc.json"))

    # The source the document was made from is back
    source.write_text("def f():\n    return 1\n")
    await _run_without_docs(str(project), str(out))
    summary_dict, json_doc_dict, db_doc_dict = _knowledge_doc_dict(base_output_dir)
    assert summary_dict[a_path] == "OK"
    assert json_doc_dict[a_path] is not None and db_doc_dict[a_path] == json_doc_dict[a_path]


async def test_an_unreadable_document_is_left_out_of_the_consolidated_outputs(
        tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", False)
    monkeypatch.setattr(pipeline, "KNOWLEDGE_FORMAT", "both")
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text("def f():\n    return 1\n")
    out = tmp_path / "out"
    base_output_dir = str(out / "proj")
    await _run_without_docs(str(project), str(out))
    with open(os.path.join(resolve_file_output_dir(base_output_dir, "a.py"), "doc.json"), "w",
              encoding="utf-8") as f:
        f.write('{"file": "a.py", "su')

    await _run_without_docs(str(project), str(out))

    assert _knowledge_doc_dict(base_output_dir) == (
        {"proj/a_py/a.py": None}, {"proj/a_py/a.py": None}, {"proj/a_py/a.py": None},
    )
    assert any("a.py: doc.json cannot be read" in record.getMessage() for record in caplog.records)


class TitleStub:
    """Answers every prompt with fixed text and records the prompts."""

    def __init__(self) -> None:
        self.prompt_list: list[str] = []

    async def generate(self, prompt: str, max_tokens: int | None = None) -> str:
        self.prompt_list.append(prompt)
        return "OK"


async def test_a_document_of_another_form_is_regenerated(tmp_path, monkeypatch):
    """doc.json holding no object, or no sections, is regenerated like a missing one."""
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", True)
    monkeypatch.setattr(pipeline, "KNOWLEDGE_FORMAT", "json")
    monkeypatch.setattr(doc_creator, "DOC_TEMPLATE_PATH", _write_template(tmp_path))
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text("x = 1\n")
    (project / "b.py").write_text("y = 1\n")
    out = tmp_path / "out"
    base_output_dir = str(out / "proj")
    await pipeline.process_all_files(str(project), str(out), StubLLMClient(), max_workers=1)

    a_dir = resolve_file_output_dir(base_output_dir, "a.py")
    b_dir = resolve_file_output_dir(base_output_dir, "b.py")
    with open(os.path.join(a_dir, "doc.json"), "w", encoding="utf-8") as f:
        json.dump(["x"], f)
    with open(os.path.join(b_dir, "doc.json"), "w", encoding="utf-8") as f:
        json.dump({"file": "b.py", "source_hash": compute_file_hash(str(project / "b.py"))}, f)
    later = os.path.getmtime(os.path.join(b_dir, "doc.json")) + 10
    os.utime(os.path.join(b_dir, "doc.md"), (later, later))
    stub = TitleStub()

    run_result = await pipeline.process_all_files(str(project), str(out), stub, max_workers=1)

    assert run_result["doc_fail_list"] == []
    assert any("# Target File: a.py" in prompt for prompt in stub.prompt_list)
    assert any("# Target File: b.py" in prompt for prompt in stub.prompt_list)
    for out_dir in (a_dir, b_dir):
        assert [section["id"] for section in _section_list_of(out_dir)] == ["overview"]


def _section_list_of(out_dir: str) -> list[dict]:
    """Return the sections of the doc.json of an output directory."""
    with open(os.path.join(out_dir, "doc.json"), encoding="utf-8") as f:
        return json.load(f)["sections"]
