"""A file analyzed without a language keeps no design document of an earlier run."""

import json
import os

from codetwine import pipeline
from codetwine.extractors import dependency_graph
from codetwine.parsers.ts_parser import parse_cache_dict
from codetwine.utils.file_utils import compute_file_hash, rel_to_copy_path


async def test_document_of_a_file_that_lost_its_language_is_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", False)
    monkeypatch.setattr(dependency_graph, "EXCLUDE_PATTERNS", [".git"])
    project = tmp_path / "proj"
    project.mkdir()
    (project / "doc.Rmd").write_text("# Title\n\n```{r}\nx <- 1\n```\n")
    (project / "keep.R").write_text("y <- 1\n")
    out = tmp_path / "out"
    parse_cache_dict.clear()

    await pipeline.process_all_files(str(project), str(out), None, max_workers=1)
    doc_dir = out / "proj" / os.path.dirname(rel_to_copy_path("doc.Rmd"))
    keep_dir = out / "proj" / os.path.dirname(rel_to_copy_path("keep.R"))
    for output_dir, file_rel in ((doc_dir, "doc.Rmd"), (keep_dir, "keep.R")):
        source_hash = compute_file_hash(str(project / file_rel))
        (output_dir / "doc.json").write_text(json.dumps({"summary": "old", "source_hash": source_hash}))
        (output_dir / "doc.md").write_text("old\n")

    (project / "doc.Rmd").write_text("# Title\n\nNo chunk any more.\n")
    await pipeline.process_all_files(str(project), str(out), None, max_workers=1)

    assert not (doc_dir / "doc.json").exists()
    assert not (doc_dir / "doc.md").exists()
    assert (keep_dir / "doc.json").exists()
    knowledge = json.loads((out / "proj" / "project_knowledge.json").read_text())
    summary_dict = {entry["file"]: entry["summary"] for entry in knowledge["project_dependencies"]}
    assert summary_dict["proj/" + rel_to_copy_path("doc.Rmd")] is None
    assert summary_dict["proj/" + rel_to_copy_path("keep.R")] == "old"


async def test_a_file_named_like_an_output_file_is_not_copied(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", False)
    monkeypatch.setattr(dependency_graph, "EXCLUDE_PATTERNS", [".git"])
    project = tmp_path / "proj"
    (project / "data").mkdir(parents=True)
    (project / "data" / "file_dependencies.json").write_text('{"file": "elsewhere"}\n')
    (project / "data" / "doc.json").write_text('{"summary": "of the project"}\n')
    (project / "data" / "doc.md").write_text("of the project\n")
    (project / "keep.R").write_text("y <- 1\n")
    out = tmp_path / "out"
    parse_cache_dict.clear()

    run_result = await pipeline.process_all_files(str(project), str(out), None, max_workers=1)

    assert run_result["dependency_fail_list"] == []
    for name in ("file_dependencies.json", "doc.json", "doc.md"):
        output_dir = out / "proj" / os.path.dirname(rel_to_copy_path(f"data/{name}"))
        dependency = json.loads((output_dir / "file_dependencies.json").read_text())
        assert dependency["file"] == "proj/" + rel_to_copy_path(f"data/{name}")
        assert dependency["definitions"] == []
        assert sorted(os.listdir(output_dir)) == ["file_dependencies.json"]
    knowledge = json.loads((out / "proj" / "project_knowledge.json").read_text())
    summary_dict = {entry["file"]: entry["summary"] for entry in knowledge["project_dependencies"]}
    assert summary_dict["proj/" + rel_to_copy_path("data/doc.json")] is None
    keep_dir = out / "proj" / os.path.dirname(rel_to_copy_path("keep.R"))
    assert sorted(os.listdir(keep_dir)) == ["file_dependencies.json", "keep.R"]
