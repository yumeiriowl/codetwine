import logging
from collections import OrderedDict
from tree_sitter import Node, Parser
from codetwine.config.settings import (
    BMS_EXT_SET,
    COBOL_EXT_SET,
    EXT_TO_LANGUAGE_DICT,
    PARSE_CACHE_MAX_FILES,
    R_MARKDOWN_EXT_SET,
    language_ext,
)
from codetwine.extractors.bms_source import read_bms_source
from codetwine.extractors.cobol_source import CobolSource, read_cobol_source
from codetwine.parsers.r_markdown import r_chunk_code
from codetwine.utils.file_utils import lone_cr_to_lf, read_source

logger = logging.getLogger(__name__)


# Module-level cache for parse results, ordered from least to most recently used.
# One entry holds one file's whole syntax tree: a tree-sitter Node keeps its tree alive.
# The entry of a COBOL file or a BMS source holds its CobolSource.
# The number of entries is capped by PARSE_CACHE_MAX_FILES.
parse_cache: OrderedDict[str, tuple[Node | CobolSource, bytes]] = OrderedDict()


def _read_utf8_content(file_path: str) -> bytes:
    """Read a file with read_source() and return its text encoded as UTF-8.

    A lone "\\r" is turned into "\\n" (lone_cr_to_lf), so a row of the syntax tree is a line
    as line_list_of() splits the file; every byte stays where it is.

    A warning is logged when the file is read with invalid bytes replaced, and a debug
    line when it is read in an encoding other than UTF-8.

    Args:
        file_path: Absolute path of the file to read.

    Returns:
        The file's text as UTF-8 bytes.
    """
    text, encoding = read_source(file_path)
    if encoding == "":
        logger.warning(
            f"No encoding decodes {file_path}; it is read as UTF-8 with invalid bytes "
            f"replaced. Set SOURCE_ENCODING to the encoding it is stored in."
        )
    elif encoding != "utf-8":
        logger.debug(f"{file_path} is read as {encoding}")
    return lone_cr_to_lf(text).encode("utf-8")


def parse_file(file_path: str) -> tuple[Node | CobolSource, bytes]:
    """Read a file, parse it with tree-sitter, and return (AST root node, byte content).

    The file is decoded by read_source() and parsed as UTF-8, whatever encoding it is
    stored in; the byte content is that UTF-8 text, and the text of every node decodes
    as UTF-8. Line numbers are those of line_list_of() over the file.

    For a COBOL file the first element is a CobolSource (read_cobol_source) in place of
    the root node, and for a BMS source the CobolSource of its symbolic maps
    (read_bms_source); extract_definitions(), extract_imports() and extract_usages()
    take it as they take a root node. The language is the one language_ext() gives.

    For an R Markdown or Quarto file the root node is the tree of its R code chunks
    (r_chunk_code), each node at the position its code has in the file; the byte content
    is the whole file.

    Parse results are cached at module level; a file found in the cache is not parsed again.
    The cache holds at most PARSE_CACHE_MAX_FILES entries; when it is full, the least
    recently used entry is discarded and that file is parsed again the next time it is
    requested. PARSE_CACHE_MAX_FILES = 0 disables the limit.

    Args:
        file_path: Absolute path of the file to parse.

    Returns:
        A (root_node, content) tuple.
    """
    # Return from cache if available, marking the entry as most recently used
    cache_entry = parse_cache.get(file_path)
    if cache_entry is not None:
        parse_cache.move_to_end(file_path)
        return cache_entry

    # Get the corresponding language from the file extension
    ext = language_ext(file_path)

    # Read the file as UTF-8 bytes
    content = _read_utf8_content(file_path)

    if ext in COBOL_EXT_SET:
        # Split the COBOL text into statements and parse each of them
        cobol_source = read_cobol_source(content.decode("utf-8"), EXT_TO_LANGUAGE_DICT[ext])
        parse_result = (cobol_source, content)
    elif ext in BMS_EXT_SET:
        # Read the maps of the BMS macros
        parse_result = (read_bms_source(content.decode("utf-8")), content)
    elif ext in R_MARKDOWN_EXT_SET:
        # Parse the R code chunks of the document, everything else blanked
        tree = Parser(EXT_TO_LANGUAGE_DICT[ext]).parse(r_chunk_code(content))
        parse_result = (tree.root_node, content)
    else:
        # Parse with tree-sitter to generate the AST
        tree = Parser(EXT_TO_LANGUAGE_DICT[ext]).parse(content)
        parse_result = (tree.root_node, content)

    # Store in cache and drop the oldest entries once the limit is exceeded
    parse_cache[file_path] = parse_result
    if PARSE_CACHE_MAX_FILES > 0:
        while len(parse_cache) > PARSE_CACHE_MAX_FILES:
            parse_cache.popitem(last=False)
    return parse_result
