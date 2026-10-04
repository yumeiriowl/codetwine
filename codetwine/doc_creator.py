import os
import json
import re
import hashlib
import asyncio
import logging
from bisect import bisect_left, bisect_right
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from codetwine.llm import ContextWindowExceededError
from codetwine.llm.client import LLMClient
from codetwine.utils.file_utils import (
    compute_file_hash,
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
)

logger = logging.getLogger(__name__)


# Prompt header: heading indicating the target file
HEADER_TARGET_FILE = "# Target File: {file}"

# Source code section heading
HEADER_SOURCE_CODE = "## Source Code"

# ===== Dependency (callee_usages) section =====

HEADER_CALLEE_USAGES = "## External Functions/Classes Used by This File (Dependencies)"

CALLEE_USAGES_SCHEMA_NOTE = (
    "* Schema: name=symbol name being used, from=file path where that symbol is defined\n"
    "* The 'dependency source code' shown below each symbol is the full source code "
    "of the dependency symbol (or, for large symbols, a concise behavior summary of it). "
    "Refer to it to understand what external code this target file depends on."
)

CALLEE_SOURCE_CODE_LABEL = "  Dependency Source Code:"

# ===== Dependent (caller_usages) section =====

HEADER_CALLER_USAGES = "## External Files Using This File (Dependents)"

CALLER_USAGES_SCHEMA_NOTE = (
    "* Schema: name=symbol name being used, from=file path of the file using it"
)

CALLER_SOURCE_CODE_LABEL = "  Usage Location Source Code:"

# ===== Dependency design document summary section =====

HEADER_CALLEE_CONTEXT = "## Design Document Summaries of Dependency Files"

CALLEE_CONTEXT_NOTE = (
    "* The following are summaries of previously generated design documents for each dependency file. "
    "Use them as reference information to understand the responsibilities and public interfaces "
    "of external modules that the target file depends on."
)

# ===== Request section =====

HEADER_REQUEST = "## Request"

# {title} is replaced with the section title (e.g. "Overview & Purpose")
SECTION_REQUEST_TEMPLATE = "Write the content for the \"{title}\" section following the instructions below."

# {language} is replaced with the output language (e.g. "Japanese")
OUTPUT_LANGUAGE_INSTRUCTION = "Write the output in {language}."

# Instruction to ensure consistency with source code
FACTUAL_ACCURACY_INSTRUCTION = (
    "\n[IMPORTANT] Do not describe information not found in the source code based on speculation. "
    "Descriptions that contradict the source code implementation are strictly prohibited. "
    "Write only based on the provided source code and dependency information."
)

# ===== Implementation file context (for header files) =====

HEADER_IMPL_CONTEXT = "## Corresponding Implementation File"

IMPL_CONTEXT_NOTE = (
    "* The following is the source code of the implementation (.cpp/.c) file "
    "corresponding to this header file. "
    "Refer to it to understand how the classes and functions declared in the header are implemented."
)

# ===== Summary prompt =====

HEADER_DOC_CONTENT = "## Design Document Content"

# {max_chars} is replaced with the maximum character count
SUMMARY_CHAR_LIMIT = "({max_chars} characters or fewer)"

# ===== Code behavior summary prompt (context-overflow fallback) =====

# {name} = symbol name, {max_chars} = character limit, {language} = output language.
# Used to shrink large code blocks while keeping their behavior information.
CODE_SUMMARY_PROMPT = (
    "Summarize the behavior of the following code symbol `{name}` in {max_chars} "
    "characters or fewer. Keep the signature (first line) as-is, then describe what "
    "it takes, what it does internally, and what it returns or its side effects. "
    "This is reference context for documenting another file that depends on it, so be "
    "concise and factual. Write the description in {language}.\n\n"
    "```\n{code}\n```"
)

# {name} = what the piece is, {max_chars} = character limit, {language} = output language.
# Used for a piece of code that is no whole symbol: a run of lines of a file, a half of a
# symbol that does not fit in one prompt.
CODE_PIECE_SUMMARY_PROMPT = (
    "Summarize the behavior of the following piece of source code ({name}) in "
    "{max_chars} characters or fewer. Name the functions, classes and variables it "
    "defines, and describe what each of them does. This is reference context for "
    "documenting the file it is taken from, so be concise and factual. Write the "
    "description in {language}.\n\n"
    "```\n{code}\n```"
)

# Placeholder header prefixed to a summarized code block in the prompt.
CODE_SUMMARY_MARKER = "# [summarized] {name}"

# Name of a half of a code block, and of a run of lines of a file, in the prompt and the marker
CODE_PART_NAME = "{name}, part"
CODE_PIECE_NAME = "part {index} of {count}"

# Characters of a line counted as one line by _line_count()
_LINE_WIDTH = 80

# Deterministic fallback body appended when summary generation fails.
CODE_SUMMARY_FAILED_NOTE = "# ...(body omitted; summary unavailable)"

# C/C++ header extension set
_HEADER_EXT_SET = {".h", ".hpp", ".hh", ".hxx"}
# Implementation file extensions paired with header extensions
_IMPL_EXT_LIST = ["cpp", "c", "cc", "cxx"]


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


def _topological_sort_by_level(project_dep_list: list[dict]) -> list[list[str]]:
    """Topologically sort the files of a dependency list and return them
    as a list grouped by level (dependency depth).

    Level 0 = files with no dependencies (processed first).
    Level N = files that depend only on files at level N-1 or below.

    If circular dependencies exist, files remaining from Kahn's algorithm
    are included in the last level, and a warning is logged.

    Args:
        project_dep_list: Dependency list whose paths are relative to the project root.
                          Each element is {"file": str, "callers": list, "callees": list}.

    Returns:
        A file list grouped by level. The outer list index is the level number.
        Example: [["config.py", "utils.py"], ["parser.py"], ["main.py"]]
    """
    # file -> files it depends on
    callee_dict: dict[str, set[str]] = {}
    all_file_set: set[str] = set()

    for dep_info in project_dep_list:
        file_path = dep_info["file"]
        all_file_set.add(file_path)
        callee_dict.setdefault(file_path, set())

        # Add callees (dependencies) to the adjacency list
        for callee in dep_info.get("callees", []):
            all_file_set.add(callee)
            callee_dict.setdefault(callee, set())
            callee_dict[file_path].add(callee)

    # file -> files depending on it, and the number of callees not yet placed in a level
    dependent_dict: dict[str, set[str]] = {f: set() for f in all_file_set}
    callee_count_dict: dict[str, int] = {f: 0 for f in all_file_set}

    for file_path, callee_set in callee_dict.items():
        for callee in callee_set:
            dependent_dict[callee].add(file_path)
            callee_count_dict[file_path] += 1

    # Execute BFS level by level
    level_list: list[list[str]] = []
    # First level: files without callees
    level_file_list = [f for f in all_file_set if callee_count_dict[f] == 0]
    level_file_set: set[str] = set()

    while level_file_list:
        level_file_list.sort()
        level_list.append(level_file_list)
        level_file_set.update(level_file_list)

        next_file_list: list[str] = []
        for file_path in level_file_list:
            for dependent in dependent_dict[file_path]:
                callee_count_dict[dependent] -= 1
                if callee_count_dict[dependent] == 0:
                    next_file_list.append(dependent)

        level_file_list = next_file_list

    # Add files not processed due to circular dependencies to the last level
    cycle_file_set = all_file_set - level_file_set
    if cycle_file_set:
        logger.warning(
            f"Circular dependencies detected. The following files will be processed at the last level: "
            f"{sorted(cycle_file_set)}"
        )
        level_list.append(sorted(cycle_file_set))

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
        header: Section heading (HEADER_CALLEE_USAGES / HEADER_CALLER_USAGES).
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
    file_deps: dict,
    callee_context: str,
    implementation_context: str = "",
) -> str:
    """Assemble the LLM prompt for one section.

    Args:
        section: One section definition from the template (dict with id, title, prompt).
        source_code: Full source code of the target file.
        file_deps: Contents of file_dependencies.json (definitions, callee_usages, caller_usages).
        callee_context: Text combining design document summaries of dependency files (may be empty string).
        implementation_context: For header files. Source code of the corresponding implementation file.

    Returns:
        The completed prompt string to send to the LLM.
    """
    # Basic prompt structure: target file name + source code
    part_list = [
        HEADER_TARGET_FILE.format(file=output_path_to_rel(file_deps.get('file', 'unknown'))),
        "",
        HEADER_SOURCE_CODE,
        "```",
        source_code,
        "```",
        "",
    ]

    # For header files, include the corresponding implementation file's source code
    if implementation_context:
        part_list.append(HEADER_IMPL_CONTEXT)
        part_list.append(IMPL_CONTEXT_NOTE)
        part_list.append("```")
        part_list.append(implementation_context)
        part_list.append("```")
        part_list.append("")

    # Dependencies with their source code (target_context)
    callee_usages = file_deps.get("callee_usages", [])
    if callee_usages:
        part_list.extend(_usage_part_list(
            callee_usages, HEADER_CALLEE_USAGES, CALLEE_USAGES_SCHEMA_NOTE,
            "from", "target_context", CALLEE_SOURCE_CODE_LABEL,
        ))

    # Dependents with the source code around each usage (usage_context)
    caller_usages = file_deps.get("caller_usages", [])
    if caller_usages:
        part_list.extend(_usage_part_list(
            caller_usages, HEADER_CALLER_USAGES, CALLER_USAGES_SCHEMA_NOTE,
            "file", "usage_context", CALLER_SOURCE_CODE_LABEL,
        ))

    # Add dependency file design document summaries as context
    if callee_context:
        part_list.append(HEADER_CALLEE_CONTEXT)
        part_list.append(CALLEE_CONTEXT_NOTE)
        part_list.append(callee_context)
        part_list.append("")

    # Add section-specific instructions
    part_list.append(HEADER_REQUEST)
    part_list.append(SECTION_REQUEST_TEMPLATE.format(title=section['title']))
    part_list.append(section["prompt"])
    # Append output language specification at the end
    part_list.append("\n" + OUTPUT_LANGUAGE_INSTRUCTION.format(language=OUTPUT_LANGUAGE))
    # Append source code consistency instruction at the end
    part_list.append(FACTUAL_ACCURACY_INSTRUCTION)

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
        summary_max_chars: Maximum character count for the summary.

    Returns:
        The completed prompt string to send to the LLM.
    """
    # Basic prompt structure: target file name + all section contents of the design document
    part_list = [
        HEADER_TARGET_FILE.format(file=file_path),
        "",
        HEADER_DOC_CONTENT,
    ]

    # Add each section's heading and content to the prompt
    for section in section_list:
        part_list.append(f"### {section['title']}")
        part_list.append(section["content"])
        part_list.append("")

    # Add summary instructions and character limit
    part_list.append(HEADER_REQUEST)
    part_list.append(summary_prompt)
    part_list.append(SUMMARY_CHAR_LIMIT.format(max_chars=summary_max_chars))
    part_list.append(OUTPUT_LANGUAGE_INSTRUCTION.format(language=OUTPUT_LANGUAGE))

    return "\n".join(part_list)


def _build_callee_context_summary(
    file_deps: dict,
    doc_summary_map: dict[str, str],
) -> str:
    """Concatenate the design document summaries of the dependency files into a single string.

    Args:
        file_deps: The target file's file_dependencies.json.
        doc_summary_map: A map of file relative path -> generated design document summary.

    Returns:
        Context text combining only the summaries.
    """
    # Create a deduplicated list of dependency files from callee_usages
    callee_set: set[str] = set()
    for usage in file_deps.get("callee_usages", []):
        from_file = usage.get("from")
        if from_file:
            callee_set.add(from_file)
    callee_file_list = sorted(callee_set)

    # Retrieve and concatenate summaries for each dependency file
    # callee_usages' from is in output format; doc_summary_map keys are source relative paths, so reverse-convert
    part_list = []
    for callee_file in callee_file_list:
        summary = doc_summary_map.get(output_path_to_rel(callee_file))
        if summary:
            part_list.append(f"- **{output_path_to_rel(callee_file)}**: {summary}")
    return "\n".join(part_list)


class LlmCallError(Exception):
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

    A line longer than _LINE_WIDTH characters counts as one line per _LINE_WIDTH
    characters, so code written on few long lines is as long as the same code on
    short ones.
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
        LlmCallError: When a call fails for another reason than the context window.
    """
    try:
        generated_text = await llm_client.generate(build_prompt(text, is_part))
    except ContextWindowExceededError:
        half_list = _half_list(text) if len(text) > min_chars else None
    else:
        if generated_text is None:
            raise LlmCallError("LLM call failed")
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
    summary_cache: dict[str, str],
    is_piece: bool = False,
) -> str:
    """Summarize a code block into a concise behavior description via the LLM.

    A summary is cached in summary_cache by the SHA256 of the code text. A code block
    whose prompt exceeds the context window is summarized in halves
    (_generate_by_half). When even a part that is not cut further exceeds it, the
    first line of the code followed by CODE_SUMMARY_FAILED_NOTE is returned and
    nothing is cached.

    Args:
        code: Full source of the code to summarize.
        name: Symbol name (used in the prompt and the placeholder marker).
        llm_client: LLM client used to generate the summary.
        summary_cache: Shared cache mapping code-hash -> summary text.
        is_piece: True for code that is no whole symbol (CODE_PIECE_SUMMARY_PROMPT).

    Returns:
        The behavior summary text (or the deterministic fallback).

    Raises:
        LlmCallError: When a call fails for another reason than the context window.
    """
    cache_key = hashlib.sha256(code.encode("utf-8")).hexdigest()
    if cache_key in summary_cache:
        return summary_cache[cache_key]

    def build_prompt(text: str, is_part: bool) -> str:
        """Return the summary prompt of the code, or of a part of it."""
        template = CODE_PIECE_SUMMARY_PROMPT if is_piece or is_part else CODE_SUMMARY_PROMPT
        return template.format(
            name=CODE_PART_NAME.format(name=name) if is_part else name,
            max_chars=CODE_SUMMARY_MAX_CHARS,
            language=OUTPUT_LANGUAGE,
            code=text,
        )

    summary = await _generate_by_half(code, build_prompt, llm_client, CODE_SUMMARY_MAX_CHARS)
    if summary:
        summary_cache[cache_key] = summary
        return summary

    # Fallback: the first line (signature) and a note
    first_line = code.split("\n", 1)[0]
    return f"{first_line}\n{CODE_SUMMARY_FAILED_NOTE}"


def _reduce_caller_usages(file_deps: dict) -> dict:
    """Return a shallow copy of file_deps with caller usage_context bodies removed.

    Keeps name / file / lines and drops the source snippets.

    Args:
        file_deps: The target file's file_dependencies.json contents.

    Returns:
        A shallow copy with each caller_usages entry stripped of usage_context;
        file_deps itself when no entry carries one.
    """
    return _without_usage_context(file_deps, "caller_usages", "usage_context")


def _without_callee_body(file_deps: dict) -> dict:
    """Return a shallow copy of file_deps with callee target_context bodies removed.

    Keeps name / from / lines and drops the source of the dependency symbols.

    Args:
        file_deps: The target file's file_dependencies.json contents.

    Returns:
        A shallow copy with each callee_usages entry stripped of target_context;
        file_deps itself when no entry carries one.
    """
    return _without_usage_context(file_deps, "callee_usages", "target_context")


def _without_usage_context(file_deps: dict, usage_key: str, context_key: str) -> dict:
    """Return file_deps without the source code its usages of one kind carry.

    Args:
        file_deps: The target file's file_dependencies.json contents.
        usage_key: "callee_usages" or "caller_usages".
        context_key: Key of the source code of a usage.

    Returns:
        A shallow copy whose usage_key entries have no context_key; file_deps itself
        when no entry carries one.
    """
    usage_list = file_deps.get(usage_key, [])
    if not any(usage.get(context_key) for usage in usage_list):
        return file_deps

    deps_copy = dict(file_deps)
    deps_copy[usage_key] = [
        {key: value for key, value in usage.items() if key != context_key}
        for usage in usage_list
    ]
    return deps_copy


async def _summarize_callee_usages(
    file_deps: dict,
    llm_client: LLMClient,
    summary_cache: dict[str, str],
    file_path: str = "",
) -> dict:
    """Return a copy of file_deps where large callee target_context is summarized.

    Only dependency symbols longer than CODE_SUMMARY_TRIGGER_LINES (_line_count) are
    replaced by an LLM behavior summary, and only by a summary shorter than the
    symbol; the others are kept verbatim.

    Args:
        file_deps: The target file's file_dependencies.json contents.
        llm_client: LLM client used for summarization.
        summary_cache: Shared cache mapping code-hash -> summary text.
        file_path: Relative path of the target file (for the progress message).

    Returns:
        A shallow copy with large callee_usages[].target_context summarized;
        file_deps itself when none is replaced.
    """
    callee_usage_list = file_deps.get("callee_usages", [])
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
                target_context, usage.get("name", "symbol"), llm_client, summary_cache
            )
            if len(summary) < len(target_context):
                usage = {**usage, "target_context": summary}
                has_summary = True
        usage_list.append(usage)
    if not has_summary:
        return file_deps

    deps_copy = dict(file_deps)
    deps_copy["callee_usages"] = usage_list
    return deps_copy


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
        name_list = name_list_dict.setdefault(line_range, [])
        if name not in name_list:
            name_list.append(name)
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


async def _splice_large_definitions(
    source_code: str,
    definition_list: list[dict],
    llm_client: LLMClient,
    summary_cache: dict[str, str],
    file_path: str = "",
) -> tuple[str, set[int]]:
    """Replace large definitions in the source with LLM behavior summaries.

    Line numbers are 1-based and match the source copy exactly, so the
    [start_line, end_line] range of each selected definition
    (_large_leaf_definition_list) is spliced out and replaced by a summary
    block when that block is shorter than the definition. Non-definition lines,
    small definitions and the lines of a definition around the definitions it holds
    are kept as-is.

    Args:
        source_code: Full source of the file (as read from its copy).
        definition_list: definitions[] from the file_dependencies.json of the file.
        llm_client: LLM client used for summarization.
        summary_cache: Shared cache mapping code-hash -> summary text.
        file_path: Relative path of the file (for the progress message).

    Returns:
        (the source with large definitions replaced by summary blocks, the 0-based
        indexes of the lines of that text the definitions start on). The source is
        the original one, unchanged, when no definition is replaced.
    """
    # split("\n") keeps 1-based mapping: source line N -> lines[N-1] (read_source_text() leaves
    # only "\n" line breaks, and its lines are the rows of the syntax tree)
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
            CODE_SUMMARY_MARKER.format(name=name),
            await _summarize_code(code, name, llm_client, summary_cache),
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
    summary_cache: dict[str, str],
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
        summary_cache: Shared cache mapping code-hash -> summary text.
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
            name = CODE_PIECE_NAME.format(index=index + 1, count=len(range_list))
            summary_block = "\n".join([
                CODE_SUMMARY_MARKER.format(name=name),
                await _summarize_code(code, name, llm_client, summary_cache, is_piece=True),
            ])
            if len(summary_block) < len(code):
                out_list.append(summary_block)
                has_summary = True
                continue
        out_list.append(code)

    return "\n".join(out_list) if has_summary else source_code


def _find_implementation_file(file_rel: str, file_output_dir: str) -> str | None:
    """Find the copy of the implementation file (.cpp/.c etc.) corresponding to a header file.

    Search for an implementation file with the same base name as the header
    in the same level of the output directory.

    Args:
        file_rel: Relative path of the target file (e.g. "MainWindow.h").
        file_output_dir: Output directory of the target file (e.g. ".../MainWindow_h/").

    Returns:
        Path of the copy of the implementation file. None if not found or non-header file.
    """
    _, ext = os.path.splitext(file_rel)
    if ext not in _HEADER_EXT_SET:
        return None

    stem = os.path.splitext(os.path.basename(file_rel))[0]
    base_dir = os.path.dirname(file_output_dir)

    for impl_ext in _IMPL_EXT_LIST:
        impl_dir = os.path.join(base_dir, f"{stem}_{impl_ext}")
        impl_file = os.path.join(impl_dir, f"{stem}.{impl_ext}")
        if os.path.isfile(impl_file):
            return impl_file

    return None


def _build_implementation_context(
    file_rel: str,
    file_output_dir: str,
) -> str:
    """Retrieve the source code of the implementation file (.cpp/.c etc.) corresponding to a header file.

    Args:
        file_rel: Relative path of the target file (e.g. "MainWindow.h").
        file_output_dir: Output directory of the target file (e.g. ".../MainWindow_h/").

    Returns:
        Source code text of the implementation file (_find_implementation_file).
        Empty string if not found or non-header file.
    """
    impl_file = _find_implementation_file(file_rel, file_output_dir)
    return read_source_text(impl_file) if impl_file else ""


def _implementation_definition_list(file_rel: str, file_output_dir: str) -> list[dict]:
    """Return the definitions of the implementation file corresponding to a header file.

    Args:
        file_rel: Relative path of the target file.
        file_output_dir: Output directory of the target file.

    Returns:
        definitions[] of the file_dependencies.json written beside the copy of the
        implementation file. Empty when there is no implementation file or no such
        JSON file.
    """
    impl_file = _find_implementation_file(file_rel, file_output_dir)
    if not impl_file:
        return []
    deps_path = os.path.join(os.path.dirname(impl_file), "file_dependencies.json")
    if not os.path.exists(deps_path):
        return []
    with open(deps_path, "r", encoding="utf-8") as f:
        return json.load(f).get("definitions", [])


@dataclass(frozen=True)
class _SectionInput:
    """What the prompt of a section is built from."""

    source_code: str                    # Source of the target file
    file_deps: dict                     # Contents of its file_dependencies.json
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
        summary_cache: dict[str, str],
        implementation_definition_list: list[dict] | None = None,
        file_path: str = "",
    ) -> None:
        """
        Args:
            section_input: The full input of the section prompts of the file.
            llm_client: LLM client used for summarization.
            summary_cache: Shared cache mapping code-hash -> summary text.
            implementation_definition_list: For header files. definitions[] of the
                implementation file.
            file_path: Relative path of the file (for the progress messages).
        """
        self._file_path = file_path
        self.section_input = section_input
        # Label of the stage section_input is at
        self.stage_label = "full"
        self._llm_client = llm_client
        self._summary_cache = summary_cache
        self._implementation_definition_list = implementation_definition_list or []
        # Indexes of the lines of the source the definitions of the file start on
        self._source_boundary_index_set = _definition_start_index_set(
            section_input.file_deps.get("definitions", [])
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
            LlmCallError: When a summary call of a stage fails for another reason
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

    async def _drop_caller_body(self) -> _SectionInput:
        """Return the input without the caller usage_context bodies."""
        file_deps = _reduce_caller_usages(self.section_input.file_deps)
        if file_deps is self.section_input.file_deps:
            return self.section_input
        return replace(self.section_input, file_deps=file_deps)

    async def _drop_callee_context(self) -> _SectionInput:
        """Return the input without the dependency doc summaries."""
        if not self.section_input.callee_context:
            return self.section_input
        return replace(self.section_input, callee_context="")

    async def _summarize_callee_body(self) -> _SectionInput:
        """Return the input with the large callee dependency symbols summarized."""
        file_deps = await _summarize_callee_usages(
            self.section_input.file_deps, self._llm_client, self._summary_cache, self._file_path,
        )
        if file_deps is self.section_input.file_deps:
            return self.section_input
        return replace(self.section_input, file_deps=file_deps)

    async def _drop_callee_body(self) -> _SectionInput:
        """Return the input without the callee target_context bodies."""
        file_deps = _without_callee_body(self.section_input.file_deps)
        if file_deps is self.section_input.file_deps:
            return self.section_input
        return replace(self.section_input, file_deps=file_deps)

    async def _summarize_implementation_definition(self) -> _SectionInput:
        """Return the input with the large definitions of the implementation file summarized."""
        implementation_context = self.section_input.implementation_context
        if not implementation_context:
            return self.section_input
        short_context, _ = await _splice_large_definitions(
            implementation_context, self._implementation_definition_list,
            self._llm_client, self._summary_cache, self._file_path,
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
        short_code, boundary_index_set = await _splice_large_definitions(
            source_code, self.section_input.file_deps.get("definitions", []),
            self._llm_client, self._summary_cache, self._file_path,
        )
        if short_code == source_code:
            return self.section_input
        self._source_boundary_index_set = boundary_index_set
        return replace(self.section_input, source_code=short_code)

    async def _drop_usage_list(self) -> _SectionInput:
        """Return the input without the lists of callee and caller usages."""
        file_deps = self.section_input.file_deps
        if not file_deps.get("callee_usages") and not file_deps.get("caller_usages"):
            return self.section_input
        return replace(
            self.section_input, file_deps={**file_deps, "callee_usages": [], "caller_usages": []},
        )

    async def _summarize_source_piece(self) -> _SectionInput:
        """Return the input with the source summarized piece by piece."""
        source_code = self.section_input.source_code
        short_code = await _summarize_source_by_piece(
            source_code, self._source_boundary_index_set, self._llm_client, self._summary_cache,
            self._file_path,
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
            section, section_input.source_code, section_input.file_deps,
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
            except LlmCallError:
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
    doc_summary_map: dict[str, str],
    template: dict,
    llm_client: LLMClient,
    summary_cache: dict[str, str],
) -> dict | None:
    """Generate a design document for one file.

    Read the original file copy and file_dependencies.json from file_output_dir,
    and generate text for each template section via the LLM.
    Handle context window exceeded errors with progressive fallback
    (_PromptReducer); a section starts from the stage the section before it fit at.

    Args:
        file_rel: Relative path from the project root (e.g. "src/foo.py").
        file_output_dir: Output directory for this file (source copy and JSON are stored here).
        doc_summary_map: Design document summaries of processed files, keyed by file
            relative path (for callee context reference).
        template: Template dict.
        llm_client: LLM client.
        summary_cache: Shared cache mapping code-hash -> summary text (context-overflow fallback).

    Returns:
        Design document dict ({file, sections, summary, source_hash}), or None if
        generation completely fails. source_hash is the SHA256 of the source the
        document was generated from.
    """
    # Read the source code
    source_file = _find_source_file(file_output_dir, file_rel)
    if not source_file:
        logger.warning(f"Source file not found: {file_output_dir}")
        return None

    source_code = read_source_text(source_file)

    # Read file_dependencies.json
    deps_path = os.path.join(file_output_dir, "file_dependencies.json")
    if not os.path.exists(deps_path):
        logger.warning(f"file_dependencies.json not found: {deps_path}")
        return None

    with open(deps_path, "r", encoding="utf-8") as f:
        file_deps = json.load(f)

    # The full input of the section prompts: the source, the dependency information,
    # the dependency doc summaries and, for header files, the implementation file
    prompt_reducer = _PromptReducer(
        _SectionInput(
            source_code, file_deps,
            _build_callee_context_summary(file_deps, doc_summary_map),
            _build_implementation_context(file_rel, file_output_dir),
        ),
        llm_client, summary_cache,
        _implementation_definition_list(file_rel, file_output_dir), file_rel,
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
            "content": section_text,
        })

    if not section_list:
        logger.error(f"Design document generation completely failed: {file_rel}")
        return None

    # Generate summary
    summary = await _generate_summary(
        file_rel, section_list, template, llm_client
    )

    return {
        "file": file_rel,
        "sections": section_list,
        "summary": summary or "",
        "source_hash": compute_file_hash(source_file),
    }


async def _generate_summary(
    file_path: str,
    section_list: list[dict],
    template: dict,
    llm_client: LLMClient,
) -> str | None:
    """Generate a summary from all sections of the design document.

    When the prompt exceeds the context window, each section is summarized by itself
    first (in halves while it still exceeds it, _generate_by_half) and the summary
    is generated from those.

    Args:
        file_path: Relative path of the target file.
        section_list: List of already-generated sections.
        template: Template dict.
        llm_client: LLM client.

    Returns:
        Summary text, or None on failure.
    """
    def build_prompt(summary_section_list: list[dict]) -> str:
        """Return the summary prompt over the sections given."""
        return _build_summary_prompt(
            file_path, summary_section_list, template["summary_prompt"], SUMMARY_MAX_CHARS,
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


def load_doc(output_dir: str) -> dict | None:
    """Read the design document (doc.json) saved in a file's output directory.

    Args:
        output_dir: Output directory of the file.

    Returns:
        The design document dict, or None if doc.json does not exist or cannot be read.
    """
    json_path = os.path.join(output_dir, "doc.json")
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _save_doc(doc: dict, output_dir: str) -> None:
    """Save a design document to file in both JSON and Markdown formats.

    Args:
        doc: Design document dict ({file, sections, summary, source_hash}).
        output_dir: Output directory.
    """
    # doc.md is written before doc.json, so doc.json is never older than doc.md
    md_path = os.path.join(output_dir, "doc.md")

    # Strip duplicate section title headers that the LLM may include in its response
    for section in doc["sections"]:
        section["content"] = re.sub(
            r"\A\s*#+\s+" + re.escape(section["title"]) + r"\s*\n*",
            "",
            section["content"],
        )

    md_line_list = [f"# Design Document: {doc['file']}", ""]

    # Add heading and content for each section in Markdown format
    for section in doc["sections"]:
        md_line_list.append(f"# {section['title']}")
        md_line_list.append("")
        md_line_list.append(section["content"])
        md_line_list.append("")

    # Append summary as a section at the end if present
    if doc.get("summary"):
        md_line_list.append("# Summary")
        md_line_list.append("")
        md_line_list.append(doc["summary"])
        md_line_list.append("")

    # Write to Markdown file
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_line_list))

    # JSON output
    json_path = os.path.join(output_dir, "doc.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)


def _parse_md_sections(md_text: str, section_title_list: list[str]) -> dict[str, str]:
    """Split markdown text by known section titles and return the content of each section.

    Use ``# {title}`` lines matching known section titles as delimiters.

    Args:
        md_text: Full text of doc.md.
        section_title_list: List of section titles used as split keys (including "Summary").

    Returns:
        Dict mapping title to content text. Sections not found are omitted.
    """
    title_pattern_list = [re.escape(title) for title in section_title_list]
    title_alternation = "|".join(title_pattern_list)
    pattern = re.compile(
        r"^# (" + title_alternation + r")\s*$",
        re.MULTILINE,
    )

    match_list = list(pattern.finditer(md_text))
    content_by_title_dict: dict[str, str] = {}

    # Extract text between matches as section content
    for i, match in enumerate(match_list):
        title = match.group(1)
        content_start = match.end()
        content_end = match_list[i + 1].start() if i + 1 < len(match_list) else len(md_text)
        content_by_title_dict[title] = md_text[content_start:content_end].strip()

    return content_by_title_dict


def _sync_md_to_json(output_dir: str) -> None:
    """Sync manual edits from doc.md back to doc.json when MD is newer.

    Only operates when the MD file has a newer timestamp than the JSON.
    Parses the MD, overwrites matching section content in the existing JSON,
    and re-saves. Sections not present in the MD retain their original content.

    Args:
        output_dir: Directory containing doc.json and doc.md.
    """
    json_path = os.path.join(output_dir, "doc.json")
    md_path = os.path.join(output_dir, "doc.md")

    if not os.path.exists(json_path) or not os.path.exists(md_path):
        return

    # Timestamp comparison: sync only when MD is newer than JSON
    if os.path.getmtime(md_path) <= os.path.getmtime(json_path):
        return

    # Load existing JSON
    doc = load_doc(output_dir)
    if doc is None:
        return

    # Read full text of the MD file
    with open(md_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    # Get list of known section titles from JSON (append Summary at the end)
    section_title_list = [section["title"] for section in doc["sections"]] + ["Summary"]
    # Parse MD into sections
    md_section_dict = _parse_md_sections(md_text, section_title_list)

    if not md_section_dict:
        return

    # Compare MD and JSON section content, and apply diffs to JSON.
    # Skip if the next section heading (in JSON order) is missing from MD, as boundaries would be inaccurate.
    md_title_set = set(md_section_dict.keys())
    has_diff = False
    for index, section in enumerate(doc["sections"]):
        title = section["title"]
        if title not in md_title_set:
            continue

        # Check if the next section (in JSON order) exists in MD
        next_title = (
            doc["sections"][index + 1]["title"]
            if index + 1 < len(doc["sections"])
            else "Summary"
        )
        if next_title not in md_title_set:
            continue

        if md_section_dict[title] != section["content"]:
            section["content"] = md_section_dict[title]
            has_diff = True

    # Also apply summary section diffs
    if "Summary" in md_title_set and md_section_dict["Summary"] != doc.get("summary", ""):
        doc["summary"] = md_section_dict["Summary"]
        has_diff = True

    if not has_diff:
        return

    # Save updated JSON
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)

    # Re-output MD to match JSON content and timestamps
    _save_doc(doc, output_dir)

    logger.info(f"  MD->JSON sync: {doc['file']}")


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


def _needs_regeneration(
    file_rel: str,
    changed_files: set[str] | None,
    callee_set_dict: dict[str, set[str]],
    new_doc_file_set: set[str],
) -> bool:
    """Determine whether the design document needs regeneration.

    Regeneration is needed if any of the following conditions are met:
    - changed_files is not specified (full regeneration mode).
    - The file itself is in changed_files.
    - Any of the file's callees (dependencies) is in changed_files or new_doc_file_set.

    Args:
        file_rel: Relative path of the file.
        changed_files: Set of relative paths of changed files, or None.
        callee_set_dict: File relative path -> relative paths of its callees.
        new_doc_file_set: Files whose documents were regenerated so far in this run.

    Returns:
        True if regeneration is needed.
    """
    if changed_files is None:
        return True
    if file_rel in changed_files:
        return True
    # Regenerate if any callee was changed or regenerated
    for callee in callee_set_dict.get(file_rel, set()):
        if callee in changed_files or callee in new_doc_file_set:
            return True
    return False


async def generate_all_docs(
    base_output_dir: str,
    project_dep_list: list[dict],
    llm_client: LLMClient,
    max_workers: int = MAX_WORKERS,
    changed_files: set[str] | None = None,
) -> list[str]:
    """Main function to generate design documents for all files in topological sort order.

    Processing flow:
    1. Load the template.
    2. Topologically sort project_dependencies and arrange by level.
    3. Starting from level 0 (no dependencies), generate documents for each level in parallel.
    4. Hold generated document summaries in doc_summary_map for use as context in subsequent levels.
    5. Save each file's document in JSON + Markdown format.

    When changed_files is specified, if a file's existing document was generated from
    the current source and none of its callees (dependencies) are in changed_files
    either, the existing doc.json is reused and the LLM call is skipped.

    Args:
        base_output_dir: Base output directory for file_dependencies.
        project_dep_list: Dependency list output by save_project_dependencies.
        llm_client: LLM client.
        max_workers: Number of parallel workers within each level.
        changed_files: Set of relative paths of changed files. If None, all files are processed.

    Returns:
        Relative paths of the files left without a complete design document
        (generation failed, or a section or the summary is missing).
    """
    with open(DOC_TEMPLATE_PATH, "r", encoding="utf-8") as f:
        template = json.load(f)

    # Get level-ordered file list via topological sort
    level_list = _topological_sort_by_level(project_dep_list)
    level_count = len(level_list)
    total_file_count = sum(len(level) for level in level_list)

    log_progress(
        logger,
        f"Starting design document generation. "
        f"Dependency depth levels: {level_count}, "
        f"Total files: {total_file_count}"
    )

    # Dict holding the summaries of processed files. Only the summary is carried
    # forward between levels; the section text is not kept after a document is saved.
    # Key: file relative path, Value: design document summary text
    doc_summary_map: dict[str, str] = {}

    # Shared code-summary cache for the whole run (context-overflow fallback).
    # Key: SHA256 of a code block, Value: its behavior summary. Reused across
    # files and sections of the run.
    summary_cache: dict[str, str] = {}

    # Per-file callee (dependency) list
    callee_set_dict: dict[str, set[str]] = {}
    for info in project_dep_list:
        callee_set_dict[info["file"]] = set(info.get("callees", []))

    # Track files whose documents were regenerated in this run.
    # Caller-side files that reference a regenerated file as a callee also become regeneration targets.
    new_doc_file_set: set[str] = set()

    # Files left without a complete design document in this run
    doc_fail_list: list[str] = []

    async def process_one(file_rel: str) -> tuple[str, dict | None]:
        """Generate the design document for one file and return (file_rel, doc).

        Args:
            file_rel: Relative path from the project root.

        Returns:
            tuple[str, dict | None]: Tuple of (file relative path, design document dict). dict is None on failure.
        """
        output_dir = resolve_file_output_dir(base_output_dir, file_rel)
        if not os.path.isdir(output_dir):
            logger.warning(f"Output directory does not exist: {output_dir}")
            return file_rel, None

        # Reuse existing doc.json if no changes
        if not _needs_regeneration(file_rel, changed_files, callee_set_dict, new_doc_file_set):
            # Sync manual edits from doc.md to JSON if user edited it
            _sync_md_to_json(output_dir)
            existing_doc = load_doc(output_dir)
            # Regenerate when doc.json is missing or unreadable
            if existing_doc is not None:
                if _is_doc_complete(existing_doc, template):
                    log_progress(logger, f"  REUSE: {file_rel}")
                    return file_rel, existing_doc
                log_progress(logger, f"  INCOMPLETE: {file_rel}")

        doc = await _generate_file_doc(
            file_rel, output_dir, doc_summary_map, template, llm_client, summary_cache,
        )
        if doc:
            _save_doc(doc, output_dir)
            new_doc_file_set.add(file_rel)
            log_progress(logger, f"  OK: {file_rel}")
        else:
            print(f"  SKIP: {file_rel}")
            logger.warning(f"  SKIP: {file_rel}")

        return file_rel, doc

    for level_index, file_list in enumerate(level_list):
        log_progress(
            logger,
            f"{level_index + 1}/{level_count}: "
            f"Generating documents for {len(file_list)} files"
        )

        # Process files in the level in batches of max_workers
        for batch_start in range(0, len(file_list), max_workers):
            batch = file_list[batch_start:batch_start + max_workers]

            task_list = [asyncio.create_task(process_one(f)) for f in batch]
            result_list = await asyncio.gather(*task_list, return_exceptions=True)

            for file_rel, result in zip(batch, result_list):
                if isinstance(result, Exception):
                    logger.error(f"Error during document generation: {result}")
                    doc_fail_list.append(file_rel)
                    continue
                _, doc = result
                if doc:
                    doc_summary_map[file_rel] = doc.get("summary", "")
                if not doc or not _is_doc_complete(doc, template):
                    doc_fail_list.append(file_rel)

    log_progress(
        logger,
        f"Design document generation completed. "
        f"Generated: {len(doc_summary_map)} / "
        f"Total: {total_file_count}"
    )

    return doc_fail_list
