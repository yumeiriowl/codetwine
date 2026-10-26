import bisect
import os
import re
from collections import ChainMap
from collections.abc import Callable, Container, Mapping
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
    is_within,
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
    TypedAlias,
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
from codetwine.extractors.rust_path import MODULE_PATH_ROOT_SET
from codetwine.import_binding import (
    ImportBinder,
    ScopeBinding,
    SymbolBinding,
    import_binder,
    member_function_class_name,
)
from codetwine.utils.project_cache import project_cache_value
from codetwine.config.settings import (
    EXT_TO_DEFINITION_DICT,
    EXT_TO_USAGE_NODE_TYPE_DICT,
    language_ext,
)

# What _module_visibility() says of a name
_INSIDE = "inside"
_INVISIBLE = "hidden"

# Cache of the resolved references of the files read through their import statements:
# absolute path -> (project file set they were resolved with, targets)
_import_target_cache_dict: dict[str, tuple[set[str], list["ImportReferenceTarget"]]] = {}


# A word of a source text
_WORD_RE = re.compile(rb"[^\W\d]\w*")

# What a member is written after its object with (variable.member)
_MEMBER_SEPARATOR = "."


@dataclass
class ImportReferenceTarget:
    """The definition one reference of a file resolves to."""

    name: str               # Name of the usage as the referring file writes it
    line: int               # Line of the reference (1-based)
    file_rel: str           # Relative path of the file with the definition
    definition_name: str    # Name the definition is looked up by in file_rel
    # First line and first byte of the member the classes around the reference lead to
    # (1-based line); None when the definition is looked up by its name alone
    definition_line: int | None = None
    definition_start_byte: int | None = None


def _is_around(definition: DefinitionInfo, line: int, start_byte: int | None) -> bool:
    """Return whether the text at a line and byte is inside a definition.

    Compared by the byte when the definition and start_byte are known, else by the line.
    """
    if definition.start_byte is not None and start_byte is not None:
        return definition.start_byte <= start_byte < definition.end_byte
    return definition.start_line <= line <= definition.end_line


class _AnyNameSet(Container[str]):
    """The names that are in any of several containers, looked up without copying them."""

    def __init__(self, *container_tuple: Container[str]) -> None:
        """Keep the containers."""
        self._container_tuple = container_tuple

    def __contains__(self, name: object) -> bool:
        """Return whether a container has the name."""
        return any(name in container for container in self._container_tuple)


def _own_name_set(
    definition_list: list[DefinitionInfo],
    symbol_dict: Mapping[str, SymbolBinding],
    import_line_dict: dict[str, set[int]],
    file_import_line_dict: dict[str, set[int]],
) -> set[str]:
    """Return the names the file defines itself and refers to by those names.

    A name an import statement binds is not such a name, also when the import leads
    outside the project: a definition written on the line of an import statement that
    binds its name for the whole file (const path = require("path")) is the import
    itself, and a name defined only inside another definition gives way to the
    import. A name the file defines at its top level apart from the import stays its
    own, and so does a member of a class of the file (member_owner_dict).

    Args:
        definition_list: The definitions of the file (extract_definitions).
        symbol_dict: The names of the file that come from other project files.
        import_line_dict: Name an import statement of the file binds -> lines of those
            statements.
        file_import_line_dict: The same for the statements that bind their names for
            the whole file.

    Returns:
        The definition names of the file, without the names of symbol_dict that no
        member carries, the names described above and the names carried only by
        definitions in ATTACHED_DEFINITION_TYPE_SET or TRANSPARENT_DEFINITION_TYPE_SET.
    """
    owner_dict = member_owner_dict(definition_list)

    def is_own(definition: DefinitionInfo) -> bool:
        """Return whether a definition gives a name of the file apart from its import statements."""
        return (
            bool(definition.name)
            and definition.type not in ATTACHED_DEFINITION_TYPE_SET
            and definition.type not in TRANSPARENT_DEFINITION_TYPE_SET
            and (id(definition) in owner_dict or definition.name not in symbol_dict)
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
        and (
            id(definition) in owner_dict or definition.name not in import_line_dict
            or definition.name in top_level_name_set
        )
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
        definition_name is the name of the definition reached and the parts left
        over. A member of a C++ class that only a base class defines leads to the
        base class (ImportBinder.inherit_binding).
    """
    rest_name = usage_name[len(root_name):]
    part_list = [part for part in symbol_part_list(rest_name) if part]
    if binding.name is not None:
        member_binding, member_part_list = binder.module_member_binding(binding, part_list)
        if member_binding != binding:
            binding = member_binding
            definition_name = ".".join([binding.name, *member_part_list])
        else:
            definition_name = binding.name + rest_name
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


def _scope_binding_dict(scope_binding_list: list[ScopeBinding]) -> dict[str, list[ScopeBinding]]:
    """Return the bindings of ImportBinder.scope_binding_list by the first part of their names.

    Returns:
        {first part of a bound name: the bindings of the names that start with it, in
        the order of scope_binding_list}.
    """
    scope_binding_dict: dict[str, list[ScopeBinding]] = {}
    for scope_binding in scope_binding_list:
        scope_binding_dict.setdefault(
            symbol_part_list(scope_binding.name)[0], [],
        ).append(scope_binding)
    return scope_binding_dict


class _MemberScopeIndex:
    """The members of ImportBinder.member_scope_list, looked up by where a usage is written."""

    def __init__(
        self, member_scope_list: list[tuple[DefinitionInfo, dict[str, SymbolBinding]]],
    ) -> None:
        """Index the definitions of member_scope_list (in line order) by where they start.

        Args:
            member_scope_list: The result of ImportBinder.member_scope_list.
        """
        # The member names of every definition
        self.name_set = {name for _, member_dict in member_scope_list for name in member_dict}
        # id of a definition -> its members, and the definition of the list around it
        self._member_dict_dict: dict[int, dict[str, SymbolBinding]] = {}
        self._parent_dict: dict[int, DefinitionInfo | None] = {}
        scope_stack: list[DefinitionInfo] = []
        for definition, member_dict in member_scope_list:
            while scope_stack and not is_within(definition, scope_stack[-1]):
                scope_stack.pop()
            self._parent_dict[id(definition)] = scope_stack[-1] if scope_stack else None
            self._member_dict_dict[id(definition)] = member_dict
            scope_stack.append(definition)
        self._position_index = _PositionIndex([definition for definition, _ in member_scope_list])

    def binding(self, usage: UsageInfo) -> SymbolBinding | None:
        """Return the member a usage names by the first part of its name where it is written.

        Returns:
            The member of the innermost definition around the usage (_is_around) that
            has the first part of the usage name as a member, None when there is none.
        """
        name = symbol_part_list(usage.name)[0]
        if name not in self.name_set:
            return None
        scope, compare_byte = self._position_index.last_start(usage.line, usage.start_byte)
        while scope is not None:
            if _is_around(scope, usage.line, compare_byte):
                binding = self._member_dict_dict[id(scope)].get(name)
                if binding is not None:
                    return binding
            scope = self._parent_dict[id(scope)]
        return None


def _pattern_reference_function(
    file_ext: str,
    definition_list: list[DefinitionInfo],
    binding_dict: Mapping[str, SymbolBinding],
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

# End of the node types of a comment (comment, line_comment, block_comment, ...), and a
# character of a comment other than a line break
_COMMENT_TYPE_END = "comment"
_COMMENT_CHAR_RE = re.compile(rb"[^\n]")

# The text after the name of a call that passes arguments: name(a), name { ... }, name() { ... }
_ARGUMENT_CALL_RE = re.compile(rb"\s*(?:\{|\(\s*[^\s)]|\(\s*\)\s*\{)")

# The text after the name of a function declared without parameters: name()
_NO_PARAMETER_RE = re.compile(rb"\s*\(\s*\)")

# Type arguments, and arguments, written in the head of a class without others inside them
_TYPE_ARGUMENT_RE = re.compile(rb"<[^<>]*>")
_ARGUMENT_RE = re.compile(rb"\([^()]*\)")

# What _MemberLookup.member_definition gives for a reference of a member that leads to
# no definition of the file (compared by identity)
_NO_MEMBER = DefinitionInfo(name="", type="", start_line=0, end_line=0)


def _class_key(name: str) -> str:
    """Return the name of a class without its scopes and template arguments (geo::Box<int> -> Box)."""
    return symbol_part_list(name.split("<", 1)[0])[-1].strip()


class _PositionIndex:
    """Definitions looked up by where they start: the last one that starts at or before a text."""

    def __init__(self, definition_list: list[DefinitionInfo]) -> None:
        """Index the definitions by their first lines and, when each has them, by their first bytes."""
        self._line_list = sorted(definition_list, key=lambda definition: definition.start_line)
        self._start_line_list = [definition.start_line for definition in self._line_list]
        self._byte_list: list[DefinitionInfo] = []
        self._start_byte_list: list[int] = []
        if all(definition.start_byte is not None for definition in definition_list):
            self._byte_list = sorted(definition_list, key=lambda definition: definition.start_byte)
            self._start_byte_list = [definition.start_byte for definition in self._byte_list]

    def last_start(self, line: int, start_byte: int | None) -> tuple[DefinitionInfo | None, int | None]:
        """Return the last definition that starts at or before a text, and the byte to compare by.

        Args:
            line: Line of the text (1-based).
            start_byte: First byte of the text, None when it is not known.

        Returns:
            (the definition, None when there is none; start_byte when the definitions are
            looked up by bytes, else None).
        """
        if start_byte is not None and len(self._byte_list) == len(self._line_list):
            index = bisect.bisect_right(self._start_byte_list, start_byte) - 1
            return (self._byte_list[index] if index >= 0 else None), start_byte
        index = bisect.bisect_right(self._start_line_list, line) - 1
        return (self._line_list[index] if index >= 0 else None), None


class _MemberLookup:
    """The members of the classes of a file, looked up by the classes a usage is written in."""

    def __init__(
        self, definition_list: list[DefinitionInfo], content: bytes, root_node: Node,
        is_member_by_name: bool, is_head_argument_skip: bool, member_function_type_set: set[str],
        is_class_call: bool,
    ) -> None:
        """Keep the definitions and the text of a file; the index is built on the first lookup.

        Args:
            definition_list: The definitions of the file, sorted by start_line.
            content: The text of the file as UTF-8 bytes.
            root_node: The AST root node of the file (content is the text it is read from).
            is_member_by_name: The member_by_name of the language of the file.
            is_head_argument_skip: The class_head_argument_skip of the language of the file.
            member_function_type_set: The member_function_types of the language of the
                file (empty when it has none).
            is_class_call: The class_call of the language of the file.
        """
        self._definition_list = definition_list
        self._content = content
        self._root_node = root_node
        # content with the comments made blanks, built on the first read of a head
        self._code_content: bytes | None = None
        self._is_member_by_name = is_member_by_name
        self._is_head_argument_skip = is_head_argument_skip
        self._member_function_type_set = member_function_type_set
        self._is_class_call = is_class_call
        # Names of the definitions that are no members of a class
        self._plain_name_set: set[str] = set()
        # Built on the first lookup: member name -> [(key of its class, class, member), ...]
        self._member_dict: dict[str, list[tuple[str, DefinitionInfo, DefinitionInfo]]] | None = None
        # (member name, id of its class) -> the members, and (member name, _class_key of
        # its class) -> the members, in the order of the definitions
        self._owner_member_dict: dict[tuple[str, int], list[DefinitionInfo]] = {}
        self._key_member_dict: dict[tuple[str, str], list[DefinitionInfo]] = {}
        # (member name, is_call, whether the call passes arguments) -> whether a member of
        # the name is of the kind a usage of that form names (member_definition)
        self._kind_match_dict: dict[tuple[str, bool, bool], bool] = {}
        # The definitions in MEMBER_OWNER_TYPE_SET by where they start (the innermost one
        # around a text is the last start or a class around it)
        self._owner_index = _PositionIndex([])
        # The functions defined as a member of a class of the file outside the class
        # (member_function_class_name) by where they start, and id of each -> its class
        self._member_function_index = _PositionIndex([])
        self._member_function_owner_dict: dict[int, DefinitionInfo] = {}
        # _class_key -> the definitions in MEMBER_OWNER_TYPE_SET of that key
        self._owner_key_dict: dict[str, list[DefinitionInfo]] = {}
        # id of a definition in MEMBER_OWNER_TYPE_SET -> the one around it, None at the top
        self._parent_dict: dict[int, DefinitionInfo | None] = {}
        # _class_key -> _base_key_list of it, read once per class
        self._base_key_dict: dict[str, list[str]] = {}
        # _class_key -> _subclass_key_list of it, built on first use
        self._subclass_key_dict: dict[str, list[str]] | None = None

    def _build(self) -> None:
        """Index the members by name, and the classes and member functions by where they start."""
        owner_dict = member_owner_dict(self._definition_list)
        member_dict: dict[str, list[tuple[str, DefinitionInfo, DefinitionInfo]]] = {}
        owner_stack: list[DefinitionInfo] = []
        owner_list: list[DefinitionInfo] = []
        for definition in self._definition_list:
            owner = owner_dict.get(id(definition))
            # A member named like its class is a constructor: the name stands for the class
            if (
                owner is not None and owner.name != definition.name
                and definition.type not in ATTACHED_DEFINITION_TYPE_SET
            ):
                owner_key = _class_key(owner.name)
                member_dict.setdefault(definition.name, []).append((owner_key, owner, definition))
                self._owner_member_dict.setdefault((definition.name, id(owner)), []).append(definition)
                self._key_member_dict.setdefault((definition.name, owner_key), []).append(definition)
            elif owner is None and definition.type not in ATTACHED_DEFINITION_TYPE_SET:
                self._plain_name_set.add(definition.name)
            if definition.type in MEMBER_OWNER_TYPE_SET:
                while owner_stack and not is_within(definition, owner_stack[-1]):
                    owner_stack.pop()
                self._parent_dict[id(definition)] = owner_stack[-1] if owner_stack else None
                owner_stack.append(definition)
                owner_list.append(definition)
                self._owner_key_dict.setdefault(_class_key(definition.name), []).append(definition)
        self._member_dict = member_dict
        self._owner_index = _PositionIndex(owner_list)
        member_function_list: list[DefinitionInfo] = []
        for definition in self._definition_list:
            class_name = member_function_class_name(definition)
            class_list = self._owner_key_dict.get(_class_key(class_name)) if class_name else None
            if class_list:
                member_function_list.append(definition)
                self._member_function_owner_dict[id(definition)] = class_list[0]
        self._member_function_index = _PositionIndex(member_function_list)

    def _around_owner_list(self, line: int, start_byte: int | None) -> list[DefinitionInfo]:
        """Return the classes whose members a usage sees by their names alone, innermost first.

        These are the classes written around the usage (_is_around: by bytes when the
        usage and every class have them, else by lines), then, inside a function
        defined as a member of a class of the file outside the class
        (int Box::fit() { ... }), that class and the classes around it.

        Args:
            line: Line of the usage (1-based).
            start_byte: First byte of the usage, None when it is not known.
        """
        around_list: list[DefinitionInfo] = []
        around, compare_byte = self._owner_index.last_start(line, start_byte)
        while around is not None:
            if _is_around(around, line, compare_byte):
                around_list.append(around)
            around = self._parent_dict[id(around)]
        member_function, compare_byte = self._member_function_index.last_start(line, start_byte)
        if member_function is not None and _is_around(member_function, line, compare_byte):
            around = self._member_function_owner_dict[id(member_function)]
            while around is not None:
                around_list.append(around)
                around = self._parent_dict[id(around)]
        return around_list

    def _code_text(self) -> bytes:
        """Return the text of the file with each comment made blanks (its line breaks kept)."""
        if self._code_content is None:
            code_content = bytearray(self._content)
            node_stack = [self._root_node]
            while node_stack:
                node = node_stack.pop()
                if node.type.endswith(_COMMENT_TYPE_END):
                    code_content[node.start_byte:node.end_byte] = _COMMENT_CHAR_RE.sub(
                        b" ", code_content[node.start_byte:node.end_byte],
                    )
                    continue
                node_stack.extend(node.children)
            self._code_content = bytes(code_content)
        return self._code_content

    def _base_key_list(self, class_key: str) -> list[str]:
        """Return the classes of the file a class is made from.

        class Child(Base):  /  class B extends A<Item> implements I {  /  impl Runner for Config {
        The head of each definition of the class is read from the text without its
        comments (_code_text): its lines up to the first one that ends with "{" or
        ":", at most _CLASS_HEAD_LINE_MAX lines, without the type arguments written
        in "<>" and, for a language with class_head_argument_skip, without the
        arguments written in "()". A class of the file whose name is written there as
        a word is such a class.

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
            line_list = self._code_text()[owner.start_byte:owner.end_byte].split(
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

    def _subclass_key_list(self, class_key: str) -> list[str]:
        """Return the classes of the file made from a class (the reverse of _base_key_list)."""
        if self._subclass_key_dict is None:
            subclass_key_dict: dict[str, list[str]] = {}
            for key in self._owner_key_dict:
                for base_key in self._base_key_list(key):
                    subclass_key_dict.setdefault(base_key, []).append(key)
            self._subclass_key_dict = subclass_key_dict
        return self._subclass_key_dict.get(class_key, [])

    def _relation_member(
        self,
        class_key: str,
        name: str,
        is_match: Callable[[DefinitionInfo], bool],
        relation_key_list: Callable[[str], list[str]],
        visit_key_set: set[str],
    ) -> DefinitionInfo | None:
        """Return the member of a name of a class, or of the classes related to it.

        Args:
            class_key: The _class_key of the class to start from.
            name: The member name.
            is_match: Returns whether a member of the name may be the one.
            relation_key_list: _base_key_list or _subclass_key_list.
            visit_key_set: The classes the walk has been at; class_key is added to it.

        Returns:
            The first member is_match takes of the first class that has one: the class
            itself, then the classes relation_key_list gives for it, each followed the
            same way.
        """
        if class_key in visit_key_set:
            return None
        visit_key_set.add(class_key)
        for candidate in self._key_member_dict.get((name, class_key), ()):
            if is_match(candidate):
                return candidate
        for relation_key in relation_key_list(class_key):
            candidate = self._relation_member(
                relation_key, name, is_match, relation_key_list, visit_key_set,
            )
            if candidate is not None:
                return candidate
        return None


    def is_written_alone(self, usage: UsageInfo) -> bool:
        """Return whether the text a usage is read from starts with the first name it is written with.

        That name is the variable a usage named after the type of the variable is
        written after (UsageInfo.alias_name), else the first part of the usage name.
        ::store::Open(...) is read as Open with its scopes written before it; a usage
        without a byte range counts as written alone.
        """
        if usage.start_byte is None:
            return True
        first_name = usage.alias_name or symbol_part_list(usage.name)[0]
        return self._content.startswith(first_name.encode("utf-8"), usage.start_byte)

    def _is_parameterless(self, definition: DefinitionInfo) -> bool:
        """Return whether a function is declared without parameters (its name followed by "()")."""
        if definition.name_end_byte is None:
            return False
        return _NO_PARAMETER_RE.match(self._code_text(), definition.name_end_byte) is not None

    def _is_call_kind(self, definition: DefinitionInfo, is_call: bool) -> bool:
        """Return whether a member is of the kind a call (or a name not called) names.

        A call names a member of member_function_types, or a class
        (CLASS_DEFINITION_TYPE_SET) for a language with class_call; a name not called
        names a member of any other type.
        """
        is_function = definition.type in self._member_function_type_set
        if not is_call:
            return not is_function
        return is_function or (self._is_class_call and definition.type in CLASS_DEFINITION_TYPE_SET)

    def member_definition(
        self, usage_name: str, line: int, start_byte: int | None, is_member: bool,
        is_subclass_lookup: bool = True, is_call: bool | None = None,
        call_end_byte: int | None = None,
    ) -> DefinitionInfo | None:
        """Return the member a name names in the classes around a usage.

        class A: def run ...
        class B: def run ...; def go(self): self.run()   -> the run of B
        class C(A): def go(self): self.run()              -> the run of A

        Args:
            usage_name: Name of a usage that leads to the file itself.
            line: Line of the usage (1-based).
            start_byte: First byte of the usage, None when it is not known.
            is_member: Whether the name is written as a member (UsageInfo.is_member).
            is_subclass_lookup: False to leave out the classes made from a class
                (_subclass_key_list).
            is_call: None for a member of any type; else, for a language with
                member_function_types, True for a member a call names and False for a
                member a name not called names (_is_call_kind).
            call_end_byte: With is_call True, the end byte of the called name; a member
                of member_function_types declared without parameters is left out when
                the call passes arguments (_ARGUMENT_CALL_RE).

        Returns:
            The definition the name leads to from the definition named like its first
            part that is a member of the innermost class around the usage
            (_around_owner_list); else of another definition of that class (Rust:
            another impl block of the type; C++: another specialization of the
            template), of a class of the file that class is made from
            (_base_key_list), or of a class of the file made from it
            (_subclass_key_list). The classes around that class are tried after it.
            The parts after the first are followed through the definitions inside
            that member (member_path_definition). A member named like its class (a
            constructor) is no such member.
            When no class around the usage leads to the member: _NO_MEMBER for a name
            written as a member inside a class, unless the language is one with
            member_by_name and a definition of that name is no member of a class;
            None in every other case (the name is looked up among all definitions of
            the file).
        """
        if self._member_dict is None:
            self._build()
        part_list = symbol_part_list(usage_name)
        name = part_list[0]
        member_list = self._member_dict.get(name)
        if not member_list:
            return None
        is_kind_check = is_call is not None and bool(self._member_function_type_set)
        is_argument_call = bool(
            is_kind_check and is_call and call_end_byte is not None
            and _ARGUMENT_CALL_RE.match(self._code_text(), call_end_byte) is not None
        )

        def is_match(candidate: DefinitionInfo) -> bool:
            """Return whether a member is of the kind the usage names."""
            return not is_kind_check or (
                self._is_call_kind(candidate, bool(is_call))
                and not (
                    is_argument_call and candidate.type in self._member_function_type_set
                    and self._is_parameterless(candidate)
                )
            )

        if is_kind_check:
            kind_key_tuple = (name, bool(is_call), is_argument_call)
            if kind_key_tuple not in self._kind_match_dict:
                self._kind_match_dict[kind_key_tuple] = any(
                    is_match(member) for _, _, member in member_list
                )
            if not self._kind_match_dict[kind_key_tuple]:
                return None
        around_list = self._around_owner_list(line, start_byte)
        for around in around_list:
            candidate = next(
                (
                    candidate for candidate in self._owner_member_dict.get((name, id(around)), ())
                    if is_match(candidate)
                ),
                None,
            )
            around_key = _class_key(around.name)
            if candidate is None:
                candidate = self._relation_member(
                    around_key, name, is_match, self._base_key_list, set(),
                )
            if candidate is None and is_subclass_lookup:
                candidate = self._relation_member(
                    around_key, name, is_match, self._subclass_key_list, set(),
                )
            if candidate is not None:
                return member_path_definition(self._definition_list, candidate, part_list[1:])
        if is_member and around_list and not (
            self._is_member_by_name and part_list[0] in self._plain_name_set
        ):
            return _NO_MEMBER
        return None


class _ExtensionNameSet(Container[str]):
    """The names of a file that name a definition declared for values of another type (_extension_name_set)."""

    def __init__(
        self, own_receiver_name_set: set[str], symbol_dict: Mapping[str, SymbolBinding],
        binder: ImportBinder,
    ) -> None:
        """Keep the names the file defines with a receiver and the names it sees.

        Args:
            own_receiver_name_set: The names the file defines itself with a receiver.
            symbol_dict: The names of the file that come from other project files.
            binder: The ImportBinder of the project.
        """
        self._own_receiver_name_set = own_receiver_name_set
        self._symbol_dict = symbol_dict
        self._binder = binder

    def __contains__(self, name: object) -> bool:
        """Return whether the name is such a name."""
        if name in self._own_receiver_name_set:
            is_receiver = True
        else:
            binding = self._symbol_dict.get(name)
            is_receiver = (
                binding is not None and binding.name is not None
                and binding.name in self._binder.receiver_name_set(binding.file_rel)
            )
        return is_receiver and name not in self._binder.package_member_name_set()


def _extension_name_set(
    file_ext: str,
    definition_list: list[DefinitionInfo],
    own_name_set: set[str],
    symbol_dict: Mapping[str, SymbolBinding],
    binder: ImportBinder,
) -> Container[str]:
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
        ImportBinder.package_member_name_set, looked up by name (_ExtensionNameSet).
        An empty set for a language without extension_call.
    """
    if not (EXT_TO_USAGE_NODE_TYPE_DICT.get(file_ext) or {}).get("extension_call"):
        return set()
    return _ExtensionNameSet(
        {
            definition.name for definition in definition_list
            if definition.has_receiver and definition.name in own_name_set
        },
        symbol_dict, binder,
    )


def _limit_module_scope(
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
    symbol_dict: Mapping[str, SymbolBinding],
) -> str | None:
    """Tell whether the module around a usage hides the name the usage starts from.

    Rust: inside mod name { ... } without use super::*, a top-level name of the file
    and a name a use declaration of the file binds are not visible; a name defined
    inside the module is, and so is a macro_rules! macro of the file. A path from a
    module (crate::, self::, super::), a path that is bound as a whole and a name
    that is neither (a member of a type) are not judged.

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
        _INVISIBLE when it is a name of outside that module, None when the name is not
        judged or no module closes the view.
    """
    first_part = symbol_part_list(usage.name)[0]
    if first_part in MODULE_PATH_ROOT_SET or len(symbol_part_list(root_name)) != 1:
        return None
    limit_scope = _limit_module_scope(module_scope_list, usage.line)
    if limit_scope is None:
        return None
    if any(
        definition.name == first_part
        and limit_scope[0] <= definition.start_line <= limit_scope[1]
        for definition in definition_list
    ):
        return _INSIDE
    if first_part in top_level_name_set or root_name in symbol_dict:
        return _INVISIBLE
    return None


def clear_import_reference_cache() -> None:
    """Forget the resolved references of every project."""
    _import_target_cache_dict.clear()


def _usage_list(
    root_node: Node,
    file_ext: str,
    symbol_dict: Mapping[str, SymbolBinding],
    own_name_set: set[str],
    scope_binding_list: list[ScopeBinding],
    import_line_dict: dict[str, set[int]],
    member_name_set: set[str],
    own_type_name_set: set[str],
    is_class_name: Callable[[str], bool],
    is_reference_name: Callable[[str], bool] | None,
    extension_name_set: Container[str],
    member_only_name_set: set[str],
    definition_name_range_set: set[tuple[int, int]],
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
    variable declared outside every function that the file defines itself (a field,
    a variable of the file) is a usage under its own name as well. A variable that
    counts for a usage comes before a longer tracked name that starts with the name
    of the variable and "." (the package name app.timeout beside a variable app).

    Args:
        root_node: The AST root node of the file.
        file_ext: Extension whose language settings the file is analyzed with.
        symbol_dict: The names of the file that come from other project files.
        own_name_set: The names the file defines itself (_own_name_set).
        scope_binding_list: The names bound for a range of lines of the file.
        import_line_dict: Name an import statement of the file binds -> lines of those
            statements.
        member_name_set: The names of ImportBinder.member_scope_list of the file; a
            usage after self / this, and by itself for a language with member_by_name.
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
        definition_name_range_set: Byte ranges of the names the definitions of the file
            are written with, which are no usages (_definition_name_range_set).

    Returns:
        The usages in line order, without duplicates.
    """
    usage_node_types = EXT_TO_USAGE_NODE_TYPE_DICT.get(file_ext) or {}
    type_name_set = _AnyNameSet(symbol_dict, own_type_name_set)

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
        (own_name_set - member_only_name_set)
        | (member_name_set if usage_node_types.get("member_by_name") else set())
        | {scope_binding.name for scope_binding in scope_binding_list}
    )
    track_name_set = _AnyNameSet(symbol_dict, alias_name_set, plain_name_set)

    def counting_alias(usage: UsageInfo, name: str) -> TypedAlias | None:
        """Return the alias a variable name a usage starts from counts under where it is written."""
        if not usage.is_member:
            return typed_alias(alias_dict, name, usage.line)
        # A member is no variable of a function: only a field of its name counts
        return next(
            (
                field_alias for field_alias in alias_dict.get(name, ())
                if field_alias.is_file_level
                and field_alias.start_line <= usage.line <= field_alias.end_line
            ),
            None,
        )

    usage_list: list[UsageInfo] = []
    for usage in extract_usages(
        root_node, track_name_set, usage_node_types or None,
        member_names=own_name_set | member_name_set, alias_list=alias_list,
        import_line_dict=import_line_dict, is_reference_name=is_reference_name,
        extension_names=extension_name_set,
        definition_name_range_set=definition_name_range_set,
    ):
        root_name = usage_root_name(usage.name, track_name_set)
        # A variable comes before a longer name that starts with its name and "."
        # (Java / Kotlin: the variable app before the package name app.timeout)
        first_part = symbol_part_list(usage.name)[0]
        if (
            root_name != first_part and first_part in alias_name_set
            and usage.name.startswith(first_part + _MEMBER_SEPARATOR)
            and counting_alias(usage, first_part) is not None
        ):
            root_name = first_part
        if root_name in alias_name_set:
            alias = counting_alias(usage, root_name)
            if alias is not None and alias.type_name is not None:
                if alias.is_file_level and root_name in own_name_set:
                    usage_list.append(UsageInfo(
                        usage.name, usage.line, usage.is_member, usage.start_byte, usage.end_byte,
                        usage.is_call,
                    ))
                usage.name = alias.type_name + usage.name[len(root_name):]
                usage.alias_name = root_name
            elif (
                not usage.is_member and root_name not in plain_name_set
                and root_name not in symbol_dict
            ):
                continue
        usage_list.append(usage)
    return deduplicate_usage_list(usage_list)


def _definition_name_range_set(
    definition_list: list[DefinitionInfo],
) -> set[tuple[int, int]]:
    """Return the byte ranges of the names the definitions of a file define.

    Args:
        definition_list: The definitions of the file.

    Returns:
        (name_start_byte, name_end_byte) of each definition that has them, but of
        the ones in ATTACHED_DEFINITION_TYPE_SET (named after a definition written
        elsewhere: Rust impl Shape) and TRANSPARENT_DEFINITION_TYPE_SET (whose name is
        no name of the file: a C++ namespace).
    """
    return {
        (definition.name_start_byte, definition.name_end_byte)
        for definition in definition_list
        if definition.name_start_byte is not None
        and definition.name_end_byte is not None
        and definition.type not in ATTACHED_DEFINITION_TYPE_SET
        and definition.type not in TRANSPARENT_DEFINITION_TYPE_SET
    }


def _class_name_function(
    own_type_name_set: set[str],
    symbol_dict: Mapping[str, SymbolBinding],
    binder: ImportBinder,
    project_dir: str,
) -> Callable[[str], bool]:
    """Return the function that tells whether a name names a type an object is made of.

    Args:
        own_type_name_set: The names of the types the file defines itself.
        symbol_dict: The names of the file that come from other project files.
        binder: The ImportBinder of the project.
        project_dir: Absolute path to the project root.

    Returns:
        The function: True for a name of own_type_name_set, and for a name starting
        with a name of symbol_dict that leads to a definition in
        CLASS_DEFINITION_TYPE_SET named like the last part of the name it leads to.
    """
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

    return is_class_name


def _usage_target_list(
    usage: UsageInfo,
    file_rel: str,
    binder: ImportBinder,
    symbol_dict: Mapping[str, SymbolBinding],
    own_name_set: set[str],
    scope_binding_dict: dict[str, list[ScopeBinding]],
    member_scope_index: _MemberScopeIndex,
    module_scope_list: list[tuple[int, int, bool]],
    definition_list: list[DefinitionInfo],
    top_level_name_set: set[str],
    member_lookup: _MemberLookup,
    is_member_by_name: bool,
) -> list[ImportReferenceTarget]:
    """Return the targets one usage of a file leads to (import_reference_target_list).

    The first of these that applies decides: a name bound for a range of lines around
    the usage (no target when it is bound to nothing of the project); a member of
    ImportBinder.member_scope_list written as a member, or by itself for a language
    with member_by_name; a name of the file outside the inline module around the
    usage (no target) or defined inside it; a name of symbol_dict, unless it is a
    name the file defines itself written as a member, or the language has
    member_by_name, the usage is written without a scope before it
    (_MemberLookup.is_written_alone) and a class around the usage, or a class of the
    file it is made from, has a member of that name (with member_function_types, of
    the kind the usage names (_MemberLookup._is_call_kind), not one declared without
    parameters for a call that passes arguments); a name the file defines itself,
    looked up among the members of the classes around the usage first (no target
    when _MemberLookup.member_definition gives _NO_MEMBER).

    Args:
        usage: The usage.
        file_rel: Relative path of the file.
        binder: The ImportBinder of the project.
        symbol_dict: The names of the file that come from other project files.
        own_name_set: The names the file defines itself (_own_name_set).
        scope_binding_dict: The result of _scope_binding_dict.
        member_scope_index: The _MemberScopeIndex of the file.
        module_scope_list: The result of ImportBinder.module_scope_list.
        definition_list: The definitions of the file.
        top_level_name_set: The top-level definition names of the file that an inline
            module does not see.
        member_lookup: The _MemberLookup of the file.
        is_member_by_name: The member_by_name of the language of the file.

    Returns:
        The targets of the usage; empty when it leads to no definition.
    """
    scope_binding = _scope_binding(scope_binding_dict, usage.name, usage.line)
    member_binding = (
        member_scope_index.binding(usage) if usage.is_member or is_member_by_name else None
    )
    root_name = usage_root_name(usage.name, symbol_dict)
    visibility = _module_visibility(
        module_scope_list, usage, root_name, definition_list, top_level_name_set, symbol_dict,
    ) if module_scope_list else None
    if scope_binding is not None:
        if scope_binding.binding is None:
            return []
        return _import_target_list(
            usage.name, scope_binding.name, usage.line, scope_binding.binding, binder,
        )
    if member_binding is not None:
        return _import_target_list(
            usage.name, symbol_part_list(usage.name)[0], usage.line, member_binding, binder,
        )
    if visibility == _INVISIBLE:
        return []
    if visibility == _INSIDE:
        return [ImportReferenceTarget(usage.name, usage.line, file_rel, usage.name)]
    is_own = usage_root_name(usage.name, own_name_set) in own_name_set
    if root_name in symbol_dict and not (usage.is_member and is_own):
        # A member of the classes around the usage comes before an imported name
        member = None
        if is_own and is_member_by_name and member_lookup.is_written_alone(usage):
            is_call = usage.is_call and len(symbol_part_list(usage.name)) == 1
            member = member_lookup.member_definition(
                usage.name, usage.line, usage.start_byte, False, is_subclass_lookup=False,
                is_call=is_call, call_end_byte=usage.end_byte if is_call else None,
            )
        if member is None or member is _NO_MEMBER:
            return _import_target_list(
                usage.name, root_name, usage.line, symbol_dict[root_name], binder,
            )
        return [_own_target(usage, file_rel, member)]
    if not is_own:
        return []
    # A member is looked up in the classes around the usage first
    member = member_lookup.member_definition(
        usage.name, usage.line, usage.start_byte, usage.is_member,
    ) if usage.is_member or is_member_by_name else None
    if member is _NO_MEMBER:
        return []
    return [_own_target(usage, file_rel, member)]


def _own_target(
    usage: UsageInfo, file_rel: str, member: DefinitionInfo | None,
) -> ImportReferenceTarget:
    """Return the target of a usage that leads to the file itself.

    Args:
        usage: The usage.
        file_rel: Relative path of the file.
        member: The member the classes around the usage lead to
            (_MemberLookup.member_definition); None to look the definition up by its
            name alone.
    """
    if member is None:
        return ImportReferenceTarget(usage.name, usage.line, file_rel, usage.name)
    return ImportReferenceTarget(
        usage.name, usage.line, file_rel, usage.name, member.start_line, member.start_byte,
    )


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
    of the class or of a base class written by its name alone or after this leads to
    that member, and so does a member of a Rust type written after self inside an
    impl block of the type in another file (ImportBinder.member_scope_list), before a
    name of the import statements.

    Processing flow:
    1. Return the targets of _import_target_cache_dict when they were resolved with
       the same project file set
    2. Map the names of the import statements to the files they come from
    3. Collect the names the file defines itself
    4. Collect the usages of those names (_usage_list)
    5. Collect the top-level names an inline module does not see
    6. Add the usages of blank macros and of the members read where their module is
       required
    7. Turn each usage into its targets (_usage_target_list)

    Args:
        file_rel: Relative path of the file.
        project_file_set: Relative paths of the project files that have a language.
        project_dir: Absolute path to the project root.

    Returns:
        The targets of each usage, in the line order of the usages: none for a usage
        that leads to no definition, and one more for each other file that defines what
        a declaration names (ImportBinder.implement_file_list). A target whose file_rel
        is the file itself is a reference to a definition of the file.
        The targets are kept in _import_target_cache_dict until the project file set
        changes or the cache is cleared.
    """
    # == Step 1: Cache ========================================================
    cache_key = os.path.abspath(os.path.join(project_dir, file_rel))
    target_list = project_cache_value(_import_target_cache_dict, cache_key, project_file_set)
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
    scope_binding_dict = _scope_binding_dict(scope_binding_list)

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

    # == Step 4: Usages =======================================================
    member_scope_index = _MemberScopeIndex(binder.member_scope_list(file_rel))
    binding_dict = ChainMap(symbol_dict, {
        scope_binding.name: scope_binding.binding
        for scope_binding in scope_binding_list if scope_binding.binding is not None
    })
    usage_list = _usage_list(
        root_node, file_ext, symbol_dict, own_name_set, scope_binding_list, import_line_dict,
        member_scope_index.name_set, own_type_name_set,
        _class_name_function(own_type_name_set, symbol_dict, binder, project_dir),
        _pattern_reference_function(file_ext, definition_list, binding_dict, project_dir),
        _extension_name_set(file_ext, definition_list, own_name_set, symbol_dict, binder),
        _member_only_name_set(file_ext, definition_list, own_name_set),
        _definition_name_range_set(definition_list),
    )

    # == Step 5: Names an inline module does not see ==========================
    module_scope_list = binder.module_scope_list(file_rel)
    usage_node_types = EXT_TO_USAGE_NODE_TYPE_DICT.get(file_ext) or {}
    # The top-level names no definition of a module_open_types type carries
    open_type_set = usage_node_types.get("module_open_types", set())
    top_level_name_set = (
        set(binder.top_level_name_list(file_rel))
        - {definition.name for definition in definition_list if definition.type in open_type_set}
    ) if module_scope_list else set()

    # == Step 6: Usages of blank macros and required members ==================
    # A macro name read as spaces is a usage of the macro, and a member read where its
    # module is required a usage of the member
    extra_usage_list = [
        UsageInfo(name=name, line=line)
        for name, line in [*blank_macro_cache.get(file_abs, ()), *binder.use_list(file_rel)]
    ]
    if extra_usage_list:
        usage_list = deduplicate_usage_list(usage_list + extra_usage_list)

    # == Step 7: Targets ======================================================
    is_member_by_name = bool(usage_node_types.get("member_by_name"))
    member_lookup = _MemberLookup(
        definition_list, file_content(file_abs), root_node, is_member_by_name,
        bool(usage_node_types.get("class_head_argument_skip")),
        usage_node_types.get("member_function_types", set()),
        bool(usage_node_types.get("class_call")),
    )
    target_list = []
    for usage in usage_list:
        target_list.extend(_usage_target_list(
            usage, file_rel, binder, symbol_dict, own_name_set, scope_binding_dict,
            member_scope_index, module_scope_list, definition_list, top_level_name_set,
            member_lookup, is_member_by_name,
        ))

    _import_target_cache_dict[cache_key] = (project_file_set, target_list)
    return target_list
