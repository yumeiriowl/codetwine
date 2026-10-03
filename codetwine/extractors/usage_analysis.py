import os
import logging
from collections.abc import Callable
from tree_sitter import Node
from codetwine.parsers.ts_parser import parse_file
from codetwine.utils.file_utils import line_list_of, read_source
from codetwine.extractors.imports import ImportInfo, extract_imports
from codetwine.extractors.usages import UsageInfo, extract_usages, extract_typed_aliases, usage_root_name
from codetwine.extractors.definitions import ATTACHED_DEFINITION_TYPE_SET, extract_definitions
from codetwine.extractors.cobol_source import CobolSource
from codetwine.extractors.r_source import r_definition_list
from codetwine.extractors.dependency_graph import extract_callee_source
from codetwine.cobol_file_index import CobolReferenceTarget, cobol_reference_target_list
from codetwine.csharp_namespace_index import CsharpReferenceTarget, csharp_reference_target_list
from codetwine.r_name_index import RReferenceTarget, r_reference_target_list
from codetwine.import_to_path import (
    resolve_module_to_project_path,
    get_import_params,
    import_name_list,
    top_level_definition_names,
)
from codetwine.config.settings import (
    CSHARP_EXT_SET,
    EXT_TO_DEFINITION_DICT,
    EXT_TO_USAGE_NODE_TYPE_DICT,
    EXT_TO_IMPORT_RESOLVE_DICT,
    R_EXT_SET,
    implicit_scope_key,
    language_ext,
)

logger = logging.getLogger(__name__)

# A reference of a COBOL, C# or R file resolved to its definition
_ReferenceTarget = CobolReferenceTarget | CsharpReferenceTarget | RReferenceTarget

# Maximum number of usage lines of one name whose surrounding code becomes usage_context
_MAX_CONTEXT_LOCATION = 2

# Number of lines kept before and after a usage line in usage_context
_CONTEXT_RADIUS = 3


def _extract_typed_alias_dict(
    root_node: Node, name_set: set[str], usage_node_types: dict | None,
) -> dict[str, str]:
    """Return the variables declared with one of the given type names.

    Args:
        root_node: The AST root node of the file.
        name_set: Type names to track.
        usage_node_types: The file's EXT_TO_USAGE_NODE_TYPE_DICT entry (may be None).

    Returns:
        A {variable name: type name} dict (extract_typed_aliases). Empty when the
        language has no typed_alias_parent_types.
    """
    typed_alias_parent_types = (
        usage_node_types.get("typed_alias_parent_types", set())
        if usage_node_types else set()
    )
    return extract_typed_aliases(root_node, name_set, typed_alias_parent_types)


def build_usage_info_list(
    root_node: Node,
    symbol_to_file_map: dict[str, str],
    project_dir: str,
    file_ext: str,
    alias_to_original: dict[str, str] | None = None,
) -> list[dict]:
    """Extract usage locations of names imported from within the project and attach
    the definition source code, producing data for the callee_usages JSON output.

    When the same name appears on multiple lines, entries are merged into a single
    record with all line numbers accumulated in the lines list.

    Args:
        root_node: The AST root node of the file.
        symbol_to_file_map: A dict mapping imported names to their definition file paths.
        project_dir: Absolute path to the project root.
        file_ext: File extension (without leading ".").
        alias_to_original: A dict mapping alias names to original names (used for definition lookup).

    Returns:
        A list of dicts containing usage location information.
    """
    usage_node_types = EXT_TO_USAGE_NODE_TYPE_DICT.get(file_ext)

    # Build a variable-name -> type-name mapping from typed variable declarations
    typed_alias_dict = _extract_typed_alias_dict(
        root_node, set(symbol_to_file_map.keys()), usage_node_types
    )
    # Add alias variable names to the tracking set (map genre -> same file as Genre)
    for var_name, type_name in typed_alias_dict.items():
        if var_name not in symbol_to_file_map:
            symbol_to_file_map[var_name] = symbol_to_file_map[type_name]

    usage_info_list = extract_usages(
        root_node, set(symbol_to_file_map.keys()), usage_node_types
    )

    # Key: (definition file path, project-internal imported name) -> merged entry
    usage_group_map: dict[tuple, dict] = {}

    for usage in usage_info_list:
        # For attribute access like "helper.process", the leading "helper" is the name from the import statement
        root_symbol = usage_root_name(usage.name, symbol_to_file_map)

        # Remap alias variable names back to original type names (genre -> Genre)
        if root_symbol in typed_alias_dict:
            original_type = typed_alias_dict[root_symbol]
            remapped_name = original_type + usage.name[len(root_symbol):]
            root_symbol = original_type
        else:
            remapped_name = usage.name

        source_file = symbol_to_file_map[root_symbol]
        group_key = (source_file, remapped_name)

        if group_key in usage_group_map:
            usage_group_map[group_key]["lines"].append(usage.line)
        else:
            # If an alias exists, search for the definition using the original name
            search_name = remapped_name
            if alias_to_original and root_symbol in alias_to_original:
                original = alias_to_original[root_symbol]
                search_name = original + remapped_name[len(root_symbol):]

            # First occurrence of this name: retrieve source code from the definition file within the project
            source_code = extract_callee_source(
                source_file,
                search_name,
                project_dir,
            )
            usage_group_map[group_key] = {
                "lines":          [usage.line],
                "name":           remapped_name,
                "from":           source_file,
                "target_context": source_code,
            }

    # Remove duplicates from the lines list of each group
    for entry in usage_group_map.values():
        entry["lines"] = sorted(set(entry["lines"]))

    return list(usage_group_map.values())


def build_same_file_usages(
    root_node: Node,
    definition_list: list[dict],
    file_ext: str,
    import_name_set: set[str],
) -> list[dict]:
    """Collect the lines where names defined in this file are used within the same
    file, producing data for the same_file_usages JSON output.

    A definition name that is also in import_name_set is not tracked. A usage inside
    the line range of a definition with the same name (the definition's own name, a
    recursive call) is left out. When the same name appears on multiple lines, entries
    are merged into a single record with all line numbers accumulated in the lines list.

    Args:
        root_node: The AST root node of the file.
        definition_list: The file's definitions (dicts with name, start_line, end_line).
        file_ext: File extension (without leading ".").
        import_name_set: Names bound by the file's import statements.

    Returns:
        A list of {"lines", "name"} dicts.
    """
    # Definition name -> line ranges of the definitions with that name.
    # A name carried only by definitions in ATTACHED_DEFINITION_TYPE_SET (Rust impl blocks) is not tracked
    line_range_dict: dict[str, list[tuple[int, int]]] = {}
    for definition in definition_list:
        name = definition["name"]
        if name and name not in import_name_set:
            line_range_dict.setdefault(name, []).append(
                (definition["start_line"], definition["end_line"])
            )
    own_name_set = {
        definition["name"] for definition in definition_list
        if definition.get("type") not in ATTACHED_DEFINITION_TYPE_SET
    }
    line_range_dict = {
        name: range_list for name, range_list in line_range_dict.items() if name in own_name_set
    }

    usage_info_list = extract_usages(
        root_node, set(line_range_dict), EXT_TO_USAGE_NODE_TYPE_DICT.get(file_ext)
    )

    # Key: used name -> merged entry
    usage_group_map: dict[str, dict] = {}
    for usage in usage_info_list:
        # For attribute access like "logger.info", the leading "logger" is the definition name
        root_symbol = usage_root_name(usage.name, line_range_dict)
        if any(start <= usage.line <= end for start, end in line_range_dict[root_symbol]):
            continue
        entry = usage_group_map.setdefault(usage.name, {"lines": [], "name": usage.name})
        entry["lines"].append(usage.line)

    # Remove duplicates from the lines list of each group
    for entry in usage_group_map.values():
        entry["lines"] = sorted(set(entry["lines"]))

    return list(usage_group_map.values())


def _group_other_file_target_list(
    target_list: list[_ReferenceTarget],
    file_rel: str,
    target_context: Callable[[_ReferenceTarget], str | None],
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


def _group_same_file_target_list(
    target_list: list[_ReferenceTarget], file_rel: str, definition_list: list[dict],
) -> list[dict]:
    """Group the resolved references of a file that lead to the file itself.

    A reference inside the line range of a definition named like the first part of its
    name is left out, as in build_same_file_usages.

    Args:
        target_list: The resolved references of the file.
        file_rel: Relative path of the file.
        definition_list: The file's definitions (dicts with name, start_line, end_line).

    Returns:
        One {"lines", "name"} dict per name.
    """
    line_range_dict: dict[str, list[tuple[int, int]]] = {}
    for definition in definition_list:
        line_range_dict.setdefault(definition["name"], []).append(
            (definition["start_line"], definition["end_line"])
        )

    usage_group_map: dict[str, dict] = {}
    for target in target_list:
        if target.file_rel != file_rel:
            continue
        root_name = usage_root_name(target.name, line_range_dict)
        if any(
            start <= target.line <= end for start, end in line_range_dict.get(root_name, [])
        ):
            continue
        entry = usage_group_map.setdefault(target.name, {"lines": [], "name": target.name})
        entry["lines"].append(target.line)

    for entry in usage_group_map.values():
        entry["lines"] = sorted(set(entry["lines"]))
    return list(usage_group_map.values())


def build_cobol_usage_info_list(
    target_list: list[CobolReferenceTarget], file_rel: str, project_dir: str,
) -> list[dict]:
    """Build the callee_usages of a COBOL file from its resolved references.

    The references that resolve to another file are grouped by (file, name); the
    target_context of a group is the source text of the definition its first reference
    resolves to.

    Args:
        target_list: Return value of cobol_reference_target_list.
        file_rel: Relative path of the file.
        project_dir: Absolute path to the project root.

    Returns:
        A list of {"lines", "name", "from", "target_context"} dicts.
    """
    def definition_text(target: CobolReferenceTarget) -> str | None:
        """Return the source text of the definition a reference resolves to."""
        if target.definition is None:
            return None
        target_source = parse_file(os.path.join(project_dir, target.file_rel))[0]
        return target_source.definition_text(target.definition)

    return _group_other_file_target_list(target_list, file_rel, definition_text)


def build_cobol_same_file_usages(
    target_list: list[CobolReferenceTarget], file_rel: str, definition_list: list[dict],
) -> list[dict]:
    """Build the same_file_usages of a COBOL file from its resolved references.

    The references that resolve to the file itself are grouped by name. A reference
    inside the line range of a definition with the same name is left out, as in
    build_same_file_usages.

    Args:
        target_list: Return value of cobol_reference_target_list.
        file_rel: Relative path of the file.
        definition_list: The file's definitions (dicts with name, start_line, end_line).

    Returns:
        A list of {"lines", "name"} dicts.
    """
    return _group_same_file_target_list(target_list, file_rel, definition_list)


def build_csharp_usage_info_list(
    target_list: list[CsharpReferenceTarget], file_rel: str, project_dir: str,
) -> list[dict]:
    """Build the callee_usages of a C# file from its resolved references.

    The references that resolve to another file are grouped by (file, name); the
    target_context of a group is the source text of the definition named by the
    definition_name of its first reference (extract_callee_source).

    Args:
        target_list: Return value of csharp_reference_target_list.
        file_rel: Relative path of the file.
        project_dir: Absolute path to the project root.

    Returns:
        A list of {"lines", "name", "from", "target_context"} dicts.
    """
    return _group_other_file_target_list(
        target_list, file_rel,
        lambda target: extract_callee_source(target.file_rel, target.definition_name, project_dir),
    )


def build_csharp_same_file_usages(
    target_list: list[CsharpReferenceTarget], file_rel: str, definition_list: list[dict],
) -> list[dict]:
    """Build the same_file_usages of a C# file from its resolved references.

    The references that resolve to the file itself are grouped by name. A reference
    inside the line range of a definition named like the first part of its name is
    left out, as in build_same_file_usages.

    Args:
        target_list: Return value of csharp_reference_target_list.
        file_rel: Relative path of the file.
        definition_list: The file's definitions (dicts with name, start_line, end_line).

    Returns:
        A list of {"lines", "name"} dicts.
    """
    return _group_same_file_target_list(target_list, file_rel, definition_list)


def build_r_usage_info_list(
    target_list: list[RReferenceTarget], file_rel: str, project_dir: str,
) -> list[dict]:
    """Build the callee_usages of an R file from its resolved references.

    The references that resolve to another file are grouped by (file, name); the
    target_context of a group is the lines of the definition its first reference
    resolves to (start_line to end_line of the target), read from the file.

    Args:
        target_list: Return value of r_reference_target_list.
        file_rel: Relative path of the file.
        project_dir: Absolute path to the project root.

    Returns:
        A list of {"lines", "name", "from", "target_context"} dicts.
    """
    line_list_dict: dict[str, list[str]] = {}

    def definition_line_text(target: RReferenceTarget) -> str:
        """Return the lines of the definition a reference resolves to."""
        if target.file_rel not in line_list_dict:
            target_text = read_source(os.path.join(project_dir, target.file_rel))[0]
            line_list_dict[target.file_rel] = line_list_of(target_text)
        return "\n".join(line_list_dict[target.file_rel][target.start_line - 1:target.end_line])

    return _group_other_file_target_list(target_list, file_rel, definition_line_text)


def build_r_same_file_usages(
    target_list: list[RReferenceTarget], file_rel: str, root_node: Node,
) -> list[dict]:
    """Build the same_file_usages of an R file from its resolved references.

    The references that resolve to the file itself are grouped by name. A reference
    inside the line range of a top-level definition of the same name is left out, as in
    build_same_file_usages; the members of a class and the calls written for a name
    defined elsewhere (setMethod) are not such definitions.

    Args:
        target_list: Return value of r_reference_target_list.
        file_rel: Relative path of the file.
        root_node: The AST root node of the file.

    Returns:
        A list of {"lines", "name"} dicts.
    """
    definition_list = [
        {"name": definition.name, "start_line": definition.start_line, "end_line": definition.end_line}
        for definition in r_definition_list(root_node)
        if not definition.is_member and not definition.is_attach
    ]
    return _group_same_file_target_list(target_list, file_rel, definition_list)


def _caller_target_list(
    caller_root: Node | CobolSource,
    caller_ext: str,
    caller_rel: str,
    project_file_set: set[str],
    project_dir: str,
) -> list[_ReferenceTarget] | None:
    """Return the resolved references of a caller whose references are resolved one by one.

    The references are resolved as get_file_dependencies resolves them for the caller
    itself: a COBOL file with OF / IN qualification (cobol_reference_target_list), a C#
    file through its namespaces and using directives (csharp_reference_target_list), an
    R file through the names it sees (r_reference_target_list).

    Returns:
        The resolved references. None for a caller of another language.
    """
    if isinstance(caller_root, CobolSource):
        return cobol_reference_target_list(caller_root, caller_rel, project_file_set, project_dir)
    if caller_ext in CSHARP_EXT_SET:
        return csharp_reference_target_list(caller_root, caller_rel, project_file_set, project_dir)
    if caller_ext in R_EXT_SET:
        return r_reference_target_list(caller_rel, project_file_set, project_dir)
    return None


def _collect_target_name_list(
    caller_import_list: list[ImportInfo],
    target_file_rel: str,
    caller_ext: str,
    caller_rel: str,
    project_file_set: set[str],
    project_dir: str,
    target_definition_name_list: list[str] | None,
    source_root_set: set[str] | None = None,
) -> tuple[list[str], list[str] | None]:
    """Collect names originating from the target file based on the caller's import statements.

    The method of deriving names differs by language:
    - Python/JS/TS: Use names = ["a", "b"] directly from "from X import a, b".
    - Java/Kotlin:  Use the trailing "Bar" from "import com.foo.Bar".
    - C/C++:        "#include <header.h>" incorporates the entire file,
                    so collect all definition names from the target file.

    Args:
        caller_import_list: List of ImportInfo from the caller file.
        target_file_rel: Relative path of the target file.
        caller_ext: File extension of the caller file (without leading ".").
        caller_rel: Relative path of the caller file (used for module resolution).
        project_file_set: Set of file paths within the project.
        project_dir: Absolute path to the project root.
        target_definition_name_list: Cached target definition names for C/C++.
                                 Pass None on the first call.
        source_root_set: Set of source root prefixes (e.g. {"src/main/java/"}).

    Returns:
        A (target_name_list, target_definition_name_list) tuple.
        For C/C++, target_definition_name_list is returned as a cache to the caller.
    """
    target_name_list: list[str] = []
    caller_resolve_config = EXT_TO_IMPORT_RESOLVE_DICT.get(caller_ext, {})
    caller_separator = caller_resolve_config.get("separator", ".")

    for import_info in caller_import_list:
        resolved = resolve_module_to_project_path(
            import_info.module, caller_rel, project_file_set, source_root_set, project_dir,
        )
        if resolved == target_file_rel:
            if import_info.names:
                # "from X import a, b" form: add individual names
                name_list, _ = import_name_list(
                    import_info, caller_rel, project_file_set, project_dir,
                )
                target_name_list.extend(n for n in name_list if n != "*")
                # "from X import *" form: add all definition names from the target file,
                # except the names the caller defines itself
                if "*" in name_list:
                    if target_definition_name_list is None:
                        target_definition_name_list = _load_target_definitions(
                            target_file_rel, project_dir,
                        )
                    caller_own_name_set = set(top_level_definition_names(caller_rel, project_dir))
                    target_name_list.extend(
                        n for n in target_definition_name_list if n not in caller_own_name_set
                    )
            elif caller_separator == ".":
                # Java/Kotlin: "import com.foo.Bar" -> add trailing "Bar"
                module_part_list = import_info.module.split(".")
                leaf = module_part_list[-1]
                if leaf:
                    target_name_list.append(leaf)
            elif caller_separator == "/":
                # C/C++: #include incorporates the entire file.
                # Add all definition names from the target file to target_name_list
                if target_definition_name_list is None:
                    target_definition_name_list = _load_target_definitions(
                        target_file_rel, project_dir,
                    )
                target_name_list.extend(target_definition_name_list)
        elif (
            not resolved
            and "*" in import_info.names
            and caller_separator == "."
        ):
            # Java/Kotlin wildcard import: check if target is a file within the package
            package_dir = import_info.module.replace(".", "/")
            if target_file_rel.startswith(package_dir + "/"):
                if target_definition_name_list is None:
                    target_definition_name_list = _load_target_definitions(
                        target_file_rel, project_dir,
                    )
                caller_own_name_set = set(top_level_definition_names(caller_rel, project_dir))
                target_name_list.extend(
                    n for n in target_definition_name_list if n not in caller_own_name_set
                )

    # Target visible without an import statement (Java/Kotlin: same package, SQL: whole project)
    # Add target definition names even if there are no import matches
    if not target_name_list:
        scope_key = implicit_scope_key(caller_rel)
        if scope_key is not None and implicit_scope_key(target_file_rel) == scope_key:
            if target_definition_name_list is None:
                target_definition_name_list = _load_target_definitions(
                    target_file_rel, project_dir,
                )
            target_name_list.extend(target_definition_name_list)

    return target_name_list, target_definition_name_list


def _load_target_definitions(
    target_file_rel: str,
    project_dir: str,
) -> list[str]:
    """Parse the target file and return a list of all definition names within it.

    Args:
        target_file_rel: Relative path of the target file from the project root.
        project_dir: Absolute path to the project root.

    Returns:
        A list of definition name strings.
    """
    name_list: list[str] = []
    target_abs = os.path.join(project_dir, target_file_rel)
    target_def_dict = EXT_TO_DEFINITION_DICT.get(language_ext(target_abs))
    if target_def_dict and os.path.isfile(target_abs):
        target_root = parse_file(target_abs)[0]
        for defn in extract_definitions(target_root, target_def_dict):
            if defn.name and defn.type not in ATTACHED_DEFINITION_TYPE_SET:
                name_list.append(defn.name)
    return name_list


def _group_caller_usage_list(
    usage_list: list[UsageInfo],
    typed_alias_dict: dict[str, str],
    caller_rel: str,
) -> dict[str, dict]:
    """Group a caller's usages by name, mapping typed variables back to their type names.

    Args:
        usage_list: UsageInfo list of the caller file.
        typed_alias_dict: {variable name: type name} of the caller file.
        caller_rel: Relative path of the caller file.

    Returns:
        A {name: {"lines", "name", "file"}} dict; lines are sorted without duplicates.
    """
    group_dict: dict[str, dict] = {}
    for usage in usage_list:
        name = usage.name
        root_symbol = usage_root_name(name, typed_alias_dict)
        if root_symbol in typed_alias_dict:
            name = typed_alias_dict[root_symbol] + name[len(root_symbol):]

        if name not in group_dict:
            group_dict[name] = {
                "lines": [usage.line],
                "name":  name,
                "file":  caller_rel,
            }
        else:
            group_dict[name]["lines"].append(usage.line)

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
    source_root_set: set[str] | None = None,
) -> list[dict]:
    """Collect the lines where names defined in this file are used in other project
    files, producing data for the caller_usages JSON output.

    Args:
        target_file_rel: Relative path of this file from the project root.
        caller_file_list: Relative paths of the files depending on this file.
        project_dir: Absolute path to the project root.
        project_file_set: Set of file paths within the project.
        source_root_set: Set of source root prefixes (e.g. {"src/main/java/"}).

    Returns:
        A list of dicts containing usage location information.
    """
    caller_usages: list[dict] = []

    # For C/C++, retrieve target definition names once outside the caller loop and cache them
    target_definition_name_list: list[str] | None = None

    for caller_rel in caller_file_list:
        caller_abs = os.path.join(project_dir, caller_rel)
        caller_ext = language_ext(caller_abs)

        caller_root = parse_file(caller_abs)[0]

        # Retrieve parameters for import extraction
        language, import_query_str = get_import_params(caller_ext)
        if not language:
            continue

        # COBOL / C# / R: the caller's references that resolve to the target
        target_list = _caller_target_list(
            caller_root, caller_ext, caller_rel, project_file_set, project_dir,
        )
        if target_list is not None:
            usage_list = [
                UsageInfo(name=target.name, line=target.line)
                for target in target_list if target.file_rel == target_file_rel
            ]
            if usage_list:
                group_dict = _group_caller_usage_list(usage_list, {}, caller_rel)
                _attach_usage_context(group_dict, caller_abs)
                caller_usages.extend(group_dict.values())
            continue

        caller_import_list = extract_imports(
            caller_root, language, import_query_str
        )

        # Step 1: Collect names that the caller imports from the target
        target_name_list, target_definition_name_list = _collect_target_name_list(
            caller_import_list, target_file_rel, caller_ext,
            caller_rel, project_file_set, project_dir,
            target_definition_name_list, source_root_set,
        )

        # Step 2: Extract the lines where those names are used within the caller,
        # including variables declared with an imported type
        if not target_name_list:
            continue
        usage_node_types = EXT_TO_USAGE_NODE_TYPE_DICT.get(caller_ext)
        typed_alias_dict = _extract_typed_alias_dict(
            caller_root, set(target_name_list), usage_node_types
        )
        for var_name in typed_alias_dict:
            if var_name not in target_name_list:
                target_name_list.append(var_name)
        usage_list = extract_usages(caller_root, set(target_name_list), usage_node_types)
        if not usage_list:
            continue

        # Step 3: Group by name and attach the code around the usage lines
        group_dict = _group_caller_usage_list(usage_list, typed_alias_dict, caller_rel)
        _attach_usage_context(group_dict, caller_abs)
        caller_usages.extend(group_dict.values())

    return caller_usages
