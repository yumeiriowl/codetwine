import logging
from collections import OrderedDict
from tree_sitter import Language, Node, Parser
from codetwine.config.settings import (
    BMS_EXT_SET,
    C_FAMILY_EXT_SET,
    COBOL_EXT_SET,
    CSD_EXT_SET,
    EXT_TO_LANGUAGE_DICT,
    JCL_EXT_SET,
    KOTLIN_EXT_SET,
    PARSE_CACHE_MAX_FILES,
    R_MARKDOWN_EXT_SET,
    language_ext,
)
from codetwine.extractors.bms_source import read_bms_source
from codetwine.extractors.cobol_source import CobolSource, read_cobol_source
from codetwine.extractors.csd_source import read_csd_source
from codetwine.extractors.jcl_source import read_jcl_source
from codetwine.parsers.c_macro import parse_c_family
from codetwine.parsers.kotlin_form import parse_kotlin
from codetwine.parsers.r_markdown import r_chunk_code
from codetwine.utils.file_utils import detected_encoding, lone_cr_to_lf, read_source_info

logger = logging.getLogger(__name__)

# Name written after a piece of code to see whether the code ends where it stops (_is_whole_code)
_END_NAME = b"codetwine_end_of_code"


# Module-level cache for parse results, ordered from least to most recently used.
# One entry keeps one file's whole syntax tree in memory.
# The entry of a COBOL file, a BMS source, a JCL file or a CSD file holds its CobolSource.
# The number of entries is capped by PARSE_CACHE_MAX_FILES.
parse_cache_dict: OrderedDict[str, tuple[Node | CobolSource, bytes]] = OrderedDict()

# The macro names read as spaces and the names written in their arguments
# (parse_c_family), by file: absolute path -> (name, line) pairs of the file's last parse
blank_macro_cache: dict[str, list[tuple[str, int]]] = {}

# The encoding read_source() had to detect, by file: absolute path -> detected_encoding()
# of the file's last read by read_utf8_content
detect_encoding_cache_dict: dict[str, str | None] = {}


def read_utf8_content(file_path: str) -> bytes:
    """Read a file with read_source() and return its text encoded as UTF-8.

    A lone "\\r" is turned into "\\n" (lone_cr_to_lf); a row of the syntax tree is then a
    line as line_list_of() splits the file, and every byte stays where it is.

    A warning is logged when the file is read with invalid bytes replaced, and a debug
    line when it is read in an encoding other than UTF-8. The encoding read_source() had
    to detect is kept in detect_encoding_cache_dict.

    Args:
        file_path: Absolute path of the file to read.

    Returns:
        The file's text as UTF-8 bytes.
    """
    text, encoding, detect_encoding = read_source_info(file_path)
    detect_encoding_cache_dict[file_path] = detect_encoding
    if encoding == "":
        logger.warning(
            f"No encoding decodes {file_path}; it is read as UTF-8 with invalid bytes "
            f"replaced. Set SOURCE_ENCODING to the encoding it is stored in."
        )
    elif encoding != "utf-8":
        logger.debug(f"{file_path} is read as {encoding}")
    return lone_cr_to_lf(text).encode("utf-8")


def file_detect_encoding(file_path: str) -> str | None:
    """Return detected_encoding() of a file, from its last read when it is kept.

    The value of the last read by read_utf8_content is kept in
    detect_encoding_cache_dict.

    Args:
        file_path: Absolute path of the file.

    Returns:
        The value detected_encoding() returns for the file.

    Raises:
        OSError: When the file is read and cannot be read.
    """
    if file_path in detect_encoding_cache_dict:
        return detect_encoding_cache_dict[file_path]
    return detected_encoding(file_path)


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


def _read_cobol(file_path: str, content: bytes, ext: str) -> CobolSource:
    """Split the COBOL text into statements and parse each of them."""
    return read_cobol_source(content.decode("utf-8"), EXT_TO_LANGUAGE_DICT[ext])


def _read_bms(file_path: str, content: bytes, ext: str) -> CobolSource:
    """Read the maps of the BMS macros."""
    return read_bms_source(content.decode("utf-8"))


def _read_jcl(file_path: str, content: bytes, ext: str) -> CobolSource:
    """Read the jobs, procedures, steps and symbols of the JCL statements."""
    return read_jcl_source(content.decode("utf-8"))


def _read_csd(file_path: str, content: bytes, ext: str) -> CobolSource:
    """Read the resources and the names of the CSD commands."""
    return read_csd_source(content.decode("utf-8"))


def _read_r_markdown(file_path: str, content: bytes, ext: str) -> Node:
    """Parse the R code chunks of the document, everything else blanked.

    A chunk whose code runs on past its end is blanked as well.
    """
    language = EXT_TO_LANGUAGE_DICT[ext]
    chunk_code = r_chunk_code(content, lambda code: _is_whole_code(code, language))
    tree = Parser(language).parse(chunk_code)
    return tree.root_node


def _read_c_family(file_path: str, content: bytes, ext: str) -> Node:
    """Parse with the macro names the grammar misreads blanked; the names go to blank_macro_cache."""
    root_node, blank_macro_cache[file_path] = parse_c_family(content, EXT_TO_LANGUAGE_DICT[ext])
    return root_node


def _read_kotlin(file_path: str, content: bytes, ext: str) -> Node:
    """Parse, reading the forms the grammar does not read in another way."""
    return parse_kotlin(content, EXT_TO_LANGUAGE_DICT[ext])


def _read_code(file_path: str, content: bytes, ext: str) -> Node:
    """Parse with tree-sitter to generate the AST."""
    tree = Parser(EXT_TO_LANGUAGE_DICT[ext]).parse(content)
    return tree.root_node


# (extensions, function returning the first element of parse_file's result for a file of
# those extensions: (file_path, content, ext)), looked through in this order; _read_code
# reads a file of any other extension
_READER_TUPLE = (
    (COBOL_EXT_SET, _read_cobol),
    (BMS_EXT_SET, _read_bms),
    (JCL_EXT_SET, _read_jcl),
    (CSD_EXT_SET, _read_csd),
    (R_MARKDOWN_EXT_SET, _read_r_markdown),
    (C_FAMILY_EXT_SET, _read_c_family),
    (KOTLIN_EXT_SET, _read_kotlin),
)


def parse_file(file_path: str) -> tuple[Node | CobolSource, bytes]:
    """Read a file, parse it with tree-sitter, and return (AST root node, byte content).

    The file is decoded by read_source() and parsed as UTF-8, whatever encoding it is
    stored in; the byte content is that UTF-8 text, and the text of every node decodes
    as UTF-8. Line numbers are those of line_list_of() over the file.

    For a COBOL file the first element is a CobolSource (read_cobol_source) in place of
    the root node, for a BMS source the CobolSource of its symbolic maps
    (read_bms_source), for a JCL file its JclSource (read_jcl_source), and for a CSD
    file the JclSource of its CSD commands (read_csd_source); extract_definitions(),
    extract_imports() and extract_usages() take it as they take a root node. The
    language is the one language_ext() gives.

    For an R Markdown or Quarto file the root node is the tree of its R code chunks
    (r_chunk_code), each node at the position its code has in the file; the byte content
    is the whole file. A chunk whose code does not end inside the chunk (an open "{", an
    operator at its end) is left out of the tree.

    For a C or C++ file a macro name written between a class keyword and a class name
    (class EXPORT Shape { ... }, class EXPORT Shape;), and one written as a line of its
    own, next to the type of a declaration or after a declarator that the grammar
    misreads, is read as spaces (parse_c_family); the byte content is the whole file,
    and blank_macro_cache holds those names, the names written in their arguments, and
    their lines.

    For a Kotlin file the grammar does not read as it is, a constructor written on the
    line after its class name, a body written on one line, a call of get at the start
    of a row and the annotations of a getter or setter are read in another way
    (parse_kotlin); the byte content is the whole file.

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
    cache_tuple = parse_cache_dict.get(file_path)
    if cache_tuple is not None:
        parse_cache_dict.move_to_end(file_path)
        return cache_tuple

    # Read the file as UTF-8 bytes and parse it by the language of its extension
    ext = language_ext(file_path)
    content = read_utf8_content(file_path)
    reader = next((function for ext_set, function in _READER_TUPLE if ext in ext_set), _read_code)
    parse_result = (reader(file_path, content, ext), content)

    # Store in cache and drop the oldest entries once the limit is exceeded
    parse_cache_dict[file_path] = parse_result
    if PARSE_CACHE_MAX_FILES > 0:
        while len(parse_cache_dict) > PARSE_CACHE_MAX_FILES:
            parse_cache_dict.popitem(last=False)
    return parse_result
