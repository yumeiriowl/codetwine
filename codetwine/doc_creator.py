import os
import json
import re
import hashlib
import asyncio
import heapq
import logging
from bisect import bisect_left, bisect_right
from collections.abc import Awaitable, Callable, Container, Iterator
from dataclasses import dataclass, replace
from itertools import islice
from codetwine.llm import ContextWindowExceededError
from codetwine.llm.client import LLMClient
from codetwine.utils.file_utils import (
    DEPENDENCY_FILE_NAME,
    DOC_JSON_NAME,
    DOC_MD_NAME,
    add_unique,
    compute_file_hash,
    load_doc,
    load_file_dependencies,
    output_path_to_rel,
    read_source_text,
    resolve_file_output_dir,
)
from codetwine.config.logger import log_progress
from codetwine.config.settings import (
    MAX_WORKERS,
    DOC_TEMPLATE_PATH,
    OUTPUT_LANGUAGE,
    SUMMARY_MAX_CHARS,
    ENABLE_CODE_SUMMARY,
    CODE_SUMMARY_TRIGGER_LINES,
    CODE_SUMMARY_PIECE_LINES,
    CODE_SUMMARY_MAX_CHARS,
    C_HEADER_EXT_TUPLE,
    C_IMPL_EXT_TUPLE,
)

logger = logging.getLogger(__name__)


# Prompt header: heading indicating the target file
_HEADER_TARGET_FILE = "# Target File: {file}"

# Source code section heading
_HEADER_SOURCE_CODE = "## Source Code"

# ===== Dependency (callee_usages) section =====

_HEADER_CALLEE_USAGE = "## External Functions/Classes Used by This File (Dependencies)"

CALLEE_USAGES_SCHEMA_NOTE = (
    "* Schema: name=symbol name being used, from=file path where that symbol is defined\n"
    "* The 'dependency source code' shown below each symbol is the full source code "
    "of the dependency symbol (or, for large symbols, a concise behavior summary of it). "
    "Refer to it to understand what external code this target file depends on."
)

_CALLEE_SOURCE_CODE_LABEL = "  Dependency Source Code:"

# ===== Dependent (caller_usages) section =====

_HEADER_CALLER_USAGE = "## External Files Using This File (Dependents)"

_CALLER_USAGE_SCHEMA_NOTE = (
    "* Schema: name=symbol name being used, from=file path of the file using it"
)

_CALLER_SOURCE_CODE_LABEL = "  Usage Location Source Code:"

# ===== Dependency design document summary section =====

_HEADER_CALLEE_CONTEXT = "## Design Document Summaries of Dependency Files"

_CALLEE_CONTEXT_NOTE = (
    "* The following are summaries of previously generated design documents for each dependency file. "
    "Use them as reference information to understand the responsibilities and public interfaces "
    "of external modules that the target file depends on."
)

# ===== Request section =====

_HEADER_REQUEST = "## Request"

# {title} is replaced with the section title (e.g. "Overview & Purpose")
_SECTION_REQUEST_TEMPLATE = "Write the content for the \"{title}\" section following the instructions below."

# {language} is replaced with the output language (e.g. "Japanese")
_OUTPUT_LANGUAGE_INSTRUCTION = "Write the output in {language}."

# Instruction to ensure consistency with source code
_FACTUAL_ACCURACY_INSTRUCTION = (
    "\n[IMPORTANT] Do not describe information not found in the source code based on speculation. "
    "Descriptions that contradict the source code implementation are strictly prohibited. "
    "Write only based on the provided source code and dependency information."
)

# ===== Implementation file context (for header files) =====

_HEADER_IMPL_CONTEXT = "## Corresponding Implementation File"

_IMPL_CONTEXT_NOTE = (
    "* The following is the source code of the implementation (.cpp/.c) file "
    "corresponding to this header file. "
    "Refer to it to understand how the classes and functions declared in the header are implemented."
)

# ===== Summary prompt =====

_HEADER_DOC_CONTENT = "## Design Document Content"

# Heading of the summary in doc.md
_SUMMARY_TITLE = "Summary"

# {max_chars} is replaced with the character count the summary is asked to keep within
_SUMMARY_CHAR_LIMIT = "({max_chars} characters or fewer)"

# ===== Code behavior summary prompt (context-overflow fallback) =====

# {name} = symbol name, {max_chars} = character count asked for, {language} = output
# language. Used to shrink large code blocks while keeping their behavior information.
_CODE_SUMMARY_PROMPT = (
    "Summarize the behavior of the following code symbol `{name}` in {max_chars} "
    "characters or fewer. Keep the signature (first line) as-is, then describe what "
    "it takes, what it does internally, and what it returns or its side effects. "
    "This is reference context for documenting another file that depends on it, so be "
    "concise and factual. Write the description in {language}.\n\n"
    "```\n{code}\n```"
)

# {name} = what the piece is, {max_chars} = character count asked for, {language} = output
# language.
# Used for a piece of code that is no whole symbol: a run of lines of a file, a half of a
# symbol that does not fit in one prompt.
_CODE_PIECE_SUMMARY_PROMPT = (
    "Summarize the behavior of the following piece of source code ({name}) in "
    "{max_chars} characters or fewer. Name the functions, classes and variables it "
    "defines, and describe what each of them does. This is reference context for "
    "documenting the file it is taken from, so be concise and factual. Write the "
    "description in {language}.\n\n"
    "```\n{code}\n```"
)

# Placeholder header prefixed to a summarized code block in the prompt.
_CODE_SUMMARY_MARKER = "# [summarized] {name}"

# Name of a half of a code block, and of a run of lines of a file, in the prompt and the marker
_CODE_PART_NAME = "{name}, part"
_CODE_PIECE_NAME = "part {index} of {count}"

# Characters of a line counted as one line by _line_count()
_LINE_WIDTH = 80

# Deterministic fallback body appended when summary generation fails.
_CODE_SUMMARY_FAILED_NOTE = "# ...(body omitted; summary unavailable)"


def check_code_summary_setting() -> None:
    """Check the settings of the context-overflow fallback.

    Raises:
        ValueError: When CODE_SUMMARY_PIECE_LINES is less than 1 while
            ENABLE_CODE_SUMMARY is True.
    """
    if ENABLE_CODE_SUMMARY and CODE_SUMMARY_PIECE_LINES < 1:
        raise ValueError(
            f"CODE_SUMMARY_PIECE_LINES must be 1 or more, but got {CODE_SUMMARY_PIECE_LINES}. "
            f"Set it in the .env file or your shell."
        )


def check_worker_count(max_workers: int) -> None:
    """Check the number of design documents generated in parallel.

    Args:
        max_workers: The max_workers argument of generate_all_docs.

    Raises:
        ValueError: When max_workers is less than 1.
    """
    if max_workers < 1:
        raise ValueError(
            f"MAX_WORKERS must be 1 or more, but got {max_workers}. "
            f"Set it in the .env file or your shell, or pass max_workers of 1 or more."
        )


def check_doc_template() -> None:
    """Check that the design document template DOC_TEMPLATE_PATH names can be used.

    Raises:
        ValueError: When the template cannot be read or has a wrong form (_load_template).
    """
    _load_template()


def _load_template() -> dict:
    """Read the design document template DOC_TEMPLATE_PATH names.

    Returns:
        The template dict: "sections", a list of one or more {"id", "title", "prompt"},
        and "summary_prompt" when the template has one.

    Raises:
        ValueError: When the file cannot be read as JSON, or the template has a wrong
            form (_template_problem).
    """
    try:
        with open(DOC_TEMPLATE_PATH, "r", encoding="utf-8") as f:
            template = json.load(f)
    except (OSError, ValueError) as e:
        raise ValueError(
            f"DOC_TEMPLATE_PATH names a design document template that cannot be read: "
            f"{DOC_TEMPLATE_PATH}: {e}. Set DOC_TEMPLATE_PATH in the .env file or your "
            f"shell to the path of a template JSON file such as doc_template.json."
        ) from None
    problem = _template_problem(template)
    if problem:
        raise ValueError(
            f"The design document template {DOC_TEMPLATE_PATH} {problem}. Fix the "
            f"template, or set DOC_TEMPLATE_PATH in the .env file or your shell to "
            f"another one such as doc_template.json."
        )
    return template


def _template_problem(template: object) -> str | None:
    """Tell what is wrong with the form of a design document template.

    Args:
        template: The value read from the template file.

    Returns:
        What is wrong, as the end of a sentence about the template; None for a template
        whose "sections" is a list of one or more objects with a non-empty string "id"
        of their own, a non-empty string "title" and a string "prompt", and whose
        "summary_prompt" is a string where present.
    """
    if not isinstance(template, dict):
        return "is not a JSON object"
    section_list = template.get("sections")
    if not isinstance(section_list, list) or not section_list:
        return 'has no "sections" list holding a section'
    id_set: set[str] = set()
    for number, section in enumerate(section_list, 1):
        if not isinstance(section, dict):
            return f"has a section (number {number}) that is not a JSON object"
        for key in ("id", "title", "prompt"):
            value = section.get(key)
            if not isinstance(value, str) or (key != "prompt" and not value):
                return f'has a section (number {number}) without a "{key}" string'
        if section["id"] in id_set:
            return f'has two sections with the "id" {section["id"]}'
        id_set.add(section["id"])
    if not isinstance(template.get("summary_prompt", ""), str):
        return 'has a "summary_prompt" that is not a string'
    return None


def _component_list(callee_dict: dict[str, set[str]]) -> list[list[str]]:
    """Return the groups of files that reach each other through their dependencies.

    Each group is a strongly connected component: a file by itself, or the files of a
    cycle. The groups come in the order that puts a group after the groups it depends on.

    Args:
        callee_dict: file -> files it depends on; every file is a key.

    Returns:
        The groups, each in path order.
    """
    index_dict: dict[str, int] = {}
    low_dict: dict[str, int] = {}
    stack_list: list[str] = []
    on_stack_set: set[str] = set()
    group_list: list[list[str]] = []
    # (file, its dependencies not followed yet) of the files being followed
    work_list: list[tuple[str, Iterator[str]]] = []

    def visit(file_path: str) -> None:
        """Number a file, put it on the stack and start following its dependencies."""
        index_dict[file_path] = low_dict[file_path] = len(index_dict)
        stack_list.append(file_path)
        on_stack_set.add(file_path)
        work_list.append((file_path, iter(sorted(callee_dict[file_path]))))

    for root in sorted(callee_dict):
        if root in index_dict:
            continue
        visit(root)
        while work_list:
            file_path, callee_iter = work_list[-1]
            callee = next(callee_iter, None)
            if callee is not None:
                if callee not in index_dict:
                    visit(callee)
                elif callee in on_stack_set:
                    low_dict[file_path] = min(low_dict[file_path], index_dict[callee])
                continue
            work_list.pop()
            if work_list:
                parent = work_list[-1][0]
                low_dict[parent] = min(low_dict[parent], low_dict[file_path])
            if low_dict[file_path] == index_dict[file_path]:
                member_list: list[str] = []
                while not member_list or member_list[-1] != file_path:
                    member = stack_list.pop()
                    on_stack_set.discard(member)
                    member_list.append(member)
                group_list.append(sorted(member_list))
    return group_list


def _cycle_step_list(member_list: list[str], callee_dict: dict[str, set[str]]) -> list[list[str]]:
    """Split the files of a cycle into the steps they are documented in, one after another.

    Each step takes the files in no step yet that depend on the fewest files of the cycle
    in no step yet.

    Examples:
        a, b and c depend on d, d on a, b and c -> [["a", "b", "c"], ["d"]]

    Args:
        member_list: The files of the cycle.
        callee_dict: file -> files it depends on.

    Returns:
        The steps, each in path order.
    """
    member_set = set(member_list)
    dependent_dict: dict[str, list[str]] = {member: [] for member in member_list}
    count_dict: dict[str, int] = {}
    for member in member_list:
        callee_set = (callee_dict[member] & member_set) - {member}
        count_dict[member] = len(callee_set)
        for callee in callee_set:
            dependent_dict[callee].append(member)

    # (number of files of the cycle in no step yet it depends on, file); an entry whose
    # number is no longer the file's is skipped
    heap_list = [(count, member) for member, count in count_dict.items()]
    heapq.heapify(heap_list)
    step_member_set: set[str] = set()
    step_list: list[list[str]] = []
    while heap_list:
        count, member = heap_list[0]
        if member in step_member_set or count != count_dict[member]:
            heapq.heappop(heap_list)
            continue
        step_member_list: list[str] = []
        while heap_list and heap_list[0][0] == count:
            entry_count, entry_member = heapq.heappop(heap_list)
            if entry_member not in step_member_set and entry_count == count_dict[entry_member]:
                step_member_list.append(entry_member)
        step_member_set.update(step_member_list)
        for step_member in step_member_list:
            for dependent in dependent_dict[step_member]:
                if dependent not in step_member_set:
                    count_dict[dependent] -= 1
                    heapq.heappush(heap_list, (count_dict[dependent], dependent))
        step_list.append(sorted(step_member_list))
    return step_list


def _callee_set_dict(project_dep_list: list[dict]) -> dict[str, set[str]]:
    """Return the files each file of a dependency list depends on.

    Args:
        project_dep_list: Dependency list whose paths are relative to the project root.
                          Each element is {"file": str, "callers": list, "callees": list}.

    Returns:
        file -> files it depends on. Every file of the list and every callee is a key.
    """
    callee_set_dict: dict[str, set[str]] = {}
    for dep_info in project_dep_list:
        file_path = dep_info["file"]
        callee_set_dict.setdefault(file_path, set())
        for callee in dep_info.get("callees", []):
            callee_set_dict.setdefault(callee, set())
            callee_set_dict[file_path].add(callee)
    return callee_set_dict


def _topological_sort_by_level(callee_dict: dict[str, set[str]]) -> list[list[str]]:
    """Topologically sort files by their dependencies and return them
    as a list grouped by level (dependency depth).

    A file is placed at the level after the last level of the files it depends on.
    The files of a cycle (_component_list) start at the level after the last level of
    the files outside the cycle they depend on, and take one level per step of
    _cycle_step_list. The files of each cycle are logged with a warning.

    Args:
        callee_dict: file -> files it depends on; every file is a key (_callee_set_dict).

    Returns:
        A file list grouped by level. The outer list index is the level number.
        Example: [["config.py", "utils.py"], ["parser.py"], ["main.py"]]
    """
    group_list = _component_list(callee_dict)
    cycle_group_list = [member_list for member_list in group_list if len(member_list) > 1]
    if cycle_group_list:
        logger.warning(f"Circular dependencies detected: {cycle_group_list}")

    # Level of each file, group by group in the order of group_list
    level_dict: dict[str, int] = {}
    for member_list in group_list:
        member_set = set(member_list)
        base_level = max(
            (
                level_dict[callee] + 1
                for member in member_list for callee in callee_dict[member]
                if callee not in member_set
            ),
            default=0,
        )
        step_list = (
            _cycle_step_list(member_list, callee_dict) if len(member_list) > 1
            else [member_list]
        )
        for offset, step_member_list in enumerate(step_list):
            for member in step_member_list:
                level_dict[member] = base_level + offset

    level_list: list[list[str]] = [[] for _ in range(max(level_dict.values(), default=-1) + 1)]
    for file_path in sorted(level_dict):
        level_list[level_dict[file_path]].append(file_path)
    return level_list


def _usage_part_list(
    usage_list: list[dict],
    header: str,
    schema_note: str,
    path_key: str,
    context_key: str,
    context_label: str,
) -> list[str]:
    """Build the prompt lines listing callee_usages or caller_usages.

    Each usage becomes "- name (from path)", followed by its source code in an indented
    code block when the usage carries one. The block ends with an empty line.

    Args:
        usage_list: callee_usages or caller_usages of file_dependencies.json.
        header: Section heading (_HEADER_CALLEE_USAGE / _HEADER_CALLER_USAGE).
        schema_note: Schema explanation printed under the heading.
        path_key: Key of the other file's path ("from" / "file").
        context_key: Key of the source code ("target_context" / "usage_context").
        context_label: Label printed before the source code.

    Returns:
        The prompt lines.
    """
    part_list = [header, schema_note]
    for usage in usage_list:
        part_list.append(f"- {usage['name']} (from {output_path_to_rel(usage[path_key])})")
        context = usage.get(context_key)
        if context:
            part_list.append(context_label)
            part_list.append("  ```")
            part_list.append(f"  {context}")
            part_list.append("  ```")
    part_list.append("")
    return part_list


def _build_section_prompt(
    section: dict,
    source_code: str,
    file_dep_dict: dict,
    callee_context: str,
    implementation_context: str = "",
) -> str:
    """Assemble the LLM prompt for one section.

    Args:
        section: One section definition from the template (dict with id, title, prompt).
        source_code: Full source code of the target file.
        file_dep_dict: Contents of file_dependencies.json (definitions, callee_usages,
            caller_usages).
        callee_context: Text combining design document summaries of dependency files (may be empty string).
        implementation_context: For header files. Source code of the corresponding implementation file.

    Returns:
        The completed prompt string to send to the LLM.
    """
    # Basic prompt structure: target file name + source code
    part_list = [
        _HEADER_TARGET_FILE.format(file=output_path_to_rel(file_dep_dict.get('file', 'unknown'))),
        "",
        _HEADER_SOURCE_CODE,
        "```",
        source_code,
        "```",
        "",
    ]

    # For header files, include the corresponding implementation file's source code
    if implementation_context:
        part_list.append(_HEADER_IMPL_CONTEXT)
        part_list.append(_IMPL_CONTEXT_NOTE)
        part_list.append("```")
        part_list.append(implementation_context)
        part_list.append("```")
        part_list.append("")

    # Dependencies with their source code (target_context)
    callee_usage_list = file_dep_dict.get("callee_usages", [])
    if callee_usage_list:
        part_list.extend(_usage_part_list(
            callee_usage_list, _HEADER_CALLEE_USAGE, CALLEE_USAGES_SCHEMA_NOTE,
            "from", "target_context", _CALLEE_SOURCE_CODE_LABEL,
        ))

    # Dependents with the source code around each usage (usage_context)
    caller_usage_list = file_dep_dict.get("caller_usages", [])
    if caller_usage_list:
        part_list.extend(_usage_part_list(
            caller_usage_list, _HEADER_CALLER_USAGE, _CALLER_USAGE_SCHEMA_NOTE,
            "file", "usage_context", _CALLER_SOURCE_CODE_LABEL,
        ))

    # Add dependency file design document summaries as context
    if callee_context:
        part_list.append(_HEADER_CALLEE_CONTEXT)
        part_list.append(_CALLEE_CONTEXT_NOTE)
        part_list.append(callee_context)
        part_list.append("")

    # Section-specific instructions, then the output language and the source code
    # consistency instruction at the end
    part_list.append(_HEADER_REQUEST)
    part_list.append(_SECTION_REQUEST_TEMPLATE.format(title=section['title']))
    part_list.append(section["prompt"])
    part_list.append("\n" + _OUTPUT_LANGUAGE_INSTRUCTION.format(language=OUTPUT_LANGUAGE))
    part_list.append(_FACTUAL_ACCURACY_INSTRUCTION)

    return "\n".join(part_list)


def _build_summary_prompt(
    file_path: str,
    section_list: list[dict],
    summary_prompt: str,
    summary_max_chars: int,
) -> str:
    """Assemble the LLM prompt for generating a summary of the entire design document.

    Args:
        file_path: Relative path of the target file.
        section_list: List of generated sections (each element is {id, title, content}).
        summary_prompt: Summary instruction text defined in the template.
        summary_max_chars: Character count the summary is asked to keep within.

    Returns:
        The completed prompt string to send to the LLM.
    """
    # Basic prompt structure: target file name + all section contents of the design document
    part_list = [
        _HEADER_TARGET_FILE.format(file=file_path),
        "",
        _HEADER_DOC_CONTENT,
    ]

    # Add each section's heading and content to the prompt
    for section in section_list:
        part_list.append(f"### {section['title']}")
        part_list.append(section["content"])
        part_list.append("")

    # Add summary instructions and character limit
    part_list.append(_HEADER_REQUEST)
    part_list.append(summary_prompt)
    part_list.append(_SUMMARY_CHAR_LIMIT.format(max_chars=summary_max_chars))
    part_list.append(_OUTPUT_LANGUAGE_INSTRUCTION.format(language=OUTPUT_LANGUAGE))

    return "\n".join(part_list)


def _build_callee_context_summary(
    file_dep_dict: dict,
    doc_summary_dict: dict[str, str],
) -> str:
    """Concatenate the design document summaries of the dependency files into a single string.

    Args:
        file_dep_dict: The target file's file_dependencies.json.
        doc_summary_dict: A map of file relative path -> generated design document summary.

    Returns:
        Context text combining only the summaries.
    """
    # Create a deduplicated list of dependency files from callee_usages
    callee_set: set[str] = set()
    for usage in file_dep_dict.get("callee_usages", []):
        from_file = usage.get("from")
        if from_file:
            callee_set.add(from_file)
    callee_file_list = sorted(callee_set)

    # Retrieve and concatenate summaries for each dependency file; callee_usages' "from"
    # is an output path, converted to the source relative path doc_summary_dict is keyed by
    part_list = []
    for callee_file in callee_file_list:
        summary = doc_summary_dict.get(output_path_to_rel(callee_file))
        if summary:
            part_list.append(f"- **{output_path_to_rel(callee_file)}**: {summary}")
    return "\n".join(part_list)


class _LlmCallError(Exception):
    """An LLM call made to reduce a prompt failed for another reason than the context window."""


def _log_summary_start(code_count: int, code_kind: str, file_path: str) -> None:
    """Report that the code blocks of one reduction stage are about to be summarized.

    Args:
        code_count: Number of code blocks; nothing is reported for 0.
        code_kind: What the code blocks are (e.g. "definitions").
        file_path: Relative path of the file the prompt is reduced for.
    """
    if code_count:
        log_progress(logger, f"  Summarizing {code_kind} ({code_count}): {file_path}")


def _line_count(text: str) -> int:
    """Return the number of lines of a text block, a long line counted as several.

    A line counts as one line per _LINE_WIDTH characters begun, and an empty line as one.
    """
    return sum(
        max(1, -(-len(line) // _LINE_WIDTH)) for line in text.split("\n")
    )


def _half_list(text: str) -> list[str] | None:
    """Return a text cut in two: at a line break near its middle, or at its middle character.

    Returns:
        [first half, second half], or None for a text of fewer than two characters.
    """
    line_list = text.split("\n")
    if len(line_list) > 1:
        middle = len(line_list) // 2
        return ["\n".join(line_list[:middle]), "\n".join(line_list[middle:])]
    if len(text) < 2:
        return None
    middle = len(text) // 2
    return [text[:middle], text[middle:]]


async def _generate_by_half(
    text: str,
    build_prompt: Callable[[str, bool], str],
    llm_client: LLMClient,
    min_chars: int,
    is_part: bool = False,
) -> str | None:
    """Generate from a text, cutting it in halves while its prompt exceeds the context window.

    Args:
        text: The text the prompt is built from.
        build_prompt: Returns the prompt for a text; the second argument tells whether
            the text is a part of the text first given.
        llm_client: LLM client.
        min_chars: A text of this many characters or fewer is not cut.
        is_part: Whether text is a part of the text first given.

    Returns:
        The generated text; for a text cut in halves, the texts generated from the
        halves joined by a line break. None when a text that is not cut further
        still exceeds the context window.

    Raises:
        _LlmCallError: When a call fails for another reason than the context window.
    """
    try:
        generated_text = await llm_client.generate(build_prompt(text, is_part))
    except ContextWindowExceededError:
        half_list = _half_list(text) if len(text) > min_chars else None
    else:
        if generated_text is None:
            raise _LlmCallError("LLM call failed")
        return generated_text
    if half_list is None:
        return None
    part_list: list[str] = []
    for half in half_list:
        part = await _generate_by_half(half, build_prompt, llm_client, min_chars, is_part=True)
        if not part:
            return None
        part_list.append(part)
    return "\n".join(part_list)


async def _summarize_code(
    code: str,
    name: str,
    llm_client: LLMClient,
    summary_cache_dict: dict[str, str],
    is_piece: bool = False,
) -> str:
    """Summarize a code block into a concise behavior description via the LLM.

    A summary is cached in summary_cache_dict by the SHA256 of the code text. A code block
    whose prompt exceeds the context window is summarized in halves
    (_generate_by_half). When even a part that is not cut further exceeds it, the
    first line of the code followed by _CODE_SUMMARY_FAILED_NOTE is returned and
    nothing is cached.

    Args:
        code: Full source of the code to summarize.
        name: Symbol name (used in the prompt and the placeholder marker).
        llm_client: LLM client used to generate the summary.
        summary_cache_dict: Shared cache mapping code-hash -> summary text.
        is_piece: True for code that is no whole symbol (_CODE_PIECE_SUMMARY_PROMPT).

    Returns:
        The behavior summary text (or the deterministic fallback).

    Raises:
        _LlmCallError: When a call fails for another reason than the context window.
    """
    cache_key = hashlib.sha256(code.encode("utf-8")).hexdigest()
    if cache_key in summary_cache_dict:
        return summary_cache_dict[cache_key]

    def build_prompt(text: str, is_part: bool) -> str:
        """Return the summary prompt of the code, or of a part of it."""
        template = _CODE_PIECE_SUMMARY_PROMPT if is_piece or is_part else _CODE_SUMMARY_PROMPT
        return template.format(
            name=_CODE_PART_NAME.format(name=name) if is_part else name,
            max_chars=CODE_SUMMARY_MAX_CHARS,
            language=OUTPUT_LANGUAGE,
            code=text,
        )

    summary = await _generate_by_half(code, build_prompt, llm_client, CODE_SUMMARY_MAX_CHARS)
    if summary:
        summary_cache_dict[cache_key] = summary
        return summary

    # Fallback: the first line (signature) and a note
    first_line = code.split("\n", 1)[0]
    return f"{first_line}\n{_CODE_SUMMARY_FAILED_NOTE}"


def _without_usage_context(file_dep_dict: dict, usage_key: str, context_key: str) -> dict:
    """Return file_dep_dict without the source code its usages of one kind carry.

    Args:
        file_dep_dict: The target file's file_dependencies.json contents.
        usage_key: "callee_usages" or "caller_usages".
        context_key: Key of the source code of a usage.

    Returns:
        A shallow copy whose usage_key entries have no context_key; file_dep_dict itself
        when no entry carries one.
    """
    usage_list = file_dep_dict.get(usage_key, [])
    if not any(usage.get(context_key) for usage in usage_list):
        return file_dep_dict

    dep_copy_dict = dict(file_dep_dict)
    dep_copy_dict[usage_key] = [
        {key: value for key, value in usage.items() if key != context_key}
        for usage in usage_list
    ]
    return dep_copy_dict


async def _summarize_callee_usage(
    file_dep_dict: dict,
    llm_client: LLMClient,
    summary_cache_dict: dict[str, str],
    file_path: str = "",
) -> dict:
    """Return a copy of file_dep_dict where large callee target_context is summarized.

    Only dependency symbols longer than CODE_SUMMARY_TRIGGER_LINES (_line_count) are
    replaced by an LLM behavior summary, and only by a summary shorter than the
    symbol; the others are kept verbatim.

    Args:
        file_dep_dict: The target file's file_dependencies.json contents.
        llm_client: LLM client used for summarization.
        summary_cache_dict: Shared cache mapping code-hash -> summary text.
        file_path: Relative path of the target file (for the progress message).

    Returns:
        A shallow copy with large callee_usages[].target_context summarized;
        file_dep_dict itself when none is replaced.
    """
    callee_usage_list = file_dep_dict.get("callee_usages", [])
    is_large_list = [
        bool(usage.get("target_context"))
        and _line_count(usage["target_context"]) > CODE_SUMMARY_TRIGGER_LINES
        for usage in callee_usage_list
    ]
    _log_summary_start(sum(is_large_list), "dependency symbols", file_path)

    usage_list = []
    has_summary = False
    for usage, is_large in zip(callee_usage_list, is_large_list):
        if is_large:
            target_context = usage["target_context"]
            summary = await _summarize_code(
                target_context, usage.get("name", "symbol"), llm_client, summary_cache_dict
            )
            if len(summary) < len(target_context):
                usage = {**usage, "target_context": summary}
                has_summary = True
        usage_list.append(usage)
    if not has_summary:
        return file_dep_dict

    dep_copy_dict = dict(file_dep_dict)
    dep_copy_dict["callee_usages"] = usage_list
    return dep_copy_dict


def _definition_start_index_set(definition_list: list[dict]) -> set[int]:
    """Return the 0-based indexes of the lines the definitions start on."""
    return {
        definition["start_line"] - 1 for definition in definition_list
        if definition.get("start_line")
    }


def _large_leaf_definition_list(
    definition_list: list[dict],
    line_list: list[str],
    trigger_line_count: int,
) -> list[dict]:
    """Select the large definitions that hold no other definition and share no line with one.

    A definition longer than trigger_line_count lines (_line_count) is large. A class
    is not selected, whatever its length: its large methods are, and the lines of the
    class around them stay as they are. A definition is not selected either when
    another one starts on one of its lines, or ends on one of its lines before the
    last. Definitions of the same line range are one definition named after all of
    them; when that range is a single line, it is not selected.

    Args:
        definition_list: definitions[] from file_dependencies.json (with start_line/end_line).
        line_list: The lines of the source the definitions are read from.
        trigger_line_count: Line count above which a definition is large.

    Returns:
        The selected definitions, sorted by start_line; their line ranges do not
        overlap. A definition standing for several carries their names joined by ", ".
    """
    definition_dict: dict[tuple[int, int], dict] = {}
    name_list_dict: dict[tuple[int, int], list[str]] = {}
    for definition in definition_list:
        start_line, end_line = definition.get("start_line"), definition.get("end_line")
        if not start_line or not end_line or end_line < start_line:
            continue
        line_range = (start_line, end_line)
        definition_dict.setdefault(line_range, definition)
        name = definition.get("name", "symbol")
        add_unique(name_list_dict, line_range, name)
    start_list = sorted(start_line for start_line, _ in definition_dict)
    end_list = sorted(end_line for _, end_line in definition_dict)

    leaf_list: list[dict] = []
    for line_range in sorted(definition_dict):
        start_line, end_line = line_range
        name_list = name_list_dict[line_range]
        if start_line == end_line and len(name_list) > 1:
            continue
        # Another definition starts on a line of this one
        if bisect_right(start_list, end_line) - bisect_left(start_list, start_line) > 1:
            continue
        # Another definition ends on a line of this one before the last
        if bisect_left(end_list, end_line) > bisect_left(end_list, start_line):
            continue
        if _line_count("\n".join(line_list[start_line - 1:end_line])) > trigger_line_count:
            leaf_list.append({**definition_dict[line_range], "name": ", ".join(name_list)})
    return leaf_list


async def _splice_large_definition(
    source_code: str,
    definition_list: list[dict],
    llm_client: LLMClient,
    summary_cache_dict: dict[str, str],
    file_path: str = "",
) -> tuple[str, set[int]]:
    """Replace large definitions in the source with LLM behavior summaries.

    The [start_line, end_line] range (1-based lines of the source copy) of each
    selected definition (_large_leaf_definition_list) is spliced out and replaced by
    a summary block when that block is shorter than the definition. Non-definition lines,
    small definitions and the lines of a definition around the definitions it holds
    are kept as-is.

    Args:
        source_code: Full source of the file (as read from its copy).
        definition_list: definitions[] from the file_dependencies.json of the file.
        llm_client: LLM client used for summarization.
        summary_cache_dict: Shared cache mapping code-hash -> summary text.
        file_path: Relative path of the file (for the progress message).

    Returns:
        (the source with large definitions replaced by summary blocks, the 0-based
        indexes of the lines of that text the definitions start on). The source is
        the original one, unchanged, when no definition is replaced.
    """
    # Source line N (a row of the syntax tree) is line_list[N - 1]; source_code holds
    # only "\n" line breaks (read_source_text())
    line_list = source_code.split("\n")
    line_count = len(line_list)
    start_index_set = _definition_start_index_set(definition_list)
    leaf_list = _large_leaf_definition_list(
        definition_list, line_list, CODE_SUMMARY_TRIGGER_LINES,
    )
    if not leaf_list:
        return source_code, start_index_set
    _log_summary_start(len(leaf_list), "definitions", file_path)
    definition_by_start_dict = {definition["start_line"]: definition for definition in leaf_list}

    out_list: list[str] = []
    # Index, in the text built, of the line the next element of out_list starts on
    out_index = 0
    out_start_index_set: set[int] = set()
    has_summary = False
    line_no = 1
    while line_no <= line_count:
        if line_no - 1 in start_index_set:
            out_start_index_set.add(out_index)
        definition = definition_by_start_dict.get(line_no)
        if definition is None:
            out_list.append(line_list[line_no - 1])
            out_index += 1
            line_no += 1
            continue
        name = definition.get("name", "symbol")
        code = "\n".join(line_list[definition["start_line"] - 1:definition["end_line"]])
        summary_block = "\n".join([
            _CODE_SUMMARY_MARKER.format(name=name),
            await _summarize_code(code, name, llm_client, summary_cache_dict),
        ])
        if len(summary_block) < len(code):
            out_list.append(summary_block)
            has_summary = True
        else:
            out_list.append(code)
        out_index += out_list[-1].count("\n") + 1
        line_no = definition["end_line"] + 1

    if not has_summary:
        return source_code, start_index_set
    return "\n".join(out_list), out_start_index_set


def _piece_range_list(
    line_list: list[str], boundary_index_set: set[int],
) -> list[tuple[int, int]]:
    """Cut the lines of a source into runs of about CODE_SUMMARY_PIECE_LINES lines.

    A run ends in front of the first line of boundary_index_set once it holds
    CODE_SUMMARY_PIECE_LINES lines (_line_count), and in front of any line once it
    holds twice as many. With an empty boundary_index_set it ends in front of any
    line once it holds CODE_SUMMARY_PIECE_LINES lines.

    Args:
        line_list: The lines of the source.
        boundary_index_set: Indexes of the lines a run preferably starts on (the first
            lines of definitions).

    Returns:
        (index of the first line, index after the last line) of each run, in order.
    """
    range_list: list[tuple[int, int]] = []
    start = 0
    size = 0
    for index, line in enumerate(line_list):
        if index > start and size >= CODE_SUMMARY_PIECE_LINES and (
            not boundary_index_set or index in boundary_index_set
            or size >= 2 * CODE_SUMMARY_PIECE_LINES
        ):
            range_list.append((start, index))
            start, size = index, 0
        size += _line_count(line)
    range_list.append((start, len(line_list)))
    return range_list


async def _summarize_source_by_piece(
    source_code: str,
    boundary_index_set: set[int],
    llm_client: LLMClient,
    summary_cache_dict: dict[str, str],
    file_path: str = "",
) -> str:
    """Replace the source by the LLM behavior summaries of its consecutive pieces.

    The lines are cut into runs (_piece_range_list). A run of
    CODE_SUMMARY_PIECE_LINES lines or more, or longer than
    CODE_SUMMARY_TRIGGER_LINES lines, is replaced by a summary block when that
    block is shorter than the run.

    Args:
        source_code: The source, or the result of an earlier reduction of it.
        boundary_index_set: Indexes of the lines of source_code a run preferably
            starts on (may be empty).
        llm_client: LLM client used for summarization.
        summary_cache_dict: Shared cache mapping code-hash -> summary text.
        file_path: Relative path of the file (for the progress message).

    Returns:
        The summary blocks and the runs kept, joined by line breaks. Returns the
        original source unchanged when no run is replaced.
    """
    line_list = source_code.split("\n")
    range_list = _piece_range_list(line_list, boundary_index_set)
    code_list = ["\n".join(line_list[start:end]) for start, end in range_list]
    is_long_list = [
        line_count >= CODE_SUMMARY_PIECE_LINES or line_count > CODE_SUMMARY_TRIGGER_LINES
        for line_count in map(_line_count, code_list)
    ]
    _log_summary_start(sum(is_long_list), "runs of lines", file_path)

    out_list: list[str] = []
    has_summary = False
    for index, code in enumerate(code_list):
        if is_long_list[index]:
            name = _CODE_PIECE_NAME.format(index=index + 1, count=len(range_list))
            summary_block = "\n".join([
                _CODE_SUMMARY_MARKER.format(name=name),
                await _summarize_code(code, name, llm_client, summary_cache_dict, is_piece=True),
            ])
            if len(summary_block) < len(code):
                out_list.append(summary_block)
                has_summary = True
                continue
        out_list.append(code)

    return "\n".join(out_list) if has_summary else source_code


def _implementation_file_rel(file_rel: str, file_set: Container[str]) -> str | None:
    """Return the implementation file (.cpp/.c etc.) of a C/C++ header file.

    The implementation file is in the directory of the header and has its base name and
    the first extension of C_IMPL_EXT_TUPLE for which file_set holds such a file.

    Examples:
        ("src/a.h", {"src/a.h", "src/a.c"}) -> "src/a.c"
        ("src/a.c", {"src/a.h", "src/a.c"}) -> None

    Args:
        file_rel: Relative path of the target file.
        file_set: Relative paths of the files of the project.

    Returns:
        Relative path of the implementation file. None for a file that is no header,
        and for a header without one.
    """
    stem, ext = os.path.splitext(file_rel)
    if ext not in C_HEADER_EXT_TUPLE:
        return None
    for impl_ext in C_IMPL_EXT_TUPLE:
        if stem + impl_ext in file_set:
            return stem + impl_ext
    return None


def _implementation_definition_list(impl_file: str) -> list[dict]:
    """Return the definitions of the implementation file corresponding to a header file.

    Args:
        impl_file: Path of the copy of the implementation file.

    Returns:
        definitions[] of the file_dependencies.json written beside the copy of the
        implementation file. Empty when there is no such JSON file.
    """
    file_dep_dict = load_file_dependencies(os.path.dirname(impl_file))
    if file_dep_dict is None:
        return []
    return file_dep_dict.get("definitions", [])


@dataclass(frozen=True)
class _SectionInput:
    """What the prompt of a section is built from."""

    source_code: str                    # Source of the target file
    file_dep_dict: dict                 # Contents of its file_dependencies.json
    callee_context: str                 # Dependency design-document summaries (may be empty)
    implementation_context: str = ""    # For header files: source of the implementation file


class _PromptReducer:
    """The input of the section prompts of one file, reduced stage by stage on context-window overflow.

    section_input starts as the full input. Each advance() moves to the next stage
    that makes it smaller; the stages are cumulative:
      1. drop caller usage_context bodies                               (no LLM)
      2. drop dependency doc summaries (callee_context)                 (no LLM)
      3. summarize large callee dependency symbols                      (LLM, cached)
      4. drop callee dependency symbol bodies                           (no LLM)
      5. drop the lists of callee and caller usages                     (no LLM)
      6. summarize large definitions of the implementation file         (LLM, cached)
      7. drop the implementation file                                   (no LLM)
      8. summarize large definitions in the source                      (LLM, cached)
      9. summarize the source piece by piece, again while it gets smaller (LLM, cached)
    The LLM stages are left out when ENABLE_CODE_SUMMARY is False.
    """

    def __init__(
        self,
        section_input: _SectionInput,
        llm_client: LLMClient,
        summary_cache_dict: dict[str, str],
        implementation_definition_list: list[dict] | None = None,
        file_path: str = "",
    ) -> None:
        """Start at the full input, with the stages that ENABLE_CODE_SUMMARY allows.

        Args:
            section_input: The full input of the section prompts of the file.
            llm_client: LLM client used for summarization.
            summary_cache_dict: Shared cache mapping code-hash -> summary text.
            implementation_definition_list: For header files. definitions[] of the
                implementation file.
            file_path: Relative path of the file (for the progress messages).
        """
        self._file_path = file_path
        self.section_input = section_input
        # Label of the stage section_input is at
        self.stage_label = "full"
        self._llm_client = llm_client
        self._summary_cache_dict = summary_cache_dict
        self._implementation_definition_list = implementation_definition_list or []
        # Indexes of the lines of the source the definitions of the file start on
        self._source_boundary_index_set = _definition_start_index_set(
            section_input.file_dep_dict.get("definitions", [])
        )
        stage_list: list[tuple[str, Callable[[], Awaitable[_SectionInput]], bool]] = [
            ("drop caller bodies", self._drop_caller_body, False),
            ("drop callee context", self._drop_callee_context, False),
            ("summarize callee usages", self._summarize_callee_body, True),
            ("drop callee bodies", self._drop_callee_body, False),
            ("drop usage lists", self._drop_usage_list, False),
            ("summarize implementation defs", self._summarize_implementation_definition, True),
            ("drop implementation file", self._drop_implementation, False),
            ("summarize source defs", self._summarize_source_definition, True),
            ("summarize source pieces", self._summarize_source_piece, True),
        ]
        self._stage_list = [
            (label, reduce) for label, reduce, is_llm in stage_list
            if ENABLE_CODE_SUMMARY or not is_llm
        ]
        self._stage_index = 0

    async def advance(self) -> bool:
        """Move section_input to the next stage that makes it smaller.

        The last stage is tried again as long as it makes the input smaller.

        Returns:
            False when no stage is left that makes the input smaller.

        Raises:
            _LlmCallError: When a summary call of a stage fails for another reason
                than the context window; the stage is tried again by the next call.
        """
        while self._stage_index < len(self._stage_list):
            label, reduce = self._stage_list[self._stage_index]
            next_input = await reduce()
            is_last = self._stage_index == len(self._stage_list) - 1
            if next_input is self.section_input:
                self._stage_index += 1
                continue
            if not is_last:
                self._stage_index += 1
            self.section_input, self.stage_label = next_input, label
            return True
        return False

    def _with_file_dep_dict(self, file_dep_dict: dict) -> _SectionInput:
        """Return the input with file_dep_dict; the input itself when it already has it."""
        if file_dep_dict is self.section_input.file_dep_dict:
            return self.section_input
        return replace(self.section_input, file_dep_dict=file_dep_dict)

    async def _drop_caller_body(self) -> _SectionInput:
        """Return the input without the caller usage_context bodies."""
        return self._with_file_dep_dict(_without_usage_context(
            self.section_input.file_dep_dict, "caller_usages", "usage_context",
        ))

    async def _drop_callee_context(self) -> _SectionInput:
        """Return the input without the dependency doc summaries."""
        if not self.section_input.callee_context:
            return self.section_input
        return replace(self.section_input, callee_context="")

    async def _summarize_callee_body(self) -> _SectionInput:
        """Return the input with the large callee dependency symbols summarized."""
        return self._with_file_dep_dict(await _summarize_callee_usage(
            self.section_input.file_dep_dict, self._llm_client, self._summary_cache_dict,
            self._file_path,
        ))

    async def _drop_callee_body(self) -> _SectionInput:
        """Return the input without the callee target_context bodies."""
        return self._with_file_dep_dict(_without_usage_context(
            self.section_input.file_dep_dict, "callee_usages", "target_context",
        ))

    async def _summarize_implementation_definition(self) -> _SectionInput:
        """Return the input with the large definitions of the implementation file summarized."""
        implementation_context = self.section_input.implementation_context
        if not implementation_context:
            return self.section_input
        short_context, _ = await _splice_large_definition(
            implementation_context, self._implementation_definition_list,
            self._llm_client, self._summary_cache_dict, self._file_path,
        )
        if short_context == implementation_context:
            return self.section_input
        return replace(self.section_input, implementation_context=short_context)

    async def _drop_implementation(self) -> _SectionInput:
        """Return the input without the implementation file."""
        if not self.section_input.implementation_context:
            return self.section_input
        return replace(self.section_input, implementation_context="")

    async def _summarize_source_definition(self) -> _SectionInput:
        """Return the input with the large definitions in the source summarized."""
        source_code = self.section_input.source_code
        short_code, boundary_index_set = await _splice_large_definition(
            source_code, self.section_input.file_dep_dict.get("definitions", []),
            self._llm_client, self._summary_cache_dict, self._file_path,
        )
        if short_code == source_code:
            return self.section_input
        self._source_boundary_index_set = boundary_index_set
        return replace(self.section_input, source_code=short_code)

    async def _drop_usage_list(self) -> _SectionInput:
        """Return the input without the lists of callee and caller usages."""
        file_dep_dict = self.section_input.file_dep_dict
        if not file_dep_dict.get("callee_usages") and not file_dep_dict.get("caller_usages"):
            return self.section_input
        return replace(
            self.section_input,
            file_dep_dict={**file_dep_dict, "callee_usages": [], "caller_usages": []},
        )

    async def _summarize_source_piece(self) -> _SectionInput:
        """Return the input with the source summarized piece by piece."""
        source_code = self.section_input.source_code
        short_code = await _summarize_source_by_piece(
            source_code, self._source_boundary_index_set, self._llm_client,
            self._summary_cache_dict, self._file_path,
        )
        if len(short_code) >= len(source_code):
            return self.section_input
        self._source_boundary_index_set = set()
        return replace(self.section_input, source_code=short_code)


async def _generate_section(
    section: dict,
    prompt_reducer: _PromptReducer,
    file_path: str,
    llm_client: LLMClient,
) -> str | None:
    """Generate one section, reducing the prompt on context-window overflow.

    The prompt is built from the input prompt_reducer is at. On
    ContextWindowExceededError the reducer is advanced and the section is tried
    again; the reducer stays at the stage that fits for the sections after this one.
    A call that fails for any other reason, for the section or for a summary a stage
    needs, ends the section without advancing.

    Args:
        section: One section definition from the template.
        prompt_reducer: The input of the section prompts of the file.
        file_path: Relative path of the target file.
        llm_client: LLM client.

    Returns:
        Generated section text. None when the call fails, or when the prompt exceeds
        the context window at every stage.
    """
    while True:
        section_input = prompt_reducer.section_input
        prompt = _build_section_prompt(
            section, section_input.source_code, section_input.file_dep_dict,
            section_input.callee_context, section_input.implementation_context,
        )
        try:
            section_text = await llm_client.generate(prompt)
        except ContextWindowExceededError:
            logger.warning(
                f"Context exceeded ({prompt_reducer.stage_label}): {file_path}/{section['id']}. "
                f"Falling back to next reduction stage."
            )
            try:
                has_next_input = await prompt_reducer.advance()
            except _LlmCallError:
                logger.warning(f"LLM call failed (code summary): {file_path}/{section['id']}")
                return None
            if not has_next_input:
                logger.warning(
                    f"Context exceeded at every reduction stage: {file_path}/{section['id']}"
                )
                return None
            continue
        if section_text is None:
            logger.warning(f"LLM call failed: {file_path}/{section['id']}")
        return section_text


async def _generate_file_doc(
    file_rel: str,
    file_output_dir: str,
    doc_summary_dict: dict[str, str],
    template: dict,
    llm_client: LLMClient,
    summary_cache_dict: dict[str, str],
    impl_file: str | None = None,
) -> dict | None:
    """Generate a design document for one file.

    Read the original file copy and file_dependencies.json from file_output_dir,
    and generate text for each template section via the LLM.
    Handle context window exceeded errors with progressive fallback
    (_PromptReducer); a section starts from the stage the section before it fit at.

    Args:
        file_rel: Relative path from the project root (e.g. "src/foo.py").
        file_output_dir: Output directory for this file (source copy and JSON are stored here).
        doc_summary_dict: Design document summaries of processed files, keyed by file
            relative path (for callee context reference).
        template: Template dict.
        llm_client: LLM client.
        summary_cache_dict: Shared cache mapping code-hash -> summary text
            (context-overflow fallback).
        impl_file: For header files. Path of the copy of the implementation file
            (_implementation_file_rel), or None.

    Returns:
        Design document dict ({file, sections, summary, source_hash}), or None when the
        copy of the source or file_dependencies.json is not in file_output_dir, or when
        no section is generated. source_hash is the SHA256 of the source the document
        was generated from. summary is empty when the template has no summary_prompt
        and when the summary cannot be generated (_generate_summary).
    """
    # Read the source code
    source_file = _find_source_file(file_output_dir, file_rel)
    if not source_file:
        logger.warning(f"Source file not found: {file_output_dir}")
        return None

    source_code = read_source_text(source_file)

    # Read file_dependencies.json
    file_dep_dict = load_file_dependencies(file_output_dir)
    if file_dep_dict is None:
        logger.warning(
            f"file_dependencies.json not found: "
            f"{os.path.join(file_output_dir, DEPENDENCY_FILE_NAME)}"
        )
        return None

    # The full input of the section prompts: the source, the dependency information,
    # the dependency doc summaries and, for header files, the implementation file
    prompt_reducer = _PromptReducer(
        _SectionInput(
            source_code, file_dep_dict,
            _build_callee_context_summary(file_dep_dict, doc_summary_dict),
            read_source_text(impl_file) if impl_file else "",
        ),
        llm_client, summary_cache_dict,
        _implementation_definition_list(impl_file) if impl_file else [], file_rel,
    )

    # Generate each section
    section_list: list[dict] = []

    for section in template["sections"]:
        section_text = await _generate_section(section, prompt_reducer, file_rel, llm_client)

        if section_text is None:
            logger.warning(f"Failed to generate section '{section['title']}': {file_rel}")
            continue

        section_list.append({
            "id": section["id"],
            "title": section["title"],
            "content": _without_title_heading(section_text, section["title"]),
        })

    if not section_list:
        logger.error(f"Design document generation completely failed: {file_rel}")
        return None

    # Generate summary when the template asks for one
    summary = (
        await _generate_summary(file_rel, section_list, template["summary_prompt"], llm_client)
        if "summary_prompt" in template else ""
    )

    return {
        "file": file_rel,
        "sections": section_list,
        "summary": _without_title_heading(summary or "", _SUMMARY_TITLE),
        "source_hash": compute_file_hash(source_file),
    }


async def _generate_summary(
    file_path: str,
    section_list: list[dict],
    summary_prompt: str,
    llm_client: LLMClient,
) -> str | None:
    """Generate a summary from all sections of the design document.

    When the prompt exceeds the context window, each section is summarized by itself
    first (in halves while it still exceeds it, _generate_by_half) and the summary
    is generated from those.

    Args:
        file_path: Relative path of the target file.
        section_list: List of already-generated sections.
        summary_prompt: The summary_prompt of the template.
        llm_client: LLM client.

    Returns:
        Summary text, or None on failure.
    """
    def build_prompt(summary_section_list: list[dict]) -> str:
        """Return the summary prompt over the sections given."""
        return _build_summary_prompt(
            file_path, summary_section_list, summary_prompt, SUMMARY_MAX_CHARS,
        )

    try:
        try:
            return await llm_client.generate(build_prompt(section_list))
        except ContextWindowExceededError:
            logger.warning(f"Context exceeded (summary): {file_path}. Summarizing section by section.")

        short_section_list: list[dict] = []
        for section in section_list:
            short_content = await _generate_by_half(
                section["content"],
                lambda content, _, section=section: build_prompt([{**section, "content": content}]),
                llm_client, SUMMARY_MAX_CHARS,
            )
            if not short_content:
                logger.warning(f"Failed to generate summary: {file_path}")
                return None
            short_section_list.append({**section, "content": short_content})
        return await llm_client.generate(build_prompt(short_section_list))
    except Exception as e:
        logger.warning(f"Failed to generate summary: {file_path}: {e}")
        return None


def _find_source_file(output_dir: str, file_rel: str) -> str | None:
    """Find the path of the copied source file in the output directory.

    Args:
        output_dir: Output directory for the file.
        file_rel: Relative path of the target file.

    Returns:
        Absolute path of the found source file, or None if not found.
    """
    file_name = os.path.basename(file_rel)
    source_path = os.path.join(output_dir, file_name)
    if os.path.exists(source_path):
        return source_path
    return None


def _without_title_heading(content: str, title: str) -> str:
    """Remove a heading line holding only the title from the start of a section text or summary.

    The heading may be of any level and may end with "#"s; the blank lines after it are
    removed too. A heading holding more than the title is kept.

    Examples:
        ("## Data Flow\\n\\nThe input is read.", "Data Flow") -> "The input is read."
        ("## Data Flows\\nThe input is read.", "Data Flow")   -> "## Data Flows\\nThe input is read."

    Args:
        content: Section text or summary generated by the LLM.
        title: Title of the section, or "Summary" for the summary.

    Returns:
        The text without the heading.
    """
    return re.sub(
        r"\A\s*#+[^\S\n]+" + re.escape(title) + r"[^\S\n]*#*[^\S\n]*(?:\n|\Z)(?:[^\S\n]*\n)*",
        "",
        content,
    )


def _save_doc(doc: dict, output_dir: str) -> None:
    """Save a design document to file in both JSON and Markdown formats.

    Args:
        doc: Design document dict ({file, sections, summary, source_hash}).
        output_dir: Output directory.
    """
    # doc.md is written first and doc.json after it
    md_path = os.path.join(output_dir, DOC_MD_NAME)

    md_line_list = [f"# Design Document: {doc['file']}", ""]

    # Add heading and content for each section in Markdown format
    for section in doc["sections"]:
        md_line_list.append(f"# {section['title']}")
        md_line_list.append("")
        md_line_list.append(section["content"])
        md_line_list.append("")

    # Append summary as a section at the end if present
    if doc.get("summary"):
        md_line_list.append(f"# {_SUMMARY_TITLE}")
        md_line_list.append("")
        md_line_list.append(doc["summary"])
        md_line_list.append("")

    # Write to Markdown file
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_line_list))

    # JSON output
    _write_doc_json(doc, os.path.join(output_dir, DOC_JSON_NAME))


def _is_doc_form(doc: dict) -> bool:
    """Tell whether a dict read from doc.json has the form _save_doc writes.

    Args:
        doc: The dict read from doc.json (load_doc).

    Returns:
        True when "file" is a string, "sections" is a list of dicts with string "id",
        "title" and "content", and "summary" and "source_hash" are strings where present.
    """
    section_list = doc.get("sections")
    if not isinstance(doc.get("file"), str) or not isinstance(section_list, list):
        return False
    if not all(
        isinstance(section, dict)
        and all(isinstance(section.get(key), str) for key in ("id", "title", "content"))
        for section in section_list
    ):
        return False
    return all(isinstance(doc.get(key, ""), str) for key in ("summary", "source_hash"))


def _load_valid_doc(output_dir: str) -> dict | None:
    """Read the design document saved in a file's output directory.

    Args:
        output_dir: Output directory of the file.

    Returns:
        The design document dict; None when doc.json does not exist, cannot be read, or
        does not have the form _save_doc writes (_is_doc_form).
    """
    doc = load_doc(output_dir)
    return doc if doc is not None and _is_doc_form(doc) else None


def _write_doc_json(doc: dict, json_path: str) -> None:
    """Write a design document dict to a doc.json file.

    Args:
        doc: Design document dict.
        json_path: Path of the doc.json file.
    """
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)


def _md_heading_pattern(title: str) -> re.Pattern:
    """Return the pattern of the "# <title>" line _save_doc writes for a section."""
    return re.compile(r"^# " + re.escape(title) + r"[^\S\n]*$", re.MULTILINE)


def _md_section_split(md_text: str, doc: dict) -> tuple[list[str | None], str | None]:
    """Find the headings _save_doc writes for a document in doc.md and return the text under each.

    The headings are searched for in the order _save_doc writes them: a "# <title>" line
    for each section of doc, then a "# Summary" line when doc holds a summary. Each
    heading is searched for after the heading found before it, past as many lines of
    its form as the texts of doc between the two headings hold. A text runs from its
    heading to the next heading, and the last one to the end of doc.md.

    Args:
        md_text: Full text of doc.md.
        doc: The design document read from doc.json (_load_valid_doc).

    Returns:
        (the text of each section, stripped, or None when its heading or the heading
        after it is not found; the text of the summary, or None when its heading is not
        found or doc holds no summary).
    """
    part_list = [(section["title"], section["content"]) for section in doc["sections"]]
    if doc.get("summary"):
        part_list.append((_SUMMARY_TITLE, doc["summary"]))

    # Heading of each part, and the doc texts since the heading found before it
    match_list: list[re.Match | None] = []
    position = 0
    between_list: list[str] = []
    for title, content in part_list:
        pattern = _md_heading_pattern(title)
        skip_count = sum(len(pattern.findall(text)) for text in between_list)
        match = next(islice(pattern.finditer(md_text, position), skip_count, None), None)
        match_list.append(match)
        if match:
            position = match.end()
            between_list = []
        between_list.append(content)

    # Text of each part, up to the heading after it
    text_list: list[str | None] = []
    for index, match in enumerate(match_list):
        if index + 1 == len(match_list):
            end = len(md_text)
        else:
            end_match = match_list[index + 1]
            end = end_match.start() if end_match else None
        text_list.append(md_text[match.end():end].strip() if match and end is not None else None)
    md_summary = text_list.pop() if doc.get("summary") else None
    return text_list, md_summary


def _is_md_newer(output_dir: str) -> bool:
    """Tell whether the doc.md of a file was written after its doc.json.

    Args:
        output_dir: Directory containing doc.json and doc.md.

    Returns:
        True when both files exist and doc.md has the newer timestamp.
    """
    json_path = os.path.join(output_dir, DOC_JSON_NAME)
    md_path = os.path.join(output_dir, DOC_MD_NAME)
    if not os.path.exists(json_path) or not os.path.exists(md_path):
        return False
    return os.path.getmtime(md_path) > os.path.getmtime(json_path)


def _read_md_edit(output_dir: str) -> tuple[dict | None, list[str]] | None:
    """Read the edits of a doc.md written after its doc.json.

    The section texts and the summary of doc.md are found by the headings of the
    doc.json document (_md_section_split) and compared with it, stripped.

    Args:
        output_dir: Directory containing doc.json and doc.md.

    Returns:
        (the doc.json document with the texts found in doc.md in place of its section
        texts and summary, or None when no text found differs from doc.json; the titles
        of the sections, and "Summary", whose text is not found), or None when doc.md is
        not newer than doc.json (_is_md_newer) or doc.json cannot be read as a design
        document (_load_valid_doc).

    Raises:
        OSError: When doc.md cannot be read.
        UnicodeDecodeError: When doc.md is not UTF-8 text.
    """
    if not _is_md_newer(output_dir):
        return None
    doc = _load_valid_doc(output_dir)
    if doc is None:
        return None
    with open(os.path.join(output_dir, DOC_MD_NAME), "r", encoding="utf-8") as f:
        md_text = f.read()

    # Section texts and summary of doc.md, by the headings of doc.json
    summary = doc.get("summary", "")
    content_list, md_summary = _md_section_split(md_text, doc)
    skip_title_list = [
        section["title"] for section, content in zip(doc["sections"], content_list)
        if content is None
    ]
    if md_summary is None and summary:
        skip_title_list.append(_SUMMARY_TITLE)

    has_diff = (md_summary is not None and md_summary != summary.strip()) or any(
        content is not None and content != section["content"].strip()
        for section, content in zip(doc["sections"], content_list)
    )
    if not has_diff:
        return None, skip_title_list
    new_doc = {
        **doc,
        "sections": [
            section if content is None else {**section, "content": content}
            for section, content in zip(doc["sections"], content_list)
        ],
        "summary": summary if md_summary is None else md_summary,
    }
    return new_doc, skip_title_list


def _has_md_edit(output_dir: str) -> bool:
    """Tell whether the doc.md of a file holds edits not in its doc.json.

    Args:
        output_dir: Directory containing doc.json and doc.md.

    Returns:
        True when doc.md is newer than doc.json and differs from it or lacks a heading
        (_read_md_edit), or cannot be read as UTF-8 text.
    """
    try:
        md_edit = _read_md_edit(output_dir)
    except (OSError, UnicodeDecodeError):
        return True
    return md_edit is not None and (md_edit[0] is not None or bool(md_edit[1]))


def _log_md_not_taken(file_rel: str, error: Exception) -> None:
    """Log a warning that the edits of the doc.md of a file are not taken, with the error."""
    logger.warning(f"  The edits of {DOC_MD_NAME} are not taken: {file_rel}: {error}")


def _sync_md_to_json(output_dir: str, file_rel: str) -> None:
    """Sync manual edits from doc.md back to doc.json when doc.md is newer.

    The section texts and the summary that differ in doc.md (_read_md_edit) are written
    to doc.json, and doc.md is written again from the result (_save_doc). A section
    whose heading or the heading after it is missing from doc.md keeps its doc.json text
    and is named in a warning; doc.md is then left as it is. A doc.md that cannot be
    read as UTF-8 text is named in a warning and doc.json is left as it is.

    Args:
        output_dir: Directory containing doc.json and doc.md.
        file_rel: Relative path of the file (for the log messages).

    Raises:
        OSError: When doc.json or doc.md cannot be written.
    """
    try:
        md_edit = _read_md_edit(output_dir)
    except (OSError, UnicodeDecodeError) as e:
        _log_md_not_taken(file_rel, e)
        return
    if md_edit is None:
        return
    new_doc, skip_title_list = md_edit
    if skip_title_list:
        logger.warning(
            f"  {DOC_MD_NAME} lacks a section heading next to these sections, and their "
            f"edits are not taken: {', '.join(skip_title_list)}: {file_rel}"
        )
    if new_doc is None:
        return

    if skip_title_list:
        _write_doc_json(new_doc, os.path.join(output_dir, DOC_JSON_NAME))
    else:
        _save_doc(new_doc, output_dir)
    logger.info(f"  MD->JSON sync: {file_rel}")


def sync_md_edit(base_output_dir: str, file_list: list[str]) -> None:
    """Sync the manual edits of the doc.md of each file to its doc.json (_sync_md_to_json).

    A file whose doc.md cannot be read, or whose doc.json or doc.md cannot be written,
    is named in a warning and the other files are synced.

    Args:
        base_output_dir: Base output directory for file_dependencies.
        file_list: Relative paths of the files from the project root.
    """
    for file_rel in file_list:
        try:
            _sync_md_to_json(resolve_file_output_dir(base_output_dir, file_rel), file_rel)
        except OSError as e:
            _log_md_not_taken(file_rel, e)


def _is_doc_complete(doc: dict, template: dict) -> bool:
    """Check whether a design document contains all expected sections and summary.

    Args:
        doc: A design document dict.
        template: Template dict.

    Returns:
        False if any template section is missing/extra or if the summary is empty
        (when the template has a summary_prompt), True otherwise.
    """
    expected_id_set = {section["id"] for section in template["sections"]}
    actual_id_set = {section["id"] for section in doc.get("sections", [])}
    if expected_id_set != actual_id_set:
        return False
    if "summary_prompt" in template and not doc.get("summary"):
        return False
    return True


def _dependent_closure_set(
    change_file_set: set[str], callee_set_dict: dict[str, set[str]],
) -> set[str]:
    """Return the changed files and every file that depends on one, directly or through others.

    Args:
        change_file_set: Relative paths of the changed files.
        callee_set_dict: File relative path -> relative paths of its callees.

    Returns:
        The relative paths.
    """
    dependent_dict: dict[str, set[str]] = {}
    for file_rel, callee_set in callee_set_dict.items():
        for callee in callee_set:
            dependent_dict.setdefault(callee, set()).add(file_rel)
    closure_set = set(change_file_set)
    queue_list = list(change_file_set)
    while queue_list:
        for dependent in dependent_dict.get(queue_list.pop(), ()):
            if dependent not in closure_set:
                closure_set.add(dependent)
                queue_list.append(dependent)
    return closure_set


def _implementation_dict(callee_set_dict: dict[str, set[str]]) -> dict[str, str]:
    """Return the implementation file of each C/C++ header having one (_implementation_file_rel).

    Args:
        callee_set_dict: File relative path -> relative paths of its callees; every file
            of the run is a key.

    Returns:
        Header file relative path -> relative path of its implementation file.
    """
    implementation_dict: dict[str, str] = {}
    for file_rel in callee_set_dict:
        implementation_rel = _implementation_file_rel(file_rel, callee_set_dict)
        if implementation_rel:
            implementation_dict[file_rel] = implementation_rel
    return implementation_dict


def _regeneration_set(
    changed_files: set[str],
    callee_set_dict: dict[str, set[str]],
    implementation_dict: dict[str, str],
) -> set[str]:
    """Return the files whose design documents are regenerated for the changed files.

    Args:
        changed_files: Relative paths of the changed files.
        callee_set_dict: File relative path -> relative paths of its callees.
        implementation_dict: Header file relative path -> relative path of its
            implementation file (_implementation_dict).

    Returns:
        The changed files, the headers whose implementation file changed, and the files
        depending on them, directly or through others (_dependent_closure_set).
    """
    change_file_set = set(changed_files) | {
        header for header, implementation_rel in implementation_dict.items()
        if implementation_rel in changed_files
    }
    return _dependent_closure_set(change_file_set, callee_set_dict)


def _needs_regeneration(
    file_rel: str,
    regeneration_set: set[str] | None,
    callee_set_dict: dict[str, set[str]],
    earlier_doc_file_set: set[str],
) -> bool:
    """Determine whether the design document needs regeneration.

    Regeneration is needed if any of the following conditions are met:
    - regeneration_set is not specified (full regeneration mode).
    - The file is in regeneration_set (_dependent_closure_set of the changed files).
    - Any of the file's callees (dependencies) is in earlier_doc_file_set.

    Args:
        file_rel: Relative path of the file.
        regeneration_set: The changed files and the files depending on them, or None.
        callee_set_dict: File relative path -> relative paths of its callees.
        earlier_doc_file_set: Files whose documents were regenerated at the earlier levels
            of this run.

    Returns:
        True if regeneration is needed.
    """
    if regeneration_set is None or file_rel in regeneration_set:
        return True
    return not callee_set_dict.get(file_rel, set()).isdisjoint(earlier_doc_file_set)


def _reusable_doc(file_rel: str, output_dir: str, template: dict) -> dict | None:
    """Return the saved design document of a file when it is complete.

    Manual edits of doc.md are synced to doc.json first (_sync_md_to_json). A complete
    document is reported as REUSE, an incomplete one as INCOMPLETE.

    Args:
        file_rel: Relative path from the project root.
        output_dir: Output directory of the file.
        template: Template dict.

    Returns:
        The document read from doc.json; None when doc.json is missing or unreadable
        (_load_valid_doc), or the document is incomplete (_is_doc_complete).
    """
    # Sync manual edits from doc.md to JSON if user edited it
    _sync_md_to_json(output_dir, file_rel)
    old_doc = _load_valid_doc(output_dir)
    # Regenerate when doc.json is missing or unreadable
    if old_doc is None:
        if os.path.exists(os.path.join(output_dir, DOC_JSON_NAME)):
            logger.warning(
                f"  {DOC_JSON_NAME} cannot be read as a design document and is "
                f"regenerated: {file_rel}"
            )
        return None
    if _is_doc_complete(old_doc, template):
        log_progress(logger, f"  REUSE: {file_rel}")
        return old_doc
    log_progress(logger, f"  INCOMPLETE: {file_rel}")
    return None


async def _generate_level(
    file_list: list[str],
    process_one: Callable[[str], Awaitable[dict | None]],
    max_workers: int,
    template: dict,
    doc_summary_dict: dict[str, str],
    doc_fail_list: list[str],
) -> None:
    """Generate the design documents of the files of one level, max_workers files at a time.

    Each file of the level left without a complete document (an exception, no document,
    or _is_doc_complete False) is appended to doc_fail_list. Once every file of the level
    is done, the summary of each document of the level is put in doc_summary_dict.

    Args:
        file_list: Relative paths of the files of the level.
        process_one: Returns the design document dict of a file, or None.
        max_workers: Number of files processed in parallel.
        template: Template dict.
        doc_summary_dict: File relative path -> design document summary; updated in place.
        doc_fail_list: Files left without a complete design document; appended to in place.
    """
    # Summaries of the documents of this level
    level_summary_dict: dict[str, str] = {}

    # Process files in the level in batches of max_workers
    for batch_start in range(0, len(file_list), max_workers):
        batch_file_list = file_list[batch_start:batch_start + max_workers]

        task_list = [asyncio.create_task(process_one(file_rel)) for file_rel in batch_file_list]
        outcome_list = await asyncio.gather(*task_list, return_exceptions=True)

        for file_rel, doc in zip(batch_file_list, outcome_list):
            if isinstance(doc, Exception):
                logger.error(
                    f"Error during document generation: {file_rel}: "
                    f"{type(doc).__name__}: {doc}"
                )
                doc_fail_list.append(file_rel)
                continue
            if doc:
                level_summary_dict[file_rel] = doc.get("summary", "")
            if not doc or not _is_doc_complete(doc, template):
                doc_fail_list.append(file_rel)

    doc_summary_dict.update(level_summary_dict)


async def generate_all_docs(
    base_output_dir: str,
    project_dep_list: list[dict],
    llm_client: LLMClient,
    max_workers: int = MAX_WORKERS,
    changed_files: set[str] | None = None,
) -> list[str]:
    """Main function to generate design documents for all files in topological sort order.

    Processing flow:
    1. Load the template (_load_template).
    2. Topologically sort project_dependencies and arrange by level.
    3. Find the changed files and the files depending on them (with changed_files).
    4. Starting from level 0, generate documents for each level in parallel
       (_generate_level), save each file's document in JSON + Markdown format, and hold
       the document summaries in doc_summary_dict for use as context in subsequent levels.
    5. Report the numbers of documents generated, reused and left incomplete.

    When changed_files is specified, the existing doc.json of a file is reused and the
    LLM call is skipped when it is complete, the file is not among the changed files and
    the files depending on them (_dependent_closure_set), and none of its callees was
    regenerated at an earlier level of this run. A C/C++ header counts as changed when
    its implementation file (_implementation_file_rel) is among the changed files.

    Args:
        base_output_dir: Base output directory for file_dependencies.
        project_dep_list: The project's dependency list with paths relative to the
            project root; each element is {"file": str, "callers": list[str],
            "callees": list[str]}.
        llm_client: LLM client.
        max_workers: Number of parallel workers within each level.
        changed_files: Set of relative paths of changed files. If None, all files are processed.

    Returns:
        Relative paths of the files left without a complete design document
        (generation failed, or a section or the summary is missing).

    Raises:
        ValueError: When max_workers is less than 1, or the template cannot be read or
            has a wrong form (_load_template).
    """
    check_worker_count(max_workers)

    # == Step 1: Load the template ============================================
    template = _load_template()

    # == Step 2: Sort the files into levels ===================================
    # File relative path -> its callees (dependencies); every callee is a key too
    callee_set_dict = _callee_set_dict(project_dep_list)
    level_list = _topological_sort_by_level(callee_set_dict)
    level_count = len(level_list)
    total_file_count = sum(len(level) for level in level_list)

    log_progress(
        logger,
        f"Starting design document generation. "
        f"Dependency depth levels: {level_count}, "
        f"Total files: {total_file_count}"
    )

    # == Step 3: Find the files whose documents are regenerated ===============
    implementation_dict = _implementation_dict(callee_set_dict)
    regeneration_set = (
        None if changed_files is None
        else _regeneration_set(changed_files, callee_set_dict, implementation_dict)
    )

    # == Step 4: Generate the documents level by level ========================
    # Dict holding the summaries of processed files. Only the summary is carried
    # forward between levels; the section text is not kept after a document is saved.
    # Key: file relative path, Value: design document summary text
    doc_summary_dict: dict[str, str] = {}

    # Shared code-summary cache for the whole run (context-overflow fallback).
    # Key: SHA256 of a code block, Value: its behavior summary. Reused across
    # files and sections of the run.
    summary_cache_dict: dict[str, str] = {}

    # Track files whose documents were regenerated in this run, and those of them
    # regenerated at the levels before the current one. Caller-side files that reference
    # a file regenerated at an earlier level as a callee also become regeneration targets.
    new_doc_file_set: set[str] = set()
    earlier_doc_file_set: set[str] = set()

    # Files whose saved document was reused in this run
    reuse_file_set: set[str] = set()

    # Files left without a complete design document in this run
    doc_fail_list: list[str] = []

    async def process_one(file_rel: str) -> dict | None:
        """Generate or reuse the design document of one file.

        Args:
            file_rel: Relative path from the project root.

        Returns:
            The design document dict, or None on failure.
        """
        output_dir = resolve_file_output_dir(base_output_dir, file_rel)
        if not os.path.isdir(output_dir):
            logger.warning(f"Output directory does not exist: {output_dir}")
            return None
        is_md_edit = _has_md_edit(output_dir)

        # Reuse existing doc.json if no changes
        if not _needs_regeneration(
            file_rel, regeneration_set, callee_set_dict, earlier_doc_file_set,
        ):
            old_doc = _reusable_doc(file_rel, output_dir, template)
            if old_doc is not None:
                reuse_file_set.add(file_rel)
                return old_doc

        implementation_rel = implementation_dict.get(file_rel)
        doc = await _generate_file_doc(
            file_rel, output_dir, doc_summary_dict, template, llm_client, summary_cache_dict,
            _find_source_file(
                resolve_file_output_dir(base_output_dir, implementation_rel), implementation_rel,
            ) if implementation_rel else None,
        )
        if not doc:
            logger.warning(f"  SKIP: {file_rel}")
            return None
        _save_doc(doc, output_dir)
        new_doc_file_set.add(file_rel)
        log_progress(logger, f"  OK: {file_rel}")
        if is_md_edit:
            logger.warning(
                f"  The edits of {DOC_MD_NAME} are replaced by the regenerated document: "
                f"{file_rel}"
            )
        return doc

    for level_index, file_list in enumerate(level_list):
        log_progress(
            logger,
            f"{level_index + 1}/{level_count}: "
            f"Generating documents for {len(file_list)} files"
        )
        earlier_doc_file_set.update(new_doc_file_set)
        await _generate_level(
            file_list, process_one, max_workers, template, doc_summary_dict, doc_fail_list,
        )

    # == Step 5: Report the result ============================================
    doc_fail_set = set(doc_fail_list)
    log_progress(
        logger,
        f"Design document generation completed. "
        f"Generated: {len(new_doc_file_set - doc_fail_set)}, "
        f"Reused: {len(reuse_file_set)}, "
        f"Incomplete or failed: {len(doc_fail_set)} / "
        f"Total: {total_file_count}"
    )

    return doc_fail_list
