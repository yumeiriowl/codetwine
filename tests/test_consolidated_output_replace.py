"""The consolidated outputs are written to a temporary path and moved into place.

When writing stops part way, the project_knowledge.json, project_knowledge.sqlite,
project_dependency_summary.json and dependency_graph.md of the previous run stay as they
were. A run removes the consolidated result of the form KNOWLEDGE_FORMAT does not
select. The database holds the same entries as the JSON, the graph has one node per
file, and the partial definition search ignores the case of any letter. A database is
opened read-only whatever characters its path holds.
"""

import errno
import json
import os
import re
import sqlite3

import pytest

from codetwine import knowledge_db, output, pipeline


def _stop(*args, **kwargs):
    raise RuntimeError("stopped part way")


async def _first_run(tmp_path, monkeypatch, knowledge_format: str) -> tuple[str, str]:
    """Run the pipeline once and return (project directory, output directory)."""
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", False)
    monkeypatch.setattr(pipeline, "KNOWLEDGE_FORMAT", knowledge_format)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text("def f():\n    return 1\n")
    out = tmp_path / "out"
    await pipeline.process_all_files(str(project), str(out), None, max_workers=1)
    return str(project), str(out)


async def test_the_previous_database_stays_when_writing_stops(tmp_path, monkeypatch):
    project, out = await _first_run(tmp_path, monkeypatch, "sqlite")
    db_path = tmp_path / "out" / "proj" / "project_knowledge.sqlite"

    monkeypatch.setattr(knowledge_db, "build_file_entry", _stop)
    with pytest.raises(RuntimeError):
        await pipeline.process_all_files(project, out, None, max_workers=1)

    connection = sqlite3.connect(db_path)
    assert connection.execute("SELECT COUNT(*) FROM meta").fetchone()[0] == 3
    assert [row[0] for row in connection.execute("SELECT name FROM definitions")] == ["f"]
    connection.close()

    # The next run replaces the database and leaves no temporary file
    monkeypatch.undo()
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", False)
    monkeypatch.setattr(pipeline, "KNOWLEDGE_FORMAT", "sqlite")
    await pipeline.process_all_files(project, out, None, max_workers=1)
    assert not (tmp_path / "out" / "proj" / "project_knowledge.sqlite.tmp").exists()


async def test_the_previous_json_stays_when_writing_stops(tmp_path, monkeypatch):
    project, out = await _first_run(tmp_path, monkeypatch, "json")
    json_path = tmp_path / "out" / "proj" / "project_knowledge.json"

    monkeypatch.setattr(output, "build_file_entry", _stop)
    with pytest.raises(RuntimeError):
        await pipeline.process_all_files(project, out, None, max_workers=1)

    knowledge = json.loads(json_path.read_text())
    assert [entry["file"] for entry in knowledge["files"]] == ["proj/a_py/a.py"]


@pytest.mark.parametrize("file_name", ["project_dependency_summary.json", "dependency_graph.md"])
async def test_the_previous_summary_and_graph_stay_when_writing_stops(tmp_path, monkeypatch, file_name):
    project, out = await _first_run(tmp_path, monkeypatch, "json")
    output_path = tmp_path / "out" / "proj" / file_name
    first_text = output_path.read_text()
    (tmp_path / "proj" / "b.py").write_text("from a import f\n\nf()\n")

    def open_full_disk(path, *args, **kwargs):
        """Open a file whose writes fail when its name starts with file_name."""
        file_object = open(path, *args, **kwargs)
        if os.path.basename(path).startswith(file_name):
            def write(text):
                raise OSError(errno.ENOSPC, "No space left on device")
            file_object.write = write
        return file_object

    monkeypatch.setattr(output, "open", open_full_disk, raising=False)
    with pytest.raises(OSError):
        await pipeline.process_all_files(project, out, None, max_workers=1)

    assert output_path.read_text() == first_text
    assert not os.path.exists(str(output_path) + ".tmp")


async def test_the_result_of_the_form_not_selected_is_removed(tmp_path, monkeypatch):
    project, out = await _first_run(tmp_path, monkeypatch, "json")
    base = tmp_path / "out" / "proj"
    assert (base / "project_knowledge.json").exists()

    monkeypatch.setattr(pipeline, "KNOWLEDGE_FORMAT", "sqlite")
    await pipeline.process_all_files(project, out, None, max_workers=1)
    assert not (base / "project_knowledge.json").exists()
    assert (base / "project_knowledge.sqlite").exists()

    monkeypatch.setattr(pipeline, "KNOWLEDGE_FORMAT", "json")
    await pipeline.process_all_files(project, out, None, max_workers=1)
    assert (base / "project_knowledge.json").exists()
    assert not (base / "project_knowledge.sqlite").exists()


def test_an_empty_member_is_kept_as_it_is_in_the_database(tmp_path):
    base = tmp_path / "proj"
    file_dir = base / "a_py"
    file_dir.mkdir(parents=True)
    (file_dir / "file_dependencies.json").write_text(json.dumps({"file": "proj/a_py/a.py"}))
    (file_dir / "doc.json").write_text(json.dumps({"file": "a.py"}))
    symbol_dep_dict = {"a.py": {"callers": set(), "callees": set()}}
    summary_dict = {"a.py": None}

    output.save_consolidated_json(str(base), ["a.py"], str(base / "k.json"), symbol_dep_dict, summary_dict)
    knowledge_db.save_consolidated_sqlite(
        str(base), ["a.py"], str(base / "k.sqlite"), symbol_dep_dict, summary_dict,
    )

    json_entry = json.loads((base / "k.json").read_text())["files"][0]
    assert json_entry == {"file": "proj/a_py/a.py", "file_dependencies": {}, "doc": {}}
    connection = knowledge_db.open_knowledge(str(base / "k.sqlite"))
    assert knowledge_db.get_file(connection, "proj/a_py/a.py") == json_entry
    connection.close()


async def test_paths_that_differ_in_separators_only_get_their_own_graph_nodes(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", False)
    monkeypatch.setattr(pipeline, "KNOWLEDGE_FORMAT", "json")
    project = tmp_path / "proj"
    for package in ("x/a_b", "x/a/b", "x/a.b"):
        (project / package).mkdir(parents=True)
        (project / package / "c.py").write_text("def p():\n    return 1\n")
    (project / "main.py").write_text("from x.a_b.c import p\n\np()\n")
    (project / "other.py").write_text("from x.a.b.c import p\n\np()\n")
    (project / 'say "hi".md').write_text("hi\n")

    await pipeline.process_all_files(str(project), str(tmp_path / "out"), None, max_workers=1)

    graph_text = (tmp_path / "out" / "proj" / "dependency_graph.md").read_text()
    node_dict = {label: node_id for node_id, label in re.findall(r'^ {4}(\S+)\["(.*)"\]$', graph_text, re.M)}
    assert len(set(node_dict.values())) == len(node_dict) == 6
    assert 'say #quot;hi#quot;.md' in node_dict
    edge_set = set(re.findall(r"^ {4}(\S+) --> (\S+)$", graph_text, re.M))
    assert edge_set == {
        (node_dict["main.py"], node_dict["x/a_b/c.py"]),
        (node_dict["other.py"], node_dict["x/a/b/c.py"]),
    }


async def test_a_partial_definition_match_ignores_the_case_of_any_letter(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", False)
    monkeypatch.setattr(pipeline, "KNOWLEDGE_FORMAT", "sqlite")
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text(
        "def Überprüfen():\n    return 1\n\ndef ParseFile():\n    return 1\n\n"
        "def read_all():\n    return 1\n\ndef readXall():\n    return 1\n"
    )
    await pipeline.process_all_files(str(project), str(tmp_path / "out"), None, max_workers=1)
    connection = knowledge_db.open_knowledge(str(tmp_path / "out" / "proj" / "project_knowledge.sqlite"))

    def name_list(name: str) -> list[str]:
        return [hit["name"] for hit in knowledge_db.find_definitions(connection, name, partial=True)]

    for name in ("über", "ÜBER", "PRÜF"):
        assert name_list(name) == ["Überprüfen"]
    assert name_list("parse") == ["ParseFile"]
    assert name_list("D_A") == ["read_all"]
    assert name_list("%") == []
    connection.close()


@pytest.mark.parametrize("project_name", ["my#app", "q?x", "pct%41b", "sp ace", "C# lib"])
async def test_a_database_whose_path_has_uri_characters_is_opened_read_only(
        tmp_path, monkeypatch, project_name):
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", False)
    monkeypatch.setattr(pipeline, "KNOWLEDGE_FORMAT", "sqlite")
    project = tmp_path / project_name
    project.mkdir()
    (project / "a.py").write_text("def f():\n    return 1\n")
    out = tmp_path / "out"
    await pipeline.process_all_files(str(project), str(out), None, max_workers=1)

    monkeypatch.chdir(out)
    for db_path in (out / project_name / "project_knowledge.sqlite",
                    os.path.join(project_name, "project_knowledge.sqlite")):
        connection = knowledge_db.open_knowledge(str(db_path))
        assert knowledge_db.get_project_name(connection) == project_name
        assert [entry["file"] for entry in knowledge_db.iter_files(connection)] == [
            f"{project_name}/a_py/a.py",
        ]
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("DELETE FROM files")
        connection.close()
    assert os.listdir(out) == [project_name]
