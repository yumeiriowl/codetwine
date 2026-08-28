import os
from collections import OrderedDict
from tree_sitter import Node, Parser
from codetwine.config.settings import PARSE_CACHE_MAX_FILES, TREE_SITTER_LANGUAGES

# Refer to the extension -> Language object mapping from TREE_SITTER_LANGUAGES in settings.py.
_language_map = TREE_SITTER_LANGUAGES


# Module-level cache for parse results, ordered from least to most recently used.
# One entry holds one file's whole syntax tree: a tree-sitter Node keeps its tree alive.
# The number of entries is capped by PARSE_CACHE_MAX_FILES.
parse_cache: OrderedDict[str, tuple[Node, bytes]] = OrderedDict()


def parse_file(file_path: str) -> tuple[Node, bytes]:
    """Read a file, parse it with tree-sitter, and return (AST root node, byte content).

    Parse results are cached at module level to avoid re-parsing the same file.
    The cache holds at most PARSE_CACHE_MAX_FILES entries; when it is full, the least
    recently used entry is discarded and that file is parsed again the next time it is
    requested. PARSE_CACHE_MAX_FILES = 0 disables the limit.

    Args:
        file_path: Absolute path of the file to parse.

    Returns:
        A (root_node, content) tuple.
    """
    # Return from cache if available, marking the entry as most recently used
    cached = parse_cache.get(file_path)
    if cached is not None:
        parse_cache.move_to_end(file_path)
        return cached

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

    # Store in cache and drop the oldest entries once the limit is exceeded
    parse_cache[file_path] = result
    if PARSE_CACHE_MAX_FILES > 0:
        while len(parse_cache) > PARSE_CACHE_MAX_FILES:
            parse_cache.popitem(last=False)
    return result
