import os
import logging
from collections.abc import Callable
from dataclasses import dataclass, fields
from tree_sitter import Node
from codetwine.parsers.ts_parser import parse_file
from codetwine.utils.file_utils import line_list_of, read_source
from codetwine.extractors.cobol_source import CobolSource
from codetwine.extractors.definitions import DefinitionInfo
from codetwine.extractors.usages import symbol_part_list, usage_root_name
from codetwine.extractors.definition_source import find_definition, source_definition
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


@dataclass(frozen=True)
class _TargetDefinition:
    """The definition a reference leads to, as a usage entry carries it."""

    context: str | None      # Source text of the definition
    name: str | None         # Name of the definition, as "definitions" lists it
    start_line: int | None   # First line of the definition (1-based)
    # First byte of the definition (DefinitionInfo.start_byte); None when it has none
    start_byte: int | None = None


# A reference that leads to no definition of its file
_NO_DEFINITION = _TargetDefinition(None, None, None)


def _memo_definition_function(
    target_definition: Callable[[ReferenceTarget], _TargetDefinition],
) -> Callable[[ReferenceTarget], _TargetDefinition]:
    """Return a function that looks the definition of each distinct reference target up once.

    Args:
        target_definition: Returns the definition a reference leads to.

    Returns:
        The same function, answering from memory for a reference whose fields apart
        from its line equal those of an earlier one.
    """
    definition_dict: dict[str, _TargetDefinition] = {}

    def memo_definition(target: ReferenceTarget) -> _TargetDefinition:
        """Return the definition a reference leads to."""
        target_key = repr([
            (target_field.name, getattr(target, target_field.name))
            for target_field in fields(target) if target_field.name != "line"
        ])
        if target_key not in definition_dict:
            definition_dict[target_key] = target_definition(target)
        return definition_dict[target_key]

    return memo_definition


def _group_other_file_target_list(
    target_list: list[ReferenceTarget],
    file_rel: str,
    target_definition: Callable[[ReferenceTarget], _TargetDefinition],
) -> list[dict]:
    """Group the resolved references of a file that lead to another file.

    Args:
        target_list: The resolved references of the file.
        file_rel: Relative path of the file.
        target_definition: Returns the definition a reference leads to.

    Returns:
        One {"lines", "name", "from", "target_context", "target_name",
        "target_start_line"} dict per (file, name, definition): a name that leads to
        two definitions of a file (two overloads) gives two.
    """
    target_definition = _memo_definition_function(target_definition)
    usage_group_dict: dict[tuple, dict] = {}
    for target in target_list:
        if target.file_rel == file_rel:
            continue
        definition = target_definition(target)
        group_key_tuple = (target.file_rel, target.name, definition.name, definition.start_line)
        if group_key_tuple in usage_group_dict:
            usage_group_dict[group_key_tuple]["lines"].append(target.line)
            continue
        usage_group_dict[group_key_tuple] = {
            "lines":             [target.line],
            "name":              target.name,
            "from":              target.file_rel,
            "target_context":    definition.context,
            "target_name":       definition.name,
            "target_start_line": definition.start_line,
        }

    _sort_group_line(usage_group_dict)
    return list(usage_group_dict.values())


def _sort_group_line(group_dict: dict[tuple, dict] | dict[str, dict]) -> None:
    """Sort the "lines" of each group without duplicates, in place."""
    for group in group_dict.values():
        group["lines"] = sorted(set(group["lines"]))


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


# A function from a same-file reference and the definition it leads to, to the line
# ranges of the definition the reference names
_OwnRangeFunction = Callable[[ReferenceTarget, _TargetDefinition], list[tuple[int, int]]]


def _cobol_own_range_function(
    definition_list: list[DefinitionInfo], root_node: Node | CobolSource | None,
) -> _OwnRangeFunction:
    """Return the function giving the lines of the definition a COBOL reference resolved to."""
    def cobol_range_list(
        target: CobolReferenceTarget, definition: _TargetDefinition,
    ) -> list[tuple[int, int]]:
        """Return the lines of the definition a COBOL reference resolved to."""
        if target.definition is None:
            return []
        return [(target.definition.start_line, target.definition.end_line)]

    return cobol_range_list


def _jcl_own_range_function(
    definition_list: list[DefinitionInfo], root_node: Node | CobolSource | None,
) -> _OwnRangeFunction:
    """Return the function giving no lines for a JCL or CSD reference: each one is kept."""
    def jcl_range_list(
        target: CobolReferenceTarget, definition: _TargetDefinition,
    ) -> list[tuple[int, int]]:
        """Return no lines."""
        return []

    return jcl_range_list


def _r_own_range_function(
    definition_list: list[DefinitionInfo], root_node: Node | CobolSource | None,
) -> _OwnRangeFunction:
    """Return the function giving the lines of the top-level definitions of the name of an R reference.

    An R reference leads to every definition of its name; the members of a class and
    the calls written for a name defined elsewhere (setMethod) are not top-level
    definitions.
    """
    range_dict = _definition_range_dict([
        definition for definition in r_definition_list(root_node)
        if not definition.is_member and not definition.is_attach
    ])

    def r_range_list(
        target: RReferenceTarget, definition: _TargetDefinition,
    ) -> list[tuple[int, int]]:
        """Return the lines of the top-level definitions of the name of an R reference."""
        return range_dict.get(usage_root_name(target.name, range_dict), [])

    return r_range_list


def _name_own_range_function(
    definition_list: list[DefinitionInfo], root_node: Node | CobolSource | None,
) -> _OwnRangeFunction:
    """Return the function giving the lines of the definition a C# or import reference names.

    These are the lines of the definition the reference leads to (target_definition):
    of the definitions of its name, the one that starts at its first byte (at its first
    line when either has no bytes). For a reference written with several parts exactly
    as the name of that definition, also the first line of every other definition of
    the name (C++: the name of W::W(double b) {} written after W::W(int a) {}). For a
    reference that leads to no definition: the lines of the definition an import
    reference with a definition_line names; else the lines of the member a name with
    several parts names (Cfg.Max -> Max), else the lines of every definition named
    like the first part of the name as it is written, else like the first part of
    the name of the definition it resolves to (Rust: self::parse -> parse).
    """
    range_dict = _definition_range_dict(definition_list)
    name_definition_dict: dict[str, list[DefinitionInfo]] = {}
    for named_definition in definition_list:
        name_definition_dict.setdefault(named_definition.name, []).append(
            named_definition,
        )

    def name_range_list(
        target: CsharpReferenceTarget | ImportReferenceTarget, definition: _TargetDefinition,
    ) -> list[tuple[int, int]]:
        """Return the lines of the definition a reference names."""
        if definition.start_line is not None:
            is_scoped_name = (
                target.name == definition.name and len(symbol_part_list(target.name)) > 1
            )
            own_range_list: list[tuple[int, int]] = []
            for named_definition in name_definition_dict.get(definition.name, []):
                if _is_target_definition(named_definition, definition):
                    own_range_list.append(
                        (named_definition.start_line, named_definition.end_line),
                    )
                elif is_scoped_name:
                    own_range_list.append(
                        (named_definition.start_line, named_definition.start_line),
                    )
            return own_range_list
        part_list = symbol_part_list(target.definition_name)
        if isinstance(target, ImportReferenceTarget) and target.definition_line is not None:
            return [
                (start_line, end_line)
                for part in part_list
                for start_line, end_line in range_dict.get(part, [])
                if start_line == target.definition_line
            ]
        if len(part_list) > 1:
            definition = find_definition(definition_list, target.definition_name)
            if definition is not None and definition.name == part_list[-1]:
                return [(definition.start_line, definition.end_line)]
        return (
            range_dict.get(usage_root_name(target.name, range_dict))
            or range_dict.get(usage_root_name(target.definition_name, range_dict), [])
        )

    return name_range_list


def _is_target_definition(
    named_definition: DefinitionInfo, definition: _TargetDefinition,
) -> bool:
    """Return whether a definition of the name of a target definition is that definition.

    Compared by the first byte when both have one, else by the first line.
    """
    if named_definition.start_byte is not None and definition.start_byte is not None:
        return named_definition.start_byte == definition.start_byte
    return named_definition.start_line == definition.start_line


def _group_same_file_target_list(
    target_list: list[ReferenceTarget],
    file_rel: str,
    own_range_list: _OwnRangeFunction,
    target_definition: Callable[[ReferenceTarget], _TargetDefinition],
) -> list[dict]:
    """Group the resolved references of a file that lead to the file itself.

    A reference written inside the lines of the definition it names (the definition's
    own name, a recursive call) is left out.

    Args:
        target_list: The resolved references of the file.
        file_rel: Relative path of the file.
        own_range_list: Returns the line ranges of the definition a reference names,
            from the reference and the definition it leads to (_ReferenceKindSetting).
        target_definition: Returns the definition a reference leads to.

    Returns:
        One {"lines", "name", "target_name", "target_start_line"} dict per
        (name, definition).
    """
    target_definition = _memo_definition_function(target_definition)
    usage_group_dict: dict[tuple, dict] = {}
    for target in target_list:
        if target.file_rel != file_rel:
            continue
        definition = target_definition(target)
        if any(
            start_line <= target.line <= end_line
            for start_line, end_line in own_range_list(target, definition)
        ):
            continue
        group_key_tuple = (target.name, definition.name, definition.start_line)
        if group_key_tuple not in usage_group_dict:
            usage_group_dict[group_key_tuple] = {
                "lines":             [],
                "name":              target.name,
                "target_name":       definition.name,
                "target_start_line": definition.start_line,
            }
        usage_group_dict[group_key_tuple]["lines"].append(target.line)

    _sort_group_line(usage_group_dict)
    return list(usage_group_dict.values())


def _cobol_definition_function(
    project_dir: str,
) -> Callable[[CobolReferenceTarget], _TargetDefinition]:
    """Return the function giving the definition a COBOL, JCL or CSD reference resolved to."""
    def cobol_definition(target: CobolReferenceTarget) -> _TargetDefinition:
        """Return the definition a COBOL, JCL or CSD reference resolves to, with its lines."""
        if target.definition is None:
            return _NO_DEFINITION
        target_source = parse_file(os.path.join(project_dir, target.file_rel))[0]
        return _TargetDefinition(
            target_source.definition_text(target.definition),
            target.definition.name, target.definition.start_line,
        )

    return cobol_definition


def _r_definition_function(project_dir: str) -> Callable[[RReferenceTarget], _TargetDefinition]:
    """Return the function giving the definition an R reference resolved to.

    The text is the lines start_line to end_line of the target; each file is read once.
    """
    line_list_dict: dict[str, list[str]] = {}

    def r_definition(target: RReferenceTarget) -> _TargetDefinition:
        """Return the definition an R reference resolves to, with its lines as text."""
        if target.file_rel not in line_list_dict:
            target_text = read_source(os.path.join(project_dir, target.file_rel))[0]
            line_list_dict[target.file_rel] = line_list_of(target_text)
        return _TargetDefinition(
            "\n".join(line_list_dict[target.file_rel][target.start_line - 1:target.end_line]),
            target.definition_name, target.start_line,
        )

    return r_definition


def _source_definition_function(
    project_dir: str,
) -> Callable[[CsharpReferenceTarget | ImportReferenceTarget], _TargetDefinition]:
    """Return the function giving the definition named by the definition_name of a target.

    Used for C# and import references. A target with a definition_line leads to the
    definition of that name that starts on that line, at its definition_start_byte
    when an import reference has one (source_definition).
    """
    def named_definition(
        target: CsharpReferenceTarget | ImportReferenceTarget,
    ) -> _TargetDefinition:
        """Return the definition named by the definition_name of a target."""
        start_byte = (
            target.definition_start_byte if isinstance(target, ImportReferenceTarget) else None
        )
        return _source_target_definition(
            target.file_rel, target.definition_name, project_dir, target.definition_line,
            start_byte,
        )

    return named_definition


def _source_target_definition(
    file_rel: str, name: str, project_dir: str, start_line: int | None = None,
    start_byte: int | None = None,
) -> _TargetDefinition:
    """Return the definition source_definition() gives as a _TargetDefinition."""
    definition_tuple = source_definition(file_rel, name, project_dir, start_line, start_byte)
    if definition_tuple is None:
        return _NO_DEFINITION
    definition, context = definition_tuple
    return _TargetDefinition(
        context, definition.name, definition.start_line, definition.start_byte,
    )


@dataclass(frozen=True)
class _ReferenceKindSetting:
    """How the references of one reference kind lead to their definitions."""

    # Returns, for a project root, the function from a reference to the definition it
    # leads to (_NO_DEFINITION when the target has no definition or it is not found)
    definition_function: Callable[[str], Callable[..., _TargetDefinition]]
    # Returns, for the definitions and the root node of a file, the _OwnRangeFunction
    # of its same-file references
    own_range_function: Callable[
        [list[DefinitionInfo], Node | CobolSource | None], _OwnRangeFunction,
    ]
    # Whether own_range_function reads the root node of the file
    is_tree_range: bool = False


# Reference kind of a language -> its _ReferenceKindSetting
_KIND_SETTING_DICT: dict[str, _ReferenceKindSetting] = {
    "cobol":  _ReferenceKindSetting(_cobol_definition_function, _cobol_own_range_function),
    "jcl":    _ReferenceKindSetting(_cobol_definition_function, _jcl_own_range_function),
    "r":      _ReferenceKindSetting(
        _r_definition_function, _r_own_range_function, is_tree_range=True,
    ),
    "csharp": _ReferenceKindSetting(_source_definition_function, _name_own_range_function),
    "import": _ReferenceKindSetting(_source_definition_function, _name_own_range_function),
}

# Reference kinds whose own_range_function reads the syntax tree
TREE_RANGE_KIND_SET = {
    kind for kind, kind_setting in _KIND_SETTING_DICT.items() if kind_setting.is_tree_range
}


def build_callee_usages(
    target_list: list[ReferenceTarget], file_rel: str, project_dir: str,
) -> list[dict]:
    """Build the callee_usages of a file from its resolved references.

    The references that resolve to another file are grouped by (file, name) and the
    definition they lead to. The target_context of a group is the source text of that
    definition, target_name the name "definitions" of that file lists it under and
    target_start_line its first line; all three are None when the references lead to
    no definition of that file (a module used as a value). The definition is read the
    way the reference kind of the file gives (_KIND_SETTING_DICT).

    Args:
        target_list: Return value of reference_target_list.
        file_rel: Relative path of the file.
        project_dir: Absolute path to the project root.

    Returns:
        A list of {"lines", "name", "from", "target_context", "target_name",
        "target_start_line"} dicts. Empty for a file without a language.
    """
    file_kind = reference_kind(os.path.join(project_dir, file_rel))
    if file_kind is None:
        return []
    return _group_other_file_target_list(
        target_list, file_rel, _KIND_SETTING_DICT[file_kind].definition_function(project_dir),
    )


def build_same_file_usages(
    target_list: list[ReferenceTarget],
    file_rel: str,
    project_dir: str,
    definition_list: list[DefinitionInfo],
    root_node: Node | CobolSource | None,
) -> list[dict]:
    """Build the same_file_usages of a file from its resolved references.

    The references that resolve to the file itself are grouped by name and the
    definition they lead to. A reference written inside the lines of the definition it
    names (the definition's own name, a recursive call) is left out; the lines of that
    definition are found the way the reference kind of the file gives
    (_KIND_SETTING_DICT): the definition the reference leads to (C#, import), the
    definition it resolved to (COBOL), every top-level definition of its name (R),
    none (JCL, CSD). target_name and target_start_line of a group are the
    name and the first line of the definition its references resolve to, None when
    they lead to no definition.

    Args:
        target_list: Return value of reference_target_list.
        file_rel: Relative path of the file.
        project_dir: Absolute path to the project root.
        definition_list: The file's definitions, sorted by start_line.
        root_node: The AST root node of the file; None for a file whose reference
            kind is not in TREE_RANGE_KIND_SET.

    Returns:
        A list of {"lines", "name", "target_name", "target_start_line"} dicts. Empty
        for a file without a language.
    """
    file_kind = reference_kind(os.path.join(project_dir, file_rel))
    if file_kind is None:
        return []
    kind_setting = _KIND_SETTING_DICT[file_kind]
    return _group_same_file_target_list(
        target_list, file_rel, kind_setting.own_range_function(definition_list, root_node),
        kind_setting.definition_function(project_dir),
    )


def _group_caller_usage_list(
    target_list: list[ReferenceTarget], caller_rel: str,
) -> dict[str, dict]:
    """Group the references of a caller by name.

    Args:
        target_list: The caller's references that resolve to one file.
        caller_rel: Relative path of the caller file.

    Returns:
        A {name: {"lines", "name", "file"}} dict, in the order the names first occur;
        lines are sorted without duplicates.
    """
    group_dict: dict[str, dict] = {}
    for target in target_list:
        group = group_dict.setdefault(
            target.name, {"lines": [], "name": target.name, "file": caller_rel},
        )
        group["lines"].append(target.line)

    _sort_group_line(group_dict)
    return group_dict


def _context_range_list(line_list: list[int], line_count: int) -> list[tuple[int, int]]:
    """Return the line ranges of the code around the first usage lines of a group.

    Up to _MAX_CONTEXT_LOCATION lines are taken, each with _CONTEXT_RADIUS lines
    before and after; ranges that overlap or touch are made one.

    Examples (_CONTEXT_RADIUS 3, 100 lines):
        [10, 30]   -> [(6, 13), (26, 33)]
        [10, 14]   -> [(6, 17)]

    Args:
        line_list: Usage lines of the group (1-based), sorted.
        line_count: Number of lines of the caller file.

    Returns:
        (start index, end index) of each range, 0-based with the end excluded, in order.
    """
    range_list: list[tuple[int, int]] = []
    for line_no in line_list[:_MAX_CONTEXT_LOCATION]:
        start_index = max(0, line_no - 1 - _CONTEXT_RADIUS)
        end_index = min(line_count, line_no - 1 + _CONTEXT_RADIUS + 1)
        if range_list and start_index <= range_list[-1][1]:
            range_list[-1] = (range_list[-1][0], max(range_list[-1][1], end_index))
        else:
            range_list.append((start_index, end_index))
    return range_list


def _attach_usage_context(group_dict: dict[str, dict], caller_abs: str) -> None:
    """Add usage_context, the code around the first usage lines, to each group.

    The ranges of _context_range_list are joined by "\n...\n". The file is decoded
    by read_source(). Nothing is added when the caller file cannot be read or is empty.

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
        group["usage_context"] = "\n...\n".join(
            "\n".join(caller_source_line_list[start_index:end_index])
            for start_index, end_index in _context_range_list(group["lines"], line_count)
        )


def build_caller_usages(
    target_file_rel: str,
    caller_file_list: list[str],
    project_dir: str,
    project_file_set: set[str],
) -> list[dict]:
    """Build the caller_usages of a file: the lines of other project files that use its names.

    The references of each caller are resolved as get_file_dependencies resolves them
    for the caller itself (reference_target_list); the ones that lead to this file are
    grouped by name.

    Args:
        target_file_rel: Relative path of this file from the project root.
        caller_file_list: Relative paths of the files depending on this file.
        project_dir: Absolute path to the project root.
        project_file_set: Set of file paths within the project.

    Returns:
        A list of {"lines", "name", "file", "usage_context"} dicts, one per (caller,
        name). An entry of a caller that cannot be read or is empty has no
        usage_context.
    """
    caller_usage_list: list[dict] = []
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
        caller_usage_list.extend(group_dict.values())
    return caller_usage_list
