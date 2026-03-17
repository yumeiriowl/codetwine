import os
import logging
from codetwine.parsers.ts_parser import parse_file
from codetwine.extractors.definitions import extract_definitions
from codetwine.extractors.usage_analysis import (
    build_usage_info_list,
    build_caller_usages,
)
from codetwine.import_to_path import (
    build_symbol_to_file_map,
    get_import_params,
)
from codetwine.extractors.imports import extract_imports
from codetwine.config.settings import DEFINITION_DICTS

logger = logging.getLogger(__name__)


def get_file_dependencies(
    target_file: str,
    project_dir: str,
    project_dep_list: list[dict],
) -> dict:
    """Called for each file from process_all_files, returns a dict containing definition info,
    callee_usages, and caller_usages that serves as the source data for file_dependencies.json.

    Args:
        target_file: Absolute path of the target file to analyze.
        project_dir: Absolute path to the project root.
        project_dep_list: Dependency info list output by save_project_dependencies.

    Returns:
        A dict with {"file", "definitions", "callee_usages", "caller_usages"} keys.
    """
    target_file_rel = os.path.relpath(target_file, project_dir).replace("\\", "/")
    file_ext = os.path.splitext(target_file)[1].lstrip(".")
    # Per-language definition extraction settings (None for unsupported languages)
    definition_dict = DEFINITION_DICTS.get(file_ext)

    root_node, content = parse_file(target_file)

    # Convert content to text lines and extract source code from each definition's line range
    content_lines = content.decode("utf-8").splitlines()
    definition_list = [
        {
            "name":       d.name,
            "type":       d.type,
            "start_line": d.start_line,
            "end_line":   d.end_line,
            "context":    "\n".join(content_lines[d.start_line - 1 : d.end_line]),
        }
        for d in extract_definitions(root_node, definition_dict)
    ]

    # import / usage analysis
    usage_list: list = []
    caller_usages: list = []

    language, import_query_str = get_import_params(file_ext)

    if language and import_query_str:
        # Build the set of relative paths for project files
        project_file_set: set[str] = set()
        for dep_info in project_dep_list:
            project_file_set.add(dep_info["file"])

        # Parse import statements and create an "imported name -> dependency file" dict
        symbol_to_file_map, alias_to_original = build_symbol_to_file_map(
            extract_imports(root_node, language, import_query_str),
            target_file_rel,
            project_file_set,
            file_ext,
            project_dir,
        )

        # Get the list of usage locations and dependency target source code
        usage_list = build_usage_info_list(
            root_node,
            symbol_to_file_map,
            project_dir,
            file_ext,
            alias_to_original,
        )

        # Collect locations where functions/classes/variables defined in this file are used in other project files
        caller_usages = build_caller_usages(
            target_file_rel, project_dep_list,
            project_dir, project_file_set,
        )

    return {
        "file":          target_file_rel,
        "definitions":   definition_list,
        "callee_usages": usage_list,
        "caller_usages": caller_usages,
    }
