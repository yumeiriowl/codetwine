import logging
from collections import OrderedDict
from tree_sitter import Language, Node, Parser, Query, QueryCursor
from codetwine.config.settings import (
    BMS_EXT_SET,
    C_FAMILY_EXT_SET,
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

# Name written after a piece of code to see whether the code ends where it stops (_is_whole_code)
_END_NAME = b"codetwine_end_of_code"

# A function definition whose return type is a class, struct or union written with its
# keyword and without a body, and whose declarator is a plain name (C / C++); the C
# grammar has no class_specifier
_CLASS_MACRO_QUERY_TUPLE = (
    "(function_definition type: (struct_specifier name: (_) @macro !body) declarator: (identifier))",
    "(function_definition type: (union_specifier name: (_) @macro !body) declarator: (identifier))",
    "(function_definition type: (class_specifier name: (_) @macro !body) declarator: (identifier))",
)

# Compiled queries of _CLASS_MACRO_QUERY_TUPLE: id of the Language -> Query
_class_macro_query_cache: dict[int, Query] = {}

# How many times a C / C++ file is parsed again with the macro names in front of its
# class names blanked
_CLASS_MACRO_PASS_MAX = 8


# Module-level cache for parse results, ordered from least to most recently used.
# One entry holds one file's whole syntax tree: a tree-sitter Node keeps its tree alive.
# The entry of a COBOL file or a BMS source holds its CobolSource.
# The number of entries is capped by PARSE_CACHE_MAX_FILES.
parse_cache: OrderedDict[str, tuple[Node | CobolSource, bytes]] = OrderedDict()

# The macro names read as spaces in front of class names (_parse_c_family), by file:
# absolute path -> (macro name, line) pairs of the file's last parse
class_macro_cache: dict[str, list[tuple[str, int]]] = {}


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


def _class_macro_query(language: Language) -> Query:
    """Return the query of _CLASS_MACRO_QUERY_TUPLE for a language, compiled once.

    A pattern that names a node type the grammar of the language lacks is left out.
    """
    query = _class_macro_query_cache.get(id(language))
    if query is None:
        pattern_list = []
        for pattern in _CLASS_MACRO_QUERY_TUPLE:
            try:
                Query(language, pattern)
            except Exception:
                continue
            pattern_list.append(pattern)
        query = Query(language, "\n".join(pattern_list))
        _class_macro_query_cache[id(language)] = query
    return query


def _class_macro_range_list(root_node: Node, language: Language) -> list[tuple[int, int]]:
    """Return the byte ranges of the macro names written between a class keyword and a class name.

    class EXPORT Shape : public Base { ... }; is read by the grammar as a function
    definition whose return type is "class EXPORT" and whose declarator is a plain
    name. No function is written that way, so the name of such a type is a macro.

    Args:
        root_node: The AST root node of a C / C++ file.
        language: The tree-sitter Language of the file.

    Returns:
        (start byte, end byte) of each such macro name.
    """
    capture_dict = QueryCursor(_class_macro_query(language)).captures(root_node)
    return [(node.start_byte, node.end_byte) for node in capture_dict.get("macro", [])]


def _parse_c_family(content: bytes, language: Language) -> tuple[Node, list[tuple[str, int]]]:
    """Parse a C / C++ file, reading a class declared with a macro in front of its name as a class.

    The macro names _class_macro_range_list() finds are replaced by spaces of the same
    length and the text is parsed again, until none is left or _CLASS_MACRO_PASS_MAX
    passes are done. Every other byte stays where it is.

    Args:
        content: The file's text as UTF-8 bytes.
        language: The tree-sitter Language of the file.

    Returns:
        (AST root node of the last parse, (macro name, line) of each name replaced, in
        line order).
    """
    parser = Parser(language)
    code = content
    root_node = parser.parse(code).root_node
    macro_list: list[tuple[str, int]] = []
    for _ in range(_CLASS_MACRO_PASS_MAX):
        range_list = _class_macro_range_list(root_node, language)
        if not range_list:
            break
        blank_code = bytearray(code)
        for start_byte, end_byte in range_list:
            macro_list.append((
                code[start_byte:end_byte].decode("utf-8"), code.count(b"\n", 0, start_byte) + 1,
            ))
            blank_code[start_byte:end_byte] = b" " * (end_byte - start_byte)
        code = bytes(blank_code)
        root_node = parser.parse(code).root_node
    return root_node, sorted(macro_list, key=lambda macro: macro[1])


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
    (class EXPORT Shape { ... }) is read as spaces (_parse_c_family); the byte content
    is the whole file, and class_macro_cache holds those names and their lines.

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
        # Parse with the macro names in front of class names blanked
        root_node, class_macro_cache[file_path] = _parse_c_family(content, EXT_TO_LANGUAGE_DICT[ext])
        parse_result = (root_node, content)
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
