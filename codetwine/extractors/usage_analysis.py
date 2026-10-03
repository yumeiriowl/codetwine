import os
import logging
from collections.abc import Callable
from tree_sitter import Node
from codetwine.parsers.ts_parser import parse_file
from codetwine.utils.file_utils import line_list_of, read_source
from codetwine.extractors.cobol_source import CobolSource
from codetwine.extractors.definitions import DefinitionInfo
from codetwine.extractors.usages import symbol_part_list, usage_root_name
from codetwine.extractors.definition_source import (
    extract_callee_source,
    extract_definition_source,
    find_definition,
)
from codetwine.extractors.r_source import r_definition_list
from codetwine.cobol_file_index import CobolReferenceTarget
from codetwine.csharp_namespace_index import CsharpReferenceTarget
from codetwine.import_reference import ImportReferenceTarget
from codetwine.r_name_index import RReferenceTarget
from codetwine.reference_target import ReferenceTarget, reference_kind, reference_target_list

logger = logging.getLogger(__name__)

# Maximum number of usage lines of one name whose surrounding code becomes usage_context
_MAX_CONTEXT_LOCATION = 2

# Number of lines kept before and after a usage line in usage_context
_CONTEXT_RADIUS = 3


def _group_other_file_target_list(
    target_list: list[ReferenceTarget],
    file_rel: str,
    target_context: Callable[[ReferenceTarget], str | None],
) -> list[dict]:
    """Group the resolved references of a file that lead to another file.

    Args:
        target_list: The resolved references of the file.
        file_rel: Relative path of the file.
        target_context: Returns the source text of the definition a reference leads
            to; called for the first reference of each group.

    Returns:
        One {"lines", "name", "from", "target_context"} dict per (file, name).
    """
    usage_group_map: dict[tuple[str, str], dict] = {}
    for target in target_list:
        if target.file_rel == file_rel:
            continue
        group_key = (target.file_rel, target.name)
        if group_key in usage_group_map:
            usage_group_map[group_key]["lines"].append(target.line)
            continue
        usage_group_map[group_key] = {
            "lines":          [target.line],
            "name":           target.name,
            "from":           target.file_rel,
            "target_context": target_context(target),
        }

    for entry in usage_group_map.values():
        entry["lines"] = sorted(set(entry["lines"]))
    return list(usage_group_map.values())


def _definition_range_dict(
    definition_list: list[DefinitionInfo],
) -> dict[str, list[tuple[int, int]]]:
    """Return {definition name: (start_line, end_line) of each definition of that name}."""
    range_dict: dict[str, list[tuple[int, int]]] = {}
    for definition in definition_list:
        range_dict.setdefault(definition.name, []).append(
            (definition.start_line, definition.end_line)
        )
    return range_dict


def _cobol_own_range_function(
    definition_list: list[DefinitionInfo], root_node: Node | CobolSource,
) -> Callable[[CobolReferenceTarget], list[tuple[int, int]]]:
    """Return the function giving the lines of the definition a COBOL reference resolved to."""
    def cobol_range_list(target: CobolReferenceTarget) -> list[tuple[int, int]]:
        """Return the lines of the definition a COBOL reference resolved to."""
        if target.definition is None:
            return []
        return [(target.definition.start_line, target.definition.end_line)]

    return cobol_range_list


def _r_own_range_function(
    definition_list: list[DefinitionInfo], root_node: Node | CobolSource,
) -> Callable[[RReferenceTarget], list[tuple[int, int]]]:
    """Return the function giving the lines of the top-level definitions of the name of an R reference.

    The members of a class and the calls written for a name defined elsewhere
    (setMethod) are not such definitions.
    """
    range_dict = _definition_range_dict([
        definition for definition in r_definition_list(root_node)
        if not definition.is_member and not definition.is_attach
    ])

    def r_range_list(target: RReferenceTarget) -> list[tuple[int, int]]:
        """Return the lines of the top-level definitions of the name of an R reference."""
        return range_dict.get(usage_root_name(target.name, range_dict), [])

    return r_range_list


def _name_own_range_function(
    definition_list: list[DefinitionInfo], root_node: Node | CobolSource,
) -> Callable[[CsharpReferenceTarget | ImportReferenceTarget], list[tuple[int, int]]]:
    """Return the function giving the lines of the definitions the name of a reference names.

    These are the lines of the member a name with several parts names (Cfg.Max -> Max),
    else the lines of every definition named like the first part of the name as it is
    written, else like the first part of the name of the definition it resolves to
    (Rust: self::parse -> parse).
    """
    range_dict = _definition_range_dict(definition_list)

    def name_range_list(
        target: CsharpReferenceTarget | ImportReferenceTarget,
    ) -> list[tuple[int, int]]:
        """Return the lines of the definitions the name of a reference names."""
        part_list = symbol_part_list(target.definition_name)
        if len(part_list) > 1:
            definition = find_definition(definition_list, target.definition_name)
            if definition is not None and definition.name == part_list[-1]:
                return [(definition.start_line, definition.end_line)]
        return (
            range_dict.get(usage_root_name(target.name, range_dict))
            or range_dict.get(usage_root_name(target.definition_name, range_dict), [])
        )

    return name_range_list


# Reference kind of a language -> function that returns, for the definitions and the
# root node of a file, the function from a same-file reference to the line ranges of
# the definition it names
_OWN_RANGE_FUNCTION_DICT: dict[str, Callable[..., Callable[..., list[tuple[int, int]]]]] = {
    "cobol":  _cobol_own_range_function,
    "r":      _r_own_range_function,
    "csharp": _name_own_range_function,
    "import": _name_own_range_function,
}


def _group_same_file_target_list(
    target_list: list[ReferenceTarget],
    file_rel: str,
    own_range_list: Callable[[ReferenceTarget], list[tuple[int, int]]],
) -> list[dict]:
    """Group the resolved references of a file that lead to the file itself.

    A reference written inside the lines of the definition it names (the definition's
    own name, a recursive call) is left out.

    Args:
        target_list: The resolved references of the file.
        file_rel: Relative path of the file.
        own_range_list: Returns the line ranges of the definition a reference names
            (_OWN_RANGE_FUNCTION_DICT).

    Returns:
        One {"lines", "name"} dict per name.
    """
    usage_group_map: dict[str, dict] = {}
    for target in target_list:
        if target.file_rel != file_rel:
            continue
        if any(start <= target.line <= end for start, end in own_range_list(target)):
            continue
        entry = usage_group_map.setdefault(target.name, {"lines": [], "name": target.name})
        entry["lines"].append(target.line)

    for entry in usage_group_map.values():
        entry["lines"] = sorted(set(entry["lines"]))
    return list(usage_group_map.values())


def _cobol_text_function(project_dir: str) -> Callable[[CobolReferenceTarget], str | None]:
    """Return the function giving the source text of the definition a COBOL reference resolved to."""
    def cobol_definition_text(target: CobolReferenceTarget) -> str | None:
        """Return the source text of the definition a COBOL reference resolves to."""
        if target.definition is None:
            return None
        target_source = parse_file(os.path.join(project_dir, target.file_rel))[0]
        return target_source.definition_text(target.definition)

    return cobol_definition_text


def _r_text_function(project_dir: str) -> Callable[[RReferenceTarget], str]:
    """Return the function giving the lines of the definition an R reference resolved to.

    The lines are start_line to end_line of the target; each file is read once.
    """
    line_list_dict: dict[str, list[str]] = {}

    def r_definition_line_text(target: RReferenceTarget) -> str:
        """Return the lines of the definition an R reference resolves to."""
        if target.file_rel not in line_list_dict:
            target_text = read_source(os.path.join(project_dir, target.file_rel))[0]
            line_list_dict[target.file_rel] = line_list_of(target_text)
        return "\n".join(line_list_dict[target.file_rel][target.start_line - 1:target.end_line])

    return r_definition_line_text


def _csharp_text_function(project_dir: str) -> Callable[[CsharpReferenceTarget], str | None]:
    """Return the function giving the source text of the declaration a C# reference resolved to.

    The declaration is the one named by the definition_name of the target that starts
    on its definition_line (extract_definition_source).
    """
    def csharp_definition_text(target: CsharpReferenceTarget) -> str | None:
        """Return the source text of the declaration a C# reference resolves to."""
        return extract_definition_source(
            target.file_rel, target.definition_name, target.definition_line, project_dir,
        )

    return csharp_definition_text


def _import_text_function(project_dir: str) -> Callable[[ImportReferenceTarget], str | None]:
    """Return the function giving the source text of the definition named by the definition_name of a target (extract_callee_source)."""
    def named_definition_text(target: ImportReferenceTarget) -> str | None:
        """Return the source text of the definition named by the definition_name of a target."""
        return extract_callee_source(target.file_rel, target.definition_name, project_dir)

    return named_definition_text


# Reference kind of a language -> function that returns, for a project root, the function
# from a reference to the source text of the definition it leads to (None when the target
# has no definition or the definition is not found)
_TARGET_CONTEXT_FUNCTION_DICT: dict[str, Callable[[str], Callable[..., str | None]]] = {
    "cobol":  _cobol_text_function,
    "r":      _r_text_function,
    "csharp": _csharp_text_function,
    "import": _import_text_function,
}


def build_callee_usages(
    target_list: list[ReferenceTarget], file_rel: str, project_dir: str,
) -> list[dict]:
    """Build the callee_usages of a file from its resolved references.

    The references that resolve to another file are grouped by (file, name); the
    target_context of a group is the source text of the definition its first reference
    resolves to, read the way the reference kind of the file gives
    (_TARGET_CONTEXT_FUNCTION_DICT).

    Args:
        target_list: Return value of reference_target_list.
        file_rel: Relative path of the file.
        project_dir: Absolute path to the project root.

    Returns:
        A list of {"lines", "name", "from", "target_context"} dicts. Empty for a file
        without a language.
    """
    file_kind = reference_kind(os.path.join(project_dir, file_rel))
    if file_kind is None:
        return []
    return _group_other_file_target_list(
        target_list, file_rel, _TARGET_CONTEXT_FUNCTION_DICT[file_kind](project_dir),
    )


def build_same_file_usages(
    target_list: list[ReferenceTarget],
    file_rel: str,
    project_dir: str,
    definition_list: list[DefinitionInfo],
    root_node: Node | CobolSource,
) -> list[dict]:
    """Build the same_file_usages of a file from its resolved references.

    The references that resolve to the file itself are grouped by name. A reference
    written inside the lines of the definition it names (the definition's own name, a
    recursive call) is left out; the lines of that definition are found the way the
    reference kind of the file gives (_OWN_RANGE_FUNCTION_DICT).

    Args:
        target_list: Return value of reference_target_list.
        file_rel: Relative path of the file.
        project_dir: Absolute path to the project root.
        definition_list: The file's definitions, sorted by start_line.
        root_node: The AST root node of the file, or the CobolSource of a COBOL file.

    Returns:
        A list of {"lines", "name"} dicts. Empty for a file without a language.
    """
    file_kind = reference_kind(os.path.join(project_dir, file_rel))
    if file_kind is None:
        return []
    return _group_same_file_target_list(
        target_list, file_rel, _OWN_RANGE_FUNCTION_DICT[file_kind](definition_list, root_node),
    )


def _group_caller_usage_list(
    target_list: list[ReferenceTarget], caller_rel: str,
) -> dict[str, dict]:
    """Group the references of a caller by name.

    Args:
        target_list: The caller's references that resolve to one file.
        caller_rel: Relative path of the caller file.

    Returns:
        A {name: {"lines", "name", "file"}} dict; lines are sorted without duplicates.
    """
    group_dict: dict[str, dict] = {}
    for target in target_list:
        group = group_dict.setdefault(
            target.name, {"lines": [], "name": target.name, "file": caller_rel},
        )
        group["lines"].append(target.line)

    for group in group_dict.values():
        group["lines"] = sorted(set(group["lines"]))
    return group_dict


def _attach_usage_context(group_dict: dict[str, dict], caller_abs: str) -> None:
    """Add usage_context, the code around the first usage lines, to each group.

    Up to _MAX_CONTEXT_LOCATION lines of each group are taken, each with
    _CONTEXT_RADIUS lines before and after, joined by "\n...\n". The file is decoded
    by read_source(). Nothing is added when the caller file cannot be read.

    Args:
        group_dict: Return value of _group_caller_usage_list; modified in place.
        caller_abs: Absolute path of the caller file.
    """
    try:
        caller_source_line_list = line_list_of(read_source(caller_abs)[0])
    except OSError:
        return
    if not caller_source_line_list:
        return

    line_count = len(caller_source_line_list)
    for group in group_dict.values():
        context_part_list = []
        for line_no in group["lines"][:_MAX_CONTEXT_LOCATION]:
            start = max(0, line_no - 1 - _CONTEXT_RADIUS)
            end = min(line_count, line_no - 1 + _CONTEXT_RADIUS + 1)
            context_part_list.append("\n".join(caller_source_line_list[start:end]))
        group["usage_context"] = "\n...\n".join(context_part_list)


def build_caller_usages(
    target_file_rel: str,
    caller_file_list: list[str],
    project_dir: str,
    project_file_set: set[str],
) -> list[dict]:
    """Collect the lines where names defined in this file are used in other project
    files, producing data for the caller_usages JSON output.

    The references of each caller are resolved as get_file_dependencies resolves them
    for the caller itself (reference_target_list); the ones that lead to this file are
    grouped by name.

    Args:
        target_file_rel: Relative path of this file from the project root.
        caller_file_list: Relative paths of the files depending on this file.
        project_dir: Absolute path to the project root.
        project_file_set: Set of file paths within the project.

    Returns:
        A list of {"lines", "name", "file", "usage_context"} dicts.
    """
    caller_usages: list[dict] = []
    for caller_rel in caller_file_list:
        target_list = [
            target
            for target in reference_target_list(caller_rel, project_file_set, project_dir)
            if target.file_rel == target_file_rel
        ]
        if not target_list:
            continue
        group_dict = _group_caller_usage_list(target_list, caller_rel)
        _attach_usage_context(group_dict, os.path.join(project_dir, caller_rel))
        caller_usages.extend(group_dict.values())
    return caller_usages
