import os
from dataclasses import dataclass
from tree_sitter import Node
from codetwine.parsers.ts_parser import parse_file
from codetwine.extractors.definitions import (
    ATTACHED_DEFINITION_TYPE_SET,
    TRANSPARENT_DEFINITION_TYPE_SET,
    DefinitionInfo,
    extract_definitions,
    select_top_level_definitions,
)
from codetwine.extractors.usages import (
    UsageInfo,
    deduplicate_usage_list,
    extract_typed_aliases,
    extract_usages,
    symbol_part_list,
    typed_alias_type,
    usage_root_name,
)
from codetwine.import_binding import ImportBinder, ScopeBinding, SymbolBinding, import_binder
from codetwine.utils.project_cache import project_cache_value
from codetwine.config.settings import (
    EXT_TO_DEFINITION_DICT,
    EXT_TO_USAGE_NODE_TYPE_DICT,
    language_ext,
)

# Cache of the resolved references of the files read through their import statements:
# absolute path -> (project file set they were resolved with, targets)
import_target_cache: dict[str, tuple[set[str], list["ImportReferenceTarget"]]] = {}


@dataclass
class ImportReferenceTarget:
    """The definition one reference of a file resolves to."""

    name: str               # Name of the usage as the referring file writes it
    line: int               # Line of the reference (1-based)
    file_rel: str           # Relative path of the file with the definition
    definition_name: str    # Name the definition is looked up by in file_rel


def _own_name_set(
    root_node: Node,
    file_ext: str,
    symbol_dict: dict[str, SymbolBinding],
    import_line_dict: dict[str, set[int]],
    file_import_line_dict: dict[str, set[int]],
) -> set[str]:
    """Return the names the file defines itself and refers to by those names.

    A name an import statement binds is not such a name, also when the import leads
    outside the project: a definition written on the line of an import statement that
    binds its name for the whole file (const path = require("path")) is the import
    itself, and a name defined only inside another definition gives way to the
    import. A name the file defines at its top level apart from the import stays its
    own.

    Args:
        root_node: The AST root node of the file.
        file_ext: Extension whose language settings the file is analyzed with.
        symbol_dict: The names of the file that come from other project files.
        import_line_dict: Name an import statement of the file binds -> lines of those
            statements.
        file_import_line_dict: The same for the statements that bind their names for
            the whole file.

    Returns:
        The definition names of the file, without the names of symbol_dict, the names
        described above and the names carried only by definitions in
        ATTACHED_DEFINITION_TYPE_SET or TRANSPARENT_DEFINITION_TYPE_SET.
    """
    def is_own(definition: DefinitionInfo) -> bool:
        """Return whether a definition gives a name of the file apart from its import statements."""
        return (
            bool(definition.name)
            and definition.type not in ATTACHED_DEFINITION_TYPE_SET
            and definition.type not in TRANSPARENT_DEFINITION_TYPE_SET
            and definition.name not in symbol_dict
            and not any(
                definition.start_line <= line <= definition.end_line
                for line in file_import_line_dict.get(definition.name, ())
            )
        )

    definition_list = extract_definitions(root_node, EXT_TO_DEFINITION_DICT[file_ext])
    top_level_name_set = {
        definition.name
        for definition in select_top_level_definitions(definition_list) if is_own(definition)
    }
    return {
        definition.name for definition in definition_list
        if is_own(definition)
        and (definition.name not in import_line_dict or definition.name in top_level_name_set)
    }


def _import_target_list(
    usage_name: str,
    root_name: str,
    line: int,
    binding: SymbolBinding,
    binder: ImportBinder,
) -> list["ImportReferenceTarget"]:
    """Build the targets of a usage whose first name an import statement binds.

    Examples (usage -> file, definition_name):
        "mk" (from pkg import make as mk)         -> pkg/core.py, "make"
        "hh.clamp" (import pkg.helpers as hh)     -> pkg/helpers.py, "clamp"
        "pkg.Engine.build" (import pkg)           -> pkg/core.py, "Engine.build"
        "express" (const express = require("./lib/express"), which has
            module.exports = createApplication)   -> lib/express.js, "createApplication"
        "Settings::new" (use crate::config::Settings) -> src/config.rs, "Settings::new"
        "list_push" (#include "list.h")           -> include/list.h, "list_push"
                                                     and src/list.c, "list_push"

    Args:
        usage_name: Name of the usage, starting with root_name.
        root_name: The bound name the usage starts from.
        line: Line of the usage (1-based).
        binding: What root_name stands for.
        binder: The ImportBinder of the project.

    Returns:
        The target of the definition the name leads to, then one target per file that
        defines a function the first one only declares (ImportBinder.implement_file_list).
        A name that stands for a file is followed through the parts written after it
        (member_binding), and stands for the default export of a JS/TS file when they
        name nothing of it; the definition_name is the name of the definition reached
        and the parts left over.
    """
    rest = usage_name[len(root_name):]
    if binding.name is not None:
        definition_name = binding.name + rest
    else:
        part_list = [part for part in symbol_part_list(rest) if part]
        binding, part_list = binder.member_binding(binding, part_list)
        if binding.name is None:
            # The value of a JS/TS module is its default export
            binding = binder.default_binding(binding.file_rel) or binding
        name_part_list = ([binding.name] if binding.name else []) + part_list
        definition_name = ".".join(name_part_list) or usage_name

    target_list = [ImportReferenceTarget(usage_name, line, binding.file_rel, definition_name)]
    target_list.extend(
        ImportReferenceTarget(usage_name, line, implement_file, implement_name)
        for implement_file, implement_name in binder.implement_file_list(
            binding.file_rel, definition_name,
        )
    )
    return target_list


def _scope_binding(
    scope_binding_dict: dict[str, list[ScopeBinding]], usage_name: str, line: int,
) -> ScopeBinding | None:
    """Return the binding of the smallest range of lines around a usage that binds the name it starts from.

    Args:
        scope_binding_dict: First part of a bound name -> the bindings of the names
            that start with it (ImportBinder.scope_binding_list).
        usage_name: Name of the usage.
        line: Line of the usage (1-based).

    Returns:
        The binding whose lines hold the line and whose name is a leading part of
        usage_name (cut at "." or "::"); of several, the one with the fewest lines,
        then the one with the longest name, then the first. None when there is none.
    """
    match_list = [
        scope_binding
        for scope_binding in scope_binding_dict.get(symbol_part_list(usage_name)[0], [])
        if scope_binding.start_line <= line <= scope_binding.end_line
        and usage_root_name(usage_name, (scope_binding.name,)) == scope_binding.name
    ]
    if not match_list:
        return None
    return min(
        match_list,
        key=lambda scope_binding: (
            scope_binding.end_line - scope_binding.start_line, -len(scope_binding.name),
        ),
    )


def clear_import_reference_cache() -> None:
    """Forget the resolved references of every project."""
    import_target_cache.clear()


def _usage_list(
    root_node: Node,
    file_ext: str,
    symbol_dict: dict[str, SymbolBinding],
    own_name_set: set[str],
    scope_binding_list: list[ScopeBinding],
    import_line_dict: dict[str, set[int]],
) -> list[UsageInfo]:
    """Return the usages of the names a file can resolve.

    The names of symbol_dict, own_name_set and scope_binding_list are tracked, and
    the variables declared with a type of symbol_dict. Such a variable is named after
    the type on the lines it is declared for (Genre genre; genre.label -> Genre.label);
    elsewhere its name is a usage only when it is a name of own_name_set.

    Args:
        root_node: The AST root node of the file.
        file_ext: Extension whose language settings the file is analyzed with.
        symbol_dict: The names of the file that come from other project files.
        own_name_set: The names the file defines itself (_own_name_set).
        scope_binding_list: The names bound for a range of lines of the file.
        import_line_dict: Name an import statement of the file binds -> lines of those
            statements.

    Returns:
        The usages in line order, without duplicates.
    """
    usage_node_types = EXT_TO_USAGE_NODE_TYPE_DICT.get(file_ext) or {}
    alias_list = [
        alias for alias in extract_typed_aliases(
            root_node, set(symbol_dict),
            usage_node_types.get("typed_alias_parent_types", set()),
            usage_node_types.get("scope_types", set()),
        )
        if alias.name not in symbol_dict
    ]
    alias_name_set = {alias.name for alias in alias_list}
    track_name_set = (
        set(symbol_dict) | alias_name_set | own_name_set
        | {scope_binding.name for scope_binding in scope_binding_list}
    )

    usage_list: list[UsageInfo] = []
    for usage in extract_usages(
        root_node, track_name_set, usage_node_types or None,
        member_names=own_name_set, alias_list=alias_list, import_line_dict=import_line_dict,
    ):
        root_name = usage_root_name(usage.name, track_name_set)
        if root_name in alias_name_set:
            type_name = typed_alias_type(alias_list, root_name, usage.line)
            if type_name is not None:
                usage.name = type_name + usage.name[len(root_name):]
            elif root_name not in own_name_set:
                continue
        usage_list.append(usage)
    return deduplicate_usage_list(usage_list)


def import_reference_target_list(
    file_rel: str,
    project_file_set: set[str],
    project_dir: str,
) -> list[ImportReferenceTarget]:
    """Resolve each reference of a file to the definition it names, through its import statements.

    A name the import statements bind to a project file leads to the file that defines
    it (ImportBinder.symbol_dict); so does a name visible without an import statement
    (Java / Kotlin: same package, SQL: whole project). A variable declared with such a
    type is named after the type. Every other name the file defines itself leads to
    the file itself, also when it is written after self / this (_own_name_set).
    A name bound for a range of lines (ImportBinder.scope_binding_list: an import
    statement written in a function, a JS/TS export statement with a source, a Rust
    use declaration in a block) leads to what it is bound to on those lines before
    any of the above, and to nothing when it is bound to nothing of the project.

    Processing flow:
    1. Return the targets of import_target_cache when they were resolved with the same
       project file set
    2. Map the names of the import statements to the files they come from
    3. Collect the names the file defines itself
    4. Turn each usage of those names into a target

    Args:
        file_rel: Relative path of the file.
        project_file_set: Relative paths of the project files that have a language.
        project_dir: Absolute path to the project root.

    Returns:
        One target per usage, in the order extract_usages() returns them. A target
        whose file_rel is the file itself is a reference to a definition of the file.
        The targets are kept in import_target_cache until the project file set
        changes or the cache is cleared.
    """
    # == Step 1: Cache ========================================================
    cache_key = os.path.abspath(os.path.join(project_dir, file_rel))
    target_list = project_cache_value(import_target_cache, cache_key, project_file_set)
    if target_list is not None:
        return target_list

    # == Step 2: Names of the import statements ===============================
    file_abs = os.path.join(project_dir, file_rel)
    file_ext = language_ext(file_abs)
    root_node = parse_file(file_abs)[0]
    binder = import_binder(project_dir, project_file_set)
    symbol_dict = binder.symbol_dict(file_rel)
    import_line_dict = binder.import_line_dict(file_rel)
    scope_binding_list = binder.scope_binding_list(file_rel)
    scope_binding_dict: dict[str, list[ScopeBinding]] = {}
    for scope_binding in scope_binding_list:
        scope_binding_dict.setdefault(
            symbol_part_list(scope_binding.name)[0], [],
        ).append(scope_binding)

    # == Step 3: Names of the file itself =====================================
    own_name_set = _own_name_set(
        root_node, file_ext, symbol_dict, import_line_dict,
        binder.import_line_dict(file_rel, is_whole_file=True),
    )

    # == Step 4: Targets ======================================================
    target_list = []
    for usage in _usage_list(
        root_node, file_ext, symbol_dict, own_name_set, scope_binding_list, import_line_dict,
    ):
        scope_binding = _scope_binding(scope_binding_dict, usage.name, usage.line)
        root_name = usage_root_name(usage.name, symbol_dict)
        if scope_binding is not None:
            if scope_binding.binding is not None:
                target_list.extend(_import_target_list(
                    usage.name, scope_binding.name, usage.line, scope_binding.binding, binder,
                ))
        elif root_name in symbol_dict:
            target_list.extend(_import_target_list(
                usage.name, root_name, usage.line, symbol_dict[root_name], binder,
            ))
        elif usage_root_name(usage.name, own_name_set) in own_name_set:
            target_list.append(ImportReferenceTarget(usage.name, usage.line, file_rel, usage.name))

    import_target_cache[cache_key] = (project_file_set, target_list)
    return target_list
