import os
import json
import re
import hashlib
import asyncio
import logging
from codetwine.llm import ContextWindowExceededError
from codetwine.llm.client import LLMClient
from codetwine.utils.file_utils import (
    compute_file_hash,
    output_path_to_rel,
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

# Placeholder header prefixed to a summarized code block in the prompt.
CODE_SUMMARY_MARKER = "# [summarized] {name}"

# Deterministic fallback body appended when summary generation fails.
CODE_SUMMARY_FAILED_NOTE = "# ...(body omitted; summary unavailable)"

# C/C++ header extension set
_HEADER_EXT_SET = {".h", ".hpp", ".hh", ".hxx"}
# Implementation file extensions paired with header extensions
_IMPL_EXT_LIST = ["cpp", "c", "cc", "cxx"]


def _topological_sort_by_level(project_dep_list: list[dict]) -> list[list[str]]:
    """Topologically sort files from project_dependencies.json and return them
    as a list grouped by level (dependency depth).

    Level 0 = files with no dependencies (processed first).
    Level N = files that depend only on files at level N-1 or below.

    If circular dependencies exist, files remaining from Kahn's algorithm
    are included in the last level, and a warning is logged.

    Args:
        project_dep_list: Dependency list output by save_project_dependencies.
                          Each element is {"file": str, "callers": list, "callees": list}.

    Returns:
        A file list grouped by level. The outer list index is the level number.
        Example: [["config.py", "utils.py"], ["parser.py"], ["main.py"]]
    """
    # Build adjacency list (file -> files it depends on)
    adjacency: dict[str, set[str]] = {}
    all_file_set: set[str] = set()

    # Build adjacency list from the dependency list
    for dep_info in project_dep_list:
        file_path = dep_info["file"]
        all_file_set.add(file_path)
        adjacency.setdefault(file_path, set())

        # Add callees (dependencies) to the adjacency list
        for callee in dep_info.get("callees", []):
            all_file_set.add(callee)
            adjacency.setdefault(callee, set())
            adjacency[file_path].add(callee)

    # Build reverse graph adjacency list and in-degree
    dependent_map: dict[str, set[str]] = {f: set() for f in all_file_set}
    reverse_in_degree: dict[str, int] = {f: 0 for f in all_file_set}

    for file_path, callee_set in adjacency.items():
        for callee in callee_set:
            dependent_map[callee].add(file_path)
            reverse_in_degree[file_path] += 1

    # Execute BFS level by level
    level_list: list[list[str]] = []
    # First level: files with in-degree 0 in reverse graph (= files with empty callees in original graph)
    current_level = [f for f in all_file_set if reverse_in_degree[f] == 0]
    level_file_set: set[str] = set()

    while current_level:
        current_level.sort()
        level_list.append(current_level)
        level_file_set.update(current_level)

        next_level: list[str] = []
        for file_path in current_level:
            for dependent in dependent_map[file_path]:
                reverse_in_degree[dependent] -= 1
                if reverse_in_degree[dependent] == 0:
                    next_level.append(dependent)

        current_level = next_level

    # Add files not processed due to circular dependencies to the last level
    cycle_file_set = all_file_set - level_file_set
    if cycle_file_set:
        logger.warning(
            f"Circular dependencies detected. The following files will be processed at the last level: "
            f"{sorted(cycle_file_set)}"
        )
        level_list.append(sorted(cycle_file_set))

    return level_list


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

    # Include callee_usages (provide dependency source code via target_context)
    callee_usages = file_deps.get("callee_usages", [])
    if callee_usages:
        # List each callee_usage's symbol name and definition file
        part_list.append(HEADER_CALLEE_USAGES)
        part_list.append(CALLEE_USAGES_SCHEMA_NOTE)
        for usage in callee_usages:
            part_list.append(f"- {usage['name']} (from {output_path_to_rel(usage['from'])})")
            # Attach the full dependency source code if available
            target_context = usage.get("target_context")
            if target_context:
                part_list.append(CALLEE_SOURCE_CODE_LABEL)
                part_list.append("  ```")
                part_list.append(f"  {target_context}")
                part_list.append("  ```")
        part_list.append("")

    # Include caller_usages (information about external files using this file)
    caller_usages = file_deps.get("caller_usages", [])
    if caller_usages:
        # List each caller_usage's symbol name and referencing file
        part_list.append(HEADER_CALLER_USAGES)
        part_list.append(CALLER_USAGES_SCHEMA_NOTE)
        for usage in caller_usages:
            part_list.append(f"- {usage['name']} (from {output_path_to_rel(usage['file'])})")
            usage_context = usage.get("usage_context")
            if usage_context:
                part_list.append(CALLER_SOURCE_CODE_LABEL)
                part_list.append("  ```")
                part_list.append(f"  {usage_context}")
                part_list.append("  ```")
        part_list.append("")

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


def _line_count(text: str) -> int:
    """Return the number of lines in a text block (newline count + 1)."""
    return text.count("\n") + 1


async def _summarize_code(
    code: str,
    name: str,
    llm_client: LLMClient,
    summary_cache: dict[str, str],
) -> str:
    """Summarize a code block into a concise behavior description via the LLM.

    Results are cached by the SHA256 of the code text, so the same symbol is
    summarized only once across all files and sections in a single run. When
    generation fails, a deterministic fallback (signature line + note) is used
    so the caller always gets usable text.

    Args:
        code: Full source of the code symbol to summarize.
        name: Symbol name (used in the prompt and the placeholder marker).
        llm_client: LLM client used to generate the summary.
        summary_cache: Shared cache mapping code-hash -> summary text.

    Returns:
        The behavior summary text (or a deterministic fallback on failure).
    """
    cache_key = hashlib.sha256(code.encode("utf-8")).hexdigest()
    if cache_key in summary_cache:
        return summary_cache[cache_key]

    prompt = CODE_SUMMARY_PROMPT.format(
        name=name,
        max_chars=CODE_SUMMARY_MAX_CHARS,
        language=OUTPUT_LANGUAGE,
        code=code,
    )
    try:
        summary = await llm_client.generate(prompt)
    except ContextWindowExceededError:
        summary = None

    # Deterministic fallback keeps the signature so the block stays informative
    if not summary:
        first_line = code.split("\n", 1)[0]
        summary = f"{first_line}\n{CODE_SUMMARY_FAILED_NOTE}"

    summary_cache[cache_key] = summary
    return summary


def _reduce_caller_usages(file_deps: dict) -> dict:
    """Return a shallow copy of file_deps with caller usage_context bodies removed.

    Keeps name / file / lines so the dependent references stay listed, but drops
    the source snippets to shrink the prompt (fallback stage 1).

    Args:
        file_deps: The target file's file_dependencies.json contents.

    Returns:
        A shallow copy with each caller_usages entry stripped of usage_context.
    """
    caller_usages = file_deps.get("caller_usages", [])
    if not caller_usages:
        return file_deps

    deps_copy = dict(file_deps)
    deps_copy["caller_usages"] = [
        {key: value for key, value in usage.items() if key != "usage_context"}
        for usage in caller_usages
    ]
    return deps_copy


async def _summarize_callee_usages(
    file_deps: dict,
    llm_client: LLMClient,
    summary_cache: dict[str, str],
) -> dict:
    """Return a copy of file_deps where large callee target_context is summarized.

    Only dependency symbols longer than CODE_SUMMARY_TRIGGER_LINES are replaced
    by an LLM behavior summary; smaller ones are kept verbatim (fallback stage 3).

    Args:
        file_deps: The target file's file_dependencies.json contents.
        llm_client: LLM client used for summarization.
        summary_cache: Shared cache mapping code-hash -> summary text.

    Returns:
        A shallow copy with large callee_usages[].target_context summarized.
    """
    callee_usages = file_deps.get("callee_usages", [])
    if not callee_usages:
        return file_deps

    usage_list = []
    for usage in callee_usages:
        target_context = usage.get("target_context")
        if target_context and _line_count(target_context) > CODE_SUMMARY_TRIGGER_LINES:
            summary = await _summarize_code(
                target_context, usage.get("name", "symbol"), llm_client, summary_cache
            )
            usage = {**usage, "target_context": summary}
        usage_list.append(usage)

    deps_copy = dict(file_deps)
    deps_copy["callee_usages"] = usage_list
    return deps_copy


def _select_outermost_large_definitions(
    definition_list: list[dict],
    trigger_line_count: int,
) -> list[dict]:
    """Select large definitions, excluding ones nested inside a larger selection.

    Definitions spanning more than trigger_line_count lines are candidates. When a
    class and its methods are both large, only the outermost (the class) is kept
    so a range is never summarized twice.

    Args:
        definition_list: definitions[] from file_dependencies.json (with start_line/end_line).
        trigger_line_count: Minimum line span for a definition to be summarized.

    Returns:
        Outermost large definitions, sorted by start_line.
    """
    large_list = [
        definition
        for definition in definition_list
        if definition.get("start_line")
        and definition.get("end_line")
        and (definition["end_line"] - definition["start_line"] + 1) > trigger_line_count
    ]
    # Outer-first ordering: earliest start, and on ties the wider range first
    large_list.sort(key=lambda definition: (definition["start_line"], -definition["end_line"]))

    outer_list: list[dict] = []
    covered_end = 0
    for definition in large_list:
        # Skip definitions that start within an already-selected outer range
        if definition["start_line"] <= covered_end:
            continue
        outer_list.append(definition)
        covered_end = definition["end_line"]
    return outer_list


async def _splice_large_definitions(
    source_code: str,
    definition_list: list[dict],
    llm_client: LLMClient,
    summary_cache: dict[str, str],
) -> str:
    """Replace large definitions in the source with LLM behavior summaries.

    Used as the last-resort fallback (stage 4) when the target file itself is too
    large. Line numbers are 1-based and match the source copy exactly, so the
    [start_line, end_line] range of each selected definition is spliced out and
    replaced by a summary block. Non-definition lines and small definitions are
    kept as-is.

    Args:
        source_code: Full source of the target file (as read from its copy).
        definition_list: definitions[] from file_dependencies.json.
        llm_client: LLM client used for summarization.
        summary_cache: Shared cache mapping code-hash -> summary text.

    Returns:
        The source with large definitions replaced by summary blocks. Returns the
        original source unchanged when no definition exceeds the threshold.
    """
    outer_list = _select_outermost_large_definitions(definition_list, CODE_SUMMARY_TRIGGER_LINES)
    if not outer_list:
        return source_code

    # split("\n") keeps 1-based mapping: source line N -> lines[N-1] (tree-sitter rows are \n-based)
    line_list = source_code.split("\n")
    line_count = len(line_list)
    definition_by_start_dict = {definition["start_line"]: definition for definition in outer_list}

    out_line_list: list[str] = []
    line_no = 1
    while line_no <= line_count:
        definition = definition_by_start_dict.get(line_no)
        if definition:
            name = definition.get("name", "symbol")
            code = definition.get("context") or "\n".join(
                line_list[definition["start_line"] - 1 : definition["end_line"]]
            )
            summary = await _summarize_code(code, name, llm_client, summary_cache)
            out_line_list.append(CODE_SUMMARY_MARKER.format(name=name))
            out_line_list.append(summary)
            line_no = definition["end_line"] + 1
        else:
            out_line_list.append(line_list[line_no - 1])
            line_no += 1

    return "\n".join(out_line_list)


def _build_implementation_context(
    file_rel: str,
    file_output_dir: str,
) -> str:
    """Retrieve the source code of the implementation file (.cpp/.c etc.) corresponding to a header file.

    Search for an implementation file with the same base name as the header
    in the same level of the output directory, and return its full source code
    if found. Returns an empty string for non-header files.

    Args:
        file_rel: Relative path of the target file (e.g. "MainWindow.h").
        file_output_dir: Output directory of the target file (e.g. ".../MainWindow_h/").

    Returns:
        Source code text of the implementation file. Empty string if not found or non-header file.
    """
    _, ext = os.path.splitext(file_rel)
    if ext not in _HEADER_EXT_SET:
        return ""

    stem = os.path.splitext(os.path.basename(file_rel))[0]
    base_dir = os.path.dirname(file_output_dir)

    for impl_ext in _IMPL_EXT_LIST:
        impl_dir = os.path.join(base_dir, f"{stem}_{impl_ext}")
        impl_file = os.path.join(impl_dir, f"{stem}.{impl_ext}")
        if os.path.isfile(impl_file):
            with open(impl_file, "r", encoding="utf-8") as f:
                return f.read()

    return ""


async def _generate_section_with_fallback(
    section: dict,
    source_code: str,
    file_deps: dict,
    callee_context: str,
    file_path: str,
    llm_client: LLMClient,
    summary_cache: dict[str, str],
    implementation_context: str = "",
) -> str | None:
    """Generate one section, reducing the prompt on context-window overflow.

    On ContextWindowExceededError, the prompt is shrunk cumulatively:
      Stage 0: full (source + callee/caller usages + dependency doc summaries)
      Stage 1: drop caller usage_context bodies                 (no LLM)
      Stage 2: drop dependency doc summaries (callee_context)   (no LLM)
      Stage 3: summarize large callee dependency symbols        (LLM, cached)
      Stage 4: summarize large definitions in the source        (LLM, cached)
    Stages 3-4 run only when ENABLE_CODE_SUMMARY is True. Returns None if every
    stage still fails.

    Args:
        section: One section definition from the template.
        source_code: Full source code of the target file.
        file_deps: Contents of file_dependencies.json.
        callee_context: Dependency design-document summaries (may be empty).
        file_path: Relative path of the target file.
        llm_client: LLM client.
        summary_cache: Shared cache mapping code-hash -> summary text.
        implementation_context: For header files. Source of the implementation file.

    Returns:
        Generated section text, or None if all stages fail.
    """
    async def _try(source: str, deps_dict: dict, context: str, label: str) -> str | None:
        """Build the prompt for one reduction stage and try generating the section."""
        prompt = _build_section_prompt(section, source, deps_dict, context, implementation_context)
        try:
            return await llm_client.generate(prompt)
        except ContextWindowExceededError:
            logger.warning(
                f"Context exceeded ({label}): {file_path}/{section['id']}. "
                f"Falling back to next reduction stage."
            )
            return None

    # Stage 0: full context
    result = await _try(source_code, file_deps, callee_context, "full")
    if result is not None:
        return result

    # Stage 1: drop caller usage_context bodies
    deps_without_caller = _reduce_caller_usages(file_deps)
    result = await _try(source_code, deps_without_caller, callee_context, "drop caller bodies")
    if result is not None:
        return result

    # Stage 2: drop dependency doc summaries
    result = await _try(source_code, deps_without_caller, "", "drop callee context")
    if result is not None:
        return result

    if not ENABLE_CODE_SUMMARY:
        return None

    # Stage 3: summarize large dependency symbols
    deps_with_summary = await _summarize_callee_usages(
        deps_without_caller, llm_client, summary_cache
    )
    result = await _try(source_code, deps_with_summary, "", "summarize callee usages")
    if result is not None:
        return result

    # Stage 4: summarize large definitions in the source itself
    source_with_summary = await _splice_large_definitions(
        source_code, deps_with_summary.get("definitions", []), llm_client, summary_cache
    )
    result = await _try(source_with_summary, deps_with_summary, "", "summarize source defs")
    if result is not None:
        return result

    return None


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
    Handle context window exceeded errors with progressive fallback.

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

    with open(source_file, "r", encoding="utf-8") as f:
        source_code = f.read()

    # Read file_dependencies.json
    deps_file = os.path.join(file_output_dir, "file_dependencies.json")
    if not os.path.exists(deps_file):
        logger.warning(f"file_dependencies.json not found: {deps_file}")
        return None

    with open(deps_file, "r", encoding="utf-8") as f:
        file_deps = json.load(f)

    # Prepare callee context (dependency doc summaries only)
    callee_context = _build_callee_context_summary(file_deps, doc_summary_map)

    # For header files, get the corresponding implementation file's source code
    implementation_context = _build_implementation_context(file_rel, file_output_dir)

    # Generate each section
    section_list: list[dict] = []

    for section in template["sections"]:
        section_text = await _generate_section_with_fallback(
            section, source_code, file_deps,
            callee_context,
            file_rel, llm_client,
            summary_cache,
            implementation_context,
        )

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

    Args:
        file_path: Relative path of the target file.
        section_list: List of already-generated sections.
        template: Template dict.
        llm_client: LLM client.

    Returns:
        Summary text, or None on failure.
    """
    summary_prompt = template["summary_prompt"]
    summary_max_chars = SUMMARY_MAX_CHARS

    prompt = _build_summary_prompt(file_path, section_list, summary_prompt, summary_max_chars)

    try:
        return await llm_client.generate(prompt)
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
    # Markdown output (write first)
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

    # JSON output (written after MD so that mtime >= MD)
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
    for idx, section in enumerate(doc["sections"]):
        title = section["title"]
        if title not in md_title_set:
            continue

        # Check if the next section (in JSON order) exists in MD
        next_title = (
            doc["sections"][idx + 1]["title"]
            if idx + 1 < len(doc["sections"])
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


async def generate_all_docs(
    base_output_dir: str,
    project_dep_list: list[dict],
    llm_client: LLMClient,
    max_workers: int = MAX_WORKERS,
    changed_files: set[str] | None = None,
) -> None:
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
    """
    with open(DOC_TEMPLATE_PATH, "r", encoding="utf-8") as f:
        template = json.load(f)

    # Get level-ordered file list via topological sort
    level_list = _topological_sort_by_level(project_dep_list)
    level_count = len(level_list)

    log_progress(

        logger,
        f"Starting design document generation. "
        f"Dependency depth levels: {level_count}, "
        f"Total files: {sum(len(level) for level in level_list)}"
    )

    # Dict holding the summaries of processed files. Only the summary is carried
    # forward between levels; the section text is not kept after a document is saved.
    # Key: file relative path, Value: design document summary text
    doc_summary_map: dict[str, str] = {}

    # Shared code-summary cache for the whole run (context-overflow fallback).
    # Key: SHA256 of a code block, Value: its behavior summary. Reused across
    # files and sections so the same symbol is summarized only once.
    summary_cache: dict[str, str] = {}

    # Per-file callee (dependency) list
    callee_set_by_file: dict[str, set[str]] = {}
    for info in project_dep_list:
        callee_set_by_file[info["file"]] = set(info.get("callees", []))

    # Track files whose documents were regenerated in this run.
    # Caller-side files that reference a regenerated file as a callee also become regeneration targets.
    new_doc_file_set: set[str] = set()

    def _needs_regeneration(file_rel: str) -> bool:
        """Determine whether the design document needs regeneration.

        Regeneration is needed if any of the following conditions are met:
        - changed_files is not specified (full regeneration mode).
        - The file itself is in changed_files.
        - Any of the file's callees (dependencies) is in changed_files or regenerated_files.

        Args:
            file_rel: Relative path of the file.

        Returns:
            True if regeneration is needed.
        """
        if changed_files is None:
            return True
        if file_rel in changed_files:
            return True
        # Regenerate if any callee was changed or regenerated
        for callee in callee_set_by_file.get(file_rel, set()):
            if callee in changed_files or callee in new_doc_file_set:
                return True
        return False

    def _is_doc_complete(doc: dict) -> bool:
        """Check whether a design document contains all expected sections and summary.

        Returns False if any template section is missing/extra or if the summary is empty.
        """
        expected_id_set = {section["id"] for section in template["sections"]}
        actual_id_set = {section["id"] for section in doc.get("sections", [])}
        if expected_id_set != actual_id_set:
            return False
        if "summary_prompt" in template and not doc.get("summary"):
            return False
        return True

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
        if not _needs_regeneration(file_rel):
            # Sync manual edits from doc.md to JSON if user edited it
            _sync_md_to_json(output_dir)
            existing_doc = load_doc(output_dir)
            # Regenerate when doc.json is missing or unreadable
            if existing_doc is not None:
                if _is_doc_complete(existing_doc):
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

            for result in result_list:
                if isinstance(result, Exception):
                    logger.error(f"Error during document generation: {result}")
                    continue
                file_rel, doc = result
                if doc:
                    doc_summary_map[file_rel] = doc.get("summary", "")

    log_progress(

        logger,
        f"Design document generation completed. "
        f"Generated: {len(doc_summary_map)} / "
        f"Total: {sum(len(level) for level in level_list)}"
    )
