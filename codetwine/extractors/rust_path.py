from tree_sitter import Node


# AST node types of a Rust path written with "::" (a::b::c, a::B)
PATH_NODE_TYPE_SET = {"scoped_identifier", "scoped_type_identifier"}

# AST node types that make up one segment of a Rust path
_SEGMENT_NODE_TYPE_SET = {"identifier", "type_identifier", "crate", "self", "super"}


def _segment_text(node: Node) -> str:
    """Return the text of a path segment without the raw identifier prefix (r#type -> type)."""
    return node.text.decode("utf-8").removeprefix("r#")


def path_segment_list(node: Node) -> list[str] | None:
    """Split a Rust path node into its segments.

    Generic arguments and the raw identifier prefix r# are dropped.

    Examples:
        crate::util::log      -> ["crate", "util", "log"]
        Vec::<u8>::new        -> ["Vec", "new"]
        r#type::Kind          -> ["type", "Kind"]
        <T as Trait>::method  -> None

    Args:
        node: A path node (scoped_identifier, scoped_type_identifier, identifier, etc.).

    Returns:
        The segments, or None when the path does not start with a name
        (a qualified type, a leading "::", etc.).
    """
    if node.type in PATH_NODE_TYPE_SET:
        path_node = node.child_by_field_name("path")
        name_node = node.child_by_field_name("name")
        if path_node is None or name_node is None:
            return None
        prefix = path_segment_list(path_node)
        if prefix is None:
            return None
        return prefix + [_segment_text(name_node)]
    if node.type == "generic_type":
        type_node = node.child_by_field_name("type")
        return path_segment_list(type_node) if type_node else None
    if node.type in _SEGMENT_NODE_TYPE_SET:
        return [_segment_text(node)]
    return None


def _drop_trailing_self(segment_list: list[str]) -> list[str]:
    """Remove a trailing "self" segment (use a::b::{self} names a::b itself)."""
    if len(segment_list) > 1 and segment_list[-1] == "self":
        return segment_list[:-1]
    return segment_list


def expand_use_tree(
    node: Node, prefix: list[str],
) -> list[tuple[list[str], str | None]]:
    """Expand the argument of a use declaration into one entry per imported name.

    Examples:
        use a::b::C;              -> (["a", "b", "C"], "C")
        use a::{b::C, D as E};    -> (["a", "b", "C"], "C"), (["a", "D"], "E")
        use a::b::{self};         -> (["a", "b"], "b")
        use a::b::*;              -> (["a", "b", "*"], None)

    Args:
        node: The argument node of a use_declaration, or a node inside it.
        prefix: Segments of the enclosing use list path ([] at the top).

    Returns:
        (segments, name bound in the file) pairs. A glob import ends its segments
        with "*" and binds no name. An entry whose path cannot be read is left out.
    """
    if node.type == "use_as_clause":
        path_list = path_segment_list(node.child_by_field_name("path"))
        alias_node = node.child_by_field_name("alias")
        if path_list is None or alias_node is None:
            return []
        return [(_drop_trailing_self(prefix + path_list), alias_node.text.decode("utf-8"))]

    if node.type == "use_wildcard":
        child_node_list = node.named_children
        inner_list = path_segment_list(child_node_list[0]) if child_node_list else []
        if inner_list is None:
            return []
        return [(prefix + inner_list + ["*"], None)]

    if node.type == "scoped_use_list":
        path_node = node.child_by_field_name("path")
        inner_list = path_segment_list(path_node) if path_node else []
        use_list_node = node.child_by_field_name("list")
        if inner_list is None or use_list_node is None:
            return []
        return expand_use_tree(use_list_node, prefix + inner_list)

    if node.type == "use_list":
        entry_list: list[tuple[list[str], str | None]] = []
        for child in node.named_children:
            entry_list.extend(expand_use_tree(child, prefix))
        return entry_list

    path_list = path_segment_list(node)
    if path_list is None:
        return []
    segment_list = _drop_trailing_self(prefix + path_list)
    return [(segment_list, segment_list[-1])]


def inline_module_depth(node: Node) -> int:
    """Return how many inline modules (mod name { ... }) enclose the node."""
    depth = 0
    current = node.parent
    while current is not None:
        if current.type == "mod_item" and current.child_by_field_name("body") is not None:
            depth += 1
        current = current.parent
    return depth


def file_module_path(segment_list: list[str], depth: int) -> list[str] | None:
    """Rewrite a path written inside inline modules into a path from the file's module.

    Examples (depth 1, inside mod tests { ... }):
        ["super", "Config"]       -> ["self", "Config"]
        ["super", "super", "x"]   -> ["super", "x"]
        ["self", "helper"]        -> None
        ["crate", "util"]         -> ["crate", "util"]

    Args:
        segment_list: Segments of the path as written.
        depth: Number of inline modules enclosing the path (inline_module_depth).

    Returns:
        The rewritten segments, or None when the path points inside the same file.
        An empty segment_list is returned as it is.
    """
    if depth == 0 or not segment_list:
        return segment_list
    if segment_list[0] == "self":
        return None
    super_count = 0
    while super_count < len(segment_list) and segment_list[super_count] == "super":
        super_count += 1
    if super_count == 0:
        return segment_list
    if super_count < depth:
        return None
    rest_list = segment_list[super_count:]
    if super_count == depth:
        return ["self"] + rest_list
    return ["super"] * (super_count - depth) + rest_list


def mod_declaration(node: Node) -> tuple[str, str | None] | None:
    """Read a module declaration without a body (mod name;).

    Examples:
        mod config;                          -> ("config", None)
        mod r#type;                          -> ("type", None)
        #[path = "unix.rs"] mod imp;         -> ("imp", "unix.rs")
        mod tests { ... }                    -> None

    Args:
        node: A mod_item node.

    Returns:
        (module name, value of its #[path] attribute or None), or None when the
        node is not a module declaration without a body.
    """
    if node.type != "mod_item" or node.child_by_field_name("body") is not None:
        return None
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return None

    # Look through the attributes written right before the declaration for #[path = "..."]
    path_value = None
    sibling = node.prev_named_sibling
    while sibling is not None and sibling.type in ("attribute_item", "line_comment", "block_comment"):
        if sibling.type == "attribute_item":
            for attribute in sibling.named_children:
                key = attribute.named_children[0] if attribute.named_children else None
                value = attribute.child_by_field_name("value")
                if key is not None and key.text == b"path" and value is not None:
                    for part in value.named_children:
                        if part.type == "string_content":
                            path_value = part.text.decode("utf-8")
        sibling = sibling.prev_named_sibling
    return _segment_text(name_node), path_value


def _is_inside_use_declaration(node: Node) -> bool:
    """Return whether the node is part of a use declaration."""
    current = node.parent
    while current is not None:
        if current.type == "use_declaration":
            return True
        current = current.parent
    return False


def _read_use_declaration(node: Node) -> list[tuple[str, str | None, str | None]]:
    """Read a use declaration into one import per imported name (expand_use_tree)."""
    argument = node.child_by_field_name("argument")
    if argument is None:
        return []
    depth = inline_module_depth(node)
    entry_list: list[tuple[str, str | None, str | None]] = []
    for segment_list, local_name in expand_use_tree(argument, []):
        is_glob = segment_list[-1] == "*"
        module_list = file_module_path(segment_list[:-1] if is_glob else segment_list, depth)
        if not module_list:
            continue
        if is_glob:
            entry_list.append(("::".join(module_list), "*", None))
            continue
        original = segment_list[-1] if local_name != segment_list[-1] else None
        entry_list.append((
            "::".join(module_list),
            None if local_name == "_" else local_name,
            None if local_name == "_" else original,
        ))
    return entry_list


def _read_mod_declaration(node: Node) -> list[tuple[str, str | None, str | None]]:
    """Read a module declaration without a body outside inline modules."""
    declaration = mod_declaration(node)
    if declaration is None or inline_module_depth(node) > 0:
        return []
    name, path_value = declaration
    return [(path_value or "self::" + name, name, None)]


def _read_extern_crate(node: Node) -> list[tuple[str, str | None, str | None]]:
    """Read an extern crate declaration (extern crate name; / extern crate name as alias;)."""
    name_node = node.child_by_field_name("name")
    alias_node = node.child_by_field_name("alias")
    if name_node is None or name_node.type != "identifier":
        return []
    crate_name = name_node.text.decode("utf-8")
    return [(crate_name, alias_node.text.decode("utf-8") if alias_node else crate_name, None)]


def _read_path(node: Node) -> list[tuple[str, str | None, str | None]]:
    """Read the outermost path outside use declarations and visibility modifiers."""
    parent = node.parent
    if parent is not None and parent.type in PATH_NODE_TYPE_SET | {"visibility_modifier"}:
        return []
    if _is_inside_use_declaration(node):
        return []
    segment_list = path_segment_list(node)
    if not segment_list:
        return []
    module_list = file_module_path(segment_list, inline_module_depth(node))
    if not module_list:
        return []
    return [("::".join(module_list), "::".join(segment_list), None)]


# AST node type -> function reading the imports of that node
_IMPORT_READER_DICT = {
    "use_declaration": _read_use_declaration,
    "mod_item": _read_mod_declaration,
    "extern_crate_declaration": _read_extern_crate,
    "scoped_identifier": _read_path,
    "scoped_type_identifier": _read_path,
}


def rust_import_list(node: Node) -> list[tuple[str, str | None, str | None]]:
    """Return the module paths a Rust node brings into use, for extract_imports.

    Handles:
        use_declaration          -> one entry per imported name (expand_use_tree)
        mod_item without a body  -> ("self::<name>", name), or (the #[path] value, name)
        extern_crate_declaration -> (crate name, bound name)
        scoped_identifier / scoped_type_identifier
                                 -> (path, path as written) for the outermost path
                                    outside use declarations and visibility modifiers

    Args:
        node: A node captured by @path_item.

    Returns:
        (module, name bound in the file, original name when renamed) triples.
        module joins the segments with "::"; a glob import has the name "*" and an
        import renamed to "_" has the name None. Paths inside inline modules are
        rewritten by file_module_path, and one that points inside the same file is left out.
    """
    reader = _IMPORT_READER_DICT.get(node.type)
    return reader(node) if reader else []
