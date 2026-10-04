from tree_sitter import Node


def error_size(root_node: Node) -> tuple[int, int]:
    """Return how much of a tree the grammar did not read.

    Args:
        root_node: The AST root node of a file.

    Returns:
        (number of ERROR and MISSING nodes, bytes of the ERROR nodes that are inside no
        other ERROR node).
    """
    node_count = 0
    byte_count = 0
    node_stack = [(root_node, False)]
    while node_stack:
        node, is_inside_error = node_stack.pop()
        if node.is_missing:
            node_count += 1
            continue
        if node.type == "ERROR":
            node_count += 1
            if not is_inside_error:
                byte_count += node.end_byte - node.start_byte
            is_inside_error = True
        if node.has_error:
            node_stack.extend((child, is_inside_error) for child in node.children)
    return node_count, byte_count


def is_smaller_error(size_tuple: tuple[int, int], other_size_tuple: tuple[int, int]) -> bool:
    """Return whether the first error_size() has fewer nodes, or as many nodes and fewer bytes."""
    return size_tuple < other_size_tuple


def first_error_node(node: Node) -> Node | None:
    """Return the first ERROR or MISSING node of a node in the order of the text, None when it has none."""
    node_stack = [node]
    while node_stack:
        current = node_stack.pop()
        if current.type == "ERROR" or current.is_missing:
            return current
        if current.has_error:
            node_stack.extend(reversed(current.children))
    return None


def error_line_set(root_node: Node) -> set[int]:
    """Return the rows (0-based) of the ERROR nodes of a tree that are inside no other ERROR node."""
    row_set: set[int] = set()
    node_stack = [root_node]
    while node_stack:
        node = node_stack.pop()
        if node.type == "ERROR":
            row_set.update(range(node.start_point[0], node.end_point[0] + 1))
        elif node.has_error:
            node_stack.extend(node.children)
    return row_set
