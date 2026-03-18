import os
import json
import re
import asyncio
import logging
from codetwine.llm import ContextWindowExceededError
from codetwine.llm.client import LLMClient
from codetwine.utils.file_utils import output_path_to_rel, resolve_file_output_dir
from codetwine.config.settings import (
    MAX_WORKERS,
    DOC_TEMPLATE_PATH,
    OUTPUT_LANGUAGE,
    SUMMARY_MAX_CHARS,
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
    "of the dependency file where that symbol is defined. "
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

# C/C++ header extension set
_HEADER_EXTENSIONS = {".h", ".hpp", ".hh", ".hxx"}
# Implementation file extensions paired with header extensions
_IMPL_EXTENSIONS = ["cpp", "c", "cc", "cxx"]


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
    # Build adjacency list (file -> files it depends on) and in-degree
    adjacency: dict[str, set[str]] = {}
    in_degree: dict[str, int] = {}
    all_files: set[str] = set()

    # Build adjacency list from the dependency list
    for dep_info in project_dep_list:
        file_path = dep_info["file"]
        all_files.add(file_path)
        adjacency.setdefault(file_path, set())

        # Add callees (dependencies) to the adjacency list
        for callee in dep_info.get("callees", []):
            all_files.add(callee)
            adjacency.setdefault(callee, set())
            adjacency[file_path].add(callee)

    # Calculate in-degree (number of files depending on each file)
    for file_path in all_files:
        in_degree[file_path] = 0
    for file_path, callees in adjacency.items():
        for callee in callees:
            in_degree[callee] = in_degree.get(callee, 0) + 1

    # Build reverse graph adjacency list and in-degree
    reverse_adj: dict[str, set[str]] = {f: set() for f in all_files}
    reverse_in_degree: dict[str, int] = {f: 0 for f in all_files}

    for file_path, callees in adjacency.items():
        for callee in callees:
            reverse_adj[callee].add(file_path)
            reverse_in_degree[file_path] += 1

    # Execute BFS level by level
    level_list: list[list[str]] = []
    # First level: files with in-degree 0 in reverse graph (= files with empty callees in original graph)
    current_level = [f for f in all_files if reverse_in_degree[f] == 0]
    processed: set[str] = set()

    while current_level:
        current_level.sort()
        level_list.append(current_level)
        processed.update(current_level)

        next_level: list[str] = []
        for file_path in current_level:
            for dependent in reverse_adj[file_path]:
                reverse_in_degree[dependent] -= 1
                if reverse_in_degree[dependent] == 0:
                    next_level.append(dependent)

        current_level = next_level

    # Add files not processed due to circular dependencies to the last level
    remaining = all_files - processed
    if remaining:
        logger.warning(
            f"Circular dependencies detected. The following files will be processed at the last level: "
            f"{sorted(remaining)}"
        )
        level_list.append(sorted(remaining))

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
    parts = [
        HEADER_TARGET_FILE.format(file=file_deps.get('file', 'unknown')),
        "",
        HEADER_SOURCE_CODE,
        "```",
        source_code,
        "```",
        "",
    ]

    # For header files, include the corresponding implementation file's source code
    if implementation_context:
        parts.append(HEADER_IMPL_CONTEXT)
        parts.append(IMPL_CONTEXT_NOTE)
        parts.append("```")
        parts.append(implementation_context)
        parts.append("```")
        parts.append("")

    # Include callee_usages (provide dependency source code via target_context)
    callee_usages = file_deps.get("callee_usages", [])
    if callee_usages:
        # List each callee_usage's symbol name and definition file
        parts.append(HEADER_CALLEE_USAGES)
        parts.append(CALLEE_USAGES_SCHEMA_NOTE)
        for u in callee_usages:
            parts.append(f"- {u['name']} (from {output_path_to_rel(u['from'])})")
            # Attach the full dependency source code if available
            target_context = u.get("target_context")
            if target_context:
                parts.append(CALLEE_SOURCE_CODE_LABEL)
                parts.append(f"  ```")
                parts.append(f"  {target_context}")
                parts.append(f"  ```")
        parts.append("")

    # Include caller_usages (information about external files using this file)
    caller_usages = file_deps.get("caller_usages", [])
    if caller_usages:
        # List each caller_usage's symbol name and referencing file
        parts.append(HEADER_CALLER_USAGES)
        parts.append(CALLER_USAGES_SCHEMA_NOTE)
        for u in caller_usages:
            parts.append(f"- {u['name']} (from {output_path_to_rel(u['file'])})")
            usage_context = u.get("usage_context")
            if usage_context:
                parts.append(CALLER_SOURCE_CODE_LABEL)
                parts.append(f"  ```")
                parts.append(f"  {usage_context}")
                parts.append(f"  ```")
        parts.append("")

    # Add dependency file design document summaries as context
    if callee_context:
        parts.append(HEADER_CALLEE_CONTEXT)
        parts.append(CALLEE_CONTEXT_NOTE)
        parts.append(callee_context)
        parts.append("")

    # Add section-specific instructions
    parts.append(HEADER_REQUEST)
    parts.append(SECTION_REQUEST_TEMPLATE.format(title=section['title']))
    parts.append(section["prompt"])
    # Append output language specification at the end
    parts.append("\n" + OUTPUT_LANGUAGE_INSTRUCTION.format(language=OUTPUT_LANGUAGE))
    # Append source code consistency instruction at the end
    parts.append(FACTUAL_ACCURACY_INSTRUCTION)

    return "\n".join(parts)


def _build_summary_prompt(
    file_path: str,
    section_contents: list[dict],
    summary_prompt: str,
    summary_max_chars: int,
) -> str:
    """Assemble the LLM prompt for generating a summary of the entire design document.

    Args:
        file_path: Relative path of the target file.
        section_contents: List of generated sections (each element is {id, title, content}).
        summary_prompt: Summary instruction text defined in the template.
        summary_max_chars: Maximum character count for the summary.

    Returns:
        The completed prompt string to send to the LLM.
    """
    # Basic prompt structure: target file name + all section contents of the design document
    parts = [
        HEADER_TARGET_FILE.format(file=file_path),
        "",
        HEADER_DOC_CONTENT,
    ]

    # Add each section's heading and content to the prompt
    for sec in section_contents:
        parts.append(f"### {sec['title']}")
        parts.append(sec["content"])
        parts.append("")

    # Add summary instructions and character limit
    parts.append(HEADER_REQUEST)
    parts.append(f"{summary_prompt}")
    parts.append(SUMMARY_CHAR_LIMIT.format(max_chars=summary_max_chars))
    parts.append(OUTPUT_LANGUAGE_INSTRUCTION.format(language=OUTPUT_LANGUAGE))

    return "\n".join(parts)


def _build_callee_context_summary(
    file_deps: dict,
    doc_map: dict[str, dict],
    compact: bool = False,
) -> str:
    """Extract only summary text (doc_map[file]["summary"]) from design documents
    of dependency files and concatenate them into a single string.

    compact=False: Concatenate each dependency's summary as-is.
    compact=True: Truncate each dependency's summary to the first 100 characters.

    Args:
        file_deps: The target file's file_dependencies.json.
        doc_map: A map of file relative path -> generated design document dict.
        compact: If True, truncate summaries to the first 100 characters.

    Returns:
        Context text combining only the summaries.
    """
    # Create a deduplicated list of dependency files from callee_usages
    callee_set: set[str] = set()
    for usage in file_deps.get("callee_usages", []):
        from_file = usage.get("from")
        if from_file:
            callee_set.add(from_file)
    callee_files = sorted(callee_set)

    # Retrieve and concatenate summaries for each dependency file
    # callee_usages' from is in output format; doc_map keys are source relative paths, so reverse-convert
    parts = []
    _compact_max_chars = 100
    for callee_file in callee_files:
        doc = doc_map.get(output_path_to_rel(callee_file))
        if not doc:
            continue
        summary = doc.get("summary", "")
        if summary:
            if compact:
                summary = summary[:_compact_max_chars] + ("..." if len(summary) > _compact_max_chars else "")
            parts.append(f"- **{output_path_to_rel(callee_file)}**: {summary}")
    return "\n".join(parts)


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
    if ext not in _HEADER_EXTENSIONS:
        return ""

    stem = os.path.splitext(os.path.basename(file_rel))[0]
    base_dir = os.path.dirname(file_output_dir)

    for impl_ext in _IMPL_EXTENSIONS:
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
    callee_context_summary: str,
    callee_context_compact: str,
    file_path: str,
    llm_client: LLMClient,
    implementation_context: str = "",
) -> str | None:
    """Generate one section with progressive fallback.

    When a ContextWindowExceededError occurs, retry in the following order:
      Attempt 1: With dependency design document summary context
      Attempt 2: With compressed dependency summaries (first 100 chars each)
      Attempt 3: Without callee context
    Returns None if all attempts fail.

    Args:
        section: One section definition from the template.
        source_code: Full source code of the target file.
        file_deps: Contents of file_dependencies.json.
        callee_context_summary: Callee summary context.
        callee_context_compact: Compressed version of callee summary context.
        file_path: Relative path of the target file.
        llm_client: LLM client.
        implementation_context: For header files. Source code of the corresponding implementation file.

    Returns:
        Generated section text, or None if all attempts fail.
    """
    # Attempt list: (label, callee context)
    attempts = [
        ("with callee summary", callee_context_summary),
        ("compact callee summary", callee_context_compact),
        ("without callee", ""),
    ]

    for label, callee_ctx in attempts:
        prompt = _build_section_prompt(
            section, source_code, file_deps, callee_ctx, implementation_context,
        )
        try:
            result = await llm_client.generate(prompt)
            if result is not None:
                return result
        except ContextWindowExceededError:
            logger.warning(
                f"Context exceeded ({label}): {file_path}/{section['id']}. "
                f"Falling back to next attempt."
            )
            continue

    return None


async def _generate_file_doc(
    file_rel: str,
    file_output_dir: str,
    doc_map: dict[str, dict],
    template: dict,
    llm_client: LLMClient,
) -> dict | None:
    """Generate a design document for one file.

    Read the original file copy and file_dependencies.json from file_output_dir,
    and generate text for each template section via the LLM.
    Handle context window exceeded errors with progressive fallback.

    Args:
        file_rel: Relative path from the project root (e.g. "src/foo.py").
        file_output_dir: Output directory for this file (source copy and JSON are stored here).
        doc_map: Design document dict of processed files (for callee context reference).
        template: Template dict.
        llm_client: LLM client.

    Returns:
        Design document dict ({file, sections, summary}), or None if generation completely fails.
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

    # Prepare callee context (summary only)
    callee_context_summary = _build_callee_context_summary(file_deps, doc_map)
    callee_context_compact = _build_callee_context_summary(file_deps, doc_map, compact=True)

    # For header files, get the corresponding implementation file's source code
    implementation_context = _build_implementation_context(file_rel, file_output_dir)

    # Generate each section
    section_list: list[dict] = []

    for section in template["sections"]:
        result = await _generate_section_with_fallback(
            section, source_code, file_deps,
            callee_context_summary,
            callee_context_compact,
            file_rel, llm_client,
            implementation_context,
        )

        if result is None:
            logger.warning(f"Failed to generate section '{section['title']}': {file_rel}")
            continue

        section_list.append({
            "id": section["id"],
            "title": section["title"],
            "content": result,
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


def _save_doc(doc: dict, output_dir: str) -> None:
    """Save a design document to file in both JSON and Markdown formats.

    Args:
        doc: Design document dict ({file, sections, summary}).
        output_dir: Output directory.
    """
    # Markdown output (write first)
    md_path = os.path.join(output_dir, "doc.md")
    md_lines = [f"# Design Document: {doc['file']}", ""]

    # Add heading and content for each section in Markdown format
    for section in doc["sections"]:
        md_lines.append(f"## {section['title']}")
        md_lines.append("")
        md_lines.append(section["content"])
        md_lines.append("")

    # Append summary as a section at the end if present
    if doc.get("summary"):
        md_lines.append("## Summary")
        md_lines.append("")
        md_lines.append(doc["summary"])
        md_lines.append("")

    # Write to Markdown file
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))

    # JSON output (written after MD so that mtime >= MD)
    json_path = os.path.join(output_dir, "doc.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)


def _parse_md_sections(md_text: str, section_titles: list[str]) -> dict[str, str]:
    """Split markdown text by known section titles and return the content of each section.

    Use ``## {title}`` lines matching known section titles as delimiters.
    Even if the LLM-generated content contains ``##`` headings,
    they are treated as part of the content unless they exactly match a known title.

    Args:
        md_text: Full text of doc.md.
        section_titles: List of section titles used as split keys (including "Summary").

    Returns:
        Dict mapping title to content text. Sections not found are omitted.
    """
    escaped_titles = [re.escape(t) for t in section_titles]
    pattern = re.compile(
        r"^## (" + "|".join(escaped_titles) + r")\s*$",
        re.MULTILINE,
    )

    matches = list(pattern.finditer(md_text))
    result: dict[str, str] = {}

    # Extract text between matches as section content
    for i, match in enumerate(matches):
        title = match.group(1)
        content_start = match.end()
        content_end = matches[i + 1].start() if i + 1 < len(matches) else len(md_text)
        result[title] = md_text[content_start:content_end].strip()

    return result


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
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (json.JSONDecodeError, OSError):
        return

    # Read full text of the MD file
    with open(md_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    # Get list of known section titles from JSON (append Summary at the end)
    section_titles = [s["title"] for s in doc["sections"]] + ["Summary"]
    # Parse MD into sections
    parsed = _parse_md_sections(md_text, section_titles)

    if not parsed:
        return

    # Compare MD and JSON section content, and apply diffs to JSON.
    # Skip if the next section heading (in JSON order) is missing from MD, as boundaries would be inaccurate.
    parsed_titles = set(parsed.keys())
    changed = False
    for idx, section in enumerate(doc["sections"]):
        title = section["title"]
        if title not in parsed_titles:
            continue

        # Check if the next section (in JSON order) exists in MD
        next_title = (
            doc["sections"][idx + 1]["title"]
            if idx + 1 < len(doc["sections"])
            else "Summary"
        )
        if next_title not in parsed_titles:
            continue

        if parsed[title] != section["content"]:
            section["content"] = parsed[title]
            changed = True

    # Also apply summary section diffs
    if "Summary" in parsed_titles and parsed["Summary"] != doc.get("summary", ""):
        doc["summary"] = parsed["Summary"]
        changed = True

    if not changed:
        return

    # Save updated JSON
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)

    # Re-output MD to match JSON content and timestamps
    _save_doc(doc, output_dir)

    logger.info(f"  MD->JSON sync: {doc['file']}")


async def generate_all_docs(
    base_output_dir: str,
    project_dep_list: list,
    llm_client: LLMClient,
    max_workers: int = MAX_WORKERS,
    changed_files: set[str] | None = None,
) -> None:
    """Main function to generate design documents for all files in topological sort order.

    Processing flow:
    1. Load the template.
    2. Topologically sort project_dependencies and arrange by level.
    3. Starting from level 0 (no dependencies), generate documents for each level in parallel.
    4. Hold generated document summaries in doc_map for use as context in subsequent levels.
    5. Save each file's document in JSON + Markdown format.

    When changed_files is specified, if a file itself has not changed and none of its
    callees (dependencies) have changed either, the existing doc.json is reused
    and the LLM call is skipped.

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
    total_levels = len(level_list)

    start_msg = (
        f"Starting design document generation. "
        f"Dependency depth levels: {total_levels}, "
        f"Total files: {sum(len(level) for level in level_list)}"
    )
    print(start_msg)
    logger.info(start_msg)

    # Dict holding design documents of processed files
    # Key: file relative path, Value: design document dict {file, sections, summary}
    doc_map: dict[str, dict] = {}

    # Per-file callee (dependency) list
    file_callees: dict[str, set[str]] = {}
    for info in project_dep_list:
        file_callees[info["file"]] = set(info.get("callees", []))

    # Track files whose documents were regenerated in this run.
    # Caller-side files that reference a regenerated file as a callee also become regeneration targets.
    regenerated_files: set[str] = set()

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
        for callee in file_callees.get(file_rel, set()):
            if callee in changed_files or callee in regenerated_files:
                return True
        return False

    def _is_doc_complete(doc: dict) -> bool:
        """Check whether a design document contains all expected sections and summary.

        Returns False if any template section is missing/extra or if the summary is empty.
        """
        expected_ids = {s["id"] for s in template["sections"]}
        actual_ids = {s["id"] for s in doc.get("sections", [])}
        if expected_ids != actual_ids:
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
            existing_doc_path = os.path.join(output_dir, "doc.json")
            if os.path.exists(existing_doc_path):
                try:
                    with open(existing_doc_path, "r", encoding="utf-8") as f:
                        existing_doc = json.load(f)
                    if _is_doc_complete(existing_doc):
                        print(f"  REUSE: {file_rel}")
                        logger.info(f"  REUSE: {file_rel}")
                        return file_rel, existing_doc
                    print(f"  INCOMPLETE: {file_rel}")
                    logger.info(f"  INCOMPLETE: {file_rel} — regenerating")
                except (json.JSONDecodeError, OSError):
                    pass  # Fall back to regeneration on read failure

        doc = await _generate_file_doc(
            file_rel, output_dir, doc_map, template, llm_client,
        )
        if doc:
            _save_doc(doc, output_dir)
            regenerated_files.add(file_rel)
            print(f"  OK: {file_rel}")
            logger.info(f"  OK: {file_rel}")
        else:
            print(f"  SKIP: {file_rel}")
            logger.warning(f"  SKIP: {file_rel}")

        return file_rel, doc

    for level_index, file_list in enumerate(level_list):
        level_msg = (
            f"{level_index + 1}/{total_levels}: "
            f"Generating documents for {len(file_list)} files"
        )
        print(level_msg)
        logger.info(level_msg)

        # Process files in the level in batches of max_workers
        for batch_start in range(0, len(file_list), max_workers):
            batch = file_list[batch_start:batch_start + max_workers]

            tasks = [asyncio.create_task(process_one(f)) for f in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"Error during document generation: {result}")
                    continue
                file_rel, doc = result
                if doc:
                    doc_map[file_rel] = doc

    done_msg = (
        f"Design document generation completed. "
        f"Generated: {len(doc_map)} / "
        f"Total: {sum(len(level) for level in level_list)}"
    )
    print(done_msg)
    logger.info(done_msg)
