"""Unit tests for the context-overflow fallback in doc_creator.

Covers the cumulative reduction stages, large-definition splicing (including
nesting), piece-by-piece summaries, the per-symbol summary cache, the summary of
the document, and caller-usage reduction. A stub LLM client stands in for the real
API so no network/model access is required.
"""

import json
import os
import re

import pytest

from codetwine import doc_creator as dc
from codetwine import pipeline
from codetwine.llm import ContextWindowExceededError
from codetwine.utils.file_utils import resolve_file_output_dir


# Prefix that identifies a code-summary prompt (_CODE_SUMMARY_PROMPT, _CODE_PIECE_SUMMARY_PROMPT)
_SUMMARY_PROMPT_PREFIX = "Summarize the behavior of the following"

_SECTION = {"id": "overview", "title": "Overview", "prompt": "Describe it."}


class StubLLMClient:
    """Stand-in for LLMClient that records calls and simulates context overflow.

    A section prompt longer than ``max_prompt_chars`` and a code-summary prompt longer
    than ``max_summary_chars`` raise ContextWindowExceededError; shorter ones return
    "OK" / a short summary. The calls whose index is in ``fail_index_set`` return None.
    """

    def __init__(
        self, max_prompt_chars: int = 10**9, max_summary_chars: int = 10**9,
        fail_index_set: frozenset[int] = frozenset(),
    ) -> None:
        self.max_prompt_chars = max_prompt_chars
        self.max_summary_chars = max_summary_chars
        self.fail_index_set = fail_index_set
        self.call_count = 0
        self.section_calls: list[str] = []
        self.summary_calls: list[str] = []

    async def generate(self, prompt: str, max_tokens: int | None = None) -> str | None:
        """Return "OK"/summary text, None for a failing call, or raise overflow."""
        index = self.call_count
        self.call_count += 1
        is_summary = prompt.startswith(_SUMMARY_PROMPT_PREFIX)
        (self.summary_calls if is_summary else self.section_calls).append(prompt)
        if index in self.fail_index_set:
            return None
        if len(prompt) > (self.max_summary_chars if is_summary else self.max_prompt_chars):
            raise ContextWindowExceededError(
                message="context window exceeded", model="stub", llm_provider="stub"
            )
        return "SUMMARY: does things" if is_summary else "OK"


def _reducer(
    stub, source_code: str, file_deps: dict, callee_context: str = "",
    implementation_context: str = "", implementation_definition_list: list[dict] | None = None,
    cache: dict | None = None,
) -> dc._PromptReducer:
    return dc._PromptReducer(
        dc._SectionInput(source_code, file_deps, callee_context, implementation_context),
        stub, {} if cache is None else cache, implementation_definition_list,
    )


def _function(name: str, line_count: int, indent: str = "") -> str:
    """Return the source of a function of line_count + 1 lines."""
    return f"{indent}def {name}():\n" + "\n".join(
        f"{indent}    variable_number_{i} = compute_something_long({i}) + another_value_{i}"
        for i in range(line_count)
    )


def _definition(name: str, source_code: str, text: str) -> dict:
    """Return the definitions[] entry of a text written once in a source."""
    start_line = source_code[:source_code.index(text)].count("\n") + 1
    return {
        "name": name, "type": "function_definition", "start_line": start_line,
        "end_line": start_line + text.count("\n"), "context": text,
    }


def _make_big_function() -> tuple[str, dict]:
    """Build a source string with one large function and return (source, definition)."""
    func_src = _function("big_func", 30)
    source_code = "import os\n\n" + func_src + "\n\nCONST = 1\n"
    return source_code, _definition("big_func", source_code, func_src)


async def test_fallback_stages_order(monkeypatch):
    """Prompt reduces through the stages that change it and succeeds after source splicing."""
    monkeypatch.setattr(dc, "ENABLE_CODE_SUMMARY", True)
    monkeypatch.setattr(dc, "CODE_SUMMARY_TRIGGER_LINES", 5)

    source_code, big_def = _make_big_function()
    big_callee = "\n".join(
        f"    dependency_line_{i} = do_work_with_a_long_name({i})" for i in range(20)
    )
    file_deps = {
        "file": "proj/mod_py/mod.py",
        "definitions": [big_def],
        "callee_usages": [
            {
                "name": "compute_something_long",
                "from": "proj/util_py/util.py",
                "lines": [10],
                "target_context": big_callee,
            }
        ],
        "caller_usages": [
            {
                "name": "mod",
                "file": "proj/other_py/other.py",
                "lines": [3],
                "usage_context": "x" * 200,
            }
        ],
    }

    # Full source alone exceeds this, but the spliced source fits.
    stub = StubLLMClient(max_prompt_chars=1500)
    reducer = _reducer(stub, source_code, file_deps)

    result = await dc._generate_section(_SECTION, reducer, "proj/mod_py/mod.py", stub)

    assert result == "OK"
    assert reducer.stage_label == "summarize source defs"
    # full, drop caller bodies, summarize callee usages, drop callee bodies, drop usage
    # lists, summarize source defs; the stage without a dependency doc summary to drop
    # is passed over
    assert len(stub.section_calls) == 6
    # One summary for the large callee symbol, one for the source definition
    assert len(stub.summary_calls) == 2
    assert "# [summarized] big_func" in stub.section_calls[-1]
    assert "CONST = 1" in stub.section_calls[-1]


async def test_fallback_disabled_stops_after_dropping_context(monkeypatch):
    """With ENABLE_CODE_SUMMARY False, only the no-LLM reduction stages run."""
    monkeypatch.setattr(dc, "ENABLE_CODE_SUMMARY", False)
    monkeypatch.setattr(dc, "CODE_SUMMARY_TRIGGER_LINES", 5)

    source_code, big_def = _make_big_function()
    file_deps = {
        "file": "proj/mod_py/mod.py",
        "definitions": [big_def],
        "callee_usages": [
            {"name": "f", "from": "proj/util_py/util.py", "lines": [1], "target_context": "z" * 50}
        ],
        "caller_usages": [
            {"name": "mod", "file": "proj/other_py/other.py", "lines": [3], "usage_context": "y" * 50}
        ],
    }

    # Even the smallest prompt still exceeds this, so all stages fail.
    stub = StubLLMClient(max_prompt_chars=100)
    reducer = _reducer(stub, source_code, file_deps, callee_context="- **util.py**: helpers")

    result = await dc._generate_section(_SECTION, reducer, "proj/mod_py/mod.py", stub)

    assert result is None
    # full, drop caller bodies, drop callee context, drop callee bodies, drop usage
    # lists; no summarization
    assert len(stub.section_calls) == 5
    assert len(stub.summary_calls) == 0
    assert reducer.stage_label == "drop usage lists"
    assert reducer.section_input.file_dep_dict["callee_usages"] == []
    assert "def big_func():" in stub.section_calls[-1]


async def test_a_failed_call_does_not_reduce_the_prompt(monkeypatch):
    """A call that fails for another reason than the context window ends the section."""
    monkeypatch.setattr(dc, "ENABLE_CODE_SUMMARY", True)
    source_code, big_def = _make_big_function()
    file_deps = {
        "file": "proj/mod_py/mod.py", "definitions": [big_def], "callee_usages": [],
        "caller_usages": [
            {"name": "mod", "file": "proj/other_py/other.py", "lines": [3], "usage_context": "y" * 50}
        ],
    }
    stub = StubLLMClient(fail_index_set=frozenset({0}))
    reducer = _reducer(stub, source_code, file_deps)

    assert await dc._generate_section(_SECTION, reducer, "proj/mod_py/mod.py", stub) is None
    assert stub.call_count == 1
    assert reducer.stage_label == "full"
    # The next section is generated from the full input
    assert await dc._generate_section(_SECTION, reducer, "proj/mod_py/mod.py", stub) == "OK"
    assert "usage_context" in reducer.section_input.file_dep_dict["caller_usages"][0]


async def test_later_sections_start_from_the_stage_that_fit(monkeypatch):
    monkeypatch.setattr(dc, "ENABLE_CODE_SUMMARY", True)
    monkeypatch.setattr(dc, "CODE_SUMMARY_TRIGGER_LINES", 5)
    source_code, big_def = _make_big_function()
    file_deps = {"file": "proj/mod_py/mod.py", "definitions": [big_def],
                 "callee_usages": [], "caller_usages": []}
    stub = StubLLMClient(max_prompt_chars=1000)
    reducer = _reducer(stub, source_code, file_deps)

    for _ in range(3):
        assert await dc._generate_section(_SECTION, reducer, "proj/mod_py/mod.py", stub) == "OK"

    # One overflow and one success for the first section, one call for each later one
    assert len(stub.section_calls) == 4
    assert len(stub.summary_calls) == 1


async def test_splice_large_definitions_nesting(monkeypatch):
    """A large method is summarized; the lines of the class around it are kept."""
    monkeypatch.setattr(dc, "CODE_SUMMARY_TRIGGER_LINES", 5)

    method_src = _function("do", 15, indent="    ")
    small_src = "    def small(self):\n        return 2"
    class_src = "class Big:\n    limit = 3\n\n" + method_src + "\n\n" + small_src
    helper_src = "def helper():\n    return 3"
    source_code = "import x\n\n" + class_src + "\n\n" + helper_src

    definitions = [
        _definition("Big", source_code, class_src),
        _definition("do", source_code, method_src),
        _definition("small", source_code, small_src),
        _definition("helper", source_code, helper_src),
    ]

    stub = StubLLMClient()
    result, start_index_set = await dc._splice_large_definition(source_code, definitions, stub, {})

    # The method is replaced; the class is not replaced as a whole
    assert result.count("# [summarized] do") == 1
    assert "# [summarized] Big" not in result
    assert "variable_number_3" not in result
    # The class head, its small members and the other lines are preserved verbatim
    for line in ("import x", "class Big:", "    limit = 3", "    def small(self):",
                 "def helper():", "    return 3"):
        assert line in result.split("\n")
    assert len(stub.summary_calls) == 1
    # The lines the definitions start on, in the text returned
    result_line_list = result.split("\n")
    assert sorted(result_line_list[index] for index in start_index_set) == [
        "    def small(self):", "# [summarized] do", "class Big:", "def helper():",
    ]


def test_definitions_that_share_a_line_are_not_summarized_as_one():
    """Only a definition whose lines hold no part of another one is selected."""
    body = "\n".join(f"  step_{i}();" for i in range(20))

    def leaf_name_list(source_code: str, definition_list: list[dict]) -> list[str]:
        return [definition["name"] for definition in dc._large_leaf_definition_list(
            definition_list, source_code.split("\n"), 5,
        )]

    # Several definitions on one long line
    one_line = "function a(){" + "x();" * 200 + "}function b(){" + "y();" * 200 + "}"
    assert leaf_name_list(one_line, [
        {"name": "a", "start_line": 1, "end_line": 1},
        {"name": "b", "start_line": 1, "end_line": 1},
    ]) == []
    # One definition on one long line
    assert leaf_name_list(one_line, [{"name": "a", "start_line": 1, "end_line": 1}]) == ["a"]

    # Two names of the same lines are one definition
    struct_src = "typedef struct Node_ {\n" + body + "\n} Node;"
    assert leaf_name_list(struct_src, [
        {"name": "Node_", "start_line": 1, "end_line": 22},
        {"name": "Node", "start_line": 1, "end_line": 22},
    ]) == ["Node_, Node"]

    # A definition that starts on the last line of the one before it
    chained_src = "void a() {\n" + body + "\n} void b() {\n" + body + "\n}\nvoid c() {\n" + body + "\n}"
    assert leaf_name_list(chained_src, [
        {"name": "a", "start_line": 1, "end_line": 22},
        {"name": "b", "start_line": 22, "end_line": 43},
        {"name": "c", "start_line": 44, "end_line": 65},
    ]) == ["c"]

    # A member that starts on the first line of its class, and one that ends on its last line
    class_src = "class A { void f() {\n" + body + "\n}\nvoid g() {\n" + body + "\n}}"
    assert leaf_name_list(class_src, [
        {"name": "A", "start_line": 1, "end_line": 44},
        {"name": "f", "start_line": 1, "end_line": 22},
        {"name": "g", "start_line": 23, "end_line": 44},
    ]) == ["g"]


async def test_runs_are_cut_at_definitions_after_large_ones_are_summarized(monkeypatch):
    """The runs of a source whose large definitions are summarized start where definitions start."""
    monkeypatch.setattr(dc, "ENABLE_CODE_SUMMARY", True)
    monkeypatch.setattr(dc, "CODE_SUMMARY_TRIGGER_LINES", 20)
    monkeypatch.setattr(dc, "CODE_SUMMARY_PIECE_LINES", 50)

    large_src = _function("large", 60)
    small_list = [_function(f"s{n}", 12) for n in range(20)]
    source_code = "\n".join(small_list[:7] + [large_src] + small_list[7:])
    definitions = [_definition("large", source_code, large_src)] + [
        _definition(f"s{n}", source_code, small_src) for n, small_src in enumerate(small_list)
    ]
    file_deps = {"file": "proj/m_py/m.py", "definitions": definitions,
                 "callee_usages": [], "caller_usages": []}

    stub = StubLLMClient(max_prompt_chars=3000)
    reducer = _reducer(stub, source_code, file_deps)
    assert await dc._generate_section(_SECTION, reducer, "proj/m_py/m.py", stub) == "OK"
    assert reducer.stage_label == "summarize source pieces"
    piece_prompt_list = [prompt for prompt in stub.summary_calls if "piece of source code" in prompt]
    assert len(piece_prompt_list) >= 4
    # Every run starts on the first line of a definition, or on the summary of the large one
    for prompt in piece_prompt_list:
        first_line = prompt.split("```\n")[1].split("\n")[0]
        assert first_line.startswith("def s") or first_line == "# [summarized] large"


async def test_runs_shorter_than_the_trigger_are_summarized(monkeypatch):
    """A run of CODE_SUMMARY_PIECE_LINES lines is summarized whatever CODE_SUMMARY_TRIGGER_LINES is."""
    monkeypatch.setattr(dc, "CODE_SUMMARY_TRIGGER_LINES", 500)
    monkeypatch.setattr(dc, "CODE_SUMMARY_PIECE_LINES", 50)
    source_code = "\n".join(f"statement_number_{i} = compute({i}) + other_value_{i}" for i in range(130))

    stub = StubLLMClient()
    result = await dc._summarize_source_by_piece(source_code, set(), stub, {})
    assert len(stub.summary_calls) == 2
    assert result.count("# [summarized] part") == 2
    # The last run, of 30 lines, is kept
    assert result.endswith("statement_number_129 = compute(129) + other_value_129")


async def test_a_class_of_small_methods_is_summarized_piece_by_piece(monkeypatch):
    """No definition inside is large: the source is cut into runs of lines."""
    monkeypatch.setattr(dc, "ENABLE_CODE_SUMMARY", True)
    monkeypatch.setattr(dc, "CODE_SUMMARY_TRIGGER_LINES", 20)
    monkeypatch.setattr(dc, "CODE_SUMMARY_PIECE_LINES", 50)

    method_list = [_function(f"m{n}", 9, indent="    ") for n in range(30)]
    source_code = "class Big:\n" + "\n".join(method_list)
    definitions = [{"name": "Big", "start_line": 1, "end_line": 301, "context": source_code}] + [
        _definition(f"m{n}", source_code, method_src) for n, method_src in enumerate(method_list)
    ]
    file_deps = {"file": "proj/big_py/big.py", "definitions": definitions,
                 "callee_usages": [], "caller_usages": []}

    stub = StubLLMClient(max_prompt_chars=2000)
    reducer = _reducer(stub, source_code, file_deps)

    assert await dc._generate_section(_SECTION, reducer, "proj/big_py/big.py", stub) == "OK"
    assert reducer.stage_label == "summarize source pieces"
    # Six runs of five methods each, cut where a method starts
    assert len(stub.summary_calls) == 6
    assert reducer.section_input.source_code.count("# [summarized] part") == 6
    assert all("def m" in prompt.split("```")[1].split("\n")[1] or "class Big" in prompt
               for prompt in stub.summary_calls)


async def test_a_source_of_one_long_line_is_summarized(monkeypatch):
    """A line longer than the summary prompt allows is summarized in halves."""
    monkeypatch.setattr(dc, "ENABLE_CODE_SUMMARY", True)
    source_code = "function f(a){" + "a+=1;" * 2000 + "return a}"
    file_deps = {
        "file": "proj/m_js/m.js", "callee_usages": [], "caller_usages": [],
        "definitions": [{"name": "f", "start_line": 1, "end_line": 1, "context": source_code}],
    }
    stub = StubLLMClient(max_prompt_chars=3000, max_summary_chars=4000)
    reducer = _reducer(stub, source_code, file_deps)

    assert await dc._generate_section(_SECTION, reducer, "proj/m_js/m.js", stub) == "OK"
    assert reducer.stage_label == "summarize source defs"
    # 10,023 characters: the whole and the halves overflow, the four quarters fit
    assert len(stub.summary_calls) == 7
    assert reducer.section_input.source_code == "# [summarized] f\n" + "\n".join(
        ["SUMMARY: does things"] * 4
    )


async def test_many_small_dependencies_are_dropped(monkeypatch):
    monkeypatch.setattr(dc, "ENABLE_CODE_SUMMARY", True)
    file_deps = {
        "file": "proj/use_py/use.py", "definitions": [], "caller_usages": [],
        "callee_usages": [
            {"name": f"h{n}", "from": "proj/lib_py/lib.py", "lines": [n],
             "target_context": f"def h{n}(a):\n    return a + {n}"}
            for n in range(100)
        ],
    }
    stub = StubLLMClient(max_prompt_chars=4000)
    reducer = _reducer(stub, "import lib\n", file_deps)

    assert await dc._generate_section(_SECTION, reducer, "proj/use_py/use.py", stub) == "OK"
    assert reducer.stage_label == "drop callee bodies"
    assert len(stub.summary_calls) == 0
    assert "- h99 (from lib.py)" in stub.section_calls[-1]
    assert "return a + 99" not in stub.section_calls[-1]


async def test_implementation_file_of_a_header_is_reduced(monkeypatch):
    monkeypatch.setattr(dc, "ENABLE_CODE_SUMMARY", True)
    monkeypatch.setattr(dc, "CODE_SUMMARY_TRIGGER_LINES", 5)
    impl_function = "int f(void) {\n" + "\n".join(f"  g({i});" for i in range(200)) + "\n  return 0;\n}"
    impl_source = "#include \"a.h\"\n" + impl_function + "\n"
    impl_definitions = [_definition("f", impl_source, impl_function)]
    file_deps = {"file": "proj/a_h/a.h", "definitions": [], "callee_usages": [], "caller_usages": []}

    stub = StubLLMClient(max_prompt_chars=1000)
    reducer = _reducer(stub, "int f(void);\n", file_deps, implementation_context=impl_source,
                       implementation_definition_list=impl_definitions)
    assert await dc._generate_section(_SECTION, reducer, "proj/a_h/a.h", stub) == "OK"
    assert reducer.stage_label == "summarize implementation defs"
    assert "# [summarized] f" in reducer.section_input.implementation_context
    assert "#include \"a.h\"" in reducer.section_input.implementation_context

    # Without code summaries the implementation file is left out
    monkeypatch.setattr(dc, "ENABLE_CODE_SUMMARY", False)
    stub = StubLLMClient(max_prompt_chars=1000)
    reducer = _reducer(stub, "int f(void);\n", file_deps, implementation_context=impl_source,
                       implementation_definition_list=impl_definitions)
    assert await dc._generate_section(_SECTION, reducer, "proj/a_h/a.h", stub) == "OK"
    assert reducer.stage_label == "drop implementation file"
    assert len(stub.summary_calls) == 0


async def test_a_source_summarized_once_is_cut_at_any_line(monkeypatch):
    """After the large definitions are summarized, the runs are cut by their length alone."""
    monkeypatch.setattr(dc, "CODE_SUMMARY_PIECE_LINES", 10)
    line_list = [f"statement_number_{i} = {i}" for i in range(35)]
    assert dc._piece_range_list(line_list, set()) == [(0, 10), (10, 20), (20, 30), (30, 35)]
    assert dc._piece_range_list(line_list, {12, 31}) == [(0, 12), (12, 31), (31, 35)]
    assert dc._piece_range_list(line_list, {34}) == [(0, 20), (20, 34), (34, 35)]


async def test_summary_cache_dedup():
    """Identical code is summarized only once thanks to the shared cache."""
    stub = StubLLMClient()
    cache: dict[str, str] = {}

    first = await dc._summarize_code("some big code block", "f", stub, cache)
    second = await dc._summarize_code("some big code block", "f", stub, cache)

    assert first == second
    assert len(stub.summary_calls) == 1


async def test_summarize_code_fallback_when_it_never_fits():
    """A code block whose summary prompt always overflows keeps its signature line; nothing is cached."""
    cache: dict[str, str] = {}
    stub = StubLLMClient(max_summary_chars=10)
    summary = await dc._summarize_code("def f(a, b):\n    return a + b", "f", stub, cache)

    assert summary.startswith("def f(a, b):")
    assert dc._CODE_SUMMARY_FAILED_NOTE in summary
    assert cache == {}


async def test_a_failed_summary_call_ends_the_section_and_is_tried_again(monkeypatch):
    """A summary call that fails is no reason to drop the body of the definition."""
    monkeypatch.setattr(dc, "ENABLE_CODE_SUMMARY", True)
    monkeypatch.setattr(dc, "CODE_SUMMARY_TRIGGER_LINES", 5)
    first_src, second_src = _function("first", 30), _function("second", 30)
    source_code = first_src + "\n\n" + second_src + "\n"
    file_deps = {
        "file": "proj/mod_py/mod.py", "callee_usages": [], "caller_usages": [],
        "definitions": [_definition("first", source_code, first_src),
                        _definition("second", source_code, second_src)],
    }
    # Calls: section (overflow), summary of first (ok), summary of second (fails)
    stub = StubLLMClient(max_prompt_chars=1000, fail_index_set=frozenset({2}))
    cache: dict[str, str] = {}
    reducer = _reducer(stub, source_code, file_deps, cache=cache)

    assert await dc._generate_section(_SECTION, reducer, "proj/mod_py/mod.py", stub) is None
    assert reducer.stage_label == "full"
    assert len(cache) == 1

    # The next section summarizes only the definition that failed
    assert await dc._generate_section(_SECTION, reducer, "proj/mod_py/mod.py", stub) == "OK"
    assert reducer.stage_label == "summarize source defs"
    assert len(stub.summary_calls) == 3
    assert dc._CODE_SUMMARY_FAILED_NOTE not in reducer.section_input.source_code


async def test_summarize_code_in_halves_when_it_exceeds_the_context(monkeypatch):
    monkeypatch.setattr(dc, "CODE_SUMMARY_MAX_CHARS", 50)
    code = _function("big", 40)
    stub = StubLLMClient(max_summary_chars=len(code) // 2 + 700)

    summary = await dc._summarize_code(code, "big", stub, {})

    assert summary == "SUMMARY: does things\nSUMMARY: does things"
    assert len(stub.summary_calls) == 3
    assert stub.summary_calls[0].startswith("Summarize the behavior of the following code symbol `big`")
    assert "(big, part)" in stub.summary_calls[1]

    # A text no longer than the summary limit is not cut further
    stub = StubLLMClient(max_summary_chars=10)
    summary = await dc._summarize_code("def f():\n    return 1", "f", stub, {})
    assert dc._CODE_SUMMARY_FAILED_NOTE in summary
    assert len(stub.summary_calls) == 1


def test_line_count_counts_a_long_line_as_several():
    assert dc._line_count("a\nb\n") == 3
    assert dc._line_count("x" * 80) == 1
    assert dc._line_count("x" * 81) == 2
    assert dc._line_count("x" * 800 + "\nshort") == 11


async def test_summary_of_a_document_that_exceeds_the_context():
    """Each section is summarized by itself, then the summary is made from those."""

    class SummaryStub:
        def __init__(self) -> None:
            self.prompt_list: list[str] = []

        async def generate(self, prompt: str, max_tokens: int | None = None) -> str:
            self.prompt_list.append(prompt)
            if len(prompt) > 5000:
                raise ContextWindowExceededError(message="x", model="stub", llm_provider="stub")
            return f"short {len(self.prompt_list)}"

    section_list = [
        {"id": "a", "title": "A", "content": "a" * 3000},
        {"id": "b", "title": "B", "content": "line\n" * 1500},
    ]
    stub = SummaryStub()
    summary = await dc._generate_summary("f.py", section_list, "Summarize.", stub)

    # all sections (overflow), section a, section b (overflow), its two halves, the summary
    assert len(stub.prompt_list) == 6
    assert summary == "short 6"
    assert "### A\nshort 2" in stub.prompt_list[-1]
    assert "### B\nshort 4\nshort 5" in stub.prompt_list[-1]


def test_reduce_caller_usages_drops_bodies():
    """Caller reduction removes usage_context but keeps identifying fields, non-destructively."""
    file_deps = {
        "definitions": [],
        "callee_usages": [],
        "caller_usages": [
            {"name": "a", "file": "x", "lines": [1], "usage_context": "some code"}
        ],
    }

    reduced = dc._without_usage_context(file_deps, "caller_usages", "usage_context")

    assert "usage_context" not in reduced["caller_usages"][0]
    assert reduced["caller_usages"][0]["name"] == "a"
    assert reduced["caller_usages"][0]["lines"] == [1]
    # Original input is not mutated
    assert "usage_context" in file_deps["caller_usages"][0]
    # Nothing to drop: the same object comes back
    assert dc._without_usage_context(reduced, "caller_usages", "usage_context") is reduced


async def test_documents_of_large_files_are_complete(tmp_path, monkeypatch):
    """A header with a large implementation file and a large class get every section and a summary."""
    template_path = tmp_path / "template.json"
    template_path.write_text(json.dumps({
        "sections": [
            {"id": "overview", "title": "Overview", "prompt": "Describe it."},
            {"id": "detail", "title": "Detail", "prompt": "Describe the details."},
        ],
        "summary_prompt": "Summarize it.",
    }))
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", False)
    monkeypatch.setattr(pipeline, "KNOWLEDGE_FORMAT", "json")
    monkeypatch.setattr(dc, "DOC_TEMPLATE_PATH", str(template_path))
    monkeypatch.setattr(dc, "ENABLE_CODE_SUMMARY", True)

    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.h").write_text("int f(void);\n")
    (project / "a.c").write_text(
        "#include \"a.h\"\nint f(void) {\n"
        + "".join(f"  g({i});\n" for i in range(400)) + "  return 0;\n}\n"
    )
    (project / "big.py").write_text(
        "class Big:\n" + "\n\n".join(_function(f"m{n}", 9, indent="    ") for n in range(60)) + "\n"
    )
    out = tmp_path / "out"
    await pipeline.process_all_files(str(project), str(out), None, max_workers=1)

    stub = StubLLMClient(max_prompt_chars=3000)
    fail_list = await dc.generate_all_docs(
        str(out / "proj"),
        [
            {"file": "a.h", "callers": ["a.c"], "callees": []},
            {"file": "a.c", "callers": [], "callees": ["a.h"]},
            {"file": "big.py", "callers": [], "callees": []},
        ],
        stub, max_workers=1,
    )

    assert fail_list == []
    for file_rel in ("a.h", "a.c", "big.py"):
        doc = dc.load_doc(resolve_file_output_dir(str(out / "proj"), file_rel))
        assert [section["id"] for section in doc["sections"]] == ["overview", "detail"]
        assert doc["summary"] == "OK"


def test_files_of_a_cycle_share_a_level_and_their_dependents_follow():
    from codetwine.doc_creator import _callee_set_dict, _topological_sort_by_level

    dep_list = [
        {"file": "a", "callees": ["b"]},
        {"file": "b", "callees": ["a", "c"]},
        {"file": "c", "callees": []},
        {"file": "d", "callees": ["a"]},
        {"file": "e", "callees": ["e"]},
        {"file": "f", "callees": ["g"]},
        {"file": "g", "callees": ["h"]},
        {"file": "h", "callees": ["f", "d"]},
    ]
    assert _topological_sort_by_level(_callee_set_dict(dep_list)) == [
        ["c", "e"], ["a", "b"], ["d"], ["f", "g", "h"],
    ]


def test_files_of_a_cycle_depending_on_fewer_of_it_come_first():
    from codetwine.doc_creator import _callee_set_dict, _topological_sort_by_level

    dep_list = [
        {"file": "cbl/P1.cbl", "callees": ["csd/A.csd", "cpy/X.cpy"]},
        {"file": "cbl/P2.cbl", "callees": ["csd/A.csd"]},
        {"file": "csd/A.csd", "callees": ["cbl/P1.cbl", "cbl/P2.cbl"]},
        {"file": "cpy/X.cpy", "callees": []},
        {"file": "jcl/J.jcl", "callees": ["csd/A.csd"]},
    ]
    assert _topological_sort_by_level(_callee_set_dict(dep_list)) == [
        ["cpy/X.cpy"], ["cbl/P1.cbl", "cbl/P2.cbl"], ["csd/A.csd"], ["jcl/J.jcl"],
    ]


def test_change_regenerates_the_files_depending_on_it_through_a_cycle():
    from codetwine.doc_creator import _dependent_closure_set

    callee_set_dict = {
        "cbl/P.cbl": {"cpy/X.cpy", "csd/A.csd"}, "csd/A.csd": {"cbl/P.cbl"},
        "cpy/X.cpy": set(), "cbl/Q.cbl": set(),
    }
    assert _dependent_closure_set({"cpy/X.cpy"}, callee_set_dict) == {
        "cpy/X.cpy", "cbl/P.cbl", "csd/A.csd",
    }


# == Saving, taking edits from doc.md and regenerating the documents ===========

_TITLE_PATTERN = re.compile(r'Write the content for the "(.+)" section')


class TitleStub:
    """Answers a section prompt with reply(title) and any other prompt with summary."""

    def __init__(self, reply=lambda title: f"text of {title}", summary: str = "SUM") -> None:
        self.reply = reply
        self.summary = summary
        self.prompt_list: list[str] = []

    async def generate(self, prompt: str, max_tokens: int | None = None) -> str | None:
        self.prompt_list.append(prompt)
        match = _TITLE_PATTERN.search(prompt)
        return self.reply(match.group(1)) if match else self.summary


def _template(title_list: list[str], with_summary: bool = True) -> dict:
    """Return a template with one section per title, ids s0, s1, ..."""
    template = {"sections": [
        {"id": f"s{index}", "title": title, "prompt": "Describe it."}
        for index, title in enumerate(title_list)
    ]}
    if with_summary:
        template["summary_prompt"] = "Summarize it."
    return template


def _project(tmp_path, monkeypatch, file_dict: dict[str, str], template: dict | None = None):
    """Write a project and a template, and turn design documents on for the pipeline."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    template_path = tmp_path / "template.json"
    template_path.write_text(json.dumps(template or _template(["Alpha", "Beta"])))
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", True)
    monkeypatch.setattr(pipeline, "KNOWLEDGE_FORMAT", "json")
    monkeypatch.setattr(dc, "DOC_TEMPLATE_PATH", str(template_path))
    project = tmp_path / "proj"
    for file_rel, text in file_dict.items():
        (project / file_rel).parent.mkdir(parents=True, exist_ok=True)
        (project / file_rel).write_text(text)
    return project, tmp_path / "out"


async def _run_doc(project, out, stub, max_workers: int = 1) -> dict:
    return await pipeline.process_all_files(str(project), str(out), stub, max_workers=max_workers)


def _doc_dir(out, file_rel: str) -> str:
    return resolve_file_output_dir(str(out / "proj"), file_rel)


def _read_doc_json(out_dir: str) -> dict:
    with open(os.path.join(out_dir, "doc.json"), encoding="utf-8") as f:
        return json.load(f)


def _edit_md(out_dir: str, edit) -> str:
    """Rewrite doc.md by edit(text), give it a later time than doc.json and return it."""
    md_path = os.path.join(out_dir, "doc.md")
    with open(md_path, encoding="utf-8") as f:
        text = edit(f.read())
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(text)
    later = os.path.getmtime(os.path.join(out_dir, "doc.json")) + 10
    os.utime(md_path, (later, later))
    return text


def _doc_of(section_list: list[tuple[str, str]], summary: str = "the summary") -> dict:
    return {
        "file": "a.py",
        "sections": [
            {"id": f"s{index}", "title": title, "content": content}
            for index, (title, content) in enumerate(section_list)
        ],
        "summary": summary, "source_hash": "h",
    }


async def test_only_a_heading_of_just_the_section_title_is_taken_off_a_section(tmp_path, monkeypatch):
    project, out = _project(tmp_path, monkeypatch, {"a.py": "x = 1\n"})
    reply_dict = {"Alpha": "## Alpha Policy\n| a | b |", "Beta": "### Beta ##\n\n    code()\nrest"}

    await _run_doc(project, out, TitleStub(reply_dict.get))

    out_dir = _doc_dir(out, "a.py")
    content_list = [section["content"] for section in _read_doc_json(out_dir)["sections"]]
    assert content_list == ["## Alpha Policy\n| a | b |", "    code()\nrest"]
    with open(os.path.join(out_dir, "doc.md"), encoding="utf-8") as f:
        assert "# Alpha\n\n## Alpha Policy\n| a | b |\n\n# Beta\n\n    code()\nrest" in f.read()


def test_a_heading_line_inside_a_section_does_not_cut_it_when_doc_md_is_taken(tmp_path):
    out_dir = str(tmp_path)
    template = _template(["Alpha", "Beta"])
    dc._save_doc(_doc_of([
        ("Alpha", "intro\n\n# Summary\nThe module in short.\n\nmore text"),
        ("Beta", "beta text\n\n# Alpha\nagain"),
    ]), out_dir)
    _edit_md(out_dir, lambda text: text.replace("beta text", "beta text EDITED"))

    doc = dc._reusable_doc("a.py", out_dir, template)

    assert [section["content"] for section in doc["sections"]] == [
        "intro\n\n# Summary\nThe module in short.\n\nmore text",
        "beta text EDITED\n\n# Alpha\nagain",
    ]
    assert doc["summary"] == "the summary"


def test_sections_of_the_same_title_are_taken_from_doc_md_one_by_one(tmp_path):
    out_dir = str(tmp_path)
    dc._save_doc(_doc_of([("Same", "first"), ("Same", "second")]), out_dir)
    _edit_md(out_dir, lambda text: text.replace("second", "second EDITED"))

    doc = dc._reusable_doc("a.py", out_dir, _template(["Same", "Same"]))

    assert [section["content"] for section in doc["sections"]] == ["first", "second EDITED"]


def test_the_last_section_is_taken_from_a_doc_md_without_a_summary(tmp_path):
    out_dir = str(tmp_path)
    dc._save_doc(_doc_of([("Alpha", "first"), ("Beta", "last")], summary=""), out_dir)
    _edit_md(out_dir, lambda text: text.replace("first", "EDITED FIRST").replace("last", "EDITED LAST"))

    doc = dc._reusable_doc("a.py", out_dir, _template(["Alpha", "Beta"], with_summary=False))

    assert [section["content"] for section in doc["sections"]] == ["EDITED FIRST", "EDITED LAST"]
    with open(os.path.join(out_dir, "doc.md"), encoding="utf-8") as f:
        assert "EDITED LAST" in f.read()


def test_a_section_next_to_a_removed_heading_is_named_and_doc_md_is_kept(tmp_path, caplog):
    out_dir = str(tmp_path)
    dc._save_doc(_doc_of([("Alpha", "first"), ("Beta", "middle"), ("Gamma", "last")]), out_dir)
    md_text = _edit_md(
        out_dir, lambda text: text.replace("# Beta\n", "").replace("last", "EDITED LAST"),
    )

    doc = dc._reusable_doc("a.py", out_dir, _template(["Alpha", "Beta", "Gamma"]))

    assert [section["content"] for section in doc["sections"]] == ["first", "middle", "EDITED LAST"]
    with open(os.path.join(out_dir, "doc.md"), encoding="utf-8") as f:
        assert f.read() == md_text
    warning_list = [record.getMessage() for record in caplog.records if record.levelname == "WARNING"]
    assert any("Alpha, Beta" in message and "a.py" in message for message in warning_list)


# A section with sub-headings, as the template's single section is written
_SPEC_TEXT = "## 1. Responsibility\n\nIt writes the outputs.\n\n## 2. Error Handling\n\nNone."


@pytest.mark.parametrize("section_text, summary, edit, expect_section, expect_summary", [
    # A summary starting with its own "# Summary" heading, an edit of the section
    (_SPEC_TEXT, "# Summary\n\nThe module writes the outputs.",
     lambda text: text.replace("It writes", "It EDITED writes"),
     _SPEC_TEXT.replace("It writes", "It EDITED writes"),
     "# Summary\n\nThe module writes the outputs."),
    # The same, with the doubled "# Summary" line taken out by hand
    (_SPEC_TEXT, "# Summary\n\nThe module writes the outputs.",
     lambda text: text.replace("# Summary\n\n# Summary\n", "# Summary\n"),
     _SPEC_TEXT, "The module writes the outputs."),
    # A "# Summary" line inside the summary, an edit after it
    ("Spec.", "Short form.\n\n# Summary\nLong form.",
     lambda text: text.replace("Long form.", "Long form EDITED."),
     "Spec.", "Short form.\n\n# Summary\nLong form EDITED."),
    # A "# Summary" line inside the last section, an edit of the summary
    ("Spec.\n\n# Summary\nIn short.", "The summary.",
     lambda text: text.replace("The summary.", "The summary EDITED."),
     "Spec.\n\n# Summary\nIn short.", "The summary EDITED."),
], ids=["summary-heading", "summary-heading-taken-out", "summary-line-in-summary",
        "summary-line-in-last-section"])
def test_the_summary_heading_is_told_from_the_summary_lines_of_doc_json(
        tmp_path, section_text, summary, edit, expect_section, expect_summary):
    out_dir = str(tmp_path)
    dc._save_doc(_doc_of([("Design Specification", section_text)], summary=summary), out_dir)
    _edit_md(out_dir, edit)

    doc = dc._reusable_doc("a.py", out_dir, _template(["Design Specification"]))

    assert doc["sections"][0]["content"] == expect_section
    assert doc["summary"] == expect_summary


def test_a_summary_line_in_the_last_section_of_a_document_without_a_summary_stays_in_it(tmp_path):
    out_dir = str(tmp_path)
    dc._save_doc(_doc_of(
        [("Overview", "over"), ("Details", "detail text\n\n# Summary\nIn short, it parses.")],
        summary="",
    ), out_dir)
    _edit_md(out_dir, lambda text: text.replace("over\n", "over EDITED\n"))

    doc = dc._reusable_doc("a.py", out_dir, _template(["Overview", "Details"], with_summary=False))

    assert [section["content"] for section in doc["sections"]] == [
        "over EDITED", "detail text\n\n# Summary\nIn short, it parses.",
    ]
    assert doc["summary"] == ""


def test_a_heading_line_of_the_next_section_inside_a_section_is_kept_in_it(tmp_path):
    out_dir = str(tmp_path)
    dc._save_doc(_doc_of([
        ("Alpha", "intro\n\n# Beta\nas Beta says"), ("Beta", "beta text"), ("Gamma", "gamma"),
    ]), out_dir)
    _edit_md(out_dir, lambda text: text.replace("beta text", "beta text EDITED"))

    doc = dc._reusable_doc("a.py", out_dir, _template(["Alpha", "Beta", "Gamma"]))

    assert [section["content"] for section in doc["sections"]] == [
        "intro\n\n# Beta\nas Beta says", "beta text EDITED", "gamma",
    ]


async def test_the_summary_heading_is_taken_off_a_generated_summary(tmp_path, monkeypatch):
    project, out = _project(tmp_path, monkeypatch, {"a.py": "x = 1\n"})

    await _run_doc(project, out, TitleStub(summary="# Summary\n\nThe module sets x."))

    out_dir = _doc_dir(out, "a.py")
    assert _read_doc_json(out_dir)["summary"] == "The module sets x."
    with open(os.path.join(out_dir, "doc.md"), encoding="utf-8") as f:
        assert f.read().count("# Summary") == 1


def _write_cp932(md_path: str) -> None:
    with open(md_path, encoding="utf-8") as f:
        text = f.read()
    with open(md_path, "w", encoding="cp932") as f:
        f.write(text.replace("text of Alpha", "text of Alpha 日本語の手直し"))


def _write_utf16(md_path: str) -> None:
    with open(md_path, encoding="utf-8") as f:
        text = f.read()
    with open(md_path, "w", encoding="utf-16") as f:
        f.write(text.replace("text of Alpha", "text of Alpha EDITED"))


def _make_dir(md_path: str) -> None:
    os.remove(md_path)
    os.mkdir(md_path)


@pytest.mark.parametrize("write_md", [_write_cp932, _write_utf16, _make_dir])
async def test_a_doc_md_that_cannot_be_read_keeps_the_document_and_is_named(
        tmp_path, monkeypatch, caplog, write_md):
    project, out = _project(tmp_path, monkeypatch, {"a.py": "def f():\n    return 1\n"})
    await _run_doc(project, out, TitleStub())
    out_dir = _doc_dir(out, "a.py")
    doc_before = _read_doc_json(out_dir)
    md_path = os.path.join(out_dir, "doc.md")
    write_md(md_path)
    later = os.path.getmtime(os.path.join(out_dir, "doc.json")) + 10
    os.utime(md_path, (later, later))
    stub = TitleStub()

    run_result = await _run_doc(project, out, stub)

    assert run_result["doc_fail_list"] == []
    assert stub.prompt_list == []
    assert _read_doc_json(out_dir) == doc_before
    message_list = [
        record.getMessage() for record in caplog.records
        if record.levelname in ("WARNING", "ERROR") and "a.py" in record.getMessage()
    ]
    assert len(message_list) == 1
    assert message_list[0].startswith("  The edits of doc.md are not taken: a.py: ")


async def test_a_doc_md_newer_without_edits_gives_no_warning_when_regenerated(
        tmp_path, monkeypatch, caplog):
    project, out = _project(tmp_path, monkeypatch, {"a.py": "def f():\n    return 1\n"})
    await _run_doc(project, out, TitleStub())
    # Opened and saved again, with a blank line more at its end
    _edit_md(_doc_dir(out, "a.py"), lambda text: text + "\n")
    await _run_doc(project, out, TitleStub())
    (project / "a.py").write_text("def f():\n    return 2\n")
    caplog.clear()

    await _run_doc(project, out, TitleStub())

    assert not [
        record for record in caplog.records
        if record.levelname == "WARNING" and "doc.md" in record.getMessage()
    ]


async def test_a_warning_says_the_edits_of_doc_md_are_replaced_by_a_regenerated_document(
        tmp_path, monkeypatch, caplog):
    project, out = _project(tmp_path, monkeypatch, {"a.py": "def f():\n    return 1\n"})
    await _run_doc(project, out, TitleStub())
    _edit_md(_doc_dir(out, "a.py"), lambda text: text.replace("text of Alpha", "EDITED"))
    (project / "a.py").write_text("def f():\n    return 2\n")

    await _run_doc(project, out, TitleStub())

    assert any(
        "doc.md" in record.getMessage() and "a.py" in record.getMessage()
        for record in caplog.records if record.levelname == "WARNING"
    )


async def test_a_run_without_design_documents_takes_the_edits_of_a_current_document(
        tmp_path, monkeypatch, caplog):
    project, out = _project(tmp_path, monkeypatch, {
        "a.py": "def f():\n    return 1\n", "b.py": "def g():\n    return 1\n",
    })
    await _run_doc(project, out, TitleStub())
    _edit_md(_doc_dir(out, "a.py"), lambda text: text.replace("text of Alpha", "EDITED A"))
    _edit_md(_doc_dir(out, "b.py"), lambda text: text.replace("text of Alpha", "EDITED B"))
    (project / "b.py").write_text("def g():\n    return 2\n")
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", False)

    await pipeline.process_all_files(str(project), str(out), None, max_workers=1)

    # a.py: its doc.json is current, so the edit is taken and the outputs carry it
    a_doc = _read_doc_json(_doc_dir(out, "a.py"))
    assert [section["content"] for section in a_doc["sections"]] == ["EDITED A", "text of Beta"]
    knowledge = json.loads((out / "proj" / "project_knowledge.json").read_text())
    doc_dict = {entry["file"]: entry.get("doc") for entry in knowledge["files"]}
    assert doc_dict["proj/a_py/a.py"]["sections"][0]["content"] == "EDITED A"
    # b.py: its source changed, so its document is left out and its edit is not taken
    assert doc_dict["proj/b_py/b.py"] is None
    assert _read_doc_json(_doc_dir(out, "b.py"))["sections"][0]["content"] == "text of Alpha"

    # The next run with design documents reuses the document of a.py with the edit and
    # regenerates the one of b.py with the warning about its edits
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", True)
    caplog.clear()
    await _run_doc(project, out, TitleStub())
    assert _read_doc_json(_doc_dir(out, "a.py"))["sections"][0]["content"] == "EDITED A"
    assert [
        record.getMessage() for record in caplog.records
        if record.levelname == "WARNING" and "doc.md" in record.getMessage()
    ] == ["  The edits of doc.md are replaced by the regenerated document: b.py"]


async def test_a_header_is_regenerated_when_its_implementation_file_changes(tmp_path, monkeypatch):
    project, out = _project(tmp_path, monkeypatch, {
        "src/a.h": "int f(void);\n",
        "src/a.c": "#include \"a.h\"\nint f(void) { return 1; }\n",
    })
    await _run_doc(project, out, TitleStub())
    (project / "src" / "a.c").write_text("#include \"a.h\"\nint f(void) { return 2; }\n")
    stub = TitleStub()

    await _run_doc(project, out, stub)

    header_prompt_list = [
        prompt for prompt in stub.prompt_list
        if "# Target File: src/a.h" in prompt and _TITLE_PATTERN.search(prompt)
    ]
    assert header_prompt_list
    assert all("return 2;" in prompt for prompt in header_prompt_list)


async def test_a_header_is_documented_without_a_removed_implementation_file(tmp_path, monkeypatch):
    project, out = _project(tmp_path, monkeypatch, {
        "a.h": "int f(void);\n",
        "a.c": "#include \"a.h\"\nint f(void) { return 1; }\n",
    })
    await _run_doc(project, out, TitleStub())
    (project / "a.c").unlink()
    (project / "a.h").write_text("int f(int x);\n")
    stub = TitleStub()

    await _run_doc(project, out, stub)

    header_prompt_list = [
        prompt for prompt in stub.prompt_list
        if "# Target File: a.h" in prompt and _TITLE_PATTERN.search(prompt)
    ]
    assert header_prompt_list
    assert not any(dc._HEADER_IMPL_CONTEXT in prompt for prompt in header_prompt_list)


async def test_a_template_without_a_summary_prompt_asks_for_no_summary(tmp_path, monkeypatch, caplog):
    project, out = _project(
        tmp_path, monkeypatch, {"a.py": "x = 1\n"}, _template(["Alpha"], with_summary=False),
    )
    stub = TitleStub()

    run_result = await _run_doc(project, out, stub)

    assert run_result["doc_fail_list"] == []
    assert all(_TITLE_PATTERN.search(prompt) for prompt in stub.prompt_list)
    assert _read_doc_json(_doc_dir(out, "a.py"))["summary"] == ""
    assert not [record for record in caplog.records if record.levelname == "WARNING"]


async def test_a_file_without_a_document_is_reported_once(tmp_path, monkeypatch, capsys, caplog):
    project, out = _project(tmp_path, monkeypatch, {"a.py": "x = 1\n"})

    await _run_doc(project, out, TitleStub(lambda title: None))

    assert "SKIP" not in capsys.readouterr().out
    assert [record.getMessage() for record in caplog.records].count("  SKIP: a.py") == 1


async def test_the_documents_reused_are_not_counted_as_generated(tmp_path, monkeypatch, capsys):
    project, out = _project(tmp_path, monkeypatch, {"a.py": "x = 1\n", "b.py": "y = 1\n"})
    await _run_doc(project, out, TitleStub())
    capsys.readouterr()
    (project / "b.py").write_text("y = 2\n")

    await _run_doc(project, out, TitleStub())

    line = next(line for line in capsys.readouterr().out.splitlines() if "Generated:" in line)
    assert "Generated: 1, Reused: 1, Incomplete or failed: 0 / Total: 2" in line


async def test_an_error_of_a_file_names_the_file(tmp_path, monkeypatch, caplog):
    project, out = _project(tmp_path, monkeypatch, {"a.py": "x = 1\n"})

    async def broken_generate_file_doc(*args, **kwargs):
        raise RuntimeError("broken")

    monkeypatch.setattr(dc, "_generate_file_doc", broken_generate_file_doc)

    run_result = await _run_doc(project, out, TitleStub())

    assert run_result["doc_fail_list"] == ["a.py"]
    assert any(
        "a.py" in record.getMessage() and "broken" in record.getMessage()
        for record in caplog.records if record.levelname == "ERROR"
    )


async def test_files_of_a_cycle_documented_together_get_the_same_prompts_whatever_the_workers(
        tmp_path, monkeypatch):
    file_dict = {
        "a.py": "from b import g\n\n\ndef f():\n    return g()\n",
        "b.py": "def g():\n    from a import f\n    return f\n",
    }
    prompt_list_list = []
    for max_workers in (1, 2):
        project, out = _project(tmp_path / str(max_workers), monkeypatch, file_dict)
        stub = TitleStub()
        await _run_doc(project, out, stub, max_workers=max_workers)
        prompt_list_list.append(sorted(stub.prompt_list))

    assert prompt_list_list[0] == prompt_list_list[1]
