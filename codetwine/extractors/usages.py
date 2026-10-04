import re
from collections.abc import Callable, Container, Iterable
from dataclasses import dataclass, field
from tree_sitter import Node
from codetwine.extractors.cobol_source import CobolSource
from codetwine.extractors.definitions import (
    declarator_name_node,
    is_macro_call_declarator,
    pattern_name_list,
)
from codetwine.extractors.rust_path import macro_path_list, path_segment_list

# Separators between the parts of a usage name: "." (attribute access) and "::" (Rust / C++ path)
_SYMBOL_SEPARATOR_RE = re.compile(r"\.|::")

# Type reference / namespace reference node types (skip_parent_types check not needed)
_TYPE_REFERENCE_NODE_TYPE_SET = {"type_identifier", "namespace_identifier"}

# Node types of a name written with its scopes (C++: geo::Shape::count, Kotlin: a.b.c)
_QUALIFIED_NODE_TYPE_SET = {"qualified_identifier"}

# Node types of a name written with template arguments (C++: Box<int>, twice<int>, obj.get<int>)
_TEMPLATE_NODE_TYPE_SET = {"template_type", "template_function", "template_method"}

# Node types of the declarators of a typed variable declaration (Java, C / C++)
_DECLARATOR_TYPE_SET = {
    "variable_declarator", "init_declarator", "pointer_declarator", "array_declarator",
    "reference_declarator",
}

# Node type of a declarator read as a function, and the parent node types of a
# declaration under which it declares a variable made with arguments (C++: a
# statement of a function, Engine e(1);)
_CALL_DECLARATOR_TYPE = "function_declarator"
_STATEMENT_PARENT_TYPE_SET = {"compound_statement", "case_statement"}

# Node types that wrap the type a declaration is written with (Kotlin, Python, TS)
_TYPE_WRAP_NODE_TYPE_SET = {"user_type", "type", "type_annotation"}

# Node type of a type written around another type -> field holding that type
# (Rust: &Circle, Wrapper<T>, dyn Shape, impl Shape)
_TYPE_HOLDER_FIELD_DICT = {
    "reference_type": "type",
    "generic_type": "type",
    "dynamic_type": "trait",
    "abstract_type": "trait",
}

# The name a text starts with
_LEAD_NAME_RE = re.compile(r"[^\W\d]\w*")

# A name written with dots only (core.Engine)
_DOTTED_NAME_RE = re.compile(r"[^\W\d]\w*(?:\.[^\W\d]\w*)*")

# The text of a type written as a string: names, dots, brackets, commas and "|"; and
# two words with only white space between them, which no type is written with
_TYPE_TEXT_RE = re.compile(r"[\w\s.,|\[\]]+")
_WORD_GAP_RE = re.compile(r"\w\s+\w")

# Node types of a type written with the name it is a member of (Python: core.Engine,
# Java: Circle.Builder, TS: models.Shape)
_DOTTED_TYPE_NODE_TYPE_SET = {"attribute", "scoped_type_identifier", "nested_type_identifier"}

# Node types of the name a chain of names starts from
_CHAIN_BASE_NODE_TYPE_SET = {"identifier", "type_identifier"}

_EMPTY_SET: set[str] = set()


@dataclass
class UsageInfo:
    """Data class holding information about a single symbol usage location."""

    name: str    # The symbol name being used
    line: int    # Line number of the usage location (1-based)
    # Whether the name is one of a member: written after self / this, or by itself
    # where the members of the class around it are named that way
    is_member: bool = False


def symbol_part_list(name: str) -> list[str]:
    """Split a usage name into its parts.

    Examples:
        "helper.process"      -> ["helper", "process"]
        "config::Settings"    -> ["config", "Settings"]

    Args:
        name: A usage name (UsageInfo.name).

    Returns:
        The parts separated by "." or "::".
    """
    return _SYMBOL_SEPARATOR_RE.split(name)


def _track_root(name: str, tracked_name_set: Container[str]) -> str | None:
    """Return the tracked name a usage name starts from, None when it starts from none.

    Examples (tracked names {"helper", "pkg.core"}):
        "helper.process"   -> "helper"
        "pkg.core.COUNT"   -> "pkg.core"
        "pkg.other"        -> None

    Args:
        name: A name written in the file, its parts joined with "." or "::".
        tracked_name_set: The names whose usages are tracked.

    Returns:
        The longest leading part of name (cut at "." or "::") that is tracked.
    """
    if name in tracked_name_set:
        return name
    for separator_match in reversed(list(_SYMBOL_SEPARATOR_RE.finditer(name))):
        prefix = name[:separator_match.start()]
        if prefix in tracked_name_set:
            return prefix
    return None


def usage_root_name(name: str, tracked_name_set: Container[str]) -> str:
    """Return the tracked name a usage name starts from.

    Examples (tracked names {"helper", "super::ENCODINGS"}):
        "helper.process"               -> "helper"
        "super::ENCODINGS"             -> "super::ENCODINGS"
        "super::ENCODINGS.trim_end"    -> "super::ENCODINGS"

    Args:
        name: A usage name (UsageInfo.name).
        tracked_name_set: The names whose usages were tracked (a set or a dict keyed by name).

    Returns:
        The longest leading part of name (cut at "." or "::") that is tracked,
        or the first part when none is.
    """
    return _track_root(name, tracked_name_set) or symbol_part_list(name)[0]


@dataclass
class _UsageSetting:
    """The node type settings of one language and the names tracked in one file."""

    name_set: set[str]                 # Names whose usages are tracked
    member_name_set: set[str]          # Names tracked when written after self / this
    # Variable name -> the lines it is tracked for (typed_alias_dict)
    alias_dict: dict[str, list["TypedAlias"]]
    usage_node_types: dict             # The EXT_TO_USAGE_NODE_TYPE_DICT entry of the language
    # Name an import statement binds -> lines of those statements
    import_line_dict: dict[str, set[int]] = field(default_factory=dict)
    # Node id of a scope -> names bound inside it, filled on first use
    local_name_dict: dict[int, set[str]] = field(default_factory=dict)
    # Returns whether a name written in a pattern refers to a definition instead of
    # binding the name (Rust: a constant, a variant); None when no name does
    is_reference_name: Callable[[str], bool] | None = None
    # Names tracked when they are written after any value (Kotlin: extension functions)
    extension_name_set: set[str] = field(default_factory=set)

    def type_set(self, key: str) -> set[str]:
        """Return a node type set of the settings, empty when the language has none."""
        return self.usage_node_types.get(key, _EMPTY_SET)

    def scope_type_set(self) -> set[str]:
        """Return the node types that open a scope: the functions and the blocks (scope_type_set)."""
        return scope_type_set(self.usage_node_types)

    def type_reference_skip_set(self) -> set[str]:
        """Return the parent node types under which a type reference or a path is no usage.

        Returns:
            skip_parent_types_for_type_ref, or skip_parent_types for a language without it.
        """
        return self.usage_node_types.get(
            "skip_parent_types_for_type_ref", self.usage_node_types["skip_parent_types"],
        )


# A function that returns the usages one node gives
_UsageHandler = Callable[[Node, _UsageSetting], list[UsageInfo]]


# The node types that open a scope, by the id of the language settings they are read from
_scope_type_set_cache: dict[int, tuple[dict, set[str]]] = {}


def scope_type_set(usage_node_types: dict) -> set[str]:
    """Return the node types that open a scope for the names bound inside them.

    Args:
        usage_node_types: The EXT_TO_USAGE_NODE_TYPE_DICT entry of a language.

    Returns:
        scope_types (functions) and block_scope_types (blocks) together. Built once
        per settings dict.
    """
    cache_entry = _scope_type_set_cache.get(id(usage_node_types))
    if cache_entry is None or cache_entry[0] is not usage_node_types:
        cache_entry = (
            usage_node_types,
            set(usage_node_types.get("scope_types", ()))
            | set(usage_node_types.get("block_scope_types", ())),
        )
        _scope_type_set_cache[id(usage_node_types)] = cache_entry
    return cache_entry[1]


def _is_function_binding(node: Node, setting: _UsageSetting) -> bool:
    """Return whether a binding node binds its names for the whole function it is written in.

    JS/TS: a function declaration, and a declaration written with "var" (var x = 1,
    for (var k in o)), count for the function also when they are written in a block.

    Args:
        node: A node that binds names (local_binding_dict, scope_binding_dict).
        setting: The usage settings of the file.

    Returns:
        True for a node in function_binding_types, and for a node that has a child of
        function_binding_token_types or whose parent has one.
    """
    if node.type in setting.type_set("function_binding_types"):
        return True
    token_type_set = setting.type_set("function_binding_token_types")
    if not token_type_set:
        return False
    holder_list = [node, node.parent] if node.parent is not None else [node]
    return any(
        child.type in token_type_set for holder in holder_list for child in holder.children
    )


def _pattern_name_list(node: Node, setting: _UsageSetting) -> list[str]:
    """Return the names a binding pattern binds (pattern_name_list with the settings of the file)."""
    return pattern_name_list(
        node, setting.type_set("pattern_types"),
        setting.usage_node_types.get("pattern_field_dict", {}),
    )


def _binding_name_list(node: Node, field_name: str, setting: _UsageSetting) -> list[str]:
    """Return the names a binding node binds as local names.

    Args:
        node: A node whose type is a key of local_binding_dict or scope_binding_dict.
        field_name: The field of the node that holds the pattern; "" when the named
            children of the node are the patterns.
        setting: The usage settings of the file.

    Returns:
        The bound names, without the names an import statement written on the lines
        of their pattern binds (import_line_dict) and the names a pattern refers to
        (is_reference_name).
    """
    pattern_node_list = (
        node.children_by_field_name(field_name) if field_name else node.named_children
    )
    name_list: list[str] = []
    for pattern_node in pattern_node_list:
        start_line, end_line = pattern_node.start_point[0] + 1, pattern_node.end_point[0] + 1
        name_list.extend(
            name for name in _pattern_name_list(pattern_node, setting)
            if not any(
                start_line <= line <= end_line
                for line in setting.import_line_dict.get(name, ())
            )
            and not (setting.is_reference_name is not None and setting.is_reference_name(name))
        )
    return name_list


def _local_name_set(scope_node: Node, setting: _UsageSetting) -> set[str]:
    """Return the names bound inside a scope: parameters, local variables, inner functions.

    The nodes of the scope are walked without entering the scopes and the opaque
    nodes (class bodies) inside it; a node whose type is a key of local_binding_dict
    binds the names of its pattern. The names of unbind_types nodes (Python global /
    nonlocal) are taken out.

    A name bound in a block (block_scope_types) counts for that block. A binding that
    counts for the whole function (_is_function_binding) is left out of the block it
    is written in and counted for the function around it, whose blocks are walked for
    such bindings.

    Args:
        scope_node: A node whose type is in scope_type_set().
        setting: The usage settings of the file.

    Returns:
        The bound names. Computed once per scope node.
    """
    local_name_set = setting.local_name_dict.get(scope_node.id)
    if local_name_set is not None:
        return local_name_set

    scope_type_set = setting.scope_type_set()
    block_type_set = setting.type_set("block_scope_types")
    opaque_type_set = setting.type_set("opaque_types")
    unbind_type_set = setting.type_set("unbind_types")
    local_binding_dict = setting.usage_node_types.get("local_binding_dict", {})
    scope_binding_dict = setting.usage_node_types.get("scope_binding_dict", {})
    is_block = scope_node.type in block_type_set
    # Whether the blocks of a function are walked for the bindings that count for it
    is_block_walk = not is_block and bool(
        setting.type_set("function_binding_types")
        or setting.type_set("function_binding_token_types")
    )

    local_name_set = set()
    global_name_set: set[str] = set()
    scope_field = scope_binding_dict.get(scope_node.type)
    if scope_field is not None and not (is_block and _is_function_binding(scope_node, setting)):
        local_name_set.update(_binding_name_list(scope_node, scope_field, setting))

    # (node, whether it is written in a block inside the scope)
    node_stack = [(child, False) for child in scope_node.children]
    while node_stack:
        node, is_in_block = node_stack.pop()
        if node.type in unbind_type_set:
            global_name_set.update(
                child.text.decode("utf-8") for child in node.named_children
            )
            continue
        field_name = local_binding_dict.get(node.type)
        if field_name is not None and (
            _is_function_binding(node, setting) if is_in_block
            else not (is_block and _is_function_binding(node, setting))
        ):
            local_name_set.update(_binding_name_list(node, field_name, setting))
        if node.type in scope_type_set or node.type in opaque_type_set:
            if is_block_walk and node.type in block_type_set:
                block_field = scope_binding_dict.get(node.type)
                if block_field is not None and _is_function_binding(node, setting):
                    local_name_set.update(_binding_name_list(node, block_field, setting))
                node_stack.extend((child, True) for child in node.children)
            continue
        node_stack.extend((child, is_in_block) for child in node.children)

    local_name_set -= global_name_set
    setting.local_name_dict[scope_node.id] = local_name_set
    return local_name_set


def _has_ancestor(node: Node, node_type_set: set[str]) -> bool:
    """Return whether a node is written inside a node of one of the given types."""
    current = node.parent
    while current is not None:
        if current.type in node_type_set:
            return True
        current = current.parent
    return False


def _has_function_scope(node: Node, setting: _UsageSetting) -> bool:
    """Return whether a node is written inside a function (a scope_types node)."""
    return _has_ancestor(node, setting.type_set("scope_types"))


def _is_local_name(node: Node, name: str, setting: _UsageSetting) -> bool:
    """Return whether a name written at a node is bound by a scope around the node.

    Args:
        node: The node the name is written at.
        name: The name.
        setting: The usage settings of the file.

    Returns:
        True when a scope node (scope_type_set) among the ancestors of node binds the name
        (_local_name_set) and the name is no typed variable declared inside that scope
        for the line of the node (alias_dict). A scope whose type is a key of scope_body_dict binds
        its names only for the nodes inside that field of it (Python: a default value
        or an annotation of a function is read outside the function). A block written
        outside every function does not bind a name of member_name_set: the file
        lists what such a block declares as a definition.
    """
    scope_type_set = setting.scope_type_set()
    if not scope_type_set:
        return False
    alias = typed_alias(setting.alias_dict, name, node.start_point[0] + 1)
    scope_body_dict = setting.usage_node_types.get("scope_body_dict", {})
    block_type_set = setting.type_set("block_scope_types")
    child = node
    current = node.parent
    local_name_dict = setting.local_name_dict
    while current is not None:
        if current.type in scope_type_set and name in (
            local_name_dict.get(current.id) or _local_name_set(current, setting)
        ):
            if (
                current.type in block_type_set and name in setting.member_name_set
                and not _has_function_scope(current, setting)
            ):
                # A block written outside every function: the name is one the file defines
                return False
            body_field = scope_body_dict.get(current.type)
            if body_field is None or current.child_by_field_name(body_field) == child:
                is_alias_inside = (
                    alias is not None and alias.type_name is not None
                    and current.start_point[0] + 1 <= alias.start_line
                    and alias.end_line <= current.end_point[0] + 1
                )
                if not is_alias_inside:
                    return True
        child = current
        current = current.parent
    return False


def _is_class_member_name(node: Node, name: str, setting: _UsageSetting) -> bool:
    """Return whether a name written by itself names a member of the class it is written in.

    Python: a name written in the body of a class, outside its functions, names what
    the body has defined (LIMIT = 3 followed by DEFAULT = LIMIT). Rust: a name written
    after "self." in the arguments of a macro (assert!(self.len() > 0)).

    Args:
        node: The node the name is written at.
        name: The name.
        setting: The usage settings of the file.

    Returns:
        True when the name is in member_name_set and either the innermost scope or
        opaque_types node around the node is an opaque_types node that binds the name
        itself (_local_name_set), or the node is a child of a macro_argument_types
        node written after self and ".".
    """
    if name not in setting.member_name_set:
        return False
    parent = node.parent
    if parent is not None and parent.type in setting.type_set("macro_argument_types"):
        dot_node = node.prev_sibling
        self_node = dot_node.prev_sibling if dot_node is not None else None
        return (
            dot_node is not None and dot_node.type == "." and self_node is not None
            and (
                self_node.type in setting.type_set("self_types")
                or self_node.text.decode("utf-8") in setting.type_set("self_names")
            )
        )
    scope_type_set = setting.scope_type_set()
    opaque_type_set = setting.type_set("opaque_types")
    while parent is not None:
        if parent.type in opaque_type_set:
            return name in _local_name_set(parent, setting)
        if parent.type in scope_type_set:
            return False
        parent = parent.parent
    return False


def _chain_part(node: Node, attribute_types: set[str]) -> tuple[Node, list[str]] | None:
    """Split an attribute access into the node it starts from and the names after it.

    Examples:
        helper.process.run  -> (identifier helper, ["process", "run"])
        self.x.go           -> (self, ["x", "go"])
        head->value         -> (identifier head, ["value"])
        make().value        -> (call make(), ["value"])

    Args:
        node: An attribute access node.
        attribute_types: Node types of an attribute access.

    Returns:
        (first node that is no attribute access, names written after it), None when
        an access has no name part.
    """
    part_list: list[str] = []
    current = node
    while current.type in attribute_types:
        child_list = current.named_children
        if len(child_list) < 2:
            return None
        part_list.append(_template_name(child_list[-1]))
        current = child_list[0]
    part_list.reverse()
    return current, part_list


def _chain_usage(
    base_node: Node, part_list: list[str], line: int, setting: _UsageSetting,
    is_call_name: bool = False,
) -> UsageInfo | None:
    """Return the usage a chain of names gives.

    A chain that starts from a name gives "name.part..." when it starts from a tracked
    name no scope around it binds. A chain that starts from self / this (self_names,
    self_types) gives "part..." when its first part is in member_name_set.

    Args:
        base_node: The node the chain starts from.
        part_list: The names written after it (may be empty).
        line: Line of the usage (1-based).
        setting: The usage settings of the file.
        is_call_name: True when base_node is the name of a called function; with
            call_ignores_local such a name is a usage also where a scope binds it.

    Returns:
        UsageInfo, or None when the chain names nothing tracked.
    """
    base_text = base_node.text.decode("utf-8")
    is_self = (
        base_node.type in setting.type_set("self_types")
        or (base_node.type == "identifier" and base_text in setting.type_set("self_names"))
    )
    if is_self:
        if part_list and part_list[0] in setting.member_name_set:
            return UsageInfo(name=".".join(part_list), line=line, is_member=True)
        return None
    if base_node.type not in _CHAIN_BASE_NODE_TYPE_SET:
        return None
    name = ".".join([base_text, *part_list])
    if _track_root(name, setting.name_set) is None:
        if _is_class_member_name(base_node, base_text, setting):
            return UsageInfo(name=name, line=line, is_member=True)
        return None
    is_local_check = not (is_call_name and setting.usage_node_types.get("call_ignores_local"))
    if is_local_check and _is_local_name(base_node, base_text, setting):
        return None
    return UsageInfo(name=name, line=line)


def _qualified_segment_list(node: Node) -> list[str]:
    """Split a name written with "::" (C++) or "." (Kotlin) into its parts.

    Template arguments are dropped: geo::Box<int>::get -> ["geo", "Box", "get"].

    Args:
        node: A qualified_identifier node.

    Returns:
        The parts in the order they are written.
    """
    segment_list: list[str] = []
    current: Node | None = node
    while current is not None and current.type == "qualified_identifier":
        scope_node = current.child_by_field_name("scope")
        name_node = current.child_by_field_name("name")
        if name_node is None:
            # Kotlin: the parts are plain children
            segment_list.extend(child.text.decode("utf-8") for child in current.named_children)
            return segment_list
        if scope_node is not None:
            segment_list.append(_template_name(scope_node))
        current = name_node
    if current is not None:
        segment_list.append(_template_name(current))
    return segment_list


def _template_name(node: Node) -> str:
    """Return the name of a node without its template arguments (Box<int> -> Box)."""
    name_node = node.child_by_field_name("name") if node.type in _TEMPLATE_NODE_TYPE_SET else None
    return (name_node or node).text.decode("utf-8")


def _qualified_usage(node: Node, setting: _UsageSetting) -> UsageInfo | None:
    """Return the usage a qualified name gives.

    The name from the first tracked part on is the usage, so the namespaces written in
    front of it are dropped: geo::Shape::count with "Shape" tracked -> "Shape::count".
    A part with further parts after it that is tracked as a variable of a function
    (alias_dict) is no such part: a variable named like a namespace is not the
    namespace.

    Args:
        node: The outermost qualified_identifier node of the name.
        setting: The usage settings of the file.

    Returns:
        UsageInfo, or None when no part is tracked.
    """
    segment_list = _qualified_segment_list(node)
    for index, segment in enumerate(segment_list):
        if segment in setting.name_set and (
            index == len(segment_list) - 1
            or all(alias.is_file_level for alias in setting.alias_dict.get(segment, ()))
        ):
            return UsageInfo(name="::".join(segment_list[index:]), line=node.start_point[0] + 1)
    return None


def extract_usages(
    root_node: Node | CobolSource,
    imported_names: set[str],
    usage_node_types: dict | None = None,
    member_names: set[str] | None = None,
    alias_list: list["TypedAlias"] | None = None,
    import_line_dict: dict[str, set[int]] | None = None,
    is_reference_name: Callable[[str], bool] | None = None,
    extension_names: set[str] | None = None,
) -> list[UsageInfo]:
    """Extract usage locations of imported names from the AST and return them.

    Traverses the AST via depth-first search (DFS) and detects the following kinds:
    - Nodes in call_types (function calls)
    - Nodes in attribute_types (attribute access), named by the chain of names they
      write (helper.process.run); a chain written after self / this is named without it.
      An access whose outermost node is written in a node of attribute_skip_parent_types
      is not read
    - identifier nodes (simple variable references), nodes in identifier_types, and
      nodes of identifier_parent_dict under the parents it gives
    - type_identifier / namespace_identifier nodes (type / namespace references)
      Java: "User" in User user = new User() is a type_identifier
      C/C++: "Point" in struct Point p is a type_identifier
    - qualified_identifier nodes (C++: geo::Shape::count), named from their first
      tracked part on
    - Nodes in name_path_dict (Python: the class or constant a case pattern names,
      Kotlin: a type written with the type it is a member of)
    - Nodes in template_name_dict (Kotlin: "$name" in a string)
    - Nodes in annotation_string_dict (Python: the names of an annotation written as a string)
    - Nodes in path_types (Rust paths such as config::Settings::new), recorded
      with the whole path as the name; the paths in the arguments of a macro
      (macro_argument_types) are read the same way

    A name is not a usage where a scope around it (scope_types) binds it as a
    parameter or a local variable. A name an import statement binds inside a scope
    (import_line_dict) is no local variable of it.

    Duplicate and redundant entries are removed at the end by deduplicate_usage_list.

    For a CobolSource the places the file refers to the names are returned; names are
    compared without regard to upper and lower case, and usage_node_types is not read.

    Args:
        root_node: The AST root node covering the entire file, or the CobolSource of a COBOL file.
        imported_names: Set of names whose usage is to be tracked. A name may have several
                        parts ("pkg.core"); a usage starts from the longest tracked name.
        usage_node_types: Per-language node type settings dict, obtained from EXT_TO_USAGE_NODE_TYPE_DICT in config.py.
                          Returns an empty list when None (for languages with no usage tracking defined).
                          Required keys: "call_types", "attribute_types", "skip_parent_types"
                          Optional key: "attribute_skip_parent_types" (parent node types of an
                            attribute access that is not read)
                          Optional key: "package_path_types" (attribute node types whose chain
                            is named from its first tracked part on)
                          Optional key: "skip_parent_types_for_type_ref" (uses skip_parent_types if absent)
                          Optional key: "skip_last_child_types" (when the parent is this type,
                            only its last child is skipped)
                          Optional key: "skip_first_name_types" (when the parent is this type,
                            only its first identifier child is skipped)
                          Optional key: "skip_name_field_types" (when the parent is this type,
                            only the name-field child is skipped; the value side is detected as a usage)
                          Optional key: "identifier_parent_types" (when set, an identifier is a usage
                            only when its parent is one of these types)
                          Optional key: "identifier_types" (node types read like an identifier)
                          Optional key: "identifier_parent_dict" (node type -> parent node types
                            under which it is read like an identifier)
                          Optional key: "macro_declarator_types" (declarator node types whose
                            name is a usage when the declarator is a macro call,
                            see is_macro_call_declarator)
                          Optional keys: "annotation_string_dict", "annotation_literal_names"
                            (how the names of an annotation written as a string are read,
                            see _annotation_string_usage_list)
                          Optional key: "path_types" (node types of a path written with "::")
                          Optional key: "name_path_dict" (node type of a name whose parts are
                            its children -> {parent node type, "" for any: least number of
                            parts it is a usage with})
                          Optional key: "template_name_dict" (node type of a text of a string
                            template -> the text after which a name is written)
                          Optional key: "macro_argument_types" (node types of macro arguments,
                            whose paths are read from loose tokens)
                          Optional keys: "self_names" / "self_types" (identifier texts / node
                            types that stand for the object a member is written on)
                          Optional keys: "block_scope_types", "function_binding_types",
                            "function_binding_token_types" (the blocks that are scopes of
                            their own and the bindings that count for the whole function,
                            see _local_name_set)
                          Optional keys: "scope_types", "scope_body_dict", "scope_binding_dict",
                            "local_binding_dict", "pattern_types", "pattern_field_dict",
                            "opaque_types", "unbind_types"
                            (how the names bound inside a function are found, see _local_name_set)
        member_names: Names tracked when they are written after self / this. None for none.
        alias_list: Variables declared with a tracked type (extract_typed_aliases); such a
                    name stays a usage on the lines it is declared for, where a scope
                    binds it. None for none.
        import_line_dict: Name an import statement binds -> lines of those statements.
                    None for none.
        is_reference_name: Returns whether a name written in a pattern refers to a
                    definition instead of binding the name. None when no name does.
        extension_names: Names tracked when they are written after any value
                    (value.name, value.name(...)); such a usage is named by the name
                    alone. None for none.

    Returns:
        A list of UsageInfo (deduplicated).
    """
    if isinstance(root_node, CobolSource):
        return [
            UsageInfo(name=name, line=line)
            for name, line in root_node.usage_line_list(imported_names)
        ]
    if not usage_node_types:
        return []

    setting = _UsageSetting(
        name_set=imported_names,
        member_name_set=member_names or set(),
        alias_dict=typed_alias_dict(alias_list or []),
        usage_node_types=usage_node_types,
        import_line_dict=import_line_dict or {},
        is_reference_name=is_reference_name,
        extension_name_set=extension_names or set(),
    )

    handler_dict = _usage_handler_dict(setting)
    usage_list: list[UsageInfo] = []
    # DFS traversal of the AST using a stack
    node_stack = [root_node]
    while node_stack:
        node = node_stack.pop()
        handler = handler_dict.get(node.type)
        if handler is not None:
            usage_list.extend(handler(node, setting))
        # Add child nodes to the stack to continue traversal
        node_stack.extend(node.children)

    # Remove duplicate and redundant entries and return
    return deduplicate_usage_list(usage_list)


def _usage_handler_dict(setting: _UsageSetting) -> dict[str, _UsageHandler]:
    """Return the function that reads the usages of a node, by node type.

    Args:
        setting: The usage settings of the file.

    Returns:
        {node type: handler}. A node type of several kinds gets the handler of the
        first of them in this order: a call, an attribute access, a qualified name, a
        path, a name whose parts are its children, a text of a string template, a
        string of an annotation, the arguments of a macro, a type reference, an
        identifier.
    """
    usage_node_types = setting.usage_node_types
    identifier_type_set = (
        {"identifier"} | setting.type_set("identifier_types")
        | set(usage_node_types.get("identifier_parent_dict", {}))
    )
    kind_list: list[tuple[Iterable[str], _UsageHandler]] = [
        (usage_node_types["call_types"], _call_usage_list),
        (usage_node_types["attribute_types"], _attribute_usage_list),
        (_QUALIFIED_NODE_TYPE_SET, _qualified_usage_list),
        (setting.type_set("path_types"), _path_usage_list),
        (usage_node_types.get("name_path_dict", {}), _name_path_usage_list),
        (usage_node_types.get("template_name_dict", {}), _template_name_usage_list),
        (usage_node_types.get("annotation_string_dict", {}), _annotation_string_usage_list),
        (setting.type_set("macro_argument_types"), _macro_argument_usage_list),
        (_TYPE_REFERENCE_NODE_TYPE_SET, _type_reference_usage_list),
        (identifier_type_set, _identifier_usage_list),
    ]
    handler_dict: dict[str, _UsageHandler] = {}
    for node_type_list, handler in reversed(kind_list):
        for node_type in node_type_list:
            handler_dict[node_type] = handler
    return handler_dict


def _usage_list_of(*usage_tuple: UsageInfo | None) -> list[UsageInfo]:
    """Return the usages given, without the ones that are None."""
    return [usage for usage in usage_tuple if usage is not None]


def _call_usage_list(node: Node, setting: _UsageSetting) -> list[UsageInfo]:
    """Return the usages of a function call node: its extension name and the function it calls."""
    return _usage_list_of(
        _extension_usage(node.children[0] if node.children else None, setting),
        _parse_call_node(node, setting),
    )


def _attribute_usage_list(node: Node, setting: _UsageSetting) -> list[UsageInfo]:
    """Return the usages of an attribute access node.

    Only a standalone access is read: the function part of a call is read with the
    call, and an access inside a node of attribute_skip_parent_types (an import or
    package declaration) is not read.
    """
    if _is_function_part_of_call(node, setting.usage_node_types["call_types"]) or (
        _is_skip_attribute(node, setting.type_set("attribute_skip_parent_types"))
    ):
        return []
    return _usage_list_of(_extension_usage(node, setting), _parse_attribute_node(node, setting))


def _qualified_usage_list(node: Node, setting: _UsageSetting) -> list[UsageInfo]:
    """Return the usage of the outermost qualified name outside import / package declarations."""
    parent = node.parent
    if parent is not None and (
        parent.type in _QUALIFIED_NODE_TYPE_SET
        or parent.type in setting.type_reference_skip_set()
        or parent.type in setting.usage_node_types["call_types"]
    ):
        return []
    return _usage_list_of(_qualified_usage(node, setting))


def _path_usage_list(node: Node, setting: _UsageSetting) -> list[UsageInfo]:
    """Return the usage of the outermost path outside import statements (Rust: a::b::c)."""
    parent = node.parent
    if parent is not None and (
        parent.type in setting.type_set("path_types")
        or parent.type in setting.type_reference_skip_set()
    ):
        return []
    return _usage_list_of(_parse_path_node(node, setting.name_set))


def _name_path_usage_list(node: Node, setting: _UsageSetting) -> list[UsageInfo]:
    """Return the usage of a name whose parts are the children of one node (_parse_name_path_node)."""
    return _usage_list_of(_parse_name_path_node(node, setting))


def _template_name_usage_list(node: Node, setting: _UsageSetting) -> list[UsageInfo]:
    """Return the usage of a name written in a string template (_parse_template_name_node)."""
    return _usage_list_of(_parse_template_name_node(node, setting))


def _macro_argument_usage_list(node: Node, setting: _UsageSetting) -> list[UsageInfo]:
    """Return the usages of the paths written as loose tokens in the arguments of a macro."""
    usage_list: list[UsageInfo] = []
    for first_node, segment_list in macro_path_list(node):
        name = "::".join(segment_list)
        if name in setting.name_set or segment_list[0] in setting.name_set:
            usage_list.append(UsageInfo(name=name, line=first_node.start_point[0] + 1))
    return usage_list


def _type_reference_usage_list(node: Node, setting: _UsageSetting) -> list[UsageInfo]:
    """Return the usage of a type reference node.

    Only a parent in skip_parent_types_for_type_ref (import statements and scope
    resolution) keeps a type reference from being a usage; a type written in a
    parameter or a method declaration is one.
    """
    parent = node.parent
    if parent is not None and parent.type in setting.type_reference_skip_set():
        return []
    name = node.text.decode("utf-8")
    if name not in setting.name_set:
        return []
    return [UsageInfo(name=name, line=node.start_point[0] + 1)]


def _identifier_usage_list(node: Node, setting: _UsageSetting) -> list[UsageInfo]:
    """Return the usage of a simple identifier node.

    A node type of identifier_parent_dict is read only under the parents given there.
    """
    parent_type_set = setting.usage_node_types.get("identifier_parent_dict", {}).get(node.type)
    if parent_type_set is not None and (
        node.parent is None or node.parent.type not in parent_type_set
    ):
        return []
    return _usage_list_of(_parse_identifier_node(node, setting))


def _leading_part_set(name_set: set[str]) -> set[str]:
    """Return every leading part of the names that ends right before a "." or "::".

    Examples:
        {"a.b.c", "x::y"} -> {"a", "a.b", "x"}

    Args:
        name_set: Usage names.

    Returns:
        The text of each name up to (not including) each separator in it.
    """
    part_set: set[str] = set()
    for name in name_set:
        for index, char in enumerate(name):
            if char == "." or name.startswith("::", index):
                part_set.add(name[:index])
    return part_set


def deduplicate_usage_list(usage_list: list[UsageInfo]) -> list[UsageInfo]:
    """Remove redundant entries within the same line and eliminate duplicates.

    When both "module" and "module.attr" (or "module::item") exist on the same line,
    the more detailed name is kept and "module" is removed.
    Entries with duplicate (name, line) pairs are also removed.

    Args:
        usage_list: A UsageInfo list that may contain duplicates.

    Returns:
        A deduplicated UsageInfo list (sorted by line number in ascending order).
    """
    # Group by line number
    by_line_dict: dict[int, list[UsageInfo]] = {}
    for usage in usage_list:
        by_line_dict.setdefault(usage.line, []).append(usage)

    seen_key_set: set[tuple] = set()
    unique_list: list[UsageInfo] = []

    # Process each group in ascending line-number order
    for line in sorted(by_line_dict):
        line_usage_list = by_line_dict[line]
        shorter_name_set = _leading_part_set({usage.name for usage in line_usage_list})

        for usage in line_usage_list:
            # Exclude the shorter name if a more detailed name (usage.name.xxx) exists on the same line
            if usage.name in shorter_name_set:
                continue

            # Also remove entries with duplicate (name, line) pairs
            entry_key = (usage.name, usage.line, usage.is_member)
            if entry_key not in seen_key_set:
                seen_key_set.add(entry_key)
                unique_list.append(usage)

    return unique_list


def _extension_usage(node: Node | None, setting: _UsageSetting) -> UsageInfo | None:
    """Return the usage of an extension name written after a value.

    Examples (describe in extension_name_set):
        circle.describe()         -> "describe"
        shapes.first().describe   -> "describe"

    Args:
        node: An attribute access node, or the node a call calls; None for none.
        setting: The usage settings of the file.

    Returns:
        UsageInfo named by the last name of the access when that name is in
        extension_name_set, else None.
    """
    if (
        node is None or not setting.extension_name_set
        or node.type not in setting.usage_node_types["attribute_types"]
        or len(node.named_children) < 2
    ):
        return None
    name = _template_name(node.named_children[-1])
    if name not in setting.extension_name_set:
        return None
    return UsageInfo(name=name, line=node.start_point[0] + 1)


def _is_skip_attribute(node: Node, skip_parent_type_set: set[str]) -> bool:
    """Return whether an attribute access is part of a statement whose names are not usages.

    Args:
        node: An attribute access node.
        skip_parent_type_set: The attribute_skip_parent_types of the language settings.

    Returns:
        True when the parent of the outermost access of the same node type around the
        node is of one of the types (Java: the name of an import or package declaration).
    """
    if not skip_parent_type_set:
        return False
    outer_node = node
    while outer_node.parent is not None and outer_node.parent.type == node.type:
        outer_node = outer_node.parent
    return outer_node.parent is not None and outer_node.parent.type in skip_parent_type_set


def _is_function_part_of_call(node: Node, call_types: set[str]) -> bool:
    """Determine whether this attribute node is the function part of a call node.

    In the AST for "func()", the first child of the call node is an identifier or attribute.
    In that case, the call node handles the processing, so the attribute node alone is not processed.

    Args:
        node: The attribute node to check.
        call_types: Set of node types representing function calls.

    Returns:
        True if it is the function part of a call, False if it is a standalone attribute access.
    """
    # Check if the parent node is a call type
    parent = node.parent
    if parent and parent.type in call_types:
        # Check if the first child of the call node is this node
        for child in parent.children:
            if child.type in ("identifier", node.type):
                return child.id == node.id
    return False


def _parse_call_node(node: Node, setting: _UsageSetting) -> UsageInfo | None:
    """Extract symbol usage information from a function call node.

    Only the function part of the call is read:
        func()                    -> "func"
        module.func()             -> "module.func"
        self.method()             -> "method" (see _chain_usage)
        geo::doSomething()        -> from the first tracked part on (C++)
    A call that names its method in a name field after its object (Java:
    User.of(), repo.items.all()) is read as object + "." + name.

    Args:
        node: A call node.
        setting: The usage settings of the file.

    Returns:
        UsageInfo, or None if not applicable.
    """
    line = node.start_point[0] + 1
    attribute_types = setting.usage_node_types["attribute_types"]

    # A call with an object and a name field (Java method_invocation)
    object_node = node.child_by_field_name("object")
    name_node = node.child_by_field_name("name")
    if object_node is not None and name_node is not None:
        chain = _chain_part(object_node, attribute_types)
        if chain is None:
            return None
        base_node, part_list = chain
        return _chain_usage(base_node, [*part_list, name_node.text.decode("utf-8")], line, setting)

    # Check only the first child (function name part) of the call node
    function_node = node.children[0] if node.children else None
    if function_node is None:
        return None
    if function_node.type == "identifier":
        # Simple function call: func()
        return _chain_usage(function_node, [], line, setting, is_call_name=True)
    if function_node.type in attribute_types:
        # Call via attribute access: module.func()
        chain = _chain_part(function_node, attribute_types)
        return _chain_usage(*chain, line, setting) if chain else None
    if function_node.type == "qualified_identifier":
        # C++ scope resolution operator call: geo::doSomething()
        usage = _qualified_usage(function_node, setting)
        return UsageInfo(name=usage.name, line=line) if usage else None
    return None


def _parse_attribute_node(node: Node, setting: _UsageSetting) -> UsageInfo | None:
    """Extract symbol usage information from an attribute access node.

    Returns a UsageInfo when the chain of names the access writes starts from a
    tracked name ("module.attr"), or from self / this followed by a tracked member.
    A chain of a node type in package_path_types that starts from no tracked name is
    named from its first tracked part on (Java: com.acme.model.Circle -> "Circle").

    Args:
        node: An attribute node.
        setting: The usage settings of the file.

    Returns:
        UsageInfo, or None if not applicable.
    """
    chain = _chain_part(node, setting.usage_node_types["attribute_types"])
    if chain is None:
        return None
    line = node.start_point[0] + 1
    usage = _chain_usage(*chain, line, setting)
    if usage is None and node.type in setting.type_set("package_path_types"):
        part_list = chain[1]
        for index, part in enumerate(part_list):
            if part in setting.name_set:
                return UsageInfo(name=".".join(part_list[index:]), line=line)
    return usage


def _parse_path_node(
    node: Node,
    imported_names: set[str],
) -> UsageInfo | None:
    """Extract symbol usage information from a Rust path node.

    Returns a UsageInfo named with the whole path when the whole path or its first
    segment is an imported name.
    e.g. Settings::new() with "Settings" imported -> "Settings::new"
    e.g. crate::util::log() with "crate::util::log" imported -> "crate::util::log"

    Args:
        node: A path node (scoped_identifier / scoped_type_identifier).
        imported_names: Set of names to track.

    Returns:
        UsageInfo, or None if not applicable.
    """
    segment_list = path_segment_list(node)
    if not segment_list:
        return None
    name = "::".join(segment_list)
    if name in imported_names or segment_list[0] in imported_names:
        return UsageInfo(name=name, line=node.start_point[0] + 1)
    return None


def _parse_name_path_node(node: Node, setting: _UsageSetting) -> UsageInfo | None:
    """Extract symbol usage information from a name whose parts are the children of one node.

    The identifier children of the node are the parts of the name. It is a usage when
    the name_path_dict entry of its node type has its parent type, or "" for any
    parent, and the name has at least the number of parts given there:
        case Point(x=0):      dotted_name in class_pattern, 1 part   -> "Point"
        case core.Point():    dotted_name in class_pattern, 2 parts  -> "core.Point"
        case Color.RED:       dotted_name in case_pattern, 2 parts   -> "Color.RED"
        case value:           dotted_name in case_pattern, 1 part    -> none (the pattern binds value)
        x is Shape.Empty      user_type, 2 parts                     -> "Shape.Empty" (Kotlin)

    Args:
        node: A node whose type is a key of name_path_dict.
        setting: The usage settings of the file.

    Returns:
        UsageInfo, or None if not applicable.
    """
    part_node_list = [child for child in node.named_children if child.type == "identifier"]
    parent_dict = setting.usage_node_types["name_path_dict"][node.type]
    min_part_count = parent_dict.get(
        node.parent.type if node.parent is not None else "", parent_dict.get(""),
    )
    if min_part_count is None or len(part_node_list) < max(min_part_count, 1):
        return None
    return _chain_usage(
        part_node_list[0], [part_node.text.decode("utf-8") for part_node in part_node_list[1:]],
        node.start_point[0] + 1, setting,
    )


def _parse_template_name_node(node: Node, setting: _UsageSetting) -> UsageInfo | None:
    """Extract symbol usage information from a name written in a string template.

    Kotlin: "$name text" is read by the grammar as the text "$" followed by the text
    "name text"; the name the second text starts with is a usage.

    Args:
        node: A node whose type is a key of template_name_dict.
        setting: The usage settings of the file.

    Returns:
        UsageInfo, or None when the text of the node is not the marker
        template_name_dict gives, the node after it is of another type or starts with
        no name, or the name is not tracked or is bound by a scope around the node.
    """
    marker = setting.usage_node_types["template_name_dict"][node.type]
    name_node = node.next_sibling
    if node.text.decode("utf-8") != marker or name_node is None or name_node.type != node.type:
        return None
    name_match = _LEAD_NAME_RE.match(name_node.text.decode("utf-8"))
    if name_match is None:
        return None
    name = name_match.group()
    if name not in setting.name_set or _is_local_name(node, name, setting):
        return None
    return UsageInfo(name=name, line=name_node.start_point[0] + 1)


def _annotation_string_usage_list(node: Node, setting: _UsageSetting) -> list[UsageInfo]:
    """Extract the usages of the names an annotation written as a string names.

    Examples (Engine and core tracked):
        def run(e: "Engine") -> "list[core.Config]"   -> "Engine", "core.Config"
        mode: Literal["Engine"]                       -> none

    Args:
        node: A string node whose type is a key of annotation_string_dict.
        setting: The usage settings of the file.

    Returns:
        One UsageInfo per name of the string that starts from a tracked name no scope
        around the string binds. Empty when the string is not inside a node of the type
        annotation_string_dict gives, holds an interpolation or anything a type is not
        written with, or is an argument of a type whose name is in
        annotation_literal_names.
    """
    annotation_type = setting.usage_node_types["annotation_string_dict"][node.type]
    literal_name_set = setting.type_set("annotation_literal_names")
    # The nodes from the string up to the outermost annotation around it
    ancestor_list: list[Node] = []
    annotation_count = 0
    ancestor = node.parent
    while ancestor is not None:
        ancestor_list.append(ancestor)
        if ancestor.type == annotation_type:
            annotation_count = len(ancestor_list)
        ancestor = ancestor.parent
    if not annotation_count:
        return []
    # A string given to a type of annotation_literal_names names nothing
    for holder in ancestor_list[:annotation_count]:
        name_node = next(iter(holder.named_children), None)
        if name_node is None or name_node is node:
            continue
        name_text = name_node.text.decode("utf-8")
        if _DOTTED_NAME_RE.fullmatch(name_text) and symbol_part_list(name_text)[-1] in literal_name_set:
            return []
    if any(child.type == "interpolation" for child in node.named_children):
        return []
    text = "".join(
        child.text.decode("utf-8") for child in node.named_children
        if child.type == "string_content"
    )
    if not _TYPE_TEXT_RE.fullmatch(text) or _WORD_GAP_RE.search(text):
        return []
    line = node.start_point[0] + 1
    usage_list: list[UsageInfo] = []
    for name_match in _DOTTED_NAME_RE.finditer(text):
        name = name_match.group()
        if _track_root(name, setting.name_set) is None:
            continue
        if _is_local_name(node, symbol_part_list(name)[0], setting):
            continue
        usage_list.append(UsageInfo(name=name, line=line))
    return usage_list


def _parse_identifier_node(node: Node, setting: _UsageSetting) -> UsageInfo | None:
    """Extract symbol usage information from a simple identifier node.

    Ignored when the identifier is part of import statements, definitions,
    or argument declarations (i.e., part of syntax), and where a scope around it
    binds its name.
    For node types in skip_name_field_types, only the "name"-field child is
    skipped while the "value" side is detected as a usage. The child a pattern node
    binds (pattern_field_dict) is skipped the same way.
    When identifier_parent_types is not empty, only an identifier whose parent
    is one of those types is detected (SQL: object_reference).

    Example: in def func(x=some_var), for default_parameter:
        identifier "x" (name field) -> skipped
        identifier "some_var" (value field) -> detected as a usage

    Args:
        node: An identifier node, or a node whose type is in identifier_types.
        setting: The usage settings of the file.

    Returns:
        UsageInfo, or None if not applicable.
    """
    identifier_parent_types = setting.type_set("identifier_parent_types")
    parent = node.parent
    if identifier_parent_types and (not parent or parent.type not in identifier_parent_types):
        return None
    # The name of a macro called as a statement is a usage of the macro
    is_macro_call = (
        parent is not None and parent.type in setting.type_set("macro_declarator_types")
        and is_macro_call_declarator(parent)
    )
    if parent and not is_macro_call:
        if parent.type in setting.type_set("skip_name_field_types"):
            # Skip only the "name" field child; treat the "value" side as a usage
            name_child = parent.child_by_field_name("name")
            if name_child and name_child.id == node.id:
                return None
        elif parent.type in setting.usage_node_types["skip_parent_types"]:
            return None
        elif (
            parent.type in setting.type_set("skip_last_child_types")
            and parent.children[-1].id == node.id
        ):
            return None
        elif parent.type in setting.type_set("skip_first_name_types") and node.id == next(
            child.id for child in parent.children if child.type == node.type
        ):
            return None
        # A later part of a name whose parts are the children of one node is no name by itself
        if (
            parent.type in setting.usage_node_types.get("name_path_dict", {})
            and next(iter(parent.named_children), None) != node
        ):
            return None
        # The name a pattern binds (inner in { hp: inner = hp }) is no usage of that name
        pattern_field = setting.usage_node_types.get("pattern_field_dict", {}).get(parent.type)
        if pattern_field is not None and node in parent.children_by_field_name(pattern_field):
            return None

    # Check if the name matches an imported name
    name = node.text.decode("utf-8")
    if name in setting.name_set:
        if not _is_local_name(node, name, setting):
            return UsageInfo(name=name, line=node.start_point[0] + 1)
    elif _is_class_member_name(node, name, setting):
        return UsageInfo(name=name, line=node.start_point[0] + 1, is_member=True)

    return None


@dataclass
class TypedAlias:
    """A variable that stands for an object of a tracked type, and the lines it does so for."""

    name: str              # Name of the variable
    # Name of the type; None for the lines the variable is given a value of no tracked type
    type_name: str | None
    start_line: int        # First line the variable counts for (1-based)
    end_line: int          # Last line it counts for
    # Whether the variable is declared outside every scope (a field, a variable of the file)
    is_file_level: bool = False


def _scope_node(
    node: Node, scope_types: set[str], root_node: Node, opaque_types: set[str] = _EMPTY_SET,
) -> Node | None:
    """Return the innermost scope_types node around a node, the root node when there is none.

    Returns:
        The node; None when an opaque_types node (Python: a class body) comes before
        it, since a name written there is no variable of the scope.
    """
    scope_node = node.parent
    while scope_node is not None and scope_node.type not in scope_types:
        if scope_node.type in opaque_types:
            return None
        scope_node = scope_node.parent
    return scope_node or root_node


def extract_typed_aliases(
    root_node: Node,
    imported_names: set[str],
    usage_node_types: dict,
) -> list[TypedAlias]:
    """Traverse the AST to find the variables declared with a tracked type.

    Detects variables declared with an imported type (e.g. Genre) such as genre.
    A variable counts for the lines of the innermost scope node around its
    declaration (scope_type_set: a function, a block), for the lines of the
    declaration when it is such a node itself (the variable of a loop), or for the
    whole file when there is none (a field); a variable declared outside every
    function is is_file_level. A
    declaration written in an opaque_types node (Python: a class body) is not read.

    Supported AST patterns (typed_alias_parent_types):
      Java:   field_declaration / local_variable_declaration / formal_parameter
      Kotlin: property_declaration / parameter / class_parameter (class Car(val e: Engine))
      C/C++:  declaration / parameter_declaration
      Python: e: Engine = ... / a parameter e: Engine
      TS:     const e: Engine = ... / a parameter e: Engine
      Rust:   let c: Circle = ... / a parameter c: &Circle
    A node type in typed_alias_name_field_dict names its variable in that field ("" for
    its first named child) and its type in the field "type", or in the field
    typed_alias_type_field_dict gives for it (Java: o instanceof Circle c).

    Args:
        root_node: The AST root node covering the entire file.
        imported_names: Set of type names to track.
        usage_node_types: The EXT_TO_USAGE_NODE_TYPE_DICT entry of the language.

    Returns:
        One TypedAlias per variable whose type name is in imported_names, or starts
        with a name of it ("core.Engine"), in no particular order.
    """
    typed_alias_parent_types = usage_node_types.get("typed_alias_parent_types", set())
    if not typed_alias_parent_types:
        return []
    scope_types = scope_type_set(usage_node_types)
    function_type_set = usage_node_types.get("scope_types", set())
    opaque_types = usage_node_types.get("opaque_types", set())
    name_field_dict = usage_node_types.get("typed_alias_name_field_dict", {})
    type_field_dict = usage_node_types.get("typed_alias_type_field_dict", {})
    first_name_type_set = usage_node_types.get("skip_first_name_types", set())

    alias_list: list[TypedAlias] = []
    stack = [root_node]

    while stack:
        node = stack.pop()

        if node.type in typed_alias_parent_types:
            if node.type in name_field_dict:
                type_name, var_names = _field_type_and_var(
                    node, name_field_dict[node.type], type_field_dict.get(node.type, "type"),
                )
            else:
                type_name, var_names = _extract_type_and_var(node)
                if node.type in first_name_type_set:
                    # Only the first name is declared; a later one is its default value
                    var_names = var_names[:1]
            scope_node = None
            if type_name and _track_root(type_name, imported_names) is not None:
                # A declaration that is a scope itself binds its variable for its own lines
                scope_node = (
                    node if node.type in scope_types
                    else _scope_node(node, scope_types, root_node, opaque_types)
                )
            if scope_node is not None:
                for var_name in var_names:
                    if var_name != type_name:
                        alias_list.append(TypedAlias(
                            var_name, type_name,
                            scope_node.start_point[0] + 1, scope_node.end_point[0] + 1,
                            is_file_level=scope_node == root_node or not _has_ancestor(
                                node, function_type_set,
                            ),
                        ))

        stack.extend(node.children)

    return alias_list


def _new_type_name(value_node: Node, usage_node_types: dict) -> str | None:
    """Return the name of the type a value makes an object of, None for any other value.

    Examples (typed_alias_new_dict):
        Engine(1)         -> "Engine"       (Python: call, function)
        core.Engine()     -> "core.Engine"
        new Engine(1)     -> "Engine"       (JS / TS: new_expression, constructor)

    Args:
        value_node: The value node.
        usage_node_types: The EXT_TO_USAGE_NODE_TYPE_DICT entry of the language.

    Returns:
        The name written in the field typed_alias_new_dict gives for the node type,
        when it is a name or an attribute access made of names only.
    """
    name_field = usage_node_types.get("typed_alias_new_dict", {}).get(value_node.type)
    name_node = value_node.child_by_field_name(name_field) if name_field else None
    if name_node is None:
        return None
    if name_node.type == "identifier":
        return name_node.text.decode("utf-8")
    if name_node.type in usage_node_types["attribute_types"]:
        chain = _chain_part(name_node, usage_node_types["attribute_types"])
        if chain is not None and chain[0].type == "identifier":
            return ".".join([chain[0].text.decode("utf-8"), *chain[1]])
    return None


def extract_value_aliases(
    root_node: Node,
    usage_node_types: dict,
    is_type_name: Callable[[str], bool],
    import_line_dict: dict[str, set[int]] | None = None,
) -> list[TypedAlias]:
    """Traverse the AST to find the variables given an object of a tracked type.

    e = Engine() (Python) and const e = new Engine() (JS / TS) make e stand for Engine
    from the line of the assignment to the end of the innermost scope_types node around
    it (a function). A later node that gives the same variable anything else (another
    value, a destructuring, the variable of a loop) ends that: it gives a TypedAlias
    without a type for its own lines. A type written with a name a scope around the
    value binds (var Engine = pick(); const e = new Engine()) is no tracked type. An
    assignment outside every scope_types node is not read.

    Settings read:
        typed_alias_value_dict: node type -> (field of the variable or of a pattern of
            variables, field of the value; "" for the first named child, None for a
            node whose value is not read)
        typed_alias_new_dict:   node type of a value that makes an object -> field
            naming its type

    Args:
        root_node: The AST root node covering the entire file.
        usage_node_types: The EXT_TO_USAGE_NODE_TYPE_DICT entry of the language.
        is_type_name: Returns whether a name written as the type names a tracked type.
        import_line_dict: Name an import statement binds -> lines of those statements;
            such a name is no name a scope binds.

    Returns:
        The TypedAlias of every node that gives a value to a variable that is given an
        object of a tracked type somewhere in its scope, in no particular order.
    """
    value_dict = usage_node_types.get("typed_alias_value_dict", {})
    if not value_dict or not usage_node_types.get("typed_alias_new_dict"):
        return []
    scope_types = scope_type_set(usage_node_types)
    pattern_type_set = usage_node_types.get("pattern_types", set())
    pattern_field_dict = usage_node_types.get("pattern_field_dict", {})
    setting = _UsageSetting(
        name_set=set(), member_name_set=set(), alias_dict={},
        usage_node_types=usage_node_types, import_line_dict=import_line_dict or {},
    )

    alias_list: list[TypedAlias] = []
    stack = [root_node]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        field_tuple = value_dict.get(node.type)
        if field_tuple is None:
            continue
        name_field, value_field = field_tuple
        target_node = node.child_by_field_name(name_field)
        if target_node is None:
            continue
        if value_field is None:
            value_node = None
        elif value_field:
            value_node = node.child_by_field_name(value_field)
        else:
            value_node = next(iter(node.named_children), None)
        scope_node = _scope_node(
            node, scope_types, root_node, usage_node_types.get("opaque_types", set()),
        )
        if scope_node is None or scope_node is root_node:
            continue
        # Python: with Engine() as e holds the name in an as_pattern_target
        name_node = target_node
        if name_node.type != "identifier" and name_node.named_child_count == 1:
            name_node = name_node.named_children[0]
        if name_node.type == "identifier":
            name_list = [name_node.text.decode("utf-8")]
            type_name = (
                _new_type_name(value_node, usage_node_types) if value_node is not None else None
            )
            if type_name is not None and (
                not is_type_name(type_name)
                or _is_local_name(value_node, symbol_part_list(type_name)[0], setting)
            ):
                type_name = None
        else:
            # A pattern of several variables gives none of them an object of its own
            name_list = pattern_name_list(target_node, pattern_type_set, pattern_field_dict)
            type_name = None
        alias_list.extend(
            TypedAlias(name, type_name, node.start_point[0] + 1, scope_node.end_point[0] + 1)
            for name in name_list
        )

    typed_key_set = {
        (alias.name, alias.end_line) for alias in alias_list if alias.type_name is not None
    }
    return [alias for alias in alias_list if (alias.name, alias.end_line) in typed_key_set]


def typed_alias_dict(alias_list: list[TypedAlias]) -> dict[str, list[TypedAlias]]:
    """Return the aliases of a file by their variable names, in the order of the list."""
    alias_dict: dict[str, list[TypedAlias]] = {}
    for alias in alias_list:
        alias_dict.setdefault(alias.name, []).append(alias)
    return alias_dict


def typed_alias(
    alias_dict: dict[str, list[TypedAlias]], name: str, line: int,
) -> TypedAlias | None:
    """Return the alias a variable name counts under on a line.

    Args:
        alias_dict: The aliases of a file by their variable names (typed_alias_dict).
        name: A variable name.
        line: Line the name is written on (1-based).

    Returns:
        The alias of that name whose lines hold the line; of several, the one with the
        fewest lines. None when no alias of that name counts for the line.
    """
    match_list = [
        alias for alias in alias_dict.get(name, ())
        if alias.start_line <= line <= alias.end_line
    ]
    if not match_list:
        return None
    return min(match_list, key=lambda alias: alias.end_line - alias.start_line)


def typed_alias_type(
    alias_dict: dict[str, list[TypedAlias]], name: str, line: int,
) -> str | None:
    """Return the type a variable name stands for on a line.

    Args:
        alias_dict: The aliases of a file by their variable names (typed_alias_dict).
        name: A variable name.
        line: Line the name is written on (1-based).

    Returns:
        The type of typed_alias(); None when there is no such alias or it has no type.
    """
    alias = typed_alias(alias_dict, name, line)
    return alias.type_name if alias is not None else None


def _type_name(type_node: Node) -> str | None:
    """Return the name of the type a declaration is written with.

    Examples:
        User               -> "User"
        struct node        -> "node"
        geo::Shape         -> "Shape"
        Box<int>           -> "Box"
        core.Engine        -> "core.Engine" (Python)
        Circle.Builder     -> "Circle.Builder" (Java)
        Shape.Empty        -> "Shape.Empty" (Kotlin)
        &mut Circle        -> "Circle" (Rust)

    Args:
        type_node: The type node of a declaration.

    Returns:
        The type name, or None for a type without a name (a primitive type).
    """
    current: Node | None = type_node
    while current is not None:
        if current.type in ("type_identifier", "identifier"):
            return current.text.decode("utf-8")
        if current.type in _DOTTED_TYPE_NODE_TYPE_SET:
            # A type written with the module or type it is a member of
            text = current.text.decode("utf-8")
            return text if _DOTTED_NAME_RE.fullmatch(text) else None
        if current.type == "user_type":
            # Kotlin: a type written with the type it is a member of (Shape.Empty)
            text = current.text.decode("utf-8")
            if _DOTTED_NAME_RE.fullmatch(text):
                return text
        name_node = current.child_by_field_name("name")
        if name_node is None and current.type in _TYPE_HOLDER_FIELD_DICT:
            # Rust: a reference, a type with generic arguments, a trait object
            name_node = current.child_by_field_name(_TYPE_HOLDER_FIELD_DICT[current.type])
        if name_node is None and current.type in _TYPE_WRAP_NODE_TYPE_SET:
            # Kotlin: user_type holds the type name as a plain child; Python and TS wrap
            # an annotation in a node of its own
            name_node = next(iter(current.named_children), None)
        current = name_node
    return None


def _field_type_and_var(
    node: Node, name_field: str, type_field: str = "type",
) -> tuple[str | None, list[str]]:
    """Extract the type name and the variable name from a declaration that names both in fields.

    Args:
        node: A node whose type is a key of typed_alias_name_field_dict.
        name_field: The field holding the variable; "" for the first named child.
        type_field: The field holding the type.

    Returns:
        (type name of the type field, [variable name]); (None, []) when the node has
        no type or its variable is not a plain name, also inside the declarators that
        wrap it (C++: for (Shape& s : shapes)).
    """
    type_node = node.child_by_field_name(type_field)
    name_node = declarator_name_node(
        node.child_by_field_name(name_field) if name_field
        else next(iter(node.named_children), None)
    )
    if type_node is None or name_node is None or name_node.type != "identifier":
        return None, []
    return _type_name(type_node), [name_node.text.decode("utf-8")]


def _extract_type_and_var(node: Node) -> tuple[str | None, list[str]]:
    """Extract the type name and variable names from a typed variable declaration node.

    Absorbs AST structure differences across languages:
      Java:   type: type_identifier + declarator: variable_declarator / name: identifier
      Kotlin: variable_declaration (identifier + user_type) / identifier + user_type
      C/C++:  type: type_identifier + declarator: (init / pointer / array) declarator;
              a declarator read as a function is a variable made with arguments when the
              declaration is a statement of a function (Engine e(1);)

    Args:
        node: An AST node representing a typed variable declaration.

    Returns:
        A (type_name, [list of variable names]) tuple. Returns (None, []) if not found.
    """
    # Kotlin: val name: Type holds its name and type in a variable_declaration
    holder_node = node
    for child in node.children:
        if child.type == "variable_declaration":
            holder_node = child
            break

    type_name: str | None = None
    var_name_list: list[str] = []
    type_node = holder_node.child_by_field_name("type")
    for child in holder_node.children:
        if child == type_node or child.type in ("type_identifier", "user_type"):
            type_name = _type_name(child) or type_name
        elif child.type == "identifier":
            var_name_list.append(child.text.decode("utf-8"))
        elif child.type in _DECLARATOR_TYPE_SET:
            name_node = declarator_name_node(child)
            if name_node is not None and name_node.type == "variable_declarator":
                name_node = name_node.child_by_field_name("name")
            if name_node is not None and name_node.type == "identifier":
                var_name_list.append(name_node.text.decode("utf-8"))
        elif (
            child.type == _CALL_DECLARATOR_TYPE and node.parent is not None
            and node.parent.type in _STATEMENT_PARENT_TYPE_SET
        ):
            name_node = child.child_by_field_name("declarator")
            if name_node is not None and name_node.type == "identifier":
                var_name_list.append(name_node.text.decode("utf-8"))

    return type_name, var_name_list
