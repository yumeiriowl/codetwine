import os
import logging
from tree_sitter import Language
from codetwine.parsers.ts_parser import parse_file
from codetwine.extractors.definitions import extract_definitions
from codetwine.config.settings import (
    DEFINITION_DICTS,
    IMPORT_RESOLVE_CONFIG,
    IMPORT_QUERIES,
    SAME_PACKAGE_VISIBLE,
    TREE_SITTER_LANGUAGES,
)

logger = logging.getLogger(__name__)


def resolve_relative_import(
    module: str,
    separator: str,
    current_dir_part_list: list[str],
) -> list[str]:
    """Convert a relative import's module name into a list of directory path components.

    Detect relative imports (Python's "..", JS/TS's "./" or "../")
    and build the path starting from current_dir_part_list.
    For absolute imports, simply split by the separator.

    Args:
        module: The module name from the import statement (e.g. "..utils", "./helper", "os").
        separator: The delimiter for module names ("." or "/").
        current_dir_part_list: Path components of the directory containing the current file
                               (e.g. ["src", "app"]).

    Returns:
        A list of path components (e.g. ["src", "utils"]).
        Joining this return value with "/".join() produces the base_path.
    """
    if separator == "." and module.startswith("."):
        # Python-style relative import

        # Count the dots (1 dot = current, 2 dots = 1 level up, 3 dots = 2 levels up)
        dot_count = len(module) - len(module.lstrip("."))

        # Extract the remaining module name after removing the dots
        clean_module = module[dot_count:]

        # Copy the current directory path components as the starting point
        path_part_list = list(current_dir_part_list)

        # One dot refers to the current directory.
        # Remove trailing elements (dot_count - 1) times to traverse up to parent directories.
        for _ in range(dot_count - 1):
            if path_part_list:
                path_part_list.pop()

        # Split the remaining module name by "." and append as path components
        if clean_module:
            path_part_list.extend(clean_module.split("."))

        return path_part_list

    if separator == "/" and (module.startswith("./") or module.startswith("../")):
        # JS/TS-style relative import
        # Normalize with os.path.normpath: "src/utils/../lib" -> "src/lib"
        if current_dir_part_list:
            combined = "/".join(current_dir_part_list) + "/" + module
        else:
            combined = module
        normalized = os.path.normpath(combined).replace("\\", "/")
        return normalized.split("/")

    # Absolute import: split by separator to convert to path
    return module.split(separator)


def generate_candidate_path_list(
    base_path: str,
    src_ext_with_dot: str,
    resolve_config: dict,
    current_dir_part_list: list[str],
) -> list[str]:
    """Generate a list of file path candidates from base_path based on IMPORT_RESOLVE_CONFIG settings.

    Language-specific candidate generation rules (index files, alternative extensions,
    current-directory relative paths, etc.) are declaratively defined via config fields,
    so this function contains no language-specific if-branches.

    Deduplication: If base_path already has one of the extensions in alt_ext_list
    (e.g. C/C++ #include "stdio.h"), appending alternative extensions is skipped
    to prevent meaningless candidates like "stdio.h.h".

    Args:
        base_path: The base path converted from the module name (e.g. "src/utils", "stdio.h").
        src_ext_with_dot: Extension of the current file (with leading ".", e.g. ".py", ".c").
        resolve_config: An IMPORT_RESOLVE_CONFIG entry (per-language settings dict).
        current_dir_part_list: Path components of the directory containing the current file.

    Returns:
        A list of candidate paths in priority order. No duplicates.
    """
    try_init = resolve_config.get("try_init", False)
    index_ext_list = resolve_config.get("index_ext_list", [])
    alt_ext_list = resolve_config.get("alt_ext_list", [])
    try_bare_path = resolve_config.get("try_bare_path", False)
    try_current_dir = resolve_config.get("try_current_dir", False)

    # Check whether base_path already has one of the extensions in alt_ext_list
    base_ext = os.path.splitext(base_path)[1]
    has_known_ext = base_ext in alt_ext_list

    # Generate candidates from the project root
    root_candidate_list: list[str] = []

    # Try a file with the same extension (skip if base_path already has an extension)
    if not has_known_ext:
        root_candidate_list.append(base_path + src_ext_with_dot)

    # Python package: try __init__.py (the directory may be a package)
    if try_init:
        root_candidate_list.append(base_path + "/__init__.py")

    # Try directory index files (for JS/TS: import './components' -> './components/index.ts')
    for idx_ext in index_ext_list:
        root_candidate_list.append(base_path + "/index" + idx_ext)

    # Try alternative extensions (skip if base_path already has an extension)
    if not has_known_ext:
        for alt_ext in alt_ext_list:
            # Same extension as the current file already added. Skip
            if alt_ext != src_ext_with_dot:
                root_candidate_list.append(base_path + alt_ext)

    # Use base_path as-is without extension (for C/C++: #include "stdio.h" cases)
    if try_bare_path:
        root_candidate_list.append(base_path)

    # Add relative path candidates from the current directory
    if try_current_dir:
        current_dir = "/".join(current_dir_part_list)
        if current_dir:
            for candidate in list(root_candidate_list):
                root_candidate_list.append(current_dir + "/" + candidate)

    # Remove duplicates while preserving order
    return list(dict.fromkeys(root_candidate_list))


def resolve_module_to_project_path(
    module: str,
    current_file_rel: str,
    project_file_set: set[str],
) -> str | None:
    """Resolve an import statement's module name to a file path within the project.

    The module names passed to this function include not only project-internal modules
    but also standard library modules (os, json, etc.) and external packages (requests, etc.).
    This function generates file path candidates from the module name and checks them
    against project_file_set to determine whether the module is a project-internal file.
    Returns None if no matching file exists within the project.

    The internal processing consists of 3 steps, each delegated to a dedicated function:
    1. resolve_relative_import: Parse relative/absolute imports to determine path_part_list.
    2. generate_candidate_path_list: Generate candidate file paths from path_part_list.
    3. Match against project_file_set and return the first matching candidate.

    Args:
        module: The module name from the import statement. Both project-internal and external
                modules are passed (e.g. "..utils", "os", "requests", "com.example.Foo",
                "./helper", "stdio.h").
        current_file_rel: Relative path of the current file from the project root.
        project_file_set: Set of file paths within the project ("path/to/file.ext" format).

    Returns:
        A project-internal file path ("path/to/file.ext" format).
        None if no matching file exists within the project.
    """
    # Get the current file's extension and resolve config
    src_ext_with_dot = os.path.splitext(current_file_rel)[1]
    src_ext = src_ext_with_dot.lstrip(".")

    # Get the module resolve config for this extension
    resolve_config = IMPORT_RESOLVE_CONFIG.get(src_ext)
    if not resolve_config:
        return None

    separator = resolve_config["separator"]
    # Split the current file's directory path into components
    current_dir_part_list = current_file_rel.replace("\\", "/").split("/")[:-1]

    # Step 1: Convert the module name to path components
    path_part_list = resolve_relative_import(
        module, separator, current_dir_part_list
    )
    base_path = "/".join(path_part_list)

    # Step 2: Generate file candidates
    candidate_path_list = generate_candidate_path_list(
        base_path, src_ext_with_dot, resolve_config, current_dir_part_list
    )

    # Step 3: Match against project_file_set and return the first matching candidate
    for candidate_path in candidate_path_list:
        if candidate_path in project_file_set:
            return candidate_path

    return None


def _put_symbol(
    symbol_map: dict[str, str], name: str, path: str,
) -> None:
    """Register a symbol name into the map and warn if overwriting to a different file.

    Args:
        symbol_map: A symbol-name -> file-path dict. Modified directly by this function.
        name: The symbol name to register.
        path: The file path where the symbol is defined.
    """
    existing = symbol_map.get(name)
    if existing and existing != path:
        logger.warning(
            "Symbol '%s' definition source is being overwritten: '%s' -> '%s'",
            name, existing, path,
        )
    symbol_map[name] = path


def build_symbol_to_file_map(
    import_info_list,
    current_file_rel: str,
    project_file_set: set[str],
    file_ext: str,
    project_dir: str,
) -> tuple[dict[str, str], dict[str, str]]:
    """Build a dict mapping imported names to their definition file paths, used to
    identify "which file does this name come from" during usage tracking.

    Calls resolve_module_to_project_path for all module names in import_info_list
    and registers only those resolvable to project-internal files.
    Standard library and external packages are automatically excluded as they cannot be resolved.

    Language-specific handling:
        Python:  "import X.Y.Z" -> Register the module root "X" (for X.Y.Z.func() access)
                 "from X import a, b" -> Register "a", "b" individually
        Java:    "import com.foo.Bar" -> Register the trailing "Bar" (Java references by class name)
        C/C++:   "#include <header.h>" -> Register all definition names from the header file
                 (#include incorporates the entire file, so no individual name specification exists)

    Args:
        import_info_list: List of ImportInfo returned by extract_imports.
        current_file_rel: Relative path of the current file from the project root.
        project_file_set: Set of file paths within the project.
        file_ext: Extension of the current file (without ".", e.g. "py", "java", "c", "cpp").
        project_dir: Absolute path to the project root.

    Returns:
        A (symbol_to_file_map, alias_to_original) tuple.
        symbol_to_file_map: { imported name: definition file path }
        alias_to_original: { alias name: original name } (from X import a as b -> {"b": "a"})
    """
    symbol_to_file_map: dict[str, str] = {}
    alias_to_original: dict[str, str] = {}

    # Get the resolve config for the current file's extension
    resolve_config = IMPORT_RESOLVE_CONFIG.get(file_ext, {})
    separator = resolve_config.get("separator", ".")

    for import_info in import_info_list:
        # Resolve the module name to a file path (returns None for non-project modules)
        resolved_path = resolve_module_to_project_path(
            import_info.module, current_file_rel, project_file_set
        )

        # Java/Kotlin wildcard import: if not resolvable to a single file,
        # treat the module name as a package directory and register definitions from all files in the directory
        if not resolved_path and "*" in import_info.names and separator == ".":
            package_dir = import_info.module.replace(".", "/")
            _register_definitions_from_package(
                package_dir, file_ext, project_dir,
                project_file_set, symbol_to_file_map,
            )
            continue

        if not resolved_path:
            continue

        # "from X import a, b" form: register individual names in the dict
        for name in import_info.names:
            if name == "*":
                # from X import * -> register all definitions from the file
                _register_definitions_from_file(
                    resolved_path, project_dir, symbol_to_file_map
                )
            else:
                _put_symbol(symbol_to_file_map, name, resolved_path)

        # Transfer alias mappings to alias_to_original
        if import_info.alias_map:
            alias_to_original.update(import_info.alias_map)

        # When names is empty: derive symbols using a language-specific method
        if not import_info.names:
            if separator == ".":
                if import_info.module_alias:
                    # import X as Y -> register alias name "Y"
                    _put_symbol(symbol_to_file_map, import_info.module_alias, resolved_path)
                else:
                    # Python: "import os.path" -> register "os" (for access like os.path.join())
                    # Java:   "import com.foo.Bar" -> register "Bar" (Java references by class name directly)
                    module_parts = import_info.module.split(".")
                    # Register the root part (for Python package access: X.Y.func())
                    # Java/Kotlin don't reference package roots (com, org, etc.) alone, so skip
                    if file_ext not in ("java", "kt"):
                        module_root = module_parts[0].lstrip(".")
                        if module_root:
                            _put_symbol(symbol_to_file_map, module_root, resolved_path)
                    # Register the trailing part (for Java direct class reference: User user = new User())
                    # Registering the trailing part for Python is harmless (if unused, it won't match)
                    module_leaf = module_parts[-1]
                    if module_leaf and module_leaf != module_parts[0]:
                        _put_symbol(symbol_to_file_map, module_leaf, resolved_path)
            elif separator == "/":
                # C/C++: #include incorporates the entire file. Register all definitions from the file
                _register_definitions_from_file(
                    resolved_path, project_dir, symbol_to_file_map
                )
        else:
            # Even when names exist, register the module root for attribute access.
            # Use setdefault to avoid overwriting if already registered by direct import (import mylib).
            # Java/Kotlin don't reference package roots alone, so skip.
            if file_ext not in ("java", "kt"):
                module_root = import_info.module.split(".")[0].lstrip(".")
                if module_root:
                    symbol_to_file_map.setdefault(module_root, resolved_path)

    # Register definition names from same-package files (Java/Kotlin)
    # Add classes referenceable without import statements to symbol_to_file_map
    if SAME_PACKAGE_VISIBLE.get(file_ext):
        current_dir = os.path.dirname(current_file_rel)
        for project_file in project_file_set:
            if project_file == current_file_rel:
                continue
            if os.path.dirname(project_file) != current_dir:
                continue
            if os.path.splitext(project_file)[1].lstrip(".") != file_ext:
                continue
            _register_definitions_from_file(
                project_file, project_dir, symbol_to_file_map,
            )

    return symbol_to_file_map, alias_to_original


def _register_definitions_from_file(
    file_rel: str,
    project_dir: str,
    symbol_to_file_map: dict[str, str],
) -> None:
    """Register all definition names from the specified file into symbol_to_file_map.

    Since C/C++ #include incorporates the entire file, all names defined in the
    included file (functions, structs, classes, etc.) are registered.
    This enables symbols from #include targets to be detected as usage locations.

    Args:
        file_rel: Relative path from the project root (e.g. "c_app/utils.h").
        project_dir: Absolute path to the project root.
        symbol_to_file_map: The target dict (name -> file path). Modified directly by this function.
    """
    # Build the absolute path of the file and verify it exists
    abs_path = os.path.join(project_dir, file_rel)
    if not os.path.isfile(abs_path):
        return

    # Get the definition dict for this extension
    resolved_ext = os.path.splitext(file_rel)[1].lstrip(".")
    definition_dict = DEFINITION_DICTS.get(resolved_ext)
    if not definition_dict:
        return

    # Parse the file, extract definitions, and register each definition name in symbol_to_file_map
    root_node = parse_file(abs_path)[0]
    for defn in extract_definitions(root_node, definition_dict):
        if defn.name:
            _put_symbol(symbol_to_file_map, defn.name, file_rel)


def _register_definitions_from_package(
    package_dir: str,
    file_ext: str,
    project_dir: str,
    project_file_set: set[str],
    symbol_to_file_map: dict[str, str],
) -> None:
    """For Java/Kotlin wildcard imports: register definition names from all files
    within the package directory into symbol_to_file_map.

    Handles syntax like import com.example.model.* that imports all classes from a package.
    Extracts definitions from files of the same extension directly under package_dir.

    Args:
        package_dir: Directory path of the package (e.g. "com/example/model").
        file_ext: Extension of the current file (without ".", e.g. "java", "kt").
        project_dir: Absolute path to the project root.
        project_file_set: Set of file paths within the project.
        symbol_to_file_map: The target dict (name -> file path). Modified directly by this function.
    """
    prefix = package_dir + "/"
    for project_file in project_file_set:
        if not project_file.startswith(prefix):
            continue
        # Do not include files from sub-packages (only files directly under the directory)
        remainder = project_file[len(prefix):]
        if "/" in remainder:
            continue
        if os.path.splitext(project_file)[1].lstrip(".") == file_ext:
            _register_definitions_from_file(
                project_file, project_dir, symbol_to_file_map,
            )


def get_import_params(file_ext: str) -> tuple[Language, str] | tuple[None, None]:
    """Retrieve the Language object and query string needed for import analysis from a file extension.

    For unsupported languages (extensions not defined in IMPORT_QUERIES), returns (None, None)
    to let the caller skip import analysis.

    Args:
        file_ext: File extension (without ".", e.g. "py", "java").

    Returns:
        A (Language, import_query_str) tuple. (None, None) if unsupported.
    """
    # Get the import query string for this extension
    import_query_str = IMPORT_QUERIES.get(file_ext)
    if not import_query_str:
        return None, None

    # Get the tree-sitter Language object for this extension
    try:
        language = TREE_SITTER_LANGUAGES[file_ext]
    except KeyError:
        return None, None
    return language, import_query_str
