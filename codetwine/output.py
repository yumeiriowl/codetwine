import os
import json
import logging
import textwrap
from collections.abc import Iterator
from typing import TextIO
from codetwine.utils.file_utils import (
    DOC_JSON_NAME,
    load_doc,
    load_file_dependencies,
    rel_to_copy_path,
    output_path_to_rel,
    replace_on_success,
    resolve_file_output_dir,
)

logger = logging.getLogger(__name__)

# Indentation applied to each element written into a top-level JSON array
_ARRAY_ITEM_INDENT = "    "


def _to_mermaid_label(text: str) -> str:
    """Return a text written inside the double quotes of a Mermaid node label.

    Examples:
        'say "hi".py' -> "say #quot;hi#quot;.py"

    Args:
        text: The label text.

    Returns:
        The text with each double quote written as the entity code #quot;.
    """
    return text.replace('"', "#quot;")


def to_output_path(base_output_dir: str, rel_path: str) -> str:
    """Convert a file relative path to the "project_name/copy_path" format.

    Args:
        base_output_dir: Base output directory (the trailing directory name is the project name).
        rel_path: Relative path from the project root.

    Returns:
        A string in "project_name/copy_path" format.
    """
    project_name = os.path.basename(base_output_dir)
    return f"{project_name}/{rel_to_copy_path(rel_path)}"


def build_summary_dict(
    base_output_dir: str,
    all_file_list: list[str],
    source_hash_dict: dict[str, str],
) -> dict[str, str | None]:
    """Read the summary from the doc.json of each file whose doc.json is current.

    A doc.json is current when its source_hash is the hash of the file's source in
    source_hash_dict. A doc.json that exists but cannot be read is logged with a warning.
    The consolidated outputs take the design document of a file only when the file is a
    key of the returned dict.

    Args:
        base_output_dir: Base output directory for file_dependencies.
        all_file_list: Relative paths of the files the consolidated outputs list.
        source_hash_dict: {file relative path: SHA256 hash of its source}
            (compute_file_hash); a file without an entry has no current doc.json.

    Returns:
        A {file relative path: summary text or None} dict with one entry per file whose
        doc.json is current; the value is None when that doc.json has no summary.
    """
    summary_dict: dict[str, str | None] = {}
    for file_rel in all_file_list:
        source_hash = source_hash_dict.get(file_rel)
        if source_hash is None:
            continue
        output_file_dir = resolve_file_output_dir(base_output_dir, file_rel)
        doc = load_doc(output_file_dir)
        if doc is None:
            if os.path.exists(os.path.join(output_file_dir, DOC_JSON_NAME)):
                logger.warning(f"{file_rel}: {DOC_JSON_NAME} cannot be read; its design "
                               f"document is left out of the consolidated outputs")
            continue
        if doc.get("source_hash") == source_hash:
            summary_dict[file_rel] = doc.get("summary")
    return summary_dict


def _iter_dependency_entry(
    base_output_dir: str,
    all_file_list: list[str],
    symbol_dep_dict: dict[str, dict[str, set[str]]],
    summary_dict: dict[str, str | None],
) -> Iterator[dict]:
    """Yield one {file, summary, callers, callees} entry per file, in list order.

    This is the element structure of project_dependency_summary.json's "files" and of
    the consolidated JSON's "project_dependencies". All paths are converted to the
    "project_name/copy_path" format.

    Args:
        base_output_dir: Base output directory for file_dependencies.
        all_file_list: Relative paths of the files the consolidated outputs list.
        symbol_dep_dict: Return value of build_symbol_level_dep_dict (symbol-level dependency
            info).
        summary_dict: Return value of build_summary_dict (file relative path -> summary text
            or None).

    Yields:
        A dict with {"file", "summary", "callers", "callees"} keys.
    """
    for file_rel in all_file_list:
        dep = symbol_dep_dict[file_rel]
        yield {
            "file": to_output_path(base_output_dir, file_rel),
            "summary": summary_dict.get(file_rel),
            "callers": sorted(to_output_path(base_output_dir, other) for other in dep["callers"]),
            "callees": sorted(to_output_path(base_output_dir, other) for other in dep["callees"]),
        }


def build_file_entry(base_output_dir: str, file_rel: str, with_doc: bool) -> dict | None:
    """Read one file's file_dependencies.json and doc.json into a single consolidated entry.

    This is the element structure of the consolidated JSON's "files", and the source of
    one row of the SQLite knowledge database.

    The "file" field is held once at the top level of the entry and removed from the
    nested file_dependencies and doc.

    Args:
        base_output_dir: Base output directory for file_dependencies.
        file_rel: Relative path of the file from the project root.
        with_doc: True to read doc.json as well (a file whose doc.json is current,
            build_summary_dict), False to leave it out.

    Returns:
        A dict with a "file" key plus "file_dependencies" and "doc" where those JSON
        files exist and are read. None when neither is there.
    """
    output_file_dir = resolve_file_output_dir(base_output_dir, file_rel)

    entry: dict = {"file": to_output_path(base_output_dir, file_rel)}

    # file_dependencies.json already holds paths in output format
    file_dep_dict = load_file_dependencies(output_file_dir)
    doc = load_doc(output_file_dir) if with_doc else None
    for key, content in (("file_dependencies", file_dep_dict), ("doc", doc)):
        if content is not None:
            content.pop("file", None)
            entry[key] = content

    if len(entry) == 1:
        logger.warning(f"Analysis results not found for {file_rel}")
        return None
    return entry


def _write_object_start(f: TextIO, project_name: str) -> None:
    """Write the opening of the top-level JSON object and its "project_name" member.

    Args:
        f: An open text file at its start.
        project_name: The analyzed project's name.
    """
    f.write("{\n")
    f.write(f'  "project_name": {json.dumps(project_name, ensure_ascii=False)},\n')


def _write_array_item(f: TextIO, entry: dict, is_first: bool) -> None:
    """Write one element of a top-level JSON array, with the separator before it.

    Args:
        f: An open text file positioned right after the array's "[".
        entry: The element to write.
        is_first: True for the first element of the array (no leading comma).
    """
    if not is_first:
        f.write(",\n")
    f.write(textwrap.indent(
        json.dumps(entry, indent=2, ensure_ascii=False), _ARRAY_ITEM_INDENT
    ))


def save_consolidated_json(
    base_output_dir: str,
    all_file_list: list[str],
    output_path: str,
    symbol_dep_dict: dict[str, dict[str, set[str]]],
    summary_dict: dict[str, str | None],
) -> None:
    """Consolidate the entire project's analysis results into a single JSON file.

    Combines each file's file_dependencies.json (dependency info) and doc.json (design document),
    along with the dependency graph, into one file.

    Attach the doc.json summary to each entry in project_dependencies.
    Files that are not keys of summary_dict (no current doc.json) have a null summary
    and no doc.

    All file paths in the consolidated JSON use the "project_name/copy_path" format.

    Each entry is written to the output file as soon as it is read; only one file's
    analysis results are held in memory at a time.

    The JSON is written to a temporary path and moved to output_path once it is
    complete (replace_on_success). When writing fails, the existing file at output_path
    is left as it was.

    Args:
        base_output_dir: Base output directory for file_dependencies.
        all_file_list: Relative paths of the files the consolidated outputs list.
        output_path: Output file path for the consolidated JSON.
        symbol_dep_dict: Return value of build_symbol_level_dep_dict (symbol-level dependency
            info).
        summary_dict: Return value of build_summary_dict (file relative path -> summary text
            or None, for the files whose doc.json is current).
    """
    project_name = os.path.basename(base_output_dir)
    write_count = 0

    with replace_on_success(output_path) as tmp_path, open(tmp_path, "w", encoding="utf-8") as f:
        _write_object_start(f, project_name)

        # project_dependencies: the dependency graph with each file's summary
        f.write('  "project_dependencies": [\n')
        dep_entry_iter = _iter_dependency_entry(
            base_output_dir, all_file_list, symbol_dep_dict, summary_dict
        )
        for index, dep_entry in enumerate(dep_entry_iter):
            _write_array_item(f, dep_entry, index == 0)

        # files: each file's dependency info and design document
        f.write('\n  ],\n  "files": [\n')
        for file_rel in all_file_list:
            entry = build_file_entry(base_output_dir, file_rel, file_rel in summary_dict)
            if entry is None:
                continue
            _write_array_item(f, entry, write_count == 0)
            write_count += 1

        f.write("\n  ]\n}")

    logger.info(
        f"Consolidated JSON output: {output_path} "
        f"(files: {write_count}/{len(all_file_list)})"
    )


def build_symbol_level_dep_dict(
    base_output_dir: str,
    all_file_list: list[str],
) -> dict[str, dict[str, set[str]]]:
    """Build symbol-level dependencies from each file's file_dependencies.json.

    Calculate callees from callee_usages' from field and callers from
    caller_usages' file field.
    Returns dependency info based on actual symbol usage rather than import-level.
    Only the files of all_file_list are callers and callees.

    Args:
        base_output_dir: Base output directory.
        all_file_list: Relative paths of the files the consolidated outputs list.

    Returns:
        A {file relative path: {"callers": set, "callees": set}} dict.
    """
    # Initialize empty dependency maps for all files
    dep_dict: dict[str, dict[str, set[str]]] = {
        file_rel: {"callers": set(), "callees": set()} for file_rel in all_file_list
    }

    # Collect callee/caller from each file's file_dependencies.json
    for file_rel in all_file_list:
        output_file_dir = resolve_file_output_dir(base_output_dir, file_rel)
        file_dep_dict = load_file_dependencies(output_file_dir)
        if file_dep_dict is None:
            continue

        # Add dependency target files from callee_usages' from field, and dependency
        # source files from caller_usages' file field
        for direction, usage_key, path_key in (
            ("callees", "callee_usages", "from"), ("callers", "caller_usages", "file"),
        ):
            for usage in file_dep_dict.get(usage_key, []):
                other_path = usage.get(path_key)
                if not other_path:
                    continue
                other_rel = output_path_to_rel(other_path)
                if other_rel in dep_dict:
                    dep_dict[file_rel][direction].add(other_rel)

    return dep_dict


def save_dependency_summary(
    base_output_dir: str,
    all_file_list: list[str],
    output_path: str,
    symbol_dep_dict: dict[str, dict[str, set[str]]],
    summary_dict: dict[str, str | None],
) -> None:
    """Output a lightweight JSON combining symbol-level dependencies and summaries for each file.

    Combine symbol-level dependencies from file_dependencies.json (only actually used dependencies)
    with summaries generated from design documents into a single file.

    Files without a current doc.json (no LLM used / generation failed / source changed)
    output with a null summary, with only the dependency structure included.

    The JSON is written to a temporary path and moved to output_path once it is
    complete (replace_on_success).

    Args:
        base_output_dir: Base output directory for file_dependencies.
        all_file_list: Relative paths of the files the consolidated outputs list.
        output_path: Output file path for the dependency graph + summary JSON.
        symbol_dep_dict: Return value of build_symbol_level_dep_dict (symbol-level dependency
            info).
        summary_dict: Return value of build_summary_dict (file relative path -> summary text
            or None).
    """
    project_name = os.path.basename(base_output_dir)
    write_count = 0

    with replace_on_success(output_path) as tmp_path, open(tmp_path, "w", encoding="utf-8") as f:
        _write_object_start(f, project_name)
        f.write('  "files": [\n')
        dep_entry_iter = _iter_dependency_entry(
            base_output_dir, all_file_list, symbol_dep_dict, summary_dict
        )
        for dep_entry in dep_entry_iter:
            _write_array_item(f, dep_entry, write_count == 0)
            write_count += 1
        f.write("\n  ]\n}")

    summary_count = sum(1 for summary in summary_dict.values() if summary is not None)
    logger.info(
        f"Dependency graph + summary JSON output: {output_path} "
        f"(files: {write_count}, with summary: {summary_count})"
    )


def save_dependency_graph_as_mermaid(
    base_output_dir: str,
    output_path: str,
    symbol_dep_dict: dict[str, dict[str, set[str]]],
) -> None:
    """Generate a Mermaid flowchart from the symbol-level dependency graph
    and output it as a Markdown file.

    Each file is one node labelled with its relative path; the node IDs are n0, n1, ...
    in the order of the sorted output paths. The Markdown is written to a temporary path
    and moved to output_path once it is complete (replace_on_success).

    Args:
        base_output_dir: Base output directory.
        output_path: Output file path for the Mermaid Markdown.
        symbol_dep_dict: Return value of build_symbol_level_dep_dict (symbol-level dependency
            info).
    """

    # Collect nodes and edges
    node_set: set[str] = set()
    edge_set: set[tuple] = set()

    for file_rel, dep in symbol_dep_dict.items():
        node_path = to_output_path(base_output_dir, file_rel)
        node_set.add(node_path)
        for callee in dep["callees"]:
            callee_path = to_output_path(base_output_dir, callee)
            node_set.add(callee_path)
            edge_set.add((node_path, callee_path))

    # Build the Mermaid text: one node per file labelled with its source relative path
    node_id_dict = {node_path: f"n{index}" for index, node_path in enumerate(sorted(node_set))}
    line_list = ["```mermaid", "graph LR"]

    for node_path, node_id in node_id_dict.items():
        line_list.append(f'    {node_id}["{_to_mermaid_label(output_path_to_rel(node_path))}"]')

    for src_path, dst_path in sorted(edge_set):
        line_list.append(f"    {node_id_dict[src_path]} --> {node_id_dict[dst_path]}")

    line_list.append("```")

    with replace_on_success(output_path) as tmp_path, open(tmp_path, "w", encoding="utf-8") as f:
        f.write("\n".join(line_list))
