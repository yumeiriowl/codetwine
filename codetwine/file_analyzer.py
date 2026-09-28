import os
import logging
from codetwine.parsers.ts_parser import parse_file
from codetwine.extractors.definitions import (
    DefinitionInfo,
    extract_definitions,
    select_top_level_definitions,
)
from codetwine.extractors.cobol_source import CobolSource
from codetwine.extractors.usage_analysis import (
    build_usage_info_list,
    build_same_file_usages,
    build_caller_usages,
    build_cobol_usage_info_list,
    build_cobol_same_file_usages,
)
from codetwine.cobol_file_index import cobol_reference_target_list
from codetwine.import_to_path import (
    build_symbol_to_file_map,
    get_import_params,
)
from codetwine.extractors.imports import extract_imports
from codetwine.config.settings import EXT_TO_DEFINITION_DICT, language_ext

logger = logging.getLogger(__name__)


def _definition_entry(definition: DefinitionInfo, content_line_list: list[str]) -> dict:
    """Return the entry of one definition in the "definitions" list.

    Args:
        definition: The definition.
        content_line_list: The lines of the file.

    Returns:
        {"name", "type", "start_line", "end_line", "context"}, with "name_line",
        "level" and "is_group" after "end_line" when the definition has them.
    """
    entry = {
        "name":       definition.name,
        "type":       definition.type,
        "start_line": definition.start_line,
        "end_line":   definition.end_line,
    }
    for key in ("name_line", "level", "is_group"):
        value = getattr(definition, key)
        if value is not None:
            entry[key] = value
    entry["context"] = "\n".join(content_line_list[definition.start_line - 1 : definition.end_line])
    return entry


def get_file_dependencies(
    target_file: str,
    project_dir: str,
    project_file_set: set[str],
    source_root_set: set[str],
    caller_map: dict[str, list[str]],
) -> dict:
    """Called for each file from process_all_files, returns a dict containing definition info,
    callee_usages, same_file_usages, and caller_usages that serves as the source data for
    file_dependencies.json.

    project_file_set, source_root_set and caller_map are the same for every file of one
    project; the caller builds them once and passes the same values to every call.

    "language" is the extension whose language settings the file is analyzed with
    (language_ext), "" for a file without a language. Such a file is not parsed: its lists
    come back empty.
    A definition of a COBOL file or a BMS source also has "name_line", and a data item
    "level" and "is_group". The references of a COBOL file are resolved with OF / IN
    qualification (cobol_reference_target_list).

    Args:
        target_file: Absolute path of the target file to analyze.
        project_dir: Absolute path to the project root.
        project_file_set: Set of relative paths of the project files that have a language.
        source_root_set: Source root prefixes present in the project (e.g. "src/main/java/").
        caller_map: A {file relative path: list of files depending on it} dict.

    Returns:
        A dict with {"file", "language", "definitions", "callee_usages",
        "same_file_usages", "caller_usages"} keys.
    """
    target_file_rel = os.path.relpath(target_file, project_dir).replace("\\", "/")
    file_ext = language_ext(target_file)
    # Per-language definition extraction settings (None for a file without a language)
    definition_dict = EXT_TO_DEFINITION_DICT.get(file_ext)
    if definition_dict is None:
        return {
            "file":          target_file_rel,
            "language":      file_ext,
            "definitions":   [],
            "callee_usages": [],
            "same_file_usages": [],
            "caller_usages": [],
        }

    root_node, content = parse_file(target_file)

    # Convert content to text lines and extract source code from each definition's line range
    content_line_list = content.decode("utf-8").splitlines()
    definition_info_list = extract_definitions(root_node, definition_dict)
    definition_list = [
        _definition_entry(definition, content_line_list) for definition in definition_info_list
    ]

    # import / usage analysis
    usage_list: list[dict] = []
    same_file_usages: list[dict] = []
    caller_usages: list[dict] = []

    language, import_query_str = get_import_params(file_ext)

    if isinstance(root_node, CobolSource):
        # Resolve each reference, with OF / IN qualification, to this file or another
        target_list = cobol_reference_target_list(
            root_node, target_file_rel, project_file_set, project_dir,
        )
        usage_list = build_cobol_usage_info_list(target_list, target_file_rel, project_dir)
        same_file_usages = build_cobol_same_file_usages(
            target_list, target_file_rel, definition_list,
        )
    elif language:
        # Parse import statements and create an "imported name -> dependency file" dict
        import_info_list = extract_imports(root_node, language, import_query_str)
        symbol_to_file_map, alias_to_original = build_symbol_to_file_map(
            import_info_list,
            target_file_rel,
            project_file_set,
            file_ext,
            project_dir,
            source_root_set,
        )

        # Get the list of usage locations and dependency target source code
        usage_list = build_usage_info_list(
            root_node,
            symbol_to_file_map,
            project_dir,
            file_ext,
            alias_to_original,
        )

        # Collect locations where names defined in this file are used within this file.
        # Not tracked: names imported from other project files, and names bound by any import
        # statement that the file defines only inside another definition (a method)
        top_level_name_set = {d.name for d in select_top_level_definitions(definition_info_list)}
        import_name_set = set(symbol_to_file_map)
        for import_info in import_info_list:
            import_name_set.update(n for n in import_info.names if n not in top_level_name_set)
        same_file_usages = build_same_file_usages(
            root_node, definition_list, file_ext, import_name_set,
        )

    if language:
        # Collect locations where functions/classes/variables defined in this file are used in other project files
        caller_usages = build_caller_usages(
            target_file_rel, caller_map.get(target_file_rel, []),
            project_dir, project_file_set, source_root_set,
        )

    return {
        "file":          target_file_rel,
        "language":      file_ext,
        "definitions":   definition_list,
        "callee_usages": usage_list,
        "same_file_usages": same_file_usages,
        "caller_usages": caller_usages,
    }
