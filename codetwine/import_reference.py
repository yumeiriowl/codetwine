import bisect
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from tree_sitter import Node
from codetwine.parsers.ts_parser import blank_macro_cache, parse_file
from codetwine.extractors.definitions import (
    ATTACHED_DEFINITION_TYPE_SET,
    CLASS_DEFINITION_TYPE_SET,
    MEMBER_OWNER_TYPE_SET,
    TRANSPARENT_DEFINITION_TYPE_SET,
    TYPE_NAME_DEFINITION_TYPE_SET,
    DefinitionInfo,
    member_owner_dict,
    select_top_level_definitions,
)
from codetwine.extractors.definition_source import (
    file_content,
    file_definition,
    file_definition_list,
    member_path_definition,
)
from codetwine.extractors.usages import (
    UsageInfo,
    deduplicate_usage_list,
    extract_typed_aliases,
    extract_usages,
    extract_value_aliases,
    symbol_part_list,
    typed_alias,
    typed_alias_dict,
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


# A word of a source text
_WORD_RE = re.compile(rb"[^\W\d]\w*")


@dataclass
class ImportReferenceTarget:
    """The definition one reference of a file resolves to."""

    name: str               # Name of the usage as the referring file writes it
    line: int               # Line of the reference (1-based)
    file_rel: str           # Relative path of the file with the definition
    definition_name: str    # Name the definition is looked up by in file_rel
    # First line of the definition when the name alone does not tell it from another
    # definition of the same name (1-based), else None
    definition_line: int | None = None


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
        (member_binding), and stands for the value of a CommonJS module when they
        name nothing of it (module_value_binding); a name bound to that value is
        followed through those parts the same way (module_member_binding). The
        definition_name is the name of the definition reached and the parts left over. A member of a C++ class that only a base class defines
        leads to the base class (ImportBinder.inherit_binding).
    """
    rest = usage_name[len(root_name):]
    part_list = [part for part in symbol_part_list(rest) if part]
    if binding.name is not None:
        member_binding, member_part_list = binder.module_member_binding(binding, part_list)
        if member_binding != binding:
            binding = member_binding
            definition_name = ".".join([binding.name, *member_part_list])
        else:
            definition_name = binding.name + rest
    else:
        binding, part_list = binder.member_binding(binding, part_list)
        if binding.name is None:
            # The value of a CommonJS module is what it assigns to module.exports
            binding = binder.module_value_binding(binding.file_rel) or binding
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


def _member_only_name_set(
    file_ext: str, definition_list: list[DefinitionInfo], own_name_set: set[str],
) -> set[str]:
    """Return the names a file defines only as members of a class or of another object.

    Python, JS/TS, Rust: a method or a field of a class is written after self / this
    or after the class; open() by itself beside a method open is a name of something
    else. JS/TS: req.accepts = function () {} and exports.run = ... define accepts and
    run, which the file writes as this.accepts and exports.run. A language whose
    members are written by their name alone inside their class (member_by_name) has
    no such names.

    Args:
        file_ext: Extension whose language settings the file is analyzed with.
        definition_list: The definitions of the file.
        own_name_set: The names the file defines itself (_own_name_set).

    Returns:
        The names of own_name_set whose definitions are all members of a class
        (member_owner_dict) or of a type in member_definition_types. Empty for a
        language with member_by_name.
    """
    usage_node_types = EXT_TO_USAGE_NODE_TYPE_DICT.get(file_ext) or {}
    if usage_node_types.get("member_by_name"):
        return set()
    member_type_set = usage_node_types.get("member_definition_types", set())
    owner_dict = member_owner_dict(definition_list)
    plain_name_set = {
        definition.name for definition in definition_list
        if definition.type not in member_type_set and id(definition) not in owner_dict
    }
    return {
        definition.name for definition in definition_list
        if definition.name in own_name_set and definition.name not in plain_name_set
    }


# Lines of a class read as its head, in which the classes it is made from are named
_CLASS_HEAD_LINE_MAX = 12

# The characters a line of the head of a class ends with on its last line
_CLASS_HEAD_END_TUPLE = (b"{", b":")

# Type arguments, and arguments, written in the head of a class without others inside them
_TYPE_ARGUMENT_RE = re.compile(rb"<[^<>]*>")
_ARGUMENT_RE = re.compile(rb"\([^()]*\)")

# A reference of a member that leads to no definition of the file
_NO_MEMBER = -1


def _class_key(name: str) -> str:
    """Return the name of a class without its scopes and template arguments (geo::Box<int> -> Box)."""
    return symbol_part_list(name.split("<", 1)[0])[-1].strip()


class _MemberLookup:
    """The members of the classes of a file, looked up by the class a line is written in."""

    def __init__(
        self, definition_list: list[DefinitionInfo], content: bytes, is_member_by_name: bool,
        is_head_argument_skip: bool,
    ) -> None:
        """
        Args:
            definition_list: The definitions of the file, sorted by start_line.
            content: The text of the file as UTF-8 bytes.
            is_member_by_name: The member_by_name of the language of the file.
            is_head_argument_skip: The class_head_argument_skip of the language of the file.
        """
        self._definition_list = definition_list
        self._content = content
        self._is_member_by_name = is_member_by_name
        self._is_head_argument_skip = is_head_argument_skip
        # Names of the definitions that are no members of a class
        self._plain_name_set: set[str] = set()
        # Built on the first lookup: member name -> (key of its class, member)
        self._member_dict: dict[str, list[tuple[str, DefinitionInfo, DefinitionInfo]]] | None = None
        self._owner_list: list[DefinitionInfo] = []
        self._owner_start_list: list[int] = []
        self._owner_key_dict: dict[str, list[DefinitionInfo]] = {}
        self._parent_dict: dict[int, DefinitionInfo | None] = {}
        self._base_key_dict: dict[str, list[str]] = {}
        self._derived_key_dict: dict[str, list[str]] | None = None

    def _build(self) -> None:
        """Index the members by name and the classes by the lines they cover."""
        owner_dict = member_owner_dict(self._definition_list)
        member_dict: dict[str, list[tuple[str, DefinitionInfo, DefinitionInfo]]] = {}
        owner_stack: list[DefinitionInfo] = []
        for definition in self._definition_list:
            owner = owner_dict.get(id(definition))
            # A member named like its class is a constructor: the name stands for the class
            if (
                owner is not None and owner.name != definition.name
                and definition.type not in ATTACHED_DEFINITION_TYPE_SET
            ):
                member_dict.setdefault(definition.name, []).append(
                    (_class_key(owner.name), owner, definition)
                )
            elif owner is None and definition.type not in ATTACHED_DEFINITION_TYPE_SET:
                self._plain_name_set.add(definition.name)
            if definition.type in MEMBER_OWNER_TYPE_SET:
                while owner_stack and owner_stack[-1].end_line < definition.start_line:
                    owner_stack.pop()
                self._parent_dict[id(definition)] = owner_stack[-1] if owner_stack else None
                owner_stack.append(definition)
                self._owner_list.append(definition)
                self._owner_start_list.append(definition.start_line)
                self._owner_key_dict.setdefault(_class_key(definition.name), []).append(definition)
        self._member_dict = member_dict

    def _base_key_list(self, class_key: str) -> list[str]:
        """Return the classes of the file a class is made from.

        class Child(Base):  /  class B extends A<Item> implements I {  /  impl Runner for Config {
        The head of each definition of the class is read: its lines up to the first
        one that ends with "{" or ":", at most _CLASS_HEAD_LINE_MAX lines, without
        the type arguments written in "<>" and, for a language with
        class_head_argument_skip, without the arguments written in "()". A class of
        the file whose name is written there as a word is such a class.

        Args:
            class_key: The _class_key of a definition in MEMBER_OWNER_TYPE_SET.

        Returns:
            The _class_key of each such class, without class_key itself. Read once per
            class.
        """
        base_key_list = self._base_key_dict.get(class_key)
        if base_key_list is not None:
            return base_key_list
        word_set: set[bytes] = set()
        for owner in self._owner_key_dict.get(class_key, ()):
            if owner.start_byte is None:
                continue
            line_list = self._content[owner.start_byte:owner.end_byte].split(
                b"\n", _CLASS_HEAD_LINE_MAX,
            )[:_CLASS_HEAD_LINE_MAX]
            head_line_list: list[bytes] = []
            for line in line_list:
                head_line_list.append(line)
                if line.rstrip().endswith(_CLASS_HEAD_END_TUPLE):
                    break
            head = b" ".join(head_line_list)
            group_re_list = [_TYPE_ARGUMENT_RE]
            if self._is_head_argument_skip:
                group_re_list.append(_ARGUMENT_RE)
            for group_re in group_re_list:
                group_count = 1
                while group_count:
                    head, group_count = group_re.subn(b" ", head)
            word_set.update(_WORD_RE.findall(head))
        base_key_list = [
            key for key in self._owner_key_dict
            if key != class_key and key.encode("utf-8") in word_set
        ]
        self._base_key_dict[class_key] = base_key_list
        return base_key_list

    def _derived_key_list(self, class_key: str) -> list[str]:
        """Return the classes of the file made from a class (the reverse of _base_key_list)."""
        if self._derived_key_dict is None:
            derived_key_dict: dict[str, list[str]] = {}
            for key in self._owner_key_dict:
                for base_key in self._base_key_list(key):
                    derived_key_dict.setdefault(base_key, []).append(key)
            self._derived_key_dict = derived_key_dict
        return self._derived_key_dict.get(class_key, [])

    def _related_member(
        self,
        class_key: str,
        member_list: list[tuple[str, DefinitionInfo, DefinitionInfo]],
        related_key_list: Callable[[str], list[str]],
        visit_key_set: set[str],
    ) -> DefinitionInfo | None:
        """Return the member of a class, or of the classes related to it, among the members given.

        Args:
            class_key: The _class_key of the class to start from.
            member_list: (key of its class, class, member) of each member of one name.
            related_key_list: _base_key_list or _derived_key_list.
            visit_key_set: The classes the walk has been at; class_key is added to it.

        Returns:
            The member of the first class that has one: the class itself, then the
            classes related_key_list gives for it, each followed the same way.
        """
        if class_key in visit_key_set:
            return None
        visit_key_set.add(class_key)
        for owner_key, _, candidate in member_list:
            if owner_key == class_key:
                return candidate
        for related_key in related_key_list(class_key):
            candidate = self._related_member(related_key, member_list, related_key_list, visit_key_set)
            if candidate is not None:
                return candidate
        return None

    def definition_line(self, usage_name: str, line: int, is_member: bool) -> int | None:
        """Return the first line of the member a name names in the classes around a line.

        class A: def run ...
        class B: def run ...; def go(self): self.run()   -> the run of B
        class C(A): def go(self): self.run()              -> the run of A

        Args:
            usage_name: Name of a usage that leads to the file itself.
            line: Line of the usage (1-based).
            is_member: Whether the name is written as a member (UsageInfo.is_member).

        Returns:
            The first line of the definition the name leads to from the definition
            named like its first part that is a member of the innermost class around
            the line; else of another definition of that class (Rust: another impl
            block of the type; C++: another specialization of the template), of a
            class of the file that class is made from (_base_key_list), or of a class
            of the file made from it (_derived_key_list). The classes around that
            class are tried after it. The parts after the first are followed through
            the definitions inside that member (member_path_definition). A member
            named like its class (a constructor) is no such member.
            When no class around the line leads to the member: _NO_MEMBER for a name
            written as a member inside a class, unless the language is one with
            member_by_name and a definition of that name is no member of a class;
            None in every other case (the name is looked up among all definitions of
            the file).
        """
        if self._member_dict is None:
            self._build()
        part_list = symbol_part_list(usage_name)
        member_list = self._member_dict.get(part_list[0])
        if not member_list:
            return None
        index = bisect.bisect_right(self._owner_start_list, line) - 1
        around = self._owner_list[index] if index >= 0 else None
        is_in_class = False
        while around is not None:
            if around.end_line >= line:
                is_in_class = True
                candidate = next(
                    (candidate for _, owner, candidate in member_list if owner is around), None,
                )
                around_key = _class_key(around.name)
                if candidate is None:
                    candidate = self._related_member(
                        around_key, member_list, self._base_key_list, set(),
                    )
                if candidate is None:
                    candidate = self._related_member(
                        around_key, member_list, self._derived_key_list, set(),
                    )
                if candidate is not None:
                    return member_path_definition(
                        self._definition_list, candidate, part_list[1:],
                    ).start_line
            around = self._parent_dict[id(around)]
        if is_member and is_in_class and not (
            self._is_member_by_name and part_list[0] in self._plain_name_set
        ):
            return _NO_MEMBER
        return None


def _extension_name_set(
    file_ext: str,
    definition_list: list[DefinitionInfo],
    own_name_set: set[str],
    symbol_dict: dict[str, SymbolBinding],
    binder: ImportBinder,
) -> set[str]:
    """Return the names of a file that name a definition declared for values of another type.

    Kotlin: fun Shape.describe() is called as value.describe(); the name is such a
    name in the file that defines it and in every file that sees it. A name that is
    also the name of a member of a type of the project is left out.

    Args:
        file_ext: Extension whose language settings the file is analyzed with.
        definition_list: The definitions of the file.
        own_name_set: The names the file defines itself (_own_name_set).
        symbol_dict: The names of the file that come from other project files.
        binder: The ImportBinder of the project.

    Returns:
        The names of own_name_set and symbol_dict whose definition has a receiver
        (DefinitionInfo.has_receiver), without the names of
        ImportBinder.package_member_name_set. Empty for a language without
        extension_call.
    """
    if not (EXT_TO_USAGE_NODE_TYPE_DICT.get(file_ext) or {}).get("extension_call"):
        return set()
    name_set = {
        definition.name for definition in definition_list
        if definition.has_receiver and definition.name in own_name_set
    }
    name_set.update(
        name for name, binding in symbol_dict.items()
        if binding.name is not None and binding.name in binder.receiver_name_set(binding.file_rel)
    )
    return name_set - binder.package_member_name_set() if name_set else name_set


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
    inside the module is, and so is a macro_rules! macro of the file. A path from a module (crate::, self::, super::), a path that
    is bound as a whole and a name that is neither (a member of a type) are not judged.

    Args:
        module_scope_list: The result of ImportBinder.module_scope_list.
        usage: The usage.
        root_name: The name of symbol_dict the usage starts from, or its first part.
        definition_list: The definitions of the file.
        top_level_name_set: The top-level definition names of the file that an inline
            module does not see.
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
    extension_name_set: set[str],
    member_only_name_set: set[str],
) -> list[UsageInfo]:
    """Return the usages of the names a file can resolve.

    The names of symbol_dict, own_name_set, scope_binding_list and member_name_set are
    tracked, and the variables that stand for an object of a type of symbol_dict or of
    own_type_name_set: a variable declared with such a type, and a variable given an
    object of one inside a function. Such a variable is named after the type on the
    lines it counts for (Genre genre; genre.label -> Genre.label; e = Engine();
    e.run() -> Engine.run); elsewhere its name is a usage only when it is tracked as
    another kind of name. A variable of a function counts also when it is named like
    a name of symbol_dict; a variable declared outside every function does not. A
    variable declared outside every function that the file
    defines itself (a field, a variable of the file) is a usage under its own name as
    well.

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
        extension_name_set: The names of the file that are a usage when written after
            any value (_extension_name_set).
        member_only_name_set: The names of own_name_set that are a usage only when
            written after self / this (_member_only_name_set).

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

    # A variable declared with a tracked type keeps it whatever it is given
    declare_alias_list = extract_typed_aliases(root_node, type_name_set, usage_node_types)
    declare_alias_dict = typed_alias_dict(declare_alias_list)
    alias_list = [
        alias for alias in [
            *declare_alias_list,
            *(
                alias for alias in extract_value_aliases(
                    root_node, usage_node_types, is_type_name, import_line_dict,
                )
                if typed_alias(declare_alias_dict, alias.name, alias.start_line) is None
            ),
        ]
        if alias.name not in symbol_dict or not alias.is_file_level
    ]
    alias_dict = typed_alias_dict(alias_list)
    alias_name_set = set(alias_dict)
    # The names that are a usage also where no alias counts for them
    plain_name_set = (
        (own_name_set - member_only_name_set) | member_name_set
        | {scope_binding.name for scope_binding in scope_binding_list}
    )
    track_name_set = set(symbol_dict) | alias_name_set | plain_name_set

    usage_list: list[UsageInfo] = []
    for usage in extract_usages(
        root_node, track_name_set, usage_node_types or None,
        member_names=own_name_set, alias_list=alias_list, import_line_dict=import_line_dict,
        is_reference_name=is_reference_name, extension_names=extension_name_set,
    ):
        root_name = usage_root_name(usage.name, track_name_set)
        if root_name in alias_name_set:
            if usage.is_member:
                # A member is no variable of a function: only a field of its name counts
                alias = next(
                    (
                        field_alias for field_alias in alias_dict[root_name]
                        if field_alias.is_file_level
                        and field_alias.start_line <= usage.line <= field_alias.end_line
                    ),
                    None,
                )
            else:
                alias = typed_alias(alias_dict, root_name, usage.line)
            if alias is not None and alias.type_name is not None:
                if alias.is_file_level and root_name in own_name_set:
                    usage_list.append(UsageInfo(usage.name, usage.line, usage.is_member))
                usage.name = alias.type_name + usage.name[len(root_name):]
            elif (
                not usage.is_member and root_name not in plain_name_set
                and root_name not in symbol_dict
            ):
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
    the file itself, also when it is written after self / this (_own_name_set); a
    member is looked up among the members of the classes around the line, of the
    classes of the file they are made from and of the classes made from them before
    the other definitions of its name, and a name written after self / this inside a
    class leads to no member of any other class (_MemberLookup).
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
        if (
            definition.type in CLASS_DEFINITION_TYPE_SET
            or definition.type in TYPE_NAME_DEFINITION_TYPE_SET
        ) and definition.name in own_name_set
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
        _extension_name_set(file_ext, definition_list, own_name_set, symbol_dict, binder),
        _member_only_name_set(file_ext, definition_list, own_name_set),
    )
    module_scope_list = binder.module_scope_list(file_rel)
    # The top-level names an inline module does not see: the ones no definition of a
    # module_open_types type carries
    open_type_set = (EXT_TO_USAGE_NODE_TYPE_DICT.get(file_ext) or {}).get("module_open_types", set())
    top_level_name_set = (
        set(binder.top_level_name_list(file_rel))
        - {definition.name for definition in definition_list if definition.type in open_type_set}
    ) if module_scope_list else set()
    # A macro name read as spaces is a usage of the macro, and a member read where its
    # module is required a usage of the member
    extra_usage_list = [
        UsageInfo(name=name, line=line)
        for name, line in [*blank_macro_cache.get(file_abs, ()), *binder.use_list(file_rel)]
    ]
    if extra_usage_list:
        usage_list = deduplicate_usage_list(usage_list + extra_usage_list)

    is_member_by_name = bool(
        (EXT_TO_USAGE_NODE_TYPE_DICT.get(file_ext) or {}).get("member_by_name")
    )
    member_lookup = _MemberLookup(
        definition_list, file_content(file_abs), is_member_by_name,
        bool((EXT_TO_USAGE_NODE_TYPE_DICT.get(file_ext) or {}).get("class_head_argument_skip")),
    )
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
            # A member is looked up in the classes around the line first
            definition_line = (
                member_lookup.definition_line(usage.name, usage.line, usage.is_member)
                if usage.is_member or is_member_by_name else None
            )
            if definition_line != _NO_MEMBER:
                target_list.append(ImportReferenceTarget(
                    usage.name, usage.line, file_rel, usage.name, definition_line,
                ))

    import_target_cache[cache_key] = (project_file_set, target_list)
    return target_list
