import os
from tree_sitter import Node, Parser
from codetwine.config.settings import TREE_SITTER_LANGUAGES

# Refer to the extension -> Language object mapping from TREE_SITTER_LANGUAGES in settings.py.
_language_map = TREE_SITTER_LANGUAGES


# Module-level cache for parse results.
parse_cache: dict[str, tuple[Node, bytes]] = {}


def parse_file(file_path: str) -> tuple[Node, bytes]:
    """Read a file, parse it with tree-sitter, and return (AST root node, byte content).

    Parse results are cached at module level to avoid re-parsing the same file.

    Args:
        file_path: Absolute path of the file to parse.

    Returns:
        A (root_node, content) tuple.
    """
    # Return from cache if available
    if file_path in parse_cache:
        return parse_cache[file_path]

    # Get the corresponding language from the file extension
    ext = os.path.splitext(file_path)[1].lstrip(".")

    # Initialize the Parser with the Language object for this extension
    parser = Parser(_language_map[ext])

    # Read the file content in binary mode
    with open(file_path, "rb") as f:
        content = f.read()

    # Parse with tree-sitter to generate the AST
    tree = parser.parse(content)
    result = (tree.root_node, content)

    # Store in cache
    parse_cache[file_path] = result
    return result
