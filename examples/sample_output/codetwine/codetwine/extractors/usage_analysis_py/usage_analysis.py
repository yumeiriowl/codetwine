import os
import logging
from codetwine.parsers.ts_parser import parse_file
from codetwine.extractors.imports import extract_imports
from codetwine.extractors.usages import extract_usages, extract_typed_aliases
from codetwine.extractors.definitions import extract_definitions
from codetwine.extractors.dependency_graph import extract_callee_source
from codetwine.import_to_path import (
    resolve_module_to_project_path,
    get_import_params,
)
from codetwine.config.settings import (
    DEFINITION_DICTS,
    USAGE_NODE_TYPES,
    IMPORT_RESOLVE_CONFIG,
    SAME_PACKAGE_VISIBLE,
)

logger = logging.getLogger(__name__)


def build_usage_info_list(
    root_node,
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
    usage_node_types = USAGE_NODE_TYPES.get(file_ext)

    # Build a variable-name -> type-name mapping from typed variable declarations
    typed_alias_parent_types = (
        usage_node_types.get("typed_alias_parent_types", set())
        if usage_node_types else set()
    )
    typed_aliases = extract_typed_aliases(
        root_node, set(symbol_to_file_map.keys()), typed_alias_parent_types
    )
    # Add alias variable names to the tracking set (map genre -> same file as Genre)
    for var_name, type_name in typed_aliases.items():
        if var_name not in symbol_to_file_map:
            symbol_to_file_map[var_name] = symbol_to_file_map[type_name]

    usage_info_list = extract_usages(
        root_node, set(symbol_to_file_map.keys()), usage_node_types
    )

    # Key: (definition file path, project-internal imported name) -> merged entry
    usage_group_map: dict[tuple, dict] = {}

    for usage in usage_info_list:
        # For attribute access like "helper.process", the leading "helper" is the name from the import statement
        root_symbol = usage.name.split(".")[0]

        # Remap alias variable names back to original type names (genre -> Genre)
        if root_symbol in typed_aliases:
            original_type = typed_aliases[root_symbol]
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


def _collect_names_from_target(
    caller_import_list: list,
    target_file_rel: str,
    caller_ext: str,
    caller_rel: str,
    project_file_set: set[str],
    project_dir: str,
    target_definition_names: list[str] | None,
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
        target_definition_names: Cached target definition names for C/C++.
                                 Pass None on the first call.

    Returns:
        A (names_from_target, target_definition_names) tuple.
        For C/C++, target_definition_names is returned as a cache to the caller.
    """
    names_from_target: list[str] = []
    caller_resolve_config = IMPORT_RESOLVE_CONFIG.get(caller_ext, {})
    caller_separator = caller_resolve_config.get("separator", ".")

    for import_info in caller_import_list:
        resolved = resolve_module_to_project_path(
            import_info.module, caller_rel, project_file_set
        )
        if resolved == target_file_rel:
            if import_info.names:
                # "from X import a, b" form: add individual names
                names_from_target.extend(n for n in import_info.names if n != "*")
                # "from X import *" form: add all definition names from the target file
                if "*" in import_info.names:
                    if target_definition_names is None:
                        target_definition_names = _load_target_definitions(
                            target_file_rel, project_dir,
                        )
                    names_from_target.extend(target_definition_names)
            elif caller_separator == ".":
                # Java/Kotlin: "import com.foo.Bar" -> add trailing "Bar"
                module_parts = import_info.module.split(".")
                leaf = module_parts[-1]
                if leaf:
                    names_from_target.append(leaf)
            elif caller_separator == "/":
                # C/C++: #include incorporates the entire file.
                # Add all definition names from the target file to names_from_target
                if target_definition_names is None:
                    target_definition_names = _load_target_definitions(
                        target_file_rel, project_dir,
                    )
                names_from_target.extend(target_definition_names)
        elif (
            not resolved
            and "*" in import_info.names
            and caller_separator == "."
        ):
            # Java/Kotlin wildcard import: check if target is a file within the package
            package_dir = import_info.module.replace(".", "/")
            if target_file_rel.startswith(package_dir + "/"):
                if target_definition_names is None:
                    target_definition_names = _load_target_definitions(
                        target_file_rel, project_dir,
                    )
                names_from_target.extend(target_definition_names)

    # Same package (same directory): references are possible without import statements (Java/Kotlin)
    # Add target definition names even if there are no import matches
    if not names_from_target and SAME_PACKAGE_VISIBLE.get(caller_ext):
        if os.path.dirname(caller_rel) == os.path.dirname(target_file_rel):
            if target_definition_names is None:
                target_definition_names = _load_target_definitions(
                    target_file_rel, project_dir,
                )
            names_from_target.extend(target_definition_names)

    return names_from_target, target_definition_names


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
    names: list[str] = []
    target_abs = os.path.join(project_dir, target_file_rel)
    target_ext = os.path.splitext(target_file_rel)[1].lstrip(".")
    target_def_dict = DEFINITION_DICTS.get(target_ext)
    if target_def_dict and os.path.isfile(target_abs):
        target_root = parse_file(target_abs)[0]
        for defn in extract_definitions(target_root, target_def_dict):
            if defn.name:
                names.append(defn.name)
    return names


def build_caller_usages(
    target_file_rel: str,
    project_dep_list: list[dict],
    project_dir: str,
    project_file_set: set[str],
) -> list[dict]:
    """Collect the lines where names defined in this file are used in other project
    files, producing data for the caller_usages JSON output.

    Args:
        target_file_rel: Relative path of this file from the project root.
        project_dep_list: Dependency info list output by save_project_dependencies.
        project_dir: Absolute path to the project root.
        project_file_set: Set of file paths within the project.

    Returns:
        A list of dicts containing usage location information.
    """
    # Step 1: Get the list of callers for this file
    caller_file_list: list[str] = []
    for dep_info in project_dep_list:
        if dep_info["file"] == target_file_rel:
            caller_file_list = dep_info["callers"]
            break

    caller_usages: list[dict] = []

    # For C/C++, retrieve target definition names once outside the caller loop and cache them
    target_definition_names: list[str] | None = None

    for caller_rel in caller_file_list:
        caller_abs = os.path.join(project_dir, caller_rel)
        caller_ext = os.path.splitext(caller_rel)[1].lstrip(".")

        caller_root = parse_file(caller_abs)[0]

        # Retrieve parameters for import extraction
        language, import_query_str = get_import_params(caller_ext)
        if not language:
            continue

        caller_import_list = extract_imports(
            caller_root, language, import_query_str
        )

        # Step 2: Collect names that the caller imports from the target
        names_from_target, target_definition_names = _collect_names_from_target(
            caller_import_list, target_file_rel, caller_ext,
            caller_rel, project_file_set, project_dir,
            target_definition_names,
        )

        # Step 3: Extract and aggregate lines where those names are used within the caller
        if names_from_target:
            usage_node_types = USAGE_NODE_TYPES.get(caller_ext)

            # Add typed variable aliases to the tracking set
            typed_alias_parent_types = (
                usage_node_types.get("typed_alias_parent_types", set())
                if usage_node_types else set()
            )
            typed_aliases = extract_typed_aliases(
                caller_root, set(names_from_target), typed_alias_parent_types
            )
            for var_name in typed_aliases:
                if var_name not in names_from_target:
                    names_from_target.append(var_name)

            usage_list = extract_usages(
                caller_root, set(names_from_target), usage_node_types
            )

            # Hold the caller's source code line by line (for usage_context extraction)
            caller_source_lines: list[str] | None = None
            if usage_list:
                try:
                    with open(caller_abs, "r", encoding="utf-8") as f:
                        caller_source_lines = f.read().splitlines()
                except (OSError, UnicodeDecodeError):
                    pass

            # Step 4: Group by (name, file) and accumulate into lines list
            # Remap alias variable names to original type names for grouping
            groups: dict[str, dict] = {}
            for usage in usage_list:
                name = usage.name
                root_symbol = name.split(".")[0]
                if root_symbol in typed_aliases:
                    name = typed_aliases[root_symbol] + name[len(root_symbol):]

                if name not in groups:
                    groups[name] = {
                        "lines": [usage.line],
                        "name":  name,
                        "file":  caller_rel,
                    }
                else:
                    groups[name]["lines"].append(usage.line)

            # Step 5: Extract usage_context from the usage locations of each group
            # Remove duplicate lines before extracting context
            for group in groups.values():
                group["lines"] = sorted(set(group["lines"]))
            _max_context_locations = 2
            _context_radius = 3
            if caller_source_lines:
                total_lines = len(caller_source_lines)
                for group in groups.values():
                    context_parts = []
                    for line_no in group["lines"][:_max_context_locations]:
                        start = max(0, line_no - 1 - _context_radius)
                        end = min(total_lines, line_no - 1 + _context_radius + 1)
                        snippet = "\n".join(caller_source_lines[start:end])
                        context_parts.append(snippet)
                    group["usage_context"] = "\n...\n".join(context_parts)

            caller_usages.extend(groups.values())

    return caller_usages
