import os
import logging
from dataclasses import dataclass, field
from codetwine.parsers.ts_parser import parse_file
from codetwine.extractors.cobol_source import (
    CALL_KIND,
    CALL_TARGET_TYPE_TUPLE,
    COPY_KIND,
    CobolSource,
)
from codetwine.extractors.imports import ImportInfo, cobol_module_part
from codetwine.config.settings import COBOL_EXT_SET

logger = logging.getLogger(__name__)

# Extension of a copybook (lower case, without ".")
_COPYBOOK_EXT = "cpy"

# Cache of file indexes: project_dir -> (project file set the index was built from, index)
file_index_cache: dict[str, tuple[set[str], "CobolFileIndex"]] = {}


@dataclass
class CobolFileIndex:
    """The names the COBOL files of a project are found by."""

    # Upper-case file name, with and without its extension -> files in path order
    file_name_dict: dict[str, list[str]] = field(default_factory=dict)
    # Upper-case program name or ENTRY name -> files that define it, in path order
    program_dict: dict[str, list[str]] = field(default_factory=dict)
    # Files that define a program
    program_file_set: set[str] = field(default_factory=set)


def _cobol_source(file_rel: str, project_dir: str) -> CobolSource | None:
    """Parse a COBOL file of the project and return its CobolSource.

    Returns:
        The CobolSource. None when reading or parsing the file raises an exception;
        the exception is logged.
    """
    try:
        return parse_file(os.path.join(project_dir, file_rel))[0]
    except Exception as e:
        logger.warning(
            f"{file_rel} is left out of the COBOL file index: {type(e).__name__}: {e}"
        )
        return None


def _build_file_index(project_dir: str, project_file_set: set[str]) -> CobolFileIndex:
    """Parse the COBOL files of a project and build their index.

    Args:
        project_dir: Absolute path to the project root.
        project_file_set: Relative paths of the project files that have a language.

    Returns:
        The CobolFileIndex of the COBOL files in project_file_set. A file that cannot
        be read or parsed is not in it.
    """
    file_index = CobolFileIndex()
    cobol_file_list = sorted(
        file_rel for file_rel in project_file_set
        if os.path.splitext(file_rel)[1].lstrip(".") in COBOL_EXT_SET
    )
    for file_rel in cobol_file_list:
        cobol_source = _cobol_source(file_rel, project_dir)
        if cobol_source is None:
            continue

        file_name = os.path.basename(file_rel).upper()
        for name in dict.fromkeys((file_name, os.path.splitext(file_name)[0])):
            file_index.file_name_dict.setdefault(name, []).append(file_rel)
        for definition in cobol_source.definition_list:
            if definition.type in CALL_TARGET_TYPE_TUPLE:
                file_list = file_index.program_dict.setdefault(definition.name.upper(), [])
                if file_rel not in file_list:
                    file_list.append(file_rel)
                file_index.program_file_set.add(file_rel)
    return file_index


def _get_file_index(project_dir: str, project_file_set: set[str]) -> CobolFileIndex:
    """Return the file index of the project, building it on the first call.

    The index is kept in file_index_cache until the project file set changes or the
    cache is cleared.

    Args:
        project_dir: Absolute path to the project root.
        project_file_set: Relative paths of the project files that have a language.

    Returns:
        The CobolFileIndex of the COBOL files in project_file_set.
    """
    cache_entry = file_index_cache.get(project_dir)
    if cache_entry is not None:
        cache_file_set, file_index = cache_entry
        if cache_file_set is project_file_set:
            return file_index
        if cache_file_set == project_file_set:
            file_index_cache[project_dir] = (project_file_set, file_index)
            return file_index

    file_index = _build_file_index(project_dir, project_file_set)
    file_index_cache[project_dir] = (project_file_set, file_index)
    return file_index


def _copybook_path(
    name: str, library: str, current_file_rel: str, file_index: CobolFileIndex,
) -> str | None:
    """Return the file a COPY statement names.

    The files whose name, with or without its extension, is the file name of the
    copybook name are the candidates; upper and lower case are not told apart. Among
    several the first of this order is taken: a file whose path ends with the copybook
    name, a file in a directory named like the library, a file in the directory of the
    current file, a file with the copybook extension, a file that defines no program,
    the first in path order.

    Args:
        name: Name of the copybook (e.g. "CUSTREC", "ERRCODES.cpy", "copy/ERRCODES.cpy").
        library: Library name of COPY ... OF library; "" without one.
        current_file_rel: Relative path of the file the statement is written in.
        file_index: The file index of the project.

    Returns:
        Relative path of the file. None when no file has the name.
    """
    current_dir = os.path.dirname(current_file_rel)
    # "copy/../lib/X.cpy" -> "LIB/X.CPY"
    name_part_list = [
        part for part in name.replace("\\", "/").upper().split("/") if part not in ("", ".", "..")
    ]
    if not name_part_list:
        return None
    name_path = "/" + "/".join(name_part_list)
    candidate_list = [
        file_rel for file_rel in file_index.file_name_dict.get(name_part_list[-1], [])
        if file_rel != current_file_rel
    ]
    if not candidate_list:
        return None

    def order(file_rel: str) -> tuple[bool, bool, bool, bool, bool, str]:
        """Return the sort key of a candidate: the first in this order is taken."""
        directory = os.path.dirname(file_rel)
        ext = os.path.splitext(file_rel)[1].lstrip(".").lower()
        return (
            not ("/" + file_rel.upper()).endswith(name_path),
            not library or os.path.basename(directory).upper() != library.upper(),
            directory != current_dir,
            ext != _COPYBOOK_EXT,
            file_rel in file_index.program_file_set,
            file_rel,
        )

    return min(candidate_list, key=order)


def _program_path(name: str, current_file_rel: str, file_index: CobolFileIndex) -> str | None:
    """Return the file a CALL statement names.

    The files that define a program or an ENTRY with the name are the candidates;
    without one, the files that define a program and whose name without its extension
    is the name. Upper and lower case are not told apart. Among several the first of
    this order is taken: the current file, a file in the directory of the current
    file, the first in path order.

    Args:
        name: The program name.
        current_file_rel: Relative path of the file the statement is written in.
        file_index: The file index of the project.

    Returns:
        Relative path of the file. None when no file defines the name.
    """
    candidate_list = file_index.program_dict.get(name.upper())
    if not candidate_list:
        candidate_list = [
            file_rel for file_rel in file_index.file_name_dict.get(name.upper(), [])
            if file_rel in file_index.program_file_set
        ]
    if not candidate_list:
        return None

    current_dir = os.path.dirname(current_file_rel)
    return min(
        candidate_list,
        key=lambda file_rel: (
            file_rel != current_file_rel, os.path.dirname(file_rel) != current_dir, file_rel,
        ),
    )


def resolve_cobol_module_path(
    module: str,
    current_file_rel: str,
    project_file_set: set[str],
    project_dir: str,
) -> str | None:
    """Resolve the module string of a COBOL COPY or CALL statement to a project file.

    Examples:
        "COPY CUSTREC"             -> "copy/CUSTREC.cpy"
        "COPY CUSTREC OF COPYLIB"  -> "COPYLIB/CUSTREC.cpy"
        "CALL TAXCALC"             -> "src/TAXCALC.cbl" (the file with PROGRAM-ID. TAXCALC)

    Args:
        module: A module string of an ImportInfo of a COBOL file.
        current_file_rel: Relative path of the file the statement is written in.
        project_file_set: Set of file paths within the project.
        project_dir: Absolute path to the project root.

    Returns:
        Relative path of the file. None when no project file has the name, or when
        the statement names the current file itself.
    """
    kind, name, library = cobol_module_part(module)
    file_index = _get_file_index(project_dir, project_file_set)
    resolved = None
    if kind == COPY_KIND:
        resolved = _copybook_path(name, library, current_file_rel, file_index)
    elif kind == CALL_KIND:
        resolved = _program_path(name, current_file_rel, file_index)
    return resolved if resolved != current_file_rel else None


def replace_name(name: str, replacing_list: list[tuple[str, str, str]]) -> str:
    """Return a name of a copybook as a COPY ... REPLACING statement writes it.

    The first operand that matches the name is applied.

    Examples (operand -> name -> result):
        ("", ":PFX:", "WS")          ":PFX:-CUST-ID"  -> "WS-CUST-ID"
        ("", "OLD-NAME", "NEW-NAME") "OLD-NAME"       -> "NEW-NAME"
        ("LEADING", "IN", "OUT")     "IN-RECORD"      -> "OUT-RECORD"
        ("TRAILING", "X", "Y")       "RECORD-X"       -> "RECORD-Y"

    Args:
        name: A definition name of the copybook.
        replacing_list: (position, replaced text, replacement text) of each operand;
            position is "" / "LEADING" / "TRAILING".

    Returns:
        The name with the replacement, or the name itself when no operand matches.
    """
    upper_name = name.upper()
    for position, old_text, new_text in replacing_list:
        upper_old_text = old_text.upper()
        if position == "LEADING":
            if upper_name.startswith(upper_old_text):
                return new_text + name[len(old_text):]
        elif position == "TRAILING":
            if upper_name.endswith(upper_old_text):
                return name[:len(name) - len(old_text)] + new_text
        elif upper_name == upper_old_text:
            return new_text
        elif ":" in old_text and upper_old_text in upper_name:
            start = upper_name.index(upper_old_text)
            return name[:start] + new_text + name[start + len(old_text):]
    return name


def cobol_import_name_dict(
    import_info: ImportInfo,
    current_file_rel: str,
    project_file_set: set[str],
    project_dir: str,
) -> dict[str, str | None]:
    """Return the names a COBOL import binds in the file.

    CALL name                  -> {name: None}
    COPY name                  -> {"*": None}
    COPY name REPLACING ...    -> every definition name of the copybook as the
                                  statement writes it, with the name in the copybook
                                  for the ones the statement changes

    Args:
        import_info: An ImportInfo of a COBOL file.
        current_file_rel: Relative path of the file the import is written in.
        project_file_set: Set of file paths within the project.
        project_dir: Absolute path to the project root.

    Returns:
        A {bound name: name in the copybook, or None when it is the same} dict.
    """
    if not import_info.replacing_list:
        return {name: None for name in import_info.names}

    resolved = resolve_cobol_module_path(
        import_info.module, current_file_rel, project_file_set, project_dir,
    )
    cobol_source = _cobol_source(resolved, project_dir) if resolved else None
    if cobol_source is None:
        return {name: None for name in import_info.names}

    name_dict: dict[str, str | None] = {}
    for definition in cobol_source.definition_list:
        new_name = replace_name(definition.name, import_info.replacing_list)
        name_dict[new_name] = definition.name if new_name != definition.name else None
    return name_dict
