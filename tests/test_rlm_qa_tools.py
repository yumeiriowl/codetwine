"""The tools of the RLM QA example read a knowledge file the pipeline writes.

The example's store and tool modules are imported from examples/rlm_qa; the agent module,
which needs dspy, is not.
"""

import asyncio
import importlib
import json
import os
import sys
from pathlib import Path

import pytest

from codetwine import pipeline

EXAMPLE_DIR = Path(__file__).resolve().parent.parent / "examples" / "rlm_qa"

SOURCE_BY_NAME_DICT = {
    "a.py": (
        "def base():\n    return 1\n\n\n"
        "def helper():\n    return base()\n\n\n"
        "class P:\n    def run(self):\n        return helper()\n\n\n"
        "class Q:\n    def run(self):\n        return base()\n"
    ),
    "b.py": "import a\n\n\ndef run():\n    return a.helper()\n\n\na.base()\n",
    "c.py": (
        "from a import helper\n\n\n"
        "def go():\n    return helper()\n\n\n"
        "class K:\n    def m(self):\n        return helper()\n"
    ),
}


@pytest.fixture(scope="module")
def example_module_tuple():
    """Import knowledge_store and qa_tools from the example directory."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(sys, "dont_write_bytecode", True)
        patch.syspath_prepend(str(EXAMPLE_DIR))
        for module_name in ("knowledge_store", "qa_tools"):
            sys.modules.pop(module_name, None)
        knowledge_store = importlib.import_module("knowledge_store")
        qa_tools = importlib.import_module("qa_tools")
    yield knowledge_store, qa_tools
    for module_name in ("knowledge_store", "qa_tools"):
        sys.modules.pop(module_name, None)


@pytest.fixture(scope="module")
def knowledge_dir(tmp_path_factory) -> Path:
    """Analyze the sources and return the output directory holding both knowledge files."""
    root = tmp_path_factory.mktemp("rlm_qa")
    project = root / "proj"
    project.mkdir()
    for name, source in SOURCE_BY_NAME_DICT.items():
        (project / name).write_text(source)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(pipeline, "ENABLE_LLM_DOC", False)
        patch.setattr(pipeline, "KNOWLEDGE_FORMAT", "both")
        asyncio.run(pipeline.process_all_files(str(project), str(root / "out"), None, max_workers=1))
    return root / "out" / "proj"


@pytest.fixture(params=["json", "sqlite"])
def qa_tools(request, example_module_tuple, knowledge_dir):
    """Return qa_tools with its store opened on one of the two knowledge files."""
    knowledge_store, qa_tools = example_module_tuple
    qa_tools.store = knowledge_store.open_store(str(knowledge_dir / f"project_knowledge.{request.param}"))
    yield qa_tools
    qa_tools.store.close()
    qa_tools.store = None


def _node_set(result: dict, via: str) -> set[tuple[str, str]]:
    """Return the (file name, definition name) pairs of the nodes reached in one direction."""
    return {(os.path.basename(node["file"]), node["name"]) for node in result["nodes"] if node["via"] == via}


def test_a_call_written_through_a_module_leads_to_the_definition(qa_tools):
    incoming = qa_tools.graph_search("helper", 1, "incoming")
    assert ("b.py", "run") in _node_set(incoming, "incoming")
    assert ("c.py", "go") in _node_set(incoming, "incoming")

    outgoing = qa_tools.graph_search("go", 1, "outgoing")
    assert [(os.path.basename(node["file"]), node["name"], node["type"]) for node in outgoing["nodes"]] == [
        ("a.py", "helper", "function_definition"),
    ]


def test_a_call_within_the_file_is_an_edge(qa_tools):
    outgoing = qa_tools.graph_search("go", 2, "outgoing")
    assert _node_set(outgoing, "outgoing") == {("a.py", "helper"), ("a.py", "base")}

    incoming = qa_tools.graph_search("base", 1, "incoming")
    assert _node_set(incoming, "incoming") == {("a.py", "helper"), ("a.py", "run"), ("b.py", "__module__")}


def test_a_usage_belongs_to_the_innermost_definition_of_each_line(qa_tools):
    incoming = qa_tools.graph_search("helper", 1, "incoming")
    assert ("c.py", "m") in _node_set(incoming, "incoming")
    assert ("c.py", "K") not in _node_set(incoming, "incoming")

    method = qa_tools.graph_search("m", 1, "outgoing")
    edge_list = [(edge["source"], edge["target"]) for edge in method["edges"]]
    assert edge_list == [(method["start"], method["nodes"][0]["key"])]
    assert (method["start"], method["nodes"][0]["key"]) in [
        (edge["source"], edge["target"]) for edge in incoming["edges"]
    ]


def test_definitions_of_one_name_in_one_file_are_separate_nodes(qa_tools):
    start_line_list = sorted(
        definition["start_line"] for definition in qa_tools.store.find_definitions("run")
        if os.path.basename(definition["file"]) == "a.py"
    )

    def run_line_list(result: dict) -> list[int]:
        """Return the start lines of the a.py "run" nodes of a result."""
        return [
            node["start_line"] for node in result["nodes"]
            if node["name"] == "run" and os.path.basename(node["file"]) == "a.py"
        ]

    assert run_line_list(qa_tools.graph_search("helper", 1, "incoming")) == [start_line_list[0]]
    assert run_line_list(qa_tools.graph_search("base", 1, "incoming")) == [start_line_list[1]]

    incoming = qa_tools.graph_search("helper", 1, "incoming")
    assert len({node["key"] for node in incoming["nodes"]}) == len(incoming["nodes"])


def test_an_edge_is_listed_once(qa_tools):
    result = qa_tools.graph_search("helper", 2, "both")
    edge_list = [(edge["source"], edge["target"]) for edge in result["edges"]]
    assert len(edge_list) == len(set(edge_list))


def test_read_source_file_reads_only_the_output_directory(qa_tools, knowledge_dir):
    entry = next(iter(qa_tools.store.iter_entries()))
    assert qa_tools.read_source_file(entry["file"]) == (
        knowledge_dir.parent / entry["file"]
    ).read_text()

    outside = knowledge_dir.parent.parent / "proj" / "a.py"
    assert qa_tools.read_source_file(str(outside)).startswith("Error")
    assert qa_tools.read_source_file(os.path.relpath(outside, knowledge_dir)).startswith("Error")


def test_search_text_with_no_room_returns_nothing(qa_tools):
    assert qa_tools.search_text("helper", 1) != []
    assert qa_tools.search_text("helper", 0) == []
    assert qa_tools.search_text("helper", -1) == []


def test_both_stores_find_the_same_definitions_in_the_same_order(example_module_tuple, knowledge_dir):
    knowledge_store, _ = example_module_tuple
    json_store = knowledge_store.open_store(str(knowledge_dir / "project_knowledge.json"))
    sqlite_store = knowledge_store.open_store(str(knowledge_dir / "project_knowledge.sqlite"))
    for name, partial in (("run", False), ("helper", False), ("E", True), ("R", True), ("a", True)):
        assert json_store.find_definitions(name, partial) == sqlite_store.find_definitions(name, partial)
    sqlite_store.close()


def test_json_store_orders_by_file_and_line_and_folds_case(example_module_tuple, tmp_path):
    knowledge_store, _ = example_module_tuple

    def entry(file: str, name_line_list: list[tuple[str, int]]) -> dict:
        definition_list = [
            {"name": name, "type": "function_definition", "start_line": line, "end_line": line}
            for name, line in name_line_list
        ]
        return {"file": file, "file_dependencies": {"definitions": definition_list}}

    knowledge_path = tmp_path / "project_knowledge.json"
    knowledge_path.write_text(json.dumps({"project_name": "p", "files": [
        entry("p/z_py/z.py", [("run", 1)]),
        entry("p/a_py/a.py", [("run", 9), ("Straße", 2), ("run", 4)]),
    ]}))
    store = knowledge_store.open_store(str(knowledge_path))

    assert [(d["file"], d["start_line"]) for d in store.find_definitions("run")] == [
        ("p/a_py/a.py", 4), ("p/a_py/a.py", 9), ("p/z_py/z.py", 1),
    ]
    assert [d["name"] for d in store.find_definitions("STRASS", partial=True)] == ["Straße"]
