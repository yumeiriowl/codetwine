import logging
from collections import OrderedDict
from tree_sitter import Language, Node, Parser
from codetwine.config.settings import (
    BMS_EXT_SET,
    C_FAMILY_EXT_SET,
    COBOL_EXT_SET,
    EXT_TO_LANGUAGE_DICT,
    KOTLIN_EXT_SET,
    PARSE_CACHE_MAX_FILES,
    R_MARKDOWN_EXT_SET,
    language_ext,
)
from codetwine.extractors.bms_source import read_bms_source
from codetwine.extractors.cobol_source import CobolSource, read_cobol_source
from codetwine.parsers.c_macro import parse_c_family
from codetwine.parsers.kotlin_form import parse_kotlin
from codetwine.parsers.r_markdown import r_chunk_code
from codetwine.utils.file_utils import lone_cr_to_lf, read_source

logger = logging.getLogger(__name__)

# Name written after a piece of code to see whether the code ends where it stops (_is_whole_code)
_END_NAME = b"codetwine_end_of_code"


# Module-level cache for parse results, ordered from least to most recently used.
# One entry holds one file's whole syntax tree: a tree-sitter Node keeps its tree alive.
# The entry of a COBOL file or a BMS source holds its CobolSource.
# The number of entries is capped by PARSE_CACHE_MAX_FILES.
parse_cache: OrderedDict[str, tuple[Node | CobolSource, bytes]] = OrderedDict()

# The macro names read as spaces and the names written in their arguments
# (parse_c_family), by file: absolute path -> (name, line) pairs of the file's last parse
blank_macro_cache: dict[str, list[tuple[str, int]]] = {}


def read_utf8_content(file_path: str) -> bytes:
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


def _is_whole_code(code: bytes, language: Language) -> bool:
    """Return whether a piece of code ends where it stops: code written after it starts a new statement.

    A name is added on a line after the code; the code is whole when the parser reads
    that name as a statement of its own (not after an open "{", an open string or an
    operator at the end of the code).

    Args:
        code: The code of one R chunk.
        language: The tree-sitter Language of the code.

    Returns:
        True when the added name is the last top-level statement of the parse.
    """
    root_node = Parser(language).parse(code + b"\n" + _END_NAME + b"\n").root_node
    child_list = root_node.named_children
    return bool(child_list) and child_list[-1].type == "identifier" and child_list[-1].text == _END_NAME


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
    is the whole file. A chunk whose code does not end inside the chunk (an open "{", an
    operator at its end) is left out of the tree.

    For a C or C++ file a macro name written between a class keyword and a class name
    (class EXPORT Shape { ... }), and one written as a line of its own, next to the
    type of a declaration or after a declarator that the grammar misreads, is read as
    spaces (parse_c_family); the byte content is the whole file, and blank_macro_cache
    holds those names, the names written in their arguments, and their lines.

    For a Kotlin file the grammar does not read as it is, a constructor written on the
    line after its class name, a body written on one line and a call of get at the
    start of a row are read in another way (parse_kotlin); the byte content is the
    whole file.

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
    content = read_utf8_content(file_path)

    if ext in COBOL_EXT_SET:
        # Split the COBOL text into statements and parse each of them
        cobol_source = read_cobol_source(content.decode("utf-8"), EXT_TO_LANGUAGE_DICT[ext])
        parse_result = (cobol_source, content)
    elif ext in BMS_EXT_SET:
        # Read the maps of the BMS macros
        parse_result = (read_bms_source(content.decode("utf-8")), content)
    elif ext in R_MARKDOWN_EXT_SET:
        # Parse the R code chunks of the document, everything else blanked; a chunk
        # whose code runs on past its end is blanked as well
        language = EXT_TO_LANGUAGE_DICT[ext]
        chunk_code = r_chunk_code(content, lambda code: _is_whole_code(code, language))
        tree = Parser(language).parse(chunk_code)
        parse_result = (tree.root_node, content)
    elif ext in C_FAMILY_EXT_SET:
        # Parse with the macro names the grammar misreads blanked
        root_node, blank_macro_cache[file_path] = parse_c_family(content, EXT_TO_LANGUAGE_DICT[ext])
        parse_result = (root_node, content)
    elif ext in KOTLIN_EXT_SET:
        # Parse, reading the forms the grammar does not read in another way
        parse_result = (parse_kotlin(content, EXT_TO_LANGUAGE_DICT[ext]), content)
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
