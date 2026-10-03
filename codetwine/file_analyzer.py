import os
import logging
from codetwine.parsers.ts_parser import parse_file
from codetwine.extractors.definitions import DefinitionInfo, extract_definitions
from codetwine.extractors.usage_analysis import (
    build_callee_usages,
    build_same_file_usages,
    build_caller_usages,
)
from codetwine.reference_target import reference_target_list
from codetwine.config.settings import EXT_TO_DEFINITION_DICT, language_ext
from codetwine.utils.file_utils import detected_encoding, line_list_of

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
    caller_map: dict[str, list[str]],
) -> dict:
    """Called for each file from process_all_files, returns a dict containing definition info,
    callee_usages, same_file_usages, and caller_usages that serves as the source data for
    file_dependencies.json.

    project_file_set and caller_map are the same for every file of one project; the
    caller builds them once and passes the same values to every call.

    "language" is the extension whose language settings the file is analyzed with
    (language_ext), "" for a file without a language. Such a file is not parsed: its lists
    come back empty.
    "detected_encoding" is the encoding read_source() had to detect for the file
    (detected_encoding), "" when it reads the file as UTF-8 with replacement, and None
    when a BOM, UTF-8 or SOURCE_ENCODING decodes it or the file has no language.
    A definition of a COBOL file or a BMS source also has "name_line", and a data item
    "level" and "is_group". The references of the file, and of the files depending on
    it, are resolved by reference_target_list().

    Args:
        target_file: Absolute path of the target file to analyze.
        project_dir: Absolute path to the project root.
        project_file_set: Set of relative paths of the project files that have a language.
        caller_map: A {file relative path: list of files depending on it} dict.

    Returns:
        A dict with {"file", "language", "detected_encoding", "definitions",
        "callee_usages", "same_file_usages", "caller_usages"} keys.
    """
    target_file_rel = os.path.relpath(target_file, project_dir).replace("\\", "/")
    file_ext = language_ext(target_file)
    # Per-language definition extraction settings (None for a file without a language)
    definition_dict = EXT_TO_DEFINITION_DICT.get(file_ext)
    if definition_dict is None:
        return {
            "file":          target_file_rel,
            "language":      file_ext,
            "detected_encoding": None,
            "definitions":   [],
            "callee_usages": [],
            "same_file_usages": [],
            "caller_usages": [],
        }

    root_node, content = parse_file(target_file)

    # Convert content to text lines and extract source code from each definition's line range
    content_line_list = line_list_of(content.decode("utf-8"))
    definition_info_list = extract_definitions(root_node, definition_dict)
    definition_list = [
        _definition_entry(definition, content_line_list) for definition in definition_info_list
    ]

    # Resolve each reference of the file to this file or another, and collect the
    # references of the files depending on this file that lead to it
    target_list = reference_target_list(target_file_rel, project_file_set, project_dir)
    usage_list = build_callee_usages(target_list, target_file_rel, project_dir)
    same_file_usages = build_same_file_usages(
        target_list, target_file_rel, project_dir, definition_info_list, root_node,
    )
    caller_usages = build_caller_usages(
        target_file_rel, caller_map.get(target_file_rel, []), project_dir, project_file_set,
    )

    return {
        "file":          target_file_rel,
        "language":      file_ext,
        "detected_encoding": detected_encoding(target_file),
        "definitions":   definition_list,
        "callee_usages": usage_list,
        "same_file_usages": same_file_usages,
        "caller_usages": caller_usages,
    }
