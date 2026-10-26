import os
from collections import OrderedDict
from codetwine.parsers.ts_parser import parse_cache_dict, parse_file, read_utf8_content
from codetwine.extractors.definitions import (
    ATTACHED_DEFINITION_TYPE_SET,
    COMPANION_NAME,
    CONTAINER_DEFINITION_TYPE_SET,
    DefinitionInfo,
    extract_definitions,
    is_within,
    member_owner_dict,
    select_top_level_definitions,
)
from codetwine.extractors.r_source import r_definition_text
from codetwine.extractors.usages import symbol_part_list
from codetwine.utils.project_cache import identity_cache_tuple
from codetwine.config.settings import (
    COBOL_SOURCE_EXT_SET,
    EXT_TO_DEFINITION_DICT,
    PARSE_CACHE_MAX_FILES,
    R_EXT_SET,
    language_ext,
)

# Cache of the definitions and the text of the files a definition was looked up in,
# ordered from least to most recently used: absolute path -> (definitions in line order,
# UTF-8 text of the file). The number of entries is capped by PARSE_CACHE_MAX_FILES.
_definition_cache_dict: OrderedDict[str, tuple[list[DefinitionInfo], bytes]] = OrderedDict()

# Cache of the definitions of the files, which hold no syntax tree:
# absolute path -> definitions in line order
_definition_list_cache_dict: dict[str, list[DefinitionInfo]] = {}

# Lookups of the definition lists a definition was looked up in, by the id of the list:
# (the list, {name: its definitions in line order}, ids of its top-level definitions,
# ids of its definitions that are members of a class, {parts of the name of a container
# named with several parts: its definitions in line order})
_definition_index_cache_dict: dict[
    int,
    tuple[
        list[DefinitionInfo], dict[str, list[DefinitionInfo]], set[int], set[int],
        dict[tuple[str, ...], list[DefinitionInfo]],
    ],
] = {}


def clear_definition_source_cache() -> None:
    """Forget the definitions and text of every file."""
    _definition_cache_dict.clear()
    _definition_list_cache_dict.clear()
    _definition_index_cache_dict.clear()


def _definition_index(
    definition_list: list[DefinitionInfo],
) -> tuple[
    dict[str, list[DefinitionInfo]], set[int], set[int],
    dict[tuple[str, ...], list[DefinitionInfo]],
]:
    """Return the lookups of a definition list, built once per list.

    Args:
        definition_list: Definitions of a file, sorted by start_line.

    Returns:
        ({name: the definitions of that name in line order}, the ids of the
        definitions select_top_level_definitions() keeps, the ids of the definitions
        that are members of a class (member_owner_dict), {the parts of the name of a
        definition of CONTAINER_DEFINITION_TYPE_SET named with several parts (TS:
        namespace App.Models; C++: class Version::Iter defined outside its class): the
        definitions of that name in line order}).
    """
    cache_tuple = identity_cache_tuple(_definition_index_cache_dict, definition_list)
    if cache_tuple is None:
        name_dict: dict[str, list[DefinitionInfo]] = {}
        path_dict: dict[tuple[str, ...], list[DefinitionInfo]] = {}
        for definition in definition_list:
            name_dict.setdefault(definition.name, []).append(definition)
            if definition.type not in CONTAINER_DEFINITION_TYPE_SET:
                continue
            part_tuple = tuple(symbol_part_list(definition.name))
            if len(part_tuple) > 1:
                path_dict.setdefault(part_tuple, []).append(definition)
        cache_tuple = (
            definition_list, name_dict,
            {id(definition) for definition in select_top_level_definitions(definition_list)},
            set(member_owner_dict(definition_list)),
            path_dict,
        )
        _definition_index_cache_dict[id(definition_list)] = cache_tuple
    return cache_tuple[1], cache_tuple[2], cache_tuple[3], cache_tuple[4]


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
    definition_list = _definition_list_cache_dict.get(absolute_path)
    if definition_list is None:
        definition_list = extract_definitions(parse_file(absolute_path)[0], definition_dict)
        _definition_list_cache_dict[absolute_path] = definition_list
    return definition_list


def file_content(absolute_path: str) -> bytes:
    """Return the UTF-8 text of a file as parse_file() returns it, without parsing the file.

    Args:
        absolute_path: Absolute path of the file.

    Returns:
        The text of the entry of parse_cache_dict when the file is in it, else the text
        read from the file (read_utf8_content).
    """
    cache_tuple = parse_cache_dict.get(absolute_path)
    return cache_tuple[1] if cache_tuple is not None else read_utf8_content(absolute_path)


def _file_definition(absolute_path: str, definition_dict: dict[str, str]) -> tuple[list[DefinitionInfo], bytes]:
    """Return the definitions and the UTF-8 text of a file, kept together while it stays in the cache.

    Args:
        absolute_path: Absolute path of the file.
        definition_dict: Definition node settings of its language.

    Returns:
        (definitions sorted by start_line, text of the file).
    """
    cache_tuple = _definition_cache_dict.get(absolute_path)
    if cache_tuple is not None:
        _definition_cache_dict.move_to_end(absolute_path)
        return cache_tuple
    cache_tuple = (
        file_definition_list(absolute_path, definition_dict), file_content(absolute_path),
    )
    _definition_cache_dict[absolute_path] = cache_tuple
    if PARSE_CACHE_MAX_FILES > 0:
        while len(_definition_cache_dict) > PARSE_CACHE_MAX_FILES:
            _definition_cache_dict.popitem(last=False)
    return cache_tuple


def _is_inside(definition: DefinitionInfo, owner_list: list[DefinitionInfo]) -> bool:
    """Return whether a definition is written inside one of the owners (is_within)."""
    return any(
        owner is not definition and is_within(definition, owner)
        for owner in owner_list
    )


def _leading_match_list(
    name_dict: dict[str, list[DefinitionInfo]],
    path_dict: dict[tuple[str, ...], list[DefinitionInfo]],
    part_list: list[str],
    owner_list: list[DefinitionInfo] | None,
) -> tuple[list[DefinitionInfo], int]:
    """Return the definitions the leading parts of a name name, a container of the most parts first.

    Examples (namespace App.Models { class User }):
        ["App", "Models", "User"]  -> ([namespace App.Models], 2)
        ["User"]                   -> ([class User], 1)

    Args:
        name_dict: {name: the definitions of that name in line order} of a file.
        path_dict: {parts of the name of a container named with several parts: its
            definitions} of the file.
        part_list: The parts of the name, at least one.
        owner_list: The definitions the definitions are to be written inside
            (_is_inside), None for any definition.

    Returns:
        (the definitions in line order, the number of parts they are named by): the
        containers of path_dict named by the most leading parts, else the definitions
        of name_dict named by the first part; ([], 0) when neither names a definition.
    """
    for count in range(len(part_list) if path_dict else 1, 0, -1):
        if count == 1:
            candidate_list = name_dict.get(part_list[0], ())
        else:
            candidate_list = path_dict.get(tuple(part_list[:count]), ())
        match_list = [
            definition for definition in candidate_list
            if owner_list is None or _is_inside(definition, owner_list)
        ]
        if match_list:
            return match_list, count
    return [], 0


def _find_by_path(
    definition_list: list[DefinitionInfo], part_list: list[str],
) -> DefinitionInfo | None:
    """Follow the parts of a name through the definitions of a file.

    The first part is looked up among all definitions; each further part among the
    definitions written inside the definitions the part before it named (a class and
    its members; a Rust struct and each of its impl blocks). The walk stops at the
    first part that names nothing; a part COMPANION_NAME that names nothing is passed
    over (Kotlin: the companion object of a class written without a name). Where a
    run of parts names a container whose own name has those parts (TS: namespace
    App.Models), the longest such run is taken as one part (_leading_match_list).

    Examples:
        ["Settings", "new"]   -> fn new inside impl Settings
        ["Mode", "Slow"]      -> enum Mode (Slow is no definition)
        ["Delete", "Handler", "Run"] -> Run inside Handler inside Delete
        ["Shape", "Companion", "unit"] -> unit inside Shape
        ["App", "Models", "User"] -> User inside namespace App.Models

    Args:
        definition_list: Definitions of a file, sorted by start_line.
        part_list: The parts of the name.

    Returns:
        The definition the last part reached names: of several, one that defines the
        name itself (not in ATTACHED_DEFINITION_TYPE_SET) first, for the first part a
        top-level one first and a member of a class last, then the first in line
        order. None when the first part names nothing.
    """
    name_dict, top_level_id_set, member_id_set, path_dict = _definition_index(definition_list)
    if not part_list:
        return None
    match_list, count = _leading_match_list(name_dict, path_dict, part_list, None)
    if not match_list:
        return None
    own_list = [
        definition for definition in match_list
        if definition.type not in ATTACHED_DEFINITION_TYPE_SET
    ]
    # The first part names a top-level definition before one written inside another
    # definition, and a member of a class last
    own_list.sort(key=lambda definition: (
        id(definition) not in top_level_id_set, id(definition) in member_id_set,
    ))
    return _inner_path_definition(
        name_dict, path_dict, match_list, (own_list or match_list)[0], part_list[count:],
    )


def _inner_path_definition(
    name_dict: dict[str, list[DefinitionInfo]],
    path_dict: dict[tuple[str, ...], list[DefinitionInfo]],
    owner_list: list[DefinitionInfo],
    last_definition: DefinitionInfo,
    part_list: list[str],
) -> DefinitionInfo:
    """Follow parts of a name through the definitions written inside the owners.

    Each part, or the longest run of parts that names a container with a name of
    several parts (_leading_match_list), is looked up among the definitions written
    inside the definitions the part before it named; a part COMPANION_NAME that names
    nothing is passed over, and the walk stops at any other part that names nothing.

    Args:
        name_dict: {name: the definitions of that name in line order} of a file.
        path_dict: {parts of the name of a container named with several parts: its
            definitions} of the file.
        owner_list: The definitions the part before the first part named.
        last_definition: The definition the part before the first part names.
        part_list: The parts to follow.

    Returns:
        The definition the last part reached names: of several, one that defines the
        name itself (not in ATTACHED_DEFINITION_TYPE_SET) first, then the first in line
        order. last_definition when no part names a definition.
    """
    part_index = 0
    while part_index < len(part_list):
        match_list, count = _leading_match_list(
            name_dict, path_dict, part_list[part_index:], owner_list,
        )
        if not match_list:
            if part_list[part_index] == COMPANION_NAME:
                part_index += 1
                continue
            break
        own_list = [
            definition for definition in match_list
            if definition.type not in ATTACHED_DEFINITION_TYPE_SET
        ]
        last_definition = (own_list or match_list)[0]
        owner_list = match_list
        part_index += count
    return last_definition


def member_path_definition(
    definition_list: list[DefinitionInfo], first_definition: DefinitionInfo, part_list: list[str],
) -> DefinitionInfo:
    """Follow the parts written after a definition through the definitions inside it.

    Args:
        definition_list: Definitions of a file, sorted by start_line.
        first_definition: The definition the first part of a name names.
        part_list: The parts written after it.

    Returns:
        The definition the last part reached names: each part is looked up among the
        definitions written inside the definition the part before it named, and inside
        the definitions of ATTACHED_DEFINITION_TYPE_SET of its name (Rust: the impl
        blocks of a struct). A part COMPANION_NAME that names nothing is passed over
        (Kotlin: the companion object of a class written without a name), and the walk
        stops at any other part that names nothing (_inner_path_definition).
    """
    name_dict, _, _, path_dict = _definition_index(definition_list)
    owner_list = [first_definition, *(
        definition for definition in name_dict.get(first_definition.name, ())
        if definition.type in ATTACHED_DEFINITION_TYPE_SET and definition is not first_definition
    )]
    return _inner_path_definition(name_dict, path_dict, owner_list, first_definition, part_list)


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
        whole_list = _definition_index(definition_list)[0].get(callee_name)
        if whole_list:
            return whole_list[0]
    for start_index in range(len(part_list)):
        definition = _find_by_path(definition_list, part_list[start_index:])
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
        tree (COBOL, BMS, JCL, CSD, R, a file without a language).
    """
    definition_tuple = source_definition(file_rel, name, project_dir)
    return definition_tuple[0] if definition_tuple is not None else None


def source_definition(
    file_rel: str, name: str, project_dir: str, start_line: int | None = None,
    start_byte: int | None = None,
) -> tuple[DefinitionInfo, str] | None:
    """Return the definition of a file a name names, with its source text.

    With start_line, the parts of the name are tried from the last one, and the
    definitions named like the first part that has definitions whose first line is
    start_line are taken: of several, the one whose first byte is start_byte, else
    the one find_definition() gives for the name, else the first. When there is none,
    and without start_line, the name is looked up by find_definition().

    Examples (class Gen<T> on line 7 and class Gen on line 11, each with Size):
        "Gen.Size", 13   -> Size inside class Gen
        "Gen", 11        -> class Gen
        "Gen.Count", 11  -> class Gen (Count is no definition)
    (class A { int X; } class B { int X; } on line 1)
        "B.X", 1         -> X inside class B

    Args:
        file_rel: Path of the file relative to the project root.
        name: Name of the definition, its parts joined with "." or "::".
        project_dir: Absolute path to the project root.
        start_line: First line of the definition (1-based), None when it is not known.
        start_byte: First byte of the definition, None when it is not known; read
            with start_line only.

    Returns:
        (definition, source text of the definition). None when the name names no
        definition of the file, and for a file whose definitions are not read by
        extract_definitions() from a syntax tree (COBOL, BMS, JCL, CSD, R, a file
        without a language).
    """
    absolute_path = os.path.join(project_dir, file_rel)
    file_ext = language_ext(absolute_path)
    definition_dict = EXT_TO_DEFINITION_DICT.get(file_ext)
    if not definition_dict or file_ext in COBOL_SOURCE_EXT_SET or file_ext in R_EXT_SET:
        return None
    definition_list, content = _file_definition(absolute_path, definition_dict)
    if start_line is None:
        definition = find_definition(definition_list, name)
    else:
        definition = _line_definition(definition_list, name, start_line, start_byte)
    if definition is None:
        return None
    return definition, content[definition.start_byte:definition.end_byte].decode("utf-8")


def _line_definition(
    definition_list: list[DefinitionInfo], name: str, start_line: int,
    start_byte: int | None = None,
) -> DefinitionInfo | None:
    """Return the definition a name names among the definitions whose first line is a line.

    Args:
        definition_list: Definitions of a file, sorted by start_line.
        name: Name of the definition, its parts joined with "." or "::".
        start_line: First line of the definition (1-based).
        start_byte: First byte of the definition, None when it is not known.

    Returns:
        Of the definitions named like the last part of the name that has definitions
        whose first line is start_line, the one whose first byte is start_byte, else
        the one find_definition() gives for the name when it is one of them, else the
        first; the definition find_definition() gives when no part has such
        definitions.
    """
    name_dict = _definition_index(definition_list)[0]
    for part in reversed(symbol_part_list(name)):
        line_list = [
            candidate for candidate in name_dict.get(part, ())
            if candidate.start_line == start_line
        ]
        if len(line_list) == 1:
            return line_list[0]
        byte_definition = next(
            (
                candidate for candidate in line_list
                if start_byte is not None and candidate.start_byte == start_byte
            ),
            None,
        )
        if byte_definition is not None:
            return byte_definition
        if line_list:
            path_definition = find_definition(definition_list, name)
            return next(
                (candidate for candidate in line_list if candidate is path_definition),
                line_list[0],
            )
    return find_definition(definition_list, name)


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

    For a COBOL file, a BMS source, a JCL file or a CSD file the lines of the first
    definition with the name are returned; names are compared without regard to upper
    and lower case.
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

    if file_ext in COBOL_SOURCE_EXT_SET:
        return parse_file(absolute_path)[0].definition_source(callee_name)
    if file_ext in R_EXT_SET:
        callee_root, callee_content = parse_file(absolute_path)
        return r_definition_text(callee_root, callee_content, callee_name)

    definition_tuple = source_definition(callee_file_path, callee_name, project_dir)
    return definition_tuple[1] if definition_tuple is not None else None
