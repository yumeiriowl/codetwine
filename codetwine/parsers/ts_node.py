from collections.abc import Container, Iterator
from tree_sitter import Node


def node_text(node: Node) -> str:
    """Return the source text of a node."""
    return node.text.decode("utf-8")


def iter_node(root_node: Node) -> Iterator[Node]:
    """Yield every node of a tree in source order, each node before the nodes inside it.

    Args:
        root_node: The node to start from; it is yielded first.

    Yields:
        root_node and every node under it.
    """
    node_stack = [root_node]
    while node_stack:
        node = node_stack.pop()
        yield node
        node_stack.extend(reversed(node.children))


def has_ancestor(node: Node, node_type_set: Container[str]) -> bool:
    """Return whether a node is written inside a node of one of the given types.

    Args:
        node: The node to look from; its own type is not looked at.
        node_type_set: Node types to look for among the parents of the node.

    Returns:
        True when a parent, grandparent, ... of the node has one of the types.
    """
    current = node.parent
    while current is not None:
        if current.type in node_type_set:
            return True
        current = current.parent
    return False
