import re
from collections.abc import Callable, Container
from dataclasses import dataclass, field
from tree_sitter import Node
from codetwine.extractors.cobol_source import CobolSource
from codetwine.extractors.definitions import declarator_name_node, pattern_name_list
from codetwine.extractors.rust_path import macro_path_list, path_segment_list

# Separators between the parts of a usage name: "." (attribute access) and "::" (Rust / C++ path)
_SYMBOL_SEPARATOR_RE = re.compile(r"\.|::")

# Type reference / namespace reference node types (skip_parent_types check not needed)
_TYPE_REFERENCE_NODE_TYPE_SET = {"type_identifier", "namespace_identifier"}

# Node types of a name written with template arguments (C++: Box<int>, twice<int>, obj.get<int>)
_TEMPLATE_NODE_TYPE_SET = {"template_type", "template_function", "template_method"}

# Node types of the declarators of a typed variable declaration (Java, C / C++)
_DECLARATOR_TYPE_SET = {
    "variable_declarator", "init_declarator", "pointer_declarator", "array_declarator",
    "reference_declarator",
}

# Node types that wrap the type a declaration is written with (Kotlin, Python, TS)
_TYPE_WRAP_NODE_TYPE_SET = {"user_type", "type", "type_annotation"}

# A name written with dots only (core.Engine)
_DOTTED_NAME_RE = re.compile(r"[^\W\d]\w*(?:\.[^\W\d]\w*)*")

_EMPTY_SET: set[str] = set()


@dataclass
class UsageInfo:
    """Data class holding information about a single symbol usage location."""

    name: str    # The symbol name being used
    line: int    # Line number of the usage location (1-based)


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

    def type_set(self, key: str) -> set[str]:
        """Return a node type set of the settings, empty when the language has none."""
        return self.usage_node_types.get(key, _EMPTY_SET)


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

    Args:
        scope_node: A node whose type is in scope_types.
        setting: The usage settings of the file.

    Returns:
        The bound names. Computed once per scope node.
    """
    local_name_set = setting.local_name_dict.get(scope_node.id)
    if local_name_set is not None:
        return local_name_set

    scope_type_set = setting.type_set("scope_types")
    opaque_type_set = setting.type_set("opaque_types")
    unbind_type_set = setting.type_set("unbind_types")
    local_binding_dict = setting.usage_node_types.get("local_binding_dict", {})

    local_name_set = set()
    global_name_set: set[str] = set()
    scope_field = setting.usage_node_types.get("scope_binding_dict", {}).get(scope_node.type)
    if scope_field is not None:
        local_name_set.update(_binding_name_list(scope_node, scope_field, setting))

    node_stack = list(scope_node.children)
    while node_stack:
        node = node_stack.pop()
        if node.type in unbind_type_set:
            global_name_set.update(
                child.text.decode("utf-8") for child in node.named_children
            )
            continue
        field_name = local_binding_dict.get(node.type)
        if field_name is not None:
            local_name_set.update(_binding_name_list(node, field_name, setting))
        if node.type in scope_type_set or node.type in opaque_type_set:
            continue
        node_stack.extend(node.children)

    local_name_set -= global_name_set
    setting.local_name_dict[scope_node.id] = local_name_set
    return local_name_set


def _is_local_name(node: Node, name: str, setting: _UsageSetting) -> bool:
    """Return whether a name written at a node is bound by a scope around the node.

    Args:
        node: The node the name is written at.
        name: The name.
        setting: The usage settings of the file.

    Returns:
        True when a scope_types node among the ancestors of node binds the name
        (_local_name_set) and the name is no typed variable declared inside that scope
        for the line of the node (alias_dict). A scope whose type is a key of scope_body_dict binds
        its names only for the nodes inside that field of it (Python: a default value
        or an annotation of a function is read outside the function).
    """
    scope_type_set = setting.type_set("scope_types")
    if not scope_type_set:
        return False
    alias = typed_alias(setting.alias_dict, name, node.start_point[0] + 1)
    scope_body_dict = setting.usage_node_types.get("scope_body_dict", {})
    child = node
    current = node.parent
    while current is not None:
        if current.type in scope_type_set and name in _local_name_set(current, setting):
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
            return UsageInfo(name=".".join(part_list), line=line)
        return None
    if base_node.type != "identifier":
        return None
    name = ".".join([base_text, *part_list])
    if _track_root(name, setting.name_set) is None:
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

    Args:
        node: The outermost qualified_identifier node of the name.
        setting: The usage settings of the file.

    Returns:
        UsageInfo, or None when no part is tracked.
    """
    segment_list = _qualified_segment_list(node)
    for index, segment in enumerate(segment_list):
        if segment in setting.name_set:
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
) -> list[UsageInfo]:
    """Extract usage locations of imported names from the AST and return them.

    Traverses the AST via depth-first search (DFS) and detects the following kinds:
    - Nodes in call_types (function calls)
    - Nodes in attribute_types (attribute access), named by the chain of names they
      write (helper.process.run); a chain written after self / this is named without it
    - identifier nodes (simple variable references), and nodes in identifier_types
    - type_identifier / namespace_identifier nodes (type / namespace references)
      Java: "User" in User user = new User() is a type_identifier
      C/C++: "Point" in struct Point p is a type_identifier
    - qualified_identifier nodes (C++: geo::Shape::count), named from their first
      tracked part on
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
                          Optional key: "skip_parent_types_for_type_ref" (uses skip_parent_types if absent)
                          Optional key: "skip_name_field_types" (when the parent is this type,
                            only the name-field child is skipped; the value side is detected as a usage)
                          Optional key: "identifier_parent_types" (when set, an identifier is a usage
                            only when its parent is one of these types)
                          Optional key: "identifier_types" (node types read like an identifier)
                          Optional key: "path_types" (node types of a path written with "::")
                          Optional key: "macro_argument_types" (node types of macro arguments,
                            whose paths are read from loose tokens)
                          Optional keys: "self_names" / "self_types" (identifier texts / node
                            types that stand for the object a member is written on)
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
    )

    # Retrieve per-language node types from the settings
    call_types: set[str] = usage_node_types["call_types"]
    attribute_types: set[str] = usage_node_types["attribute_types"]
    skip_parent_types: set[str] = usage_node_types["skip_parent_types"]
    skip_parent_types_for_type_ref: set[str] = usage_node_types.get(
        "skip_parent_types_for_type_ref", skip_parent_types
    )
    identifier_types: set[str] = {"identifier"} | setting.type_set("identifier_types")
    path_types: set[str] = setting.type_set("path_types")
    macro_argument_types: set[str] = setting.type_set("macro_argument_types")

    usage_list: list[UsageInfo] = []
    # DFS traversal of the AST using a stack
    node_stack = [root_node]

    while node_stack:
        node = node_stack.pop()
        usage: UsageInfo | None = None

        # Process function call nodes
        if node.type in call_types:
            usage = _parse_call_node(node, setting)

        elif node.type in attribute_types:
            # Process only standalone attribute access (exclude function part of a call)
            if not _is_function_part_of_call(node, call_types):
                usage = _parse_attribute_node(node, setting)

        elif node.type == "qualified_identifier":
            # Process only the outermost qualified name outside import / package declarations
            parent = node.parent
            if not parent or (
                parent.type != "qualified_identifier"
                and parent.type not in skip_parent_types_for_type_ref
                and parent.type not in call_types
            ):
                usage = _qualified_usage(node, setting)

        elif node.type in path_types:
            # Process only the outermost path outside import statements (Rust: a::b::c)
            parent = node.parent
            if not parent or (
                parent.type not in path_types and parent.type not in skip_parent_types_for_type_ref
            ):
                usage = _parse_path_node(node, imported_names)

        elif node.type in macro_argument_types:
            # Process the paths written as loose tokens in the arguments of a macro
            for first_node, segment_list in macro_path_list(node):
                name = "::".join(segment_list)
                if name in imported_names or segment_list[0] in imported_names:
                    usage_list.append(UsageInfo(name=name, line=first_node.start_point[0] + 1))

        elif node.type in _TYPE_REFERENCE_NODE_TYPE_SET:
            # Process type reference nodes.
            # Use the type-reference skip list for checking (only import statements and scope resolution are skipped).
            # Unlike the identifier skip_parent_types, type references in parameters and method declarations
            # are detected as dependencies.
            parent = node.parent
            if not parent or parent.type not in skip_parent_types_for_type_ref:
                name = node.text.decode("utf-8")
                if name in imported_names:
                    usage = UsageInfo(name=name, line=node.start_point[0] + 1)

        elif node.type in identifier_types:
            # Process simple identifier nodes
            usage = _parse_identifier_node(node, setting)

        if usage:
            usage_list.append(usage)

        # Add child nodes to the stack to continue traversal
        node_stack.extend(node.children)

    # Remove duplicate and redundant entries and return
    return deduplicate_usage_list(usage_list)


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
            entry_key = (usage.name, usage.line)
            if entry_key not in seen_key_set:
                seen_key_set.add(entry_key)
                unique_list.append(usage)

    return unique_list


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

    Args:
        node: An attribute node.
        setting: The usage settings of the file.

    Returns:
        UsageInfo, or None if not applicable.
    """
    chain = _chain_part(node, setting.usage_node_types["attribute_types"])
    if chain is None:
        return None
    return _chain_usage(*chain, node.start_point[0] + 1, setting)


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
    if parent:
        if parent.type in setting.type_set("skip_name_field_types"):
            # Skip only the "name" field child; treat the "value" side as a usage
            name_child = parent.child_by_field_name("name")
            if name_child and name_child.id == node.id:
                return None
        elif parent.type in setting.usage_node_types["skip_parent_types"]:
            return None
        # The name a pattern binds (inner in { hp: inner = hp }) is no usage of that name
        pattern_field = setting.usage_node_types.get("pattern_field_dict", {}).get(parent.type)
        if pattern_field is not None and node in parent.children_by_field_name(pattern_field):
            return None

    # Check if the name matches an imported name
    name = node.text.decode("utf-8")
    if name in setting.name_set and not _is_local_name(node, name, setting):
        return UsageInfo(name=name, line=node.start_point[0] + 1)

    return None


@dataclass
class TypedAlias:
    """A variable that stands for an object of a tracked type, and the lines it does so for."""

    name: str              # Name of the variable
    # Name of the type; None for the lines the variable is given a value of no tracked type
    type_name: str | None
    start_line: int        # First line the variable counts for (1-based)
    end_line: int          # Last line it counts for


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
    A variable counts for the lines of the innermost scope_types node around its
    declaration (a function), or for the whole file when there is none (a field). A
    declaration written in an opaque_types node (Python: a class body) is not read.

    Supported AST patterns (typed_alias_parent_types):
      Java:   field_declaration / local_variable_declaration / formal_parameter
      Kotlin: property_declaration / parameter
      C/C++:  declaration / parameter_declaration
      Python: e: Engine = ... / a parameter e: Engine
      TS:     const e: Engine = ... / a parameter e: Engine
    A node type in typed_alias_name_field_dict names its variable in that field ("" for
    its first named child) and its type in the field "type".

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
    scope_types = usage_node_types.get("scope_types", set())
    opaque_types = usage_node_types.get("opaque_types", set())
    name_field_dict = usage_node_types.get("typed_alias_name_field_dict", {})

    alias_list: list[TypedAlias] = []
    stack = [root_node]

    while stack:
        node = stack.pop()

        if node.type in typed_alias_parent_types:
            if node.type in name_field_dict:
                type_name, var_names = _field_type_and_var(node, name_field_dict[node.type])
            else:
                type_name, var_names = _extract_type_and_var(node)
            scope_node = (
                _scope_node(node, scope_types, root_node, opaque_types)
                if type_name and _track_root(type_name, imported_names) is not None else None
            )
            if scope_node is not None:
                for var_name in var_names:
                    if var_name != type_name:
                        alias_list.append(TypedAlias(
                            var_name, type_name,
                            scope_node.start_point[0] + 1, scope_node.end_point[0] + 1,
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
    scope_types = usage_node_types.get("scope_types", set())
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

    Args:
        type_node: The type node of a declaration.

    Returns:
        The type name, or None for a type without a name (a primitive type).
    """
    current: Node | None = type_node
    while current is not None:
        if current.type in ("type_identifier", "identifier"):
            return current.text.decode("utf-8")
        if current.type == "attribute":
            # Python: a type written with its module (core.Engine)
            text = current.text.decode("utf-8")
            return text if _DOTTED_NAME_RE.fullmatch(text) else None
        name_node = current.child_by_field_name("name")
        if name_node is None and current.type in _TYPE_WRAP_NODE_TYPE_SET:
            # Kotlin: user_type holds the type name as a plain child; Python and TS wrap
            # an annotation in a node of its own
            name_node = next(iter(current.named_children), None)
        current = name_node
    return None


def _field_type_and_var(node: Node, name_field: str) -> tuple[str | None, list[str]]:
    """Extract the type name and the variable name from a declaration that names both in fields.

    Args:
        node: A node whose type is a key of typed_alias_name_field_dict.
        name_field: The field holding the variable; "" for the first named child.

    Returns:
        (type name of the field "type", [variable name]); (None, []) when the node has
        no type or its variable is not a plain name.
    """
    type_node = node.child_by_field_name("type")
    name_node = (
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
      C/C++:  type: type_identifier + declarator: (init / pointer / array) declarator

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

    return type_name, var_name_list
