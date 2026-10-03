import os
from collections.abc import Callable
from dataclasses import dataclass
from tree_sitter import Node
from codetwine.parsers.ts_parser import class_macro_cache, parse_file
from codetwine.extractors.definitions import (
    ATTACHED_DEFINITION_TYPE_SET,
    CLASS_DEFINITION_TYPE_SET,
    TRANSPARENT_DEFINITION_TYPE_SET,
    DefinitionInfo,
    select_top_level_definitions,
)
from codetwine.extractors.definition_source import file_definition, file_definition_list
from codetwine.extractors.usages import (
    UsageInfo,
    deduplicate_usage_list,
    extract_typed_aliases,
    extract_usages,
    extract_value_aliases,
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

# First parts of a name written as a path from a module (Rust)
_MODULE_PATH_ROOT_SET = {"crate", "self", "super", "Self", ""}

# What _module_visibility() says of a name
_INSIDE = "inside"
_HIDDEN = "hidden"

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
    definition_list: list[DefinitionInfo],
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
        definition_list: The definitions of the file (extract_definitions).
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
        and the parts left over. A member of a C++ class that only a base class defines
        leads to the base class (ImportBinder.inherit_binding).
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

    target_file, definition_name = binder.inherit_binding(binding.file_rel, definition_name)
    target_list = [ImportReferenceTarget(usage_name, line, target_file, definition_name)]
    target_list.extend(
        ImportReferenceTarget(usage_name, line, implement_file, implement_name)
        for implement_file, implement_name in binder.implement_file_list(
            target_file, definition_name,
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


def _member_scope_dict(
    member_scope_list: list[tuple[int, int, dict[str, SymbolBinding]]],
) -> dict[str, list[tuple[int, int, SymbolBinding]]]:
    """Return the members of ImportBinder.member_scope_list by their names.

    Returns:
        {member name: [(first line, last line, the member), ...]}.
    """
    scope_dict: dict[str, list[tuple[int, int, SymbolBinding]]] = {}
    for start_line, end_line, member_dict in member_scope_list:
        for name, binding in member_dict.items():
            scope_dict.setdefault(name, []).append((start_line, end_line, binding))
    return scope_dict


def _member_scope_binding(
    member_scope_dict: dict[str, list[tuple[int, int, SymbolBinding]]], usage_name: str, line: int,
) -> SymbolBinding | None:
    """Return the member a usage names by its first part alone on the line it is written on.

    Args:
        member_scope_dict: The result of _member_scope_dict.
        usage_name: Name of the usage.
        line: Line of the usage (1-based).

    Returns:
        The member of the smallest range of lines around the line that has the first
        part of usage_name as a member, None when there is none.
    """
    match_list = [
        (end_line - start_line, binding)
        for start_line, end_line, binding in member_scope_dict.get(symbol_part_list(usage_name)[0], ())
        if start_line <= line <= end_line
    ]
    if not match_list:
        return None
    return min(match_list, key=lambda match: match[0])[1]


def _pattern_reference_function(
    file_ext: str,
    definition_list: list[DefinitionInfo],
    binding_dict: dict[str, SymbolBinding],
    project_dir: str,
) -> Callable[[str], bool] | None:
    """Return the function that tells whether a name written in a pattern refers to a definition.

    Rust: MAX in "MAX => ..." refers to the constant MAX when the file can write that
    name, and Dark to the variant after use Tone::*; any other name is bound by the
    pattern.

    Args:
        file_ext: Extension whose language settings the file is analyzed with.
        definition_list: The definitions of the file.
        binding_dict: Name the file can write -> what it stands for in the project.
        project_dir: Absolute path to the project root.

    Returns:
        The function, which looks each name up once; None for a language without
        pattern_reference_types.
    """
    usage_node_types = EXT_TO_USAGE_NODE_TYPE_DICT.get(file_ext) or {}
    reference_type_set = usage_node_types.get("pattern_reference_types")
    if reference_type_set is None:
        return None
    variant_type_set = usage_node_types.get("pattern_variant_types", set())
    own_reference_name_set = {
        definition.name for definition in definition_list if definition.type in reference_type_set
    }
    answer_dict: dict[str, bool] = {}

    def is_reference_name(name: str) -> bool:
        """Return whether the name refers to a constant, a static, a struct or a variant."""
        if name not in answer_dict:
            binding = binding_dict.get(name)
            answer = name in own_reference_name_set
            if not answer and binding is not None and binding.name is not None:
                definition = file_definition(binding.file_rel, binding.name, project_dir)
                last_part = symbol_part_list(binding.name)[-1]
                answer = definition is not None and (
                    (definition.type in reference_type_set and definition.name == last_part)
                    or (definition.type in variant_type_set and definition.name != last_part)
                )
            answer_dict[name] = answer
        return answer_dict[name]

    return is_reference_name


def _closed_module_scope(
    module_scope_list: list[tuple[int, int, bool]], line: int,
) -> tuple[int, int] | None:
    """Return the lines outside which the names of a file are not visible from a line.

    Args:
        module_scope_list: The result of ImportBinder.module_scope_list.
        line: A line of the file (1-based).

    Returns:
        (first line, last line) of the innermost module around the line that does not
        take over the names of the module around it; the modules that do are passed
        through. None when the names of the whole file are visible.
    """
    around_list = sorted(
        (scope for scope in module_scope_list if scope[0] <= line <= scope[1]),
        key=lambda scope: scope[1] - scope[0],
    )
    for start_line, end_line, is_open in around_list:
        if not is_open:
            return start_line, end_line
    return None


def _module_visibility(
    module_scope_list: list[tuple[int, int, bool]],
    usage: UsageInfo,
    root_name: str,
    definition_list: list[DefinitionInfo],
    top_level_name_set: set[str],
    symbol_dict: dict[str, SymbolBinding],
) -> str | None:
    """Tell whether the module around a usage hides the name the usage starts from.

    Rust: inside mod name { ... } without use super::*, a top-level name of the file
    and a name a use declaration of the file binds are not visible; a name defined
    inside the module is. A path from a module (crate::, self::, super::), a path that
    is bound as a whole and a name that is neither (a member of a type) are not judged.

    Args:
        module_scope_list: The result of ImportBinder.module_scope_list.
        usage: The usage.
        root_name: The name of symbol_dict the usage starts from, or its first part.
        definition_list: The definitions of the file.
        top_level_name_set: The top-level definition names of the file.
        symbol_dict: The names of the file that come from other project files.

    Returns:
        _INSIDE when the name is defined inside the module that closes the view,
        _HIDDEN when it is a name of outside that module, None when the name is not
        judged or no module closes the view.
    """
    first_part = symbol_part_list(usage.name)[0]
    if first_part in _MODULE_PATH_ROOT_SET or len(symbol_part_list(root_name)) != 1:
        return None
    closed_scope = _closed_module_scope(module_scope_list, usage.line)
    if closed_scope is None:
        return None
    if any(
        definition.name == first_part
        and closed_scope[0] <= definition.start_line <= closed_scope[1]
        for definition in definition_list
    ):
        return _INSIDE
    if first_part in top_level_name_set or root_name in symbol_dict:
        return _HIDDEN
    return None


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
    member_name_set: set[str],
    own_type_name_set: set[str],
    is_class_name: Callable[[str], bool],
    is_reference_name: Callable[[str], bool] | None,
) -> list[UsageInfo]:
    """Return the usages of the names a file can resolve.

    The names of symbol_dict, own_name_set, scope_binding_list and member_name_set are
    tracked, and the variables that stand for an object of a type of symbol_dict or of
    own_type_name_set: a variable declared with such a type, and a variable given an
    object of one inside a function. Such a variable is named after the type on the
    lines it counts for (Genre genre; genre.label -> Genre.label; e = Engine();
    e.run() -> Engine.run); elsewhere its name is a usage only when it is tracked as
    another kind of name.

    Args:
        root_node: The AST root node of the file.
        file_ext: Extension whose language settings the file is analyzed with.
        symbol_dict: The names of the file that come from other project files.
        own_name_set: The names the file defines itself (_own_name_set).
        scope_binding_list: The names bound for a range of lines of the file.
        import_line_dict: Name an import statement of the file binds -> lines of those
            statements.
        member_name_set: The names of ImportBinder.member_scope_list of the file.
        own_type_name_set: The names of the types the file defines itself.
        is_class_name: Returns whether a name, as the file writes it, names a type an
            object is made of (read for a language with typed_alias_class_only).
        is_reference_name: Returns whether a name written in a pattern refers to a
            definition instead of binding the name; None for a language without
            pattern_reference_types.

    Returns:
        The usages in line order, without duplicates.
    """
    usage_node_types = EXT_TO_USAGE_NODE_TYPE_DICT.get(file_ext) or {}
    type_name_set = set(symbol_dict) | own_type_name_set

    def is_type_name(name: str) -> bool:
        """Return whether a name written where an object is made names a tracked type."""
        if usage_root_name(name, type_name_set) not in type_name_set:
            return False
        return is_class_name(name) if usage_node_types.get("typed_alias_class_only") else True

    alias_list = [
        alias for alias in [
            *extract_typed_aliases(root_node, type_name_set, usage_node_types),
            *extract_value_aliases(root_node, usage_node_types, is_type_name),
        ]
        if alias.name not in symbol_dict
    ]
    alias_name_set = {alias.name for alias in alias_list}
    # The names that are a usage also where no alias counts for them
    plain_name_set = (
        own_name_set | member_name_set
        | {scope_binding.name for scope_binding in scope_binding_list}
    )
    track_name_set = set(symbol_dict) | alias_name_set | plain_name_set

    usage_list: list[UsageInfo] = []
    for usage in extract_usages(
        root_node, track_name_set, usage_node_types or None,
        member_names=own_name_set, alias_list=alias_list, import_line_dict=import_line_dict,
        is_reference_name=is_reference_name,
    ):
        root_name = usage_root_name(usage.name, track_name_set)
        if root_name in alias_name_set:
            type_name = typed_alias_type(alias_list, root_name, usage.line)
            if type_name is not None:
                usage.name = type_name + usage.name[len(root_name):]
            elif root_name not in plain_name_set:
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
    Inside a Rust inline module without use super::*, a name of the file outside the
    module leads to nothing (ImportBinder.module_scope_list).
    Inside a C++ function defined as a member of a class outside the class, a member
    of the class or of a base class written by its name alone leads to that member
    (ImportBinder.member_scope_list), before a name of the import statements.

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
    definition_list = file_definition_list(file_abs, EXT_TO_DEFINITION_DICT[file_ext])
    own_name_set = _own_name_set(
        definition_list, symbol_dict, import_line_dict,
        binder.import_line_dict(file_rel, is_whole_file=True),
    )
    own_type_name_set = {
        definition.name for definition in definition_list
        if definition.type in CLASS_DEFINITION_TYPE_SET and definition.name in own_name_set
    }

    def is_class_name(name: str) -> bool:
        """Return whether a name, as the file writes it, names a type an object is made of."""
        if name in own_type_name_set:
            return True
        root_name = usage_root_name(name, symbol_dict)
        if root_name not in symbol_dict:
            return False
        target = _import_target_list(name, root_name, 0, symbol_dict[root_name], binder)[0]
        definition = file_definition(target.file_rel, target.definition_name, project_dir)
        return (
            definition is not None and definition.type in CLASS_DEFINITION_TYPE_SET
            and definition.name == symbol_part_list(target.definition_name)[-1]
        )

    # == Step 4: Targets ======================================================
    member_scope_dict = _member_scope_dict(binder.member_scope_list(file_rel))
    binding_dict = {
        **{
            scope_binding.name: scope_binding.binding
            for scope_binding in scope_binding_list if scope_binding.binding is not None
        },
        **symbol_dict,
    }
    usage_list = _usage_list(
        root_node, file_ext, symbol_dict, own_name_set, scope_binding_list, import_line_dict,
        set(member_scope_dict), own_type_name_set, is_class_name,
        _pattern_reference_function(file_ext, definition_list, binding_dict, project_dir),
    )
    module_scope_list = binder.module_scope_list(file_rel)
    top_level_name_set = set(binder.top_level_name_list(file_rel)) if module_scope_list else set()
    # A macro name read as spaces in front of a class name is a usage of the macro, and
    # a member read where its module is required a usage of the member
    extra_usage_list = [
        UsageInfo(name=name, line=line)
        for name, line in [*class_macro_cache.get(file_abs, ()), *binder.use_list(file_rel)]
    ]
    if extra_usage_list:
        usage_list = deduplicate_usage_list(usage_list + extra_usage_list)

    target_list = []
    for usage in usage_list:
        scope_binding = _scope_binding(scope_binding_dict, usage.name, usage.line)
        member_binding = _member_scope_binding(member_scope_dict, usage.name, usage.line)
        root_name = usage_root_name(usage.name, symbol_dict)
        visibility = _module_visibility(
            module_scope_list, usage, root_name, definition_list, top_level_name_set, symbol_dict,
        ) if module_scope_list else None
        if scope_binding is not None:
            if scope_binding.binding is not None:
                target_list.extend(_import_target_list(
                    usage.name, scope_binding.name, usage.line, scope_binding.binding, binder,
                ))
        elif member_binding is not None:
            target_list.extend(_import_target_list(
                usage.name, symbol_part_list(usage.name)[0], usage.line, member_binding, binder,
            ))
        elif visibility == _HIDDEN:
            continue
        elif visibility == _INSIDE:
            target_list.append(ImportReferenceTarget(usage.name, usage.line, file_rel, usage.name))
        elif root_name in symbol_dict:
            target_list.extend(_import_target_list(
                usage.name, root_name, usage.line, symbol_dict[root_name], binder,
            ))
        elif usage_root_name(usage.name, own_name_set) in own_name_set:
            target_list.append(ImportReferenceTarget(usage.name, usage.line, file_rel, usage.name))

    import_target_cache[cache_key] = (project_file_set, target_list)
    return target_list
