from tree_sitter import Node


# AST node types of a Rust path written with "::" (a::b::c, a::B)
PATH_NODE_TYPE_SET = {"scoped_identifier", "scoped_type_identifier"}

# AST node types that make up one segment of a Rust path
_SEGMENT_NODE_TYPE_SET = {"identifier", "type_identifier", "crate", "self", "super"}

# First segment of a path written with a leading "::" (::name::item)
_ROOT_SEGMENT = ""

# First segment that stands for the type of the impl block, or for the trait, around the path
_SELF_TYPE_SEGMENT = "Self"

# Node type of what "Self" stands for inside it -> field holding its name
_SELF_OWNER_FIELD_DICT = {"impl_item": "type", "trait_item": "name"}

# Token types that end generic arguments or a qualified type (Vec<u8>, <T as Trait>)
_GENERIC_END_TYPE_SET = {">", ">>"}

# First segments that no use declaration binds
_ANCHOR_SEGMENT_SET = {"crate", "self", "super", _ROOT_SEGMENT}

# Maximum number of use declarations of scopes followed when reading one path
_MAX_SCOPE_USE_HOP = 8

# One import a node brings into use: (module, name bound in the file, original name when
# renamed, (first line, last line) the name is bound for or None for the whole file)
ImportEntry = tuple[str, str | None, str | None, tuple[int, int] | None]

# Cache of the use declarations of the blocks and inline modules of one syntax tree:
# node id of the block or module body -> {bound name: (segments of its use path,
# use_declaration node)}
UseCacheDict = dict[int, dict[str, tuple[list[str], Node]]]


def _segment_text(node: Node) -> str:
    """Return the text of a path segment without the raw identifier prefix (r#type -> type)."""
    return node.text.decode("utf-8").removeprefix("r#")


def _node_segment_list(node: Node) -> list[str] | None:
    """Split a Rust path node into its segments, a first segment "Self" kept (path_segment_list)."""
    if node.type in PATH_NODE_TYPE_SET:
        path_node = node.child_by_field_name("path")
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return None
        # A path without a path field starts with "::"
        prefix = [_ROOT_SEGMENT] if path_node is None else _node_segment_list(path_node)
        if prefix is None:
            return None
        return prefix + [_segment_text(name_node)]
    if node.type == "generic_type":
        type_node = node.child_by_field_name("type")
        return _node_segment_list(type_node) if type_node else None
    if node.type in _SEGMENT_NODE_TYPE_SET:
        return [_segment_text(node)]
    return None


def _impl_type_segment_list(node: Node, segment_list: list[str]) -> list[str]:
    """Replace a first segment "Self" by the name of the type of the impl block, or of the trait, around a node.

    Examples (inside impl Point { ... } / impl fmt::Display for geo::Point { ... }):
        ["Self", "new"]   -> ["Point", "new"]
        ["Point", "new"]  -> ["Point", "new"]
    Inside trait Shape { ... }:
        ["Self", "KIND"]  -> ["Shape", "KIND"]

    Args:
        node: A node of the path.
        segment_list: Segments of the path as written.

    Returns:
        The segments; unchanged outside an impl block and a trait, and inside an impl
        block whose type is not a named type.
    """
    if not segment_list or segment_list[0] != _SELF_TYPE_SEGMENT:
        return segment_list
    current = node.parent
    while current is not None and current.type not in _SELF_OWNER_FIELD_DICT:
        current = current.parent
    type_node = (
        current.child_by_field_name(_SELF_OWNER_FIELD_DICT[current.type])
        if current is not None else None
    )
    type_segment_list = _node_segment_list(type_node) if type_node is not None else None
    if not type_segment_list:
        return segment_list
    return [type_segment_list[-1], *segment_list[1:]]


def path_segment_list(node: Node) -> list[str] | None:
    """Split a Rust path node into its segments.

    Generic arguments and the raw identifier prefix r# are dropped. A leading "::"
    gives an empty first segment, and a first segment "Self" inside an impl block or a
    trait is replaced by the name of the type of the block or of the trait.

    Examples:
        crate::util::log      -> ["crate", "util", "log"]
        Vec::<u8>::new        -> ["Vec", "new"]
        r#type::Kind          -> ["type", "Kind"]
        ::core_lib::version   -> ["", "core_lib", "version"]
        Self::new (inside impl Point)  -> ["Point", "new"]
        <T as Trait>::method  -> None

    Args:
        node: A path node (scoped_identifier, scoped_type_identifier, identifier, etc.).

    Returns:
        The segments, or None when the path does not start with a name or "::"
        (a qualified type, etc.).
    """
    segment_list = _node_segment_list(node)
    if segment_list is None:
        return None
    return _impl_type_segment_list(node, segment_list)


def macro_path_list(node: Node) -> list[tuple[Node, list[str]]]:
    """Return the paths written directly in the arguments of a macro.

    The grammar leaves macro arguments as loose tokens; a run of names joined by "::"
    is read as a path. A run that starts with "::" has an empty first segment, and a
    first segment "Self" is replaced as in path_segment_list. A single name, and a run
    that starts with "::" right after ">" (Vec::<u8>::new, <T as Trait>::method), are
    left out.

    Examples:
        assert_eq!(crate::util::add(1, 2), 3)   -> [["crate", "util", "add"]]
        vec![Settings::new(), x]                -> [["Settings", "new"]]
        assert!(::core_lib::check())            -> [["", "core_lib", "check"]]

    Args:
        node: A token_tree node.

    Returns:
        (node of the first name, segments) of each path, in the order written.
    """
    path_list: list[tuple[Node, list[str]]] = []
    first_node: Node | None = None
    segment_list: list[str] = []
    is_after_separator = False
    is_after_generic = False
    previous_type = ""
    for child in [*node.children, None]:
        child_type = child.type if child is not None else ""
        is_name = child_type in _SEGMENT_NODE_TYPE_SET
        if child_type == "::":
            # A "::" with no name before it starts a path from a crate name
            if not segment_list:
                segment_list = [_ROOT_SEGMENT]
                is_after_generic = previous_type in _GENERIC_END_TYPE_SET
            is_after_separator = True
        elif is_name and is_after_separator:
            first_node = first_node or child
            segment_list.append(_segment_text(child))
            is_after_separator = False
        else:
            if len(segment_list) > 1 and first_node is not None and not is_after_generic:
                path_list.append((first_node, _impl_type_segment_list(first_node, segment_list)))
            first_node, segment_list = (child, [_segment_text(child)]) if is_name else (None, [])
            is_after_separator = False
            is_after_generic = False
        previous_type = child_type
    return path_list


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


def inline_module_name_list(node: Node) -> list[str]:
    """Return the names of the inline modules (mod name { ... }) around a node, outermost first."""
    name_list: list[str] = []
    current = node.parent
    while current is not None:
        if current.type == "mod_item" and current.child_by_field_name("body") is not None:
            name_node = current.child_by_field_name("name")
            name_list.append(_segment_text(name_node) if name_node is not None else "")
        current = current.parent
    name_list.reverse()
    return name_list


def _is_super_glob(node: Node) -> bool:
    """Return whether the argument of a use declaration takes over every name of the module around it.

    use super::*;  and  use super::{*, name};  do. A glob of any other path does not.

    Args:
        node: The argument node of a use_declaration.
    """
    if node.type == "use_wildcard":
        path_node_list = [child for child in node.named_children]
        return len(path_node_list) == 1 and path_node_list[0].type == "super"
    if node.type == "scoped_use_list":
        path_node = node.child_by_field_name("path")
        list_node = node.child_by_field_name("list")
        return (
            path_node is not None and path_node.type == "super" and list_node is not None
            and any(
                child.type == "use_wildcard" and not child.named_children
                for child in list_node.named_children
            )
        )
    return False


def inline_module_scope_list(root_node: Node) -> list[tuple[int, int, bool]]:
    """Return the lines of each inline module of a file and whether it has use super::*.

    Args:
        root_node: The AST root node of the file.

    Returns:
        (first line, last line, True when a use declaration written directly in the
        module takes over the names of the module around it) of each mod name { ... },
        in no particular order.
    """
    scope_list: list[tuple[int, int, bool]] = []
    node_stack = [root_node]
    while node_stack:
        node = node_stack.pop()
        node_stack.extend(node.children)
        if node.type != "mod_item":
            continue
        body_node = node.child_by_field_name("body")
        if body_node is None:
            continue
        is_open = any(
            child.type == "use_declaration"
            and (argument_node := child.child_by_field_name("argument")) is not None
            and _is_super_glob(argument_node)
            for child in body_node.named_children
        )
        scope_list.append((node.start_point[0] + 1, node.end_point[0] + 1, is_open))
    return scope_list


def file_module_path(segment_list: list[str], module_name_list: list[str]) -> list[str]:
    """Rewrite a path written inside inline modules into a path from the file's module.

    Examples (inside mod a { mod b { ... } }):
        ["super", "super", "Config"]      -> ["self", "Config"]
        ["super", "super", "super", "x"]  -> ["super", "x"]
        ["super", "helper"]               -> ["self", "a", "helper"]
        ["self", "helper"]                -> ["self", "a", "b", "helper"]
        ["crate", "util"]                 -> ["crate", "util"]

    Args:
        segment_list: Segments of the path as written.
        module_name_list: Names of the inline modules around the path
            (inline_module_name_list).

    Returns:
        The rewritten segments. A path that starts with neither self nor super, and an
        empty segment_list, are returned as they are.
    """
    if not module_name_list or not segment_list:
        return segment_list
    if segment_list[0] == "self":
        return ["self", *module_name_list, *segment_list[1:]]
    super_count = 0
    while super_count < len(segment_list) and segment_list[super_count] == "super":
        super_count += 1
    if super_count == 0:
        return segment_list
    depth = len(module_name_list)
    rest_list = segment_list[super_count:]
    if super_count <= depth:
        return ["self", *module_name_list[:depth - super_count], *rest_list]
    return ["super"] * (super_count - depth) + rest_list


def _use_scope_node(node: Node) -> Node | None:
    """Return the innermost block or inline module body around a node.

    Args:
        node: Any node of the file.

    Returns:
        The nearest block or body of a mod_item among the ancestors of node, None when
        node is at the top level of the file.
    """
    current = node.parent
    while current is not None:
        if current.type == "block":
            return current
        if current.type == "declaration_list" and current.parent.type == "mod_item":
            return current
        current = current.parent
    return None


def _line_tuple(node: Node) -> tuple[int, int]:
    """Return the (first line, last line) of a node (1-based)."""
    return node.start_point[0] + 1, node.end_point[0] + 1


def _scope_use_dict(
    scope_node: Node, use_cache_dict: UseCacheDict,
) -> dict[str, tuple[list[str], Node]]:
    """Return the names the use declarations written directly in a scope bind.

    Args:
        scope_node: A node returned by _use_scope_node.
        use_cache_dict: Node id of a scope -> return value of this function; filled
            on first use.

    Returns:
        A {bound name: (segments of its use path, use_declaration node)} dict. Glob
        imports and imports renamed to "_" bind no name.
    """
    use_dict = use_cache_dict.get(scope_node.id)
    if use_dict is not None:
        return use_dict
    use_dict = {}
    for child in scope_node.named_children:
        argument = child.child_by_field_name("argument") if child.type == "use_declaration" else None
        if argument is None:
            continue
        for segment_list, local_name in expand_use_tree(argument, []):
            if local_name and local_name != "_":
                use_dict[local_name] = (segment_list, child)
    use_cache_dict[scope_node.id] = use_dict
    return use_dict


def _scope_use(
    node: Node, name: str, use_cache_dict: UseCacheDict,
) -> tuple[list[str], Node] | None:
    """Return the use declaration that binds a name in the innermost scope around a node.

    Args:
        node: The node the name is written at; a use_declaration node itself is not
            looked at.
        name: The name.
        use_cache_dict: Cache of _scope_use_dict.

    Returns:
        (segments of the use path, use_declaration node), None when no block or inline
        module around the node binds the name.
    """
    scope_node = _use_scope_node(node)
    while scope_node is not None:
        scope_use = _scope_use_dict(scope_node, use_cache_dict).get(name)
        if scope_use is not None and scope_use[1].id != node.id:
            return scope_use
        scope_node = _use_scope_node(scope_node)
    return None


def _module_path(
    node: Node,
    segment_list: list[str],
    use_cache_dict: UseCacheDict,
) -> tuple[list[str], bool]:
    """Rewrite a path written at a node into a path from the file's module.

    The first segment is replaced by the path of the use declaration that binds it in
    a block or inline module around the node, the innermost first; the path is then
    rewritten by file_module_path from the place that declaration is written at.

    Examples (fn f() { use crate::config; config::load() }):
        ["config", "load"]   -> (["crate", "config", "load"], True)
        ["util", "log"]      -> (["util", "log"], False)

    Args:
        node: The node the path is written at.
        segment_list: Segments of the path as written.
        use_cache_dict: Cache of _scope_use_dict.

    Returns:
        (rewritten segments, whether a use declaration of a block or inline module
        bound the first segment).
    """
    origin_node = node
    is_scope = False
    for _ in range(_MAX_SCOPE_USE_HOP):
        if not segment_list or segment_list[0] in _ANCHOR_SEGMENT_SET:
            break
        scope_use = _scope_use(origin_node, segment_list[0], use_cache_dict)
        if scope_use is None:
            break
        segment_list = scope_use[0] + segment_list[1:]
        origin_node = scope_use[1]
        is_scope = True
    return file_module_path(segment_list, inline_module_name_list(origin_node)), is_scope


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


def _read_use_declaration(node: Node, use_cache_dict: UseCacheDict) -> list[ImportEntry]:
    """Read a use declaration into one import per imported name (expand_use_tree).

    A declaration written in a block or an inline module binds its names for the lines
    of that block or module.
    """
    argument = node.child_by_field_name("argument")
    if argument is None:
        return []
    scope_node = _use_scope_node(node)
    scope_line_tuple = _line_tuple(scope_node) if scope_node is not None else None
    entry_list: list[ImportEntry] = []
    for segment_list, local_name in expand_use_tree(argument, []):
        is_glob = segment_list[-1] == "*"
        module_list = _module_path(
            node, segment_list[:-1] if is_glob else segment_list, use_cache_dict,
        )[0]
        if not module_list:
            continue
        if is_glob:
            entry_list.append(("::".join(module_list), "*", None, scope_line_tuple))
            continue
        original = segment_list[-1] if local_name != segment_list[-1] else None
        entry_list.append((
            "::".join(module_list),
            None if local_name == "_" else local_name,
            None if local_name == "_" else original,
            scope_line_tuple,
        ))
    return entry_list


def _read_mod_declaration(node: Node, use_cache_dict: UseCacheDict) -> list[ImportEntry]:
    """Read a module declaration without a body outside inline modules."""
    declaration = mod_declaration(node)
    if declaration is None or inline_module_name_list(node):
        return []
    name, path_value = declaration
    return [(path_value or "self::" + name, name, None, None)]


def _read_extern_crate(node: Node, use_cache_dict: UseCacheDict) -> list[ImportEntry]:
    """Read an extern crate declaration (extern crate name; / extern crate name as alias;)."""
    name_node = node.child_by_field_name("name")
    alias_node = node.child_by_field_name("alias")
    if name_node is None or name_node.type != "identifier":
        return []
    crate_name = name_node.text.decode("utf-8")
    return [(crate_name, alias_node.text.decode("utf-8") if alias_node else crate_name, None, None)]


def _path_entry(
    node: Node, range_node: Node, segment_list: list[str], use_cache_dict: UseCacheDict,
) -> list[ImportEntry]:
    """Return the import of a path written outside use declarations.

    Args:
        node: The node the path starts at.
        range_node: The node whose lines the path is bound for when a use declaration
            of a block or inline module binds its first segment.
        segment_list: Segments of the path.
        use_cache_dict: Cache of _scope_use_dict.

    Returns:
        [(path from the file's module, path as written, None, lines or None)], empty
        for an empty path.
    """
    module_list, is_scope = _module_path(node, segment_list, use_cache_dict)
    if not module_list:
        return []
    return [(
        "::".join(module_list), "::".join(segment_list), None,
        _line_tuple(range_node) if is_scope else None,
    )]


def _read_path(node: Node, use_cache_dict: UseCacheDict) -> list[ImportEntry]:
    """Read the outermost path outside use declarations and visibility modifiers."""
    parent = node.parent
    if parent is not None and parent.type in PATH_NODE_TYPE_SET | {"visibility_modifier"}:
        return []
    if _is_inside_use_declaration(node):
        return []
    segment_list = path_segment_list(node)
    if not segment_list:
        return []
    return _path_entry(node, node, segment_list, use_cache_dict)


def _read_macro_argument(node: Node, use_cache_dict: UseCacheDict) -> list[ImportEntry]:
    """Read the paths written in the arguments of a macro (macro_path_list)."""
    entry_list: list[ImportEntry] = []
    for first_node, segment_list in macro_path_list(node):
        entry_list.extend(_path_entry(first_node, node, segment_list, use_cache_dict))
    return entry_list


# AST node type -> function reading the imports of that node: (node, use_cache_dict)
_IMPORT_READER_DICT = {
    "use_declaration": _read_use_declaration,
    "mod_item": _read_mod_declaration,
    "extern_crate_declaration": _read_extern_crate,
    "scoped_identifier": _read_path,
    "scoped_type_identifier": _read_path,
    "token_tree": _read_macro_argument,
}


def rust_import_list(node: Node, use_cache_dict: UseCacheDict | None = None) -> list[ImportEntry]:
    """Return the module paths a Rust node brings into use, for extract_imports.

    Handles:
        use_declaration          -> one entry per imported name (expand_use_tree)
        mod_item without a body  -> ("self::<name>", name), or (the #[path] value, name)
        extern_crate_declaration -> (crate name, bound name)
        scoped_identifier / scoped_type_identifier
                                 -> (path, path as written) for the outermost path
                                    outside use declarations and visibility modifiers
        token_tree               -> (path, path as written) for each path written in
                                    the arguments of a macro

    Args:
        node: A node captured by @path_item.
        use_cache_dict: Cache of the use declarations of the blocks and inline modules
            of the file the node belongs to, shared by the calls for one syntax tree;
            None for a call of its own.

    Returns:
        (module, name bound in the file, original name when renamed, lines) entries.
        module joins the segments with "::"; a glob import has the name "*" and an
        import renamed to "_" has the name None. A path is rewritten into a path from
        the file's module (_module_path). lines is the (first line, last line) the
        name is bound for: the block or inline module a use declaration is written
        in, the lines of a path (of the macro arguments it is written in) whose first
        segment such a use declaration binds, and None for the whole file.
    """
    reader = _IMPORT_READER_DICT.get(node.type)
    return reader(node, {} if use_cache_dict is None else use_cache_dict) if reader else []
