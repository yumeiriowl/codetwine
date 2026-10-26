"""Every non-empty text file is analysed; only files with a language get dependencies and docs.

build_project_dependencies() lists every text file that passes EXCLUDE_PATTERNS, with
empty callers and callees for a file whose extension has no tree-sitter language. The
pipeline writes such a file's copy and an empty file_dependencies.json, and leaves it
out of design document generation. A file whose path is not valid UTF-8 and the files
of an output directory inside the project are not listed. A stub LLM client stands in
for the real API.
"""

import json
import os
import threading

import pytest

import codetwine.extractors.dependency_graph as dependency_graph
from codetwine import doc_creator, pipeline
from codetwine.extractors.dependency_graph import build_project_dependencies
from codetwine.utils.file_utils import (
    add_file_name,
    copy_path_to_rel,
    is_text_file,
    resolve_file_output_dir,
)


class StubLLMClient:
    """Stand-in for LLMClient that answers every prompt with fixed text."""

    def __init__(self) -> None:
        self.call_count = 0

    async def generate(self, prompt: str, max_tokens: int | None = None) -> str:
        self.call_count += 1
        return "OK"


def _write_template(tmp_path) -> str:
    """Write a one-section template and return its path."""
    template_path = tmp_path / "template.json"
    template_path.write_text(json.dumps({
        "sections": [{"id": "overview", "title": "Overview", "prompt": "Describe it."}],
        "summary_prompt": "Summarize it.",
    }))
    return str(template_path)


def _make_project(tmp_path) -> str:
    """Create a project mixing language files, other text files, and files to skip."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text("import b\n\nb.f()\n")
    (project / "b.py").write_text("def f():\n    return 1\n")
    (project / "config.yaml").write_text("port: 8080\n")
    (project / "Makefile").write_text("all:\n\techo hi\n")
    (project / ".gitignore").write_text("*.log\n")
    (project / "empty.txt").write_text("  \n")
    (project / "blob.bin").write_bytes(b"\x89PNG\x00\x00binary")
    (project / "node_modules").mkdir()
    (project / "node_modules" / "x.js").write_text("module.exports = 1;\n")
    return str(project)


def _by_file_dict(dep_list: list[dict]) -> dict[str, dict]:
    """Key a build_project_dependencies() result by its "file" field."""
    return {dep["file"]: dep for dep in dep_list}


def test_is_text_file_rejects_empty_binary_and_missing(tmp_path):
    text_path = tmp_path / "t.txt"
    text_path.write_text("x\n")
    empty_path = tmp_path / "e.txt"
    empty_path.write_text(" \n")
    binary_path = tmp_path / "b.bin"
    binary_path.write_bytes(b"abc\x00def")

    assert is_text_file(str(text_path))
    assert not is_text_file(str(empty_path))
    assert not is_text_file(str(binary_path))
    assert not is_text_file(str(tmp_path / "missing"))


def test_is_text_file_reads_past_leading_blanks_longer_than_its_probe(tmp_path):
    text_path = tmp_path / "t.py"
    text_path.write_text("\n" * 9000 + " " * 9000 + "def f():\n    pass\n")
    blank_path = tmp_path / "e.txt"
    blank_path.write_text("\n" * 20000)
    binary_path = tmp_path / "b.bin"
    binary_path.write_bytes(b"\n" * 9000 + b"abc\x00def")

    assert is_text_file(str(text_path))
    assert not is_text_file(str(blank_path))
    assert not is_text_file(str(binary_path))


class PipeWatch:
    """Watch named pipes from a thread while a block runs.

    Each time a reader waits on one of the pipes, the pipe is recorded in open_path_list
    and opened for writing and closed at once, so the read ends.
    """

    def __init__(self, pipe_path_list: list[str]) -> None:
        self.open_path_list: list[str] = []
        self._pipe_path_list = pipe_path_list
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._watch, daemon=True)

    def _watch(self) -> None:
        """Open each pipe for writing without waiting until the block ends."""
        while not self._stop_event.is_set():
            for pipe_path in self._pipe_path_list:
                try:
                    fd = os.open(pipe_path, os.O_WRONLY | os.O_NONBLOCK)
                except OSError:
                    continue
                os.close(fd)
                self.open_path_list.append(pipe_path)
            self._stop_event.wait(0.01)

    def __enter__(self) -> "PipeWatch":
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._stop_event.set()
        self._thread.join()


need_fifo = pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs named pipes")


@need_fifo
def test_is_text_file_does_not_open_a_path_that_is_not_a_regular_file(tmp_path):
    pipe_path = str(tmp_path / "pipe.py")
    os.mkfifo(pipe_path)
    (tmp_path / "dir.py").mkdir()

    with PipeWatch([pipe_path]) as watch:
        assert not is_text_file(pipe_path)
        assert not is_text_file(str(tmp_path / "dir.py"))

    assert watch.open_path_list == []


@need_fifo
def test_a_named_pipe_is_not_listed_by_a_walk_or_among_the_given_files(tmp_path):
    project = tmp_path / "proj"
    (project / "src").mkdir(parents=True)
    (project / "a.py").write_text("def f():\n    return 1\n")
    pipe_path_list = [str(project / "pipe"), str(project / "src" / "b.py")]
    for pipe_path in pipe_path_list:
        os.mkfifo(pipe_path)

    with PipeWatch(pipe_path_list) as watch:
        walk_list = [dep["file"] for dep in build_project_dependencies(str(project))]
        given_list = [dep["file"] for dep in build_project_dependencies(
            str(project), ["a.py", "pipe", "src/b.py"])]

    assert walk_list == given_list == ["proj/a_py/a.py"]
    assert watch.open_path_list == []


@need_fifo
def test_an_r_package_whose_description_is_a_named_pipe_is_not_read(tmp_path):
    project = tmp_path / "proj"
    (project / "R").mkdir(parents=True)
    (project / "R" / "add.R").write_text("add <- function(x, y) x + y\n")
    (project / "R" / "twice.R").write_text("twice <- function(x) add(x, x)\n")
    pipe_path = str(project / "DESCRIPTION")
    os.mkfifo(pipe_path)

    with PipeWatch([pipe_path]) as watch:
        dep_list = build_project_dependencies(str(project))

    assert sorted(dep["file"] for dep in dep_list) == ["proj/R/add_R/add.R", "proj/R/twice_R/twice.R"]
    assert watch.open_path_list == []


class NoScanList(list):
    """A list that fails when it is searched for an item."""

    def __contains__(self, item: object) -> bool:
        raise AssertionError("the list is searched")


def test_add_file_name_appends_without_searching_the_list():
    name_dict: dict[str, list[str]] = {"EN.JSON": NoScanList(["a/en.json"]), "EN": NoScanList()}

    add_file_name(name_dict, "b/en.json")
    add_file_name(name_dict, "c/En.Json", ["LOCALE", "EN"])

    assert name_dict == {
        "EN.JSON": ["a/en.json", "b/en.json", "c/En.Json"],
        "EN": ["b/en.json", "c/En.Json"],
        "LOCALE": ["c/En.Json"],
    }


def test_every_text_file_is_listed_and_only_language_files_have_edges(tmp_path):
    project = _make_project(tmp_path)

    file_dict = _by_file_dict(build_project_dependencies(project))

    assert set(file_dict) == {
        "proj/a_py/a.py", "proj/b_py/b.py", "proj/config_yaml/config.yaml",
        "proj/Makefile/Makefile", "proj/.gitignore/.gitignore",
    }
    assert file_dict["proj/a_py/a.py"]["callees"] == ["proj/b_py/b.py"]
    assert file_dict["proj/b_py/b.py"]["callers"] == ["proj/a_py/a.py"]
    assert file_dict["proj/config_yaml/config.yaml"] == {
        "file": "proj/config_yaml/config.yaml", "callers": [], "callees": []}
    # The copy path of a file without an extension converts back to its name
    assert copy_path_to_rel("Makefile/Makefile") == "Makefile"


def test_exclude_patterns_apply_to_files_without_a_language(tmp_path, monkeypatch):
    project = _make_project(tmp_path)
    monkeypatch.setattr(dependency_graph, "EXCLUDE_PATTERNS",
                        list(dependency_graph.EXCLUDE_PATTERNS) + ["*.yaml"])

    file_dict = _by_file_dict(build_project_dependencies(project))

    assert "proj/config_yaml/config.yaml" not in file_dict
    assert "proj/a_py/a.py" in file_dict


async def test_an_include_of_a_file_without_a_language_does_not_fail_the_c_file(tmp_path, monkeypatch):
    """The included file is listed, but never becomes a dependency target."""
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", False)
    monkeypatch.setattr(pipeline, "KNOWLEDGE_FORMAT", "json")
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.c").write_text('#include "table.inc"\n\nint main(void) { return TABLE[0]; }\n')
    (project / "table.inc").write_text("static const int TABLE[] = {1, 2};\n")
    out = tmp_path / "out"
    base_output_dir = str(out / "proj")

    await pipeline.process_all_files(str(project), str(out), None, max_workers=1)

    with open(os.path.join(resolve_file_output_dir(base_output_dir, "a.c"),
                           "file_dependencies.json"), encoding="utf-8") as f:
        dep_dict = json.load(f)
    assert dep_dict["definitions"]
    assert all(u["from"] != "proj/table_inc/table.inc" for u in dep_dict["callee_usages"])
    file_dict = _by_file_dict(build_project_dependencies(str(project)))
    assert file_dict["proj/a_c/a.c"]["callees"] == []
    assert "proj/table_inc/table.inc" in file_dict


async def test_a_file_without_a_language_gets_a_copy_and_empty_dependencies(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", False)
    monkeypatch.setattr(pipeline, "KNOWLEDGE_FORMAT", "json")
    project = _make_project(tmp_path)
    out = tmp_path / "out"
    base_output_dir = str(out / "proj")

    await pipeline.process_all_files(project, str(out), None, max_workers=1)

    yaml_dir = resolve_file_output_dir(base_output_dir, "config.yaml")
    assert os.path.isfile(os.path.join(yaml_dir, "config.yaml"))
    with open(os.path.join(yaml_dir, "file_dependencies.json"), encoding="utf-8") as f:
        dep_dict = json.load(f)
    assert dep_dict == {"file": "proj/config_yaml/config.yaml", "language": "",
                    "detected_encoding": None, "definitions": [],
                    "callee_usages": [], "same_file_usages": [], "caller_usages": []}
    assert os.path.isfile(os.path.join(base_output_dir, "Makefile", "Makefile"))

    with open(os.path.join(base_output_dir, "project_knowledge.json"), encoding="utf-8") as f:
        knowledge_dict = json.load(f)
    entry_dict = {entry["file"]: entry for entry in knowledge_dict["files"]}
    assert entry_dict["proj/config_yaml/config.yaml"]["file_dependencies"]["definitions"] == []
    assert "doc" not in entry_dict["proj/config_yaml/config.yaml"]
    assert "proj/empty_txt/empty.txt" not in entry_dict
    assert "proj/blob_bin/blob.bin" not in entry_dict


async def test_design_documents_are_made_only_for_files_with_a_language(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", True)
    monkeypatch.setattr(pipeline, "KNOWLEDGE_FORMAT", "json")
    monkeypatch.setattr(doc_creator, "DOC_TEMPLATE_PATH", _write_template(tmp_path))
    project = _make_project(tmp_path)
    out = tmp_path / "out"
    base_output_dir = str(out / "proj")
    llm_client = StubLLMClient()

    await pipeline.process_all_files(project, str(out), llm_client, max_workers=1)

    assert os.path.isfile(os.path.join(resolve_file_output_dir(base_output_dir, "a.py"), "doc.json"))
    assert not os.path.exists(os.path.join(resolve_file_output_dir(base_output_dir, "config.yaml"), "doc.json"))
    # One section and one summary per language file, nothing for the other files
    assert llm_client.call_count == 4

    with open(os.path.join(base_output_dir, "project_dependency_summary.json"), encoding="utf-8") as f:
        summary_dict = {entry["file"]: entry["summary"] for entry in json.load(f)["files"]}
    assert summary_dict["proj/a_py/a.py"] == "OK"
    assert summary_dict["proj/config_yaml/config.yaml"] is None


def test_symbolic_links_are_not_listed_by_a_walk(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text("def f():\n    return 1\n")
    (tmp_path / "outside.py").write_text("API_TOKEN = 'xyz'\n")
    (tmp_path / "outside_dir").mkdir()
    (tmp_path / "outside_dir" / "c.py").write_text("def c():\n    return 1\n")
    os.symlink(tmp_path / "outside.py", project / "linked_out.py")
    os.symlink(project / "a.py", project / "linked_in.py")
    os.symlink(tmp_path / "outside_dir", project / "linked_dir")

    file_list = sorted(dep["file"] for dep in build_project_dependencies(str(project)))

    assert file_list == ["proj/a_py/a.py"]


def test_symbolic_links_among_the_given_files_are_left_out(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text("def f():\n    return 1\n")
    (tmp_path / "outside.py").write_text("API_TOKEN = 'xyz'\n")
    (tmp_path / "outside_dir").mkdir()
    (tmp_path / "outside_dir" / "c.py").write_text("def c():\n    return 1\n")
    os.symlink(tmp_path / "outside.py", project / "linked_out.py")
    os.symlink(project / "a.py", project / "linked_in.py")
    os.symlink(tmp_path / "outside_dir", project / "linked_dir")

    dep_list = build_project_dependencies(
        str(project), ["a.py", "linked_out.py", "linked_in.py", "linked_dir/c.py"])

    assert sorted(dep["file"] for dep in dep_list) == ["proj/a_py/a.py"]


def test_a_given_file_under_a_directory_linked_inside_the_project_is_left_out(tmp_path):
    project = tmp_path / "proj"
    (project / "src").mkdir(parents=True)
    (project / "src" / "a.py").write_text("def f():\n    return 1\n")
    os.symlink(project / "src", project / "linked_src")

    dep_list = build_project_dependencies(str(project), ["src/a.py", "linked_src/a.py"])

    assert sorted(dep["file"] for dep in dep_list) == ["proj/src/a_py/a.py"]


def test_a_project_reached_through_a_link_keeps_its_files(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text("def f():\n    return 1\n")
    os.symlink(project, tmp_path / "proj_link")

    walk_list = [dep["file"] for dep in build_project_dependencies(str(tmp_path / "proj_link"))]
    given_list = [dep["file"] for dep in build_project_dependencies(
        str(tmp_path / "proj_link"), ["a.py"])]

    assert len(walk_list) == 1 and len(given_list) == 1


async def test_a_file_whose_path_is_not_utf8_is_left_out_with_a_warning(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", False)
    monkeypatch.setattr(pipeline, "KNOWLEDGE_FORMAT", "both")
    project = tmp_path / "proj"
    project.mkdir()
    (project / "main.py").write_text("def f():\n    return 1\n")
    with open(os.path.join(os.fsencode(project), b"caf\xe9.py"), "w") as f:
        f.write("def g():\n    return 1\n")
    out = tmp_path / "out"

    run_result = await pipeline.process_all_files(str(project), str(out), None, max_workers=1)
    assert run_result["file_count"] == 1
    assert run_result["dependency_fail_list"] == []
    with open(out / "proj" / "project_knowledge.json", encoding="utf-8") as f:
        assert [entry["file"] for entry in json.load(f)["files"]] == ["proj/main_py/main.py"]
    assert (out / "proj" / "project_knowledge.sqlite").exists()
    assert any("is not valid UTF-8" in record.getMessage() for record in caplog.records)
    assert build_project_dependencies(str(project), ["caf\udce9.py", "main.py"]) == [
        {"file": "proj/main_py/main.py", "callers": [], "callees": []},
    ]


async def test_an_output_directory_inside_the_project_is_not_analysed(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", False)
    monkeypatch.setattr(pipeline, "KNOWLEDGE_FORMAT", "json")
    monkeypatch.setattr(dependency_graph, "EXCLUDE_PATTERNS", [".git"])
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text("import b\n\nb.f()\n")
    (project / "b.py").write_text("def f():\n    return 1\n")
    output_dir = os.path.join(project, "output")
    for _ in range(2):
        run_result = await pipeline.process_all_files(str(project), output_dir, None, max_workers=1)
        assert run_result["file_count"] == 2

    output_rel = os.path.join("output", "proj", "a_py", "a.py")
    run_result = await pipeline.process_all_files(
        str(project), output_dir, None, max_workers=1, file_list=["a.py", output_rel],
    )
    assert run_result["file_count"] == 1


@pytest.mark.parametrize("form", ["output_through_link", "project_through_link", "cwd_through_link"])
async def test_an_output_directory_inside_the_project_through_a_link_is_not_analysed(
        tmp_path, monkeypatch, form):
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", False)
    monkeypatch.setattr(pipeline, "KNOWLEDGE_FORMAT", "json")
    project = tmp_path / "real" / "proj"
    project.mkdir(parents=True)
    (project / "a.py").write_text("import b\n\nb.f()\n")
    (project / "b.py").write_text("def f():\n    return 1\n")
    (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)
    link_project = tmp_path / "link" / "proj"
    project_arg, output_dir = {
        "output_through_link": (str(project), str(link_project / "output")),
        "project_through_link": (str(link_project), str(project / "output")),
        "cwd_through_link": (".", str(link_project / "output")),
    }[form]
    monkeypatch.chdir(link_project)

    for _ in range(3):
        run_result = await pipeline.process_all_files(project_arg, output_dir, None, max_workers=1)
        assert run_result["file_count"] == 2
    knowledge = json.loads((project / "output" / "proj" / "project_knowledge.json").read_text())
    assert sorted(dep["file"] for dep in knowledge["project_dependencies"]) == [
        "proj/a_py/a.py", "proj/b_py/b.py",
    ]


def test_an_excluded_directory_is_compared_with_the_project_by_location(tmp_path):
    project = tmp_path / "proj"
    (project / "sub").mkdir(parents=True)
    (project / "a.py").write_text("x = 1\n")
    (project / "sub" / "b.py").write_text("x = 1\n")
    (tmp_path / "proj2").mkdir()
    (tmp_path / "proj2" / "c.py").write_text("x = 1\n")
    (tmp_path / "link").symlink_to(project, target_is_directory=True)

    def listed(exclude_dir) -> list[str]:
        dep_list = build_project_dependencies(str(project), None, str(exclude_dir))
        return sorted(dep["file"] for dep in dep_list)

    # A directory beside the project whose name starts with the project's name, and one
    # that does not exist
    assert listed(tmp_path / "proj2") == ["proj/a_py/a.py", "proj/sub/b_py/b.py"]
    assert listed(tmp_path / "out") == ["proj/a_py/a.py", "proj/sub/b_py/b.py"]
    # A directory of the project, named directly, through a link or with ".."
    assert listed(project / "sub") == ["proj/a_py/a.py"]
    assert listed(tmp_path / "link" / "sub") == ["proj/a_py/a.py"]
    assert listed(project / "sub" / ".." / "sub") == ["proj/a_py/a.py"]
    # The project directory and a directory holding it
    assert listed(project) == []
    assert listed(tmp_path) == []


def test_an_output_directory_above_the_parent_of_the_project_leaves_out_nothing(tmp_path):
    project = tmp_path / "gp" / "sub" / "proj"
    project.mkdir(parents=True)
    (project / "a.py").write_text("def f():\n    return 1\n")
    (project / "b.py").write_text("from a import f\n\nf()\n")
    (tmp_path / "link").symlink_to(tmp_path / "gp", target_is_directory=True)
    all_list = ["proj/a_py/a.py", "proj/b_py/b.py"]

    def listed(exclude_dir, file_list=None) -> list[str]:
        dep_list = build_project_dependencies(str(project), file_list, str(exclude_dir))
        return sorted(dep["file"] for dep in dep_list)

    for exclude_dir in (tmp_path / "gp", tmp_path / "link", tmp_path, os.sep):
        assert listed(exclude_dir) == all_list
        assert listed(exclude_dir, ["a.py", "b.py"]) == all_list
    # The parent, also through a link: the outputs would be written to the project itself
    assert listed(project.parent) == []
    assert listed(tmp_path / "link" / "sub") == []
    # A directory whose output location leads into the project through a link
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "proj").symlink_to(project, target_is_directory=True)
    assert listed(tmp_path / "out") == []


@pytest.mark.parametrize(
    "form", ["grandparent", "root_of_tmp", "grandparent_through_link", "beside"],
)
async def test_an_output_directory_outside_the_project_leaves_out_nothing(
        tmp_path, monkeypatch, form):
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", False)
    monkeypatch.setattr(pipeline, "KNOWLEDGE_FORMAT", "json")
    project = tmp_path / "gp" / "sub" / "proj"
    project.mkdir(parents=True)
    (project / "a.py").write_text("def f():\n    return 1\n")
    (project / "b.py").write_text("from a import f\n\nf()\n")
    (tmp_path / "link").symlink_to(tmp_path / "gp", target_is_directory=True)
    output_dir = {
        "grandparent": tmp_path / "gp",
        "root_of_tmp": tmp_path,
        "grandparent_through_link": tmp_path / "link",
        "beside": tmp_path / "gp" / "sub" / "output",
    }[form]

    for file_list in (None, ["a.py", "b.py"]):
        run_result = await pipeline.process_all_files(
            str(project), str(output_dir), None, max_workers=1, file_list=file_list,
        )
        assert run_result["file_count"] == 2
    knowledge = json.loads((output_dir / "proj" / "project_knowledge.json").read_text())
    assert sorted(dep["file"] for dep in knowledge["project_dependencies"]) == [
        "proj/a_py/a.py", "proj/b_py/b.py",
    ]
