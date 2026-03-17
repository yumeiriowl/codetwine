import os
import json
import logging
from codetwine.utils.file_utils import (
    rel_to_copy_path,
    copy_path_to_rel,
    output_path_to_rel,
    resolve_file_output_dir,
)

logger = logging.getLogger(__name__)


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


def build_summary_map(
    base_output_dir: str,
    all_file_list: list[str],
) -> dict[str, str | None]:
    """Read the summary from each file's doc.json and return it as a dict.

    Args:
        base_output_dir: Base output directory for file_dependencies.
        all_file_list: List of relative paths of files to analyze.

    Returns:
        A {file relative path: summary text or None} dict.
    """
    summary_map: dict[str, str | None] = {}
    for file_rel in all_file_list:
        output_file_dir = resolve_file_output_dir(base_output_dir, file_rel)
        doc_path = os.path.join(output_file_dir, "doc.json")
        summary = None
        if os.path.exists(doc_path):
            with open(doc_path, "r", encoding="utf-8") as f:
                doc = json.load(f)
            summary = doc.get("summary")
        summary_map[file_rel] = summary
    return summary_map


def save_consolidated_json(
    base_output_dir: str,
    all_file_list: list[str],
    output_path: str,
    symbol_deps: dict[str, dict[str, set[str]]],
    summary_map: dict[str, str | None],
) -> None:
    """Consolidate the entire project's analysis results into a single JSON file.

    Combines each file's file_dependencies.json (dependency info) and doc.json (design document),
    along with the dependency graph, into one file.

    Attach the doc.json summary to each entry in project_dependencies.
    Files without a doc.json will have a null summary.

    All file paths in the consolidated JSON use the "project_name/copy_path" format.

    Args:
        base_output_dir: Base output directory for file_dependencies.
        all_file_list: List of relative paths of files to analyze.
        output_path: Output file path for the consolidated JSON.
        symbol_deps: Return value of build_symbol_level_deps (symbol-level dependency info).
        summary_map: Return value of build_summary_map (file relative path -> summary text or None).
    """
    # Build entries for project_dependencies
    converted_deps: list[dict] = []
    for file_rel in all_file_list:
        deps = symbol_deps[file_rel]
        converted_deps.append({
            "file": to_output_path(base_output_dir, file_rel),
            "summary": summary_map.get(file_rel),
            "callers": sorted(to_output_path(base_output_dir, c) for c in deps["callers"]),
            "callees": sorted(to_output_path(base_output_dir, c) for c in deps["callees"]),
        })

    # Build consolidated entries for each file's dependency info and design document
    files_list: list[dict] = []

    for file_rel in all_file_list:
        output_file_dir = resolve_file_output_dir(base_output_dir, file_rel)

        entry: dict = {"file": to_output_path(base_output_dir, file_rel)}

        # file_dependencies.json loading
        deps_path = os.path.join(output_file_dir, "file_dependencies.json")
        if os.path.exists(deps_path):
            with open(deps_path, "r", encoding="utf-8") as f:
                file_deps = json.load(f)
            # Unify the file field at the entry's top level and remove it from the individual JSON side
            # Paths were already converted to output format during individual JSON save, so use as-is
            file_deps.pop("file", None)
            entry["file_dependencies"] = file_deps

        # doc.json loading
        doc_path = os.path.join(output_file_dir, "doc.json")
        if os.path.exists(doc_path):
            with open(doc_path, "r", encoding="utf-8") as f:
                doc = json.load(f)
            # Unify the file field at the entry's top level and remove it from the individual JSON side
            doc.pop("file", None)
            entry["doc"] = doc

        if len(entry) > 1: 
            files_list.append(entry)
        else:
            logger.warning(f"Consolidated JSON: analysis results not found for {file_rel}")

    consolidated = {
        "project_name": os.path.basename(base_output_dir),
        "project_dependencies": converted_deps,
        "files": files_list,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(consolidated, f, indent=2, ensure_ascii=False)

    logger.info(
        f"Consolidated JSON output: {output_path} "
        f"(files: {len(files_list)}/{len(all_file_list)})"
    )


def build_symbol_level_deps(
    base_output_dir: str,
    all_file_list: list[str],
) -> dict[str, dict[str, set[str]]]:
    """Build symbol-level dependencies from each file's file_dependencies.json.

    Calculate callees from callee_usages' from field and callers from
    caller_usages' file field.
    Returns dependency info based on actual symbol usage rather than import-level.

    Args:
        base_output_dir: Base output directory.
        all_file_list: List of relative paths of files to analyze.

    Returns:
        A {file relative path: {"callers": set, "callees": set}} dict.
    """
    # Initialize empty dependency maps for all files
    deps_map: dict[str, dict[str, set[str]]] = {
        f: {"callers": set(), "callees": set()} for f in all_file_list
    }

    # Collect callee/caller from each file's file_dependencies.json
    for file_rel in all_file_list:
        output_file_dir = resolve_file_output_dir(base_output_dir, file_rel)
        deps_path = os.path.join(output_file_dir, "file_dependencies.json")
        if not os.path.exists(deps_path):
            continue
        with open(deps_path, "r", encoding="utf-8") as f:
            file_deps = json.load(f)

        # Add dependency target files from callee_usages' from field
        for usage in file_deps.get("callee_usages", []):
            callee_file = usage.get("from")
            if callee_file:
                deps_map[file_rel]["callees"].add(output_path_to_rel(callee_file))

        # Add dependency source files from caller_usages' file field
        for usage in file_deps.get("caller_usages", []):
            caller_file = usage.get("file")
            if caller_file:
                deps_map[file_rel]["callers"].add(output_path_to_rel(caller_file))

    return deps_map


def save_dependency_summary(
    base_output_dir: str,
    all_file_list: list[str],
    output_path: str,
    symbol_deps: dict[str, dict[str, set[str]]],
    summary_map: dict[str, str | None],
) -> None:
    """Output a lightweight JSON combining symbol-level dependencies and summaries for each file.

    Combine symbol-level dependencies from file_dependencies.json (only actually used dependencies)
    with summaries generated from design documents into a single file.

    Files without a doc.json (no LLM used / generation failed) output with a null summary,
    with only the dependency structure included.

    Args:
        base_output_dir: Base output directory for file_dependencies.
        all_file_list: List of relative paths of files to analyze.
        output_path: Output file path for the consolidated JSON.
        symbol_deps: Return value of build_symbol_level_deps (symbol-level dependency info).
        summary_map: Return value of build_summary_map (file relative path -> summary text or None).
    """
    files_list: list[dict] = []
    for file_rel in all_file_list:
        deps = symbol_deps[file_rel]
        files_list.append({
            "file": to_output_path(base_output_dir, file_rel),
            "summary": summary_map.get(file_rel),
            "callers": sorted(to_output_path(base_output_dir, c) for c in deps["callers"]),
            "callees": sorted(to_output_path(base_output_dir, c) for c in deps["callees"]),
        })

    result = {
        "project_name": os.path.basename(base_output_dir),
        "files": files_list,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    summary_count = sum(1 for s in summary_map.values() if s is not None)
    logger.info(
        f"Dependency graph + summary JSON output: {output_path} "
        f"(files: {len(files_list)}, with summary: {summary_count})"
    )


def save_dependency_graph_as_mermaid(
    base_output_dir: str,
    output_path: str,
    symbol_deps: dict[str, dict[str, set[str]]],
) -> None:
    """Generate a Mermaid flowchart from the symbol-level dependency graph
    and output it as a Markdown file.

    Args:
        base_output_dir: Base output directory.
        output_path: Output file path for the Mermaid Markdown.
        symbol_deps: Return value of build_symbol_level_deps (symbol-level dependency info).
    """

    # Collect nodes and edges
    node_set: set[str] = set()
    edge_set: set[tuple] = set()

    for file_rel, deps in symbol_deps.items():
        output_path_file = to_output_path(base_output_dir, file_rel)
        node_set.add(output_path_file)
        for callee in deps["callees"]:
            callee_output = to_output_path(base_output_dir, callee)
            node_set.add(callee_output)
            edge_set.add((output_path_file, callee_output))

    def to_mermaid_node_id(path: str) -> str:
        """Convert a path string into a string usable as a Mermaid node ID.

        Args:
            path: The source path string.

        Returns:
            str: A string with slashes and dots replaced by "_".
        """
        return path.replace("/", "_").replace(".", "_")

    def to_display_label(path: str) -> str:
        """Convert a path in "project_name/copy_path" format to a source relative path.

        Example: "qt_project/MainWindow_cpp/MainWindow.cpp" -> "MainWindow.cpp"

        Args:
            path: A path string in "project_name/copy_path" format.

        Returns:
            str: A string with the project name removed and copy_path restored to the original relative path.
        """
        parts = path.split("/", 1)
        if len(parts) == 2:
            return copy_path_to_rel(parts[1])
        return path

    # Build the Mermaid text
    line_list = ["```mermaid", "graph LR"]

    for node_path in sorted(node_set):
        node_id = to_mermaid_node_id(node_path)
        label = to_display_label(node_path)
        line_list.append(f'    {node_id}["{label}"]')

    for src_path, dst_path in sorted(edge_set):
        line_list.append(f"    {to_mermaid_node_id(src_path)} --> {to_mermaid_node_id(dst_path)}")

    line_list.append("```")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(line_list))
