import os
from collections import OrderedDict
from codetwine.parsers.ts_parser import parse_cache, parse_file, read_utf8_content
from codetwine.extractors.definitions import (
    ATTACHED_DEFINITION_TYPE_SET,
    DefinitionInfo,
    extract_definitions,
    select_top_level_definitions,
)
from codetwine.extractors.r_source import r_definition_text
from codetwine.extractors.usages import symbol_part_list
from codetwine.config.settings import (
    BMS_DEFINITION_DICT,
    COBOL_DEFINITION_DICT,
    EXT_TO_DEFINITION_DICT,
    PARSE_CACHE_MAX_FILES,
    R_EXT_SET,
    language_ext,
)

# Cache of the definitions and the text of the files a definition was looked up in,
# ordered from least to most recently used: absolute path -> (definitions in line order,
# UTF-8 text of the file). The number of entries is capped by PARSE_CACHE_MAX_FILES.
_definition_cache: OrderedDict[str, tuple[list[DefinitionInfo], bytes]] = OrderedDict()

# Cache of the definitions of the files, which hold no syntax tree:
# absolute path -> definitions in line order
_definition_list_cache: dict[str, list[DefinitionInfo]] = {}


def clear_definition_source_cache() -> None:
    """Forget the definitions and text of every file."""
    _definition_cache.clear()
    _definition_list_cache.clear()


def file_definition_list(absolute_path: str, definition_dict: dict[str, str]) -> list[DefinitionInfo]:
    """Return the definitions of a file (extract_definitions), read once per file.

    The file is parsed the first time; the definitions are kept without its syntax
    tree until clear_definition_source_cache() is called.

    Args:
        absolute_path: Absolute path of the file.
        definition_dict: Definition node settings of its language.

    Returns:
        The definitions sorted by start_line. The list is shared: it is not to be modified.
    """
    definition_list = _definition_list_cache.get(absolute_path)
    if definition_list is None:
        definition_list = extract_definitions(parse_file(absolute_path)[0], definition_dict)
        _definition_list_cache[absolute_path] = definition_list
    return definition_list


def file_content(absolute_path: str) -> bytes:
    """Return the UTF-8 text of a file as parse_file() returns it, without parsing the file.

    Args:
        absolute_path: Absolute path of the file.

    Returns:
        The text of the entry of parse_cache when the file is in it, else the text
        read from the file (read_utf8_content).
    """
    cache_entry = parse_cache.get(absolute_path)
    return cache_entry[1] if cache_entry is not None else read_utf8_content(absolute_path)


def _file_definition(absolute_path: str, definition_dict: dict[str, str]) -> tuple[list[DefinitionInfo], bytes]:
    """Return the definitions and the UTF-8 text of a file, kept together while it stays in the cache.

    Args:
        absolute_path: Absolute path of the file.
        definition_dict: Definition node settings of its language.

    Returns:
        (definitions sorted by start_line, text of the file).
    """
    cache_entry = _definition_cache.get(absolute_path)
    if cache_entry is not None:
        _definition_cache.move_to_end(absolute_path)
        return cache_entry
    cache_entry = (
        file_definition_list(absolute_path, definition_dict), file_content(absolute_path),
    )
    _definition_cache[absolute_path] = cache_entry
    if PARSE_CACHE_MAX_FILES > 0:
        while len(_definition_cache) > PARSE_CACHE_MAX_FILES:
            _definition_cache.popitem(last=False)
    return cache_entry


def _is_inside(definition: DefinitionInfo, owner_list: list[DefinitionInfo]) -> bool:
    """Return whether a definition is written inside the lines of one of the owners."""
    return any(
        owner is not definition
        and owner.start_line <= definition.start_line
        and definition.end_line <= owner.end_line
        for owner in owner_list
    )


def _find_by_path(
    definition_list: list[DefinitionInfo], part_list: list[str],
) -> DefinitionInfo | None:
    """Follow the parts of a name through the definitions of a file.

    The first part is looked up among all definitions; each further part among the
    definitions written inside the definitions the part before it named (a class and
    its members; a Rust struct and each of its impl blocks). The walk stops at the
    first part that names nothing.

    Examples:
        ["Settings", "new"]   -> fn new inside impl Settings
        ["Mode", "Slow"]      -> enum Mode (Slow is no definition)
        ["Delete", "Handler", "Run"] -> Run inside Handler inside Delete

    Args:
        definition_list: Definitions of a file, sorted by start_line.
        part_list: The parts of the name.

    Returns:
        The definition the last part reached names: of several, one that defines the
        name itself (not in ATTACHED_DEFINITION_TYPE_SET) first, for the first part a
        top-level one first, then the first in line order. None when the first part
        names nothing.
    """
    last_definition: DefinitionInfo | None = None
    owner_list: list[DefinitionInfo] | None = None
    for part in part_list:
        match_list = [
            definition for definition in definition_list
            if definition.name == part
            and (owner_list is None or _is_inside(definition, owner_list))
        ]
        if not match_list:
            break
        own_list = [
            definition for definition in match_list
            if definition.type not in ATTACHED_DEFINITION_TYPE_SET
        ]
        if owner_list is None:
            # The first part names a top-level definition before a member of the same name
            top_level_id_set = {id(d) for d in select_top_level_definitions(definition_list)}
            own_list.sort(key=lambda definition: id(definition) not in top_level_id_set)
        last_definition = (own_list or match_list)[0]
        owner_list = match_list
    return last_definition


def find_definition(
    definition_list: list[DefinitionInfo], callee_name: str,
) -> DefinitionInfo | None:
    """Return the definition of a file that a name written with "." or "::" names.

    Tried in this order:
        1. A definition named callee_name as a whole (C++: Shape::area defined outside its class)
        2. The parts of the name followed from each part in turn (_find_by_path), so
           the leading parts that are no definitions of the file (modules, namespaces,
           a variable) are skipped

    Args:
        definition_list: Definitions of a file, sorted by start_line.
        callee_name: Name of the definition to find.

    Returns:
        The definition, or None when no part of the name is a definition of the file.
    """
    part_list = symbol_part_list(callee_name)
    if len(part_list) > 1:
        for definition in definition_list:
            if definition.name == callee_name:
                return definition
    for start in range(len(part_list)):
        definition = _find_by_path(definition_list, part_list[start:])
        if definition is not None:
            return definition
    return None


def file_definition(
    file_rel: str, name: str, project_dir: str,
) -> DefinitionInfo | None:
    """Return the definition of a file that a name names (find_definition).

    Args:
        file_rel: Path of the file relative to the project root.
        name: Name of the definition, its parts joined with "." or "::".
        project_dir: Absolute path to the project root.

    Returns:
        The definition. None when the name names no definition of the file, and for a
        file whose definitions are not read by extract_definitions() from a syntax
        tree (COBOL, BMS, R, a file without a language).
    """
    definition_tuple = source_definition(file_rel, name, project_dir)
    return definition_tuple[0] if definition_tuple is not None else None


def source_definition(
    file_rel: str, name: str, project_dir: str, start_line: int | None = None,
) -> tuple[DefinitionInfo, str] | None:
    """Return the definition of a file a name names, with its source text.

    With start_line, the parts of the name are tried from the last one and the first
    definition named like the part whose first line is start_line is taken; when
    there is none, and without start_line, the name is looked up by find_definition().

    Examples (class Gen<T> on line 7 and class Gen on line 11, each with Size):
        "Gen.Size", 13   -> Size inside class Gen
        "Gen", 11        -> class Gen
        "Gen.Count", 11  -> class Gen (Count is no definition)

    Args:
        file_rel: Path of the file relative to the project root.
        name: Name of the definition, its parts joined with "." or "::".
        project_dir: Absolute path to the project root.
        start_line: First line of the definition (1-based), None when it is not known.

    Returns:
        (definition, source text of the definition). None when the name names no
        definition of the file, and for a file whose definitions are not read by
        extract_definitions() from a syntax tree (COBOL, BMS, R, a file without a
        language).
    """
    absolute_path = os.path.join(project_dir, file_rel)
    file_ext = language_ext(absolute_path)
    definition_dict = EXT_TO_DEFINITION_DICT.get(file_ext)
    if (
        not definition_dict
        or definition_dict is COBOL_DEFINITION_DICT
        or definition_dict is BMS_DEFINITION_DICT
        or file_ext in R_EXT_SET
    ):
        return None
    definition_list, content = _file_definition(absolute_path, definition_dict)
    definition: DefinitionInfo | None = None
    if start_line is not None:
        for part in reversed(symbol_part_list(name)):
            definition = next(
                (
                    candidate for candidate in definition_list
                    if candidate.name == part and candidate.start_line == start_line
                ),
                None,
            )
            if definition is not None:
                break
    if definition is None:
        definition = find_definition(definition_list, name)
    if definition is None:
        return None
    return definition, content[definition.start_byte:definition.end_byte].decode("utf-8")


def extract_callee_source(
    callee_file_path: str,
    callee_name: str,
    project_dir: str,
) -> str | None:
    """Retrieve the definition source code for a specified name from the dependency target file.

    The name is looked up among the definitions of the file (extract_definitions) and
    the source text of the definition is returned. A name with several parts
    ("Settings::new", "Config.load", "config::Settings::new") names the definition of
    its last part inside the definitions of the parts before it; when the last part
    is no definition, the definition of the part before it is returned (the owner of
    an enum variant, the variable a method is called on). See find_definition.

    For a COBOL file the lines of the first definition with the name are returned;
    names are compared without regard to upper and lower case.
    For an R file the lines of the definition named callee_name as a whole are returned
    (r_source.r_definition_text); the name is not split into parts.

    Args:
        callee_file_path: Path of the dependency target file (relative to project root, e.g. "src/foo.py").
        callee_name: Name of the definition to retrieve (e.g. "parse_file", "helper.process",
                     "config::Settings::new").
        project_dir: Absolute path to the project root.

    Returns:
        The source code string. None if the definition is not found.
    """
    absolute_path = os.path.join(project_dir, callee_file_path)
    file_ext = language_ext(absolute_path)
    definition_dict = EXT_TO_DEFINITION_DICT.get(file_ext)
    if not definition_dict:
        return None

    if definition_dict is COBOL_DEFINITION_DICT or definition_dict is BMS_DEFINITION_DICT:
        return parse_file(absolute_path)[0].definition_source(callee_name)
    if file_ext in R_EXT_SET:
        callee_root, callee_content = parse_file(absolute_path)
        return r_definition_text(callee_root, callee_content, callee_name)

    definition_tuple = source_definition(callee_file_path, callee_name, project_dir)
    return definition_tuple[1] if definition_tuple is not None else None


def extract_definition_source(
    file_rel: str, name: str, start_line: int, project_dir: str,
) -> str | None:
    """Return the source text of the definition of a name that starts on a line.

    Args:
        file_rel: Path, relative to the project root, of a file whose definitions
            extract_definitions() reads from a syntax tree.
        name: Name of the definition, its parts joined with "." or "::".
        start_line: First line of the definition (1-based).
        project_dir: Absolute path to the project root.

    Returns:
        The source text of the definition source_definition() gives for the name and
        the line. The return value of extract_callee_source() when it gives none.
    """
    definition_tuple = source_definition(file_rel, name, project_dir, start_line)
    if definition_tuple is not None:
        return definition_tuple[1]
    return extract_callee_source(file_rel, name, project_dir)
