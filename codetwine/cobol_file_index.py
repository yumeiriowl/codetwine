import os
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from codetwine.parsers.ts_parser import parse_file
from codetwine.extractors.cobol_source import (
    CALL_KIND,
    CALL_TARGET_TYPE_TUPLE,
    COPY_KIND,
    NAME_KIND_TUPLE,
    TRANSID_KIND,
    CobolDefinition,
    CobolReference,
    CobolSource,
    has_statement,
)
from codetwine.extractors.csd_source import TRANSACTION_TYPE
from codetwine.extractors.imports import cobol_module, cobol_module_part
from codetwine.extractors.jcl_source import (
    CSD_PROGRAM,
    INCLUDE_KIND,
    MAPSET_KIND,
    PROC_KIND,
    JclSource,
)
from codetwine.jcl_member import is_job_file, member_path, register_jcl_target
from codetwine.parsers.cobol_format import (
    FILE_UNIT,
    ITEM_UNIT,
    PROCEDURE_UNIT,
    split_cobol_source,
)
from codetwine.parsers.csd_command import TRANSACTION_RESOURCE
from codetwine.utils.file_utils import (
    add_file_name,
    add_unique,
    directory_distance,
    lower_ext,
    read_source,
)
from codetwine.utils.project_cache import project_cache_value
from codetwine.config.settings import (
    BMS_EXT_SET,
    COBOL_EXT_SET,
    CSD_EXT_SET,
    EXT_TO_LANGUAGE_DICT,
    JCL_EXT_SET,
    add_copy_target_ext,
    has_language,
    language_ext,
    set_copy_target_ext,
)

logger = logging.getLogger(__name__)

# Extension of a copybook (lower case, without ".")
_COPYBOOK_EXT = "cpy"
# Extensions of the files whose CSD commands can define a transaction
_CSD_SOURCE_EXT_SET = CSD_EXT_SET | JCL_EXT_SET

# Cache of file indexes: project_dir -> (project file set the index was built from, index)
file_index_cache_dict: dict[str, tuple[set[str], "CobolFileIndex"]] = {}

# Cache of the resolved references of COBOL, JCL and CSD files: absolute path ->
# (project file set they were resolved with, targets)
reference_target_cache_dict: dict[str, tuple[set[str], list["CobolReferenceTarget"]]] = {}


@dataclass
class CobolFileIndex:
    """The names the COBOL files, BMS sources, JCL files and CSD files of a project are found by."""

    # Upper-case file name, with and without its extension, and the names in the file's
    # copy_name_list (BMS: mapset names) -> files in path order
    file_name_dict: dict[str, list[str]] = field(default_factory=dict)
    # Upper-case program name or ENTRY name -> files that define it, in path order
    program_dict: dict[str, list[str]] = field(default_factory=dict)
    # Files that define a program
    program_file_set: set[str] = field(default_factory=set)
    # Upper-case file name of a JCL file, with and without its extension -> JCL files in
    # path order
    jcl_name_dict: dict[str, list[str]] = field(default_factory=dict)
    # JCL files whose first statement is a JOB or JOBGROUP statement
    jcl_job_file_set: set[str] = field(default_factory=set)
    # Upper-case mapset name of a BMS source (DFHMSD) -> BMS sources in path order
    mapset_file_dict: dict[str, list[str]] = field(default_factory=dict)
    # CSD files and JCL files in path order, read for transaction_dict
    csd_source_file_list: list[str] = field(default_factory=list)
    # Upper-case name of a transaction the CSD commands of a CSD file or JCL file define
    # -> those files in path order; built on first use (_transaction_dict)
    transaction_dict: dict[str, list[str]] | None = None
    # Copybooks recorded from files without a language whose text is not read as COBOL
    # (_is_cobol_text): a COPY statement leads to one only when it names its whole file name
    whole_name_copybook_set: set[str] = field(default_factory=set)


def _cobol_source(file_rel: str, project_dir: str) -> CobolSource | None:
    """Parse a COBOL file, BMS source, JCL file or CSD file of the project.

    Returns:
        Its CobolSource (a JclSource for a JCL or CSD file). None when reading or
        parsing the file raises an exception; the exception is logged.
    """
    try:
        return parse_file(os.path.join(project_dir, file_rel))[0]
    except Exception as e:
        logger.warning(f"{file_rel} is not read: {type(e).__name__}: {e}")
        return None


def _file_language_ext(file_rel: str, project_dir: str) -> str:
    """Return the extension whose language settings a project file is analyzed with."""
    return language_ext(os.path.join(project_dir, file_rel))


def _is_cobol_file(file_rel: str, project_dir: str) -> bool:
    """Return whether a project file is analyzed as COBOL."""
    return _file_language_ext(file_rel, project_dir) in COBOL_EXT_SET


def _is_cobol_of_other_ext(file_rel: str, project_dir: str) -> bool:
    """Return whether a project file is analyzed as COBOL without a COBOL extension."""
    return _is_cobol_file(file_rel, project_dir) and lower_ext(file_rel) not in COBOL_EXT_SET


def _path_part_list(path: str) -> list[str]:
    """Return the upper-case parts of a path of a COPY statement, without ".", ".." and "".

    Examples:
        "copy/../lib/X.cpy"  -> ["COPY", "LIB", "X.CPY"]
        "zlib\\\\dir"          -> ["ZLIB", "DIR"]
    """
    return [
        part for part in path.replace("\\", "/").upper().split("/") if part not in ("", ".", "..")
    ]


def _iter_program_definition(cobol_source: CobolSource | None) -> Iterator[CobolDefinition]:
    """Yield the programs and ENTRY names of a file in definition order; none for None."""
    if cobol_source is None:
        return
    for definition in cobol_source.definition_list:
        if definition.type in CALL_TARGET_TYPE_TUPLE:
            yield definition


def _add_source(file_index: CobolFileIndex, file_rel: str, cobol_source: CobolSource) -> None:
    """Index a COBOL file or BMS source by its file name, its copy_name_list and its programs."""
    add_file_name(file_index.file_name_dict, file_rel, cobol_source.copy_name_list)
    for mapset_name in cobol_source.copy_name_list:
        file_index.mapset_file_dict.setdefault(mapset_name, []).append(file_rel)
    for definition in _iter_program_definition(cobol_source):
        add_unique(file_index.program_dict, definition.name.upper(), file_rel)
        file_index.program_file_set.add(file_rel)


def _sort_file_index(file_index: CobolFileIndex) -> None:
    """Put the files of each name of a file index in path order."""
    for file_list in (
        *file_index.file_name_dict.values(),
        *file_index.program_dict.values(),
        *file_index.mapset_file_dict.values(),
    ):
        file_list.sort()


def _add_jcl_name(file_index: CobolFileIndex, project_dir: str, file_rel_set: set[str]) -> None:
    """Index the JCL files among project files by their file names, and record which are jobs."""
    for file_rel in sorted(file_rel_set):
        file_abs = os.path.join(project_dir, file_rel)
        if language_ext(file_abs) not in JCL_EXT_SET:
            continue
        add_file_name(file_index.jcl_name_dict, file_rel)
        if is_job_file(file_abs, is_strict=False):
            file_index.jcl_job_file_set.add(file_rel)


def _finish_file_index(
    file_index: CobolFileIndex, project_dir: str, file_rel_set: set[str],
) -> None:
    """Sort a file index, index the JCL files and list the CSD files and JCL files.

    Args:
        file_index: The index of the COBOL files and BMS sources.
        project_dir: Absolute path to the project root.
        file_rel_set: Relative paths of the project files that have a language.
    """
    _sort_file_index(file_index)
    _add_jcl_name(file_index, project_dir, file_rel_set)
    file_index.csd_source_file_list = [
        file_rel for file_rel in sorted(file_rel_set)
        if _file_language_ext(file_rel, project_dir) in _CSD_SOURCE_EXT_SET
    ]


def _transaction_dict(file_index: CobolFileIndex, project_dir: str) -> dict[str, list[str]]:
    """Return the transactions of the CSD commands of a file index's files, building them once.

    A JCL file is read only when its text holds DFHCSDUP (in any case).

    Returns:
        file_index.transaction_dict.
    """
    if file_index.transaction_dict is not None:
        return file_index.transaction_dict
    file_index.transaction_dict = {}
    for file_rel in file_index.csd_source_file_list:
        file_abs = os.path.join(project_dir, file_rel)
        if language_ext(file_abs) in JCL_EXT_SET and not _has_csd_program(file_abs):
            continue
        jcl_source = _cobol_source(file_rel, project_dir)
        if not isinstance(jcl_source, JclSource):
            continue
        for resource in jcl_source.csd_resource_list:
            if resource.type != TRANSACTION_RESOURCE:
                continue
            add_unique(file_index.transaction_dict, resource.name.upper(), file_rel)
    return file_index.transaction_dict


def _has_csd_program(file_abs: str) -> bool:
    """Return whether a file's text holds DFHCSDUP in any case; False when it cannot be read."""
    try:
        return CSD_PROGRAM in read_source(file_abs)[0].upper()
    except OSError as e:
        logger.warning(f"{file_abs} is not read: {type(e).__name__}: {e}")
        return False


def _source_file_list(project_dir: str, project_file_set: set[str]) -> list[str]:
    """Return the COBOL files and BMS sources among project files, in path order."""
    return sorted(
        file_rel for file_rel in project_file_set
        if _file_language_ext(file_rel, project_dir) in COBOL_EXT_SET | BMS_EXT_SET
    )


def _build_file_index(project_dir: str, project_file_set: set[str]) -> CobolFileIndex:
    """Parse the COBOL files and BMS sources of a project and build their index.

    The JCL files are indexed by their file names without being parsed; the CSD files
    and JCL files are read for their transactions on first use (_transaction_dict).

    Args:
        project_dir: Absolute path to the project root.
        project_file_set: Relative paths of the project files that have a language.

    Returns:
        The CobolFileIndex of the COBOL files, BMS sources, JCL files and CSD files in
        project_file_set. A COBOL file or BMS source that cannot be read or parsed is
        not in it.
    """
    file_index = _read_source_file(project_dir, project_file_set)[0]
    file_index.whole_name_copybook_set = {
        file_rel for file_rel in _source_file_list(project_dir, project_file_set)
        if _is_cobol_of_other_ext(file_rel, project_dir)
        and not _is_cobol_text(file_rel, project_dir)
    }
    _finish_file_index(file_index, project_dir, project_file_set)
    return file_index


def _get_file_index(project_dir: str, project_file_set: set[str]) -> CobolFileIndex:
    """Return the file index of the project, building it on the first call.

    The index is kept in file_index_cache_dict until the project file set changes or
    the cache is cleared.

    Args:
        project_dir: Absolute path to the project root.
        project_file_set: Relative paths of the project files that have a language.

    Returns:
        The CobolFileIndex of the COBOL files, BMS sources, JCL files and CSD files in
        project_file_set.
    """
    file_index = project_cache_value(file_index_cache_dict, project_dir, project_file_set)
    if file_index is not None:
        return file_index

    file_index = _build_file_index(project_dir, project_file_set)
    file_index_cache_dict[project_dir] = (project_file_set, file_index)
    return file_index


def _copybook_path(
    name: str,
    library: str,
    current_file_rel: str,
    file_index: CobolFileIndex,
    skip_file_set: set[str] | None = None,
) -> str | None:
    """Return the file a COPY statement names.

    The files whose name, with or without its extension, is the file name of the
    copybook name are the candidates, and a BMS source whose mapset has the name; upper
    and lower case are not told apart. A copybook of whole_name_copybook_set is a
    candidate only when the statement names its whole file name. Among several the
    first of this order is taken: a file whose path, with or without its extension,
    ends with the copybook name, a file whose directory path ends with the library, a
    file with a COBOL extension, a BMS source, a file that defines no program, the file
    nearest to the current file (directory_distance), a file with the copybook
    extension, the first in path order.

    Args:
        name: Name of the copybook (e.g. "CUSTREC", "ERRCODES.cpy", "copy/ERRCODES.cpy").
        library: Library name of COPY ... OF library, a name or a directory path (e.g.
            "COPYLIB", "lib/copy"); "" without one.
        current_file_rel: Relative path of the file the statement is written in.
        file_index: The file index of the project.
        skip_file_set: Relative paths of files that are not candidates.

    Returns:
        Relative path of the file. None when no file has the name.
    """
    name_part_list = _path_part_list(name)
    if not name_part_list:
        return None
    name_path = "/" + "/".join(name_part_list)
    library_part_list = _path_part_list(library)
    candidate_list = [
        file_rel for file_rel in file_index.file_name_dict.get(name_part_list[-1], [])
        if file_rel != current_file_rel
        and file_rel not in (skip_file_set or ())
        and (
            file_rel not in file_index.whole_name_copybook_set or _is_named_whole(name, file_rel)
        )
    ]
    if not candidate_list:
        return None

    def order(file_rel: str) -> tuple[bool, bool, bool, bool, bool, int, int, bool, str]:
        """Return the sort key of a candidate: the first in this order is taken."""
        ext = lower_ext(file_rel)
        upper_path = "/" + file_rel.upper()
        is_named_path = any(
            path.endswith(name_path) for path in (upper_path, os.path.splitext(upper_path)[0])
        )
        directory_part_list = _path_part_list(os.path.dirname(file_rel))
        is_library_path = bool(library_part_list) and (
            directory_part_list[-len(library_part_list):] == library_part_list
        )
        return (
            not is_named_path,
            not is_library_path,
            ext not in COBOL_EXT_SET,
            ext not in BMS_EXT_SET,
            file_rel in file_index.program_file_set,
            *directory_distance(file_rel, current_file_rel),
            ext != _COPYBOOK_EXT,
            file_rel,
        )

    return min(candidate_list, key=order)


def _program_path(name: str, current_file_rel: str, file_index: CobolFileIndex) -> str | None:
    """Return the file a CALL statement names.

    The files that define a program or an ENTRY with the name are the candidates;
    without one, the files that define a program and whose name without its extension
    is the name. Upper and lower case are not told apart. Among several the current file
    is taken, else the nearest to it (_nearest_file).

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

    if current_file_rel in candidate_list:
        return current_file_rel
    return _nearest_file(candidate_list, current_file_rel)


def _nearest_file(candidate_list: list[str], current_file_rel: str) -> str | None:
    """Return the candidate nearest to the current file in the directory tree.

    The candidates whose directory shares the most leading directories with that of the
    current file come first; among them, the one with the fewest directories after
    those (a file in the directory of the current file first), then the first in path
    order (directory_distance). None for no candidate.

    Examples:
        (["other/csd/A.csd", "app/csd/B.csd"], "app/cbl/P.cbl") -> "app/csd/B.csd"
        (["csd/a/M.bms", "csd/M.bms"], "csd/APP.csd")            -> "csd/M.bms"
    """
    return min(
        candidate_list,
        key=lambda file_rel: (*directory_distance(file_rel, current_file_rel), file_rel),
        default=None,
    )


def _mapset_path(name: str, current_file_rel: str, file_index: CobolFileIndex) -> str | None:
    """Return the BMS source of the mapset a CSD command defines.

    The BMS sources that define a mapset of the name (DFHMSD) are the candidates; without
    one, the BMS sources whose file name, with or without its extension, is the name.
    Upper and lower case are not told apart. Among several the nearest to the current
    file is taken (_nearest_file).

    Args:
        name: The mapset name.
        current_file_rel: Relative path of the file the command is written in.
        file_index: The file index of the project.

    Returns:
        Relative path of the BMS source. None when no BMS source has the name.
    """
    candidate_list = file_index.mapset_file_dict.get(name.upper()) or [
        file_rel for file_rel in file_index.file_name_dict.get(name.upper(), [])
        if lower_ext(file_rel) in BMS_EXT_SET
    ]
    return _nearest_file(candidate_list, current_file_rel)


def _transid_path(
    name: str, current_file_rel: str, file_index: CobolFileIndex, project_dir: str,
) -> str | None:
    """Return the CSD file or JCL file that defines the transaction of an EXEC CICS TRANSID.

    The files whose CSD commands define a transaction of the name are the candidates;
    upper and lower case are not told apart. Among several the nearest to the current
    file is taken (_nearest_file).

    Args:
        name: The transaction name.
        current_file_rel: Relative path of the file the command is written in.
        file_index: The file index of the project.
        project_dir: Absolute path to the project root.

    Returns:
        Relative path of the file. None when no file defines the transaction.
    """
    candidate_list = _transaction_dict(file_index, project_dir).get(name.upper(), [])
    return _nearest_file(candidate_list, current_file_rel)


def resolve_cobol_module_path(
    module: str,
    current_file_rel: str,
    project_file_set: set[str],
    project_dir: str,
) -> str | None:
    """Resolve the module string of a statement of a COBOL, JCL or CSD file to a file.

    Examples:
        "COPY CUSTREC"             -> "copy/CUSTREC.cpy"
        "COPY CUSTREC OF COPYLIB"  -> "COPYLIB/CUSTREC.cpy"
        "CALL TAXCALC"             -> "src/TAXCALC.cbl" (the file with PROGRAM-ID. TAXCALC)
        "TRANSID TX01"             -> "csd/APP.csd" (_transid_path)
        "PROC REPROC"              -> "proc/REPROC.prc" (member_path)
        "INCLUDE DBSTEPS"          -> "jcl/DBSTEPS.jcl" (member_path)
        "MAPSET CUSTMAP"           -> "bms/CUSTMAP.bms" (_mapset_path)

    Args:
        module: A module string of an ImportInfo of a COBOL, JCL or CSD file.
        current_file_rel: Relative path of the file the statement is written in.
        project_file_set: Set of file paths within the project.
        project_dir: Absolute path to the project root.

    Returns:
        Relative path of the file. None when no project file has the name (for a JCL
        procedure or member, no file that is not a job), or when the statement names the
        current file itself.
    """
    kind, name, library = cobol_module_part(module)
    file_index = _get_file_index(project_dir, project_file_set)
    target_rel = None
    if kind == COPY_KIND:
        target_rel = _copybook_path(name, library, current_file_rel, file_index)
    elif kind == CALL_KIND:
        target_rel = _program_path(name, current_file_rel, file_index)
    elif kind in (PROC_KIND, INCLUDE_KIND):
        target_rel = member_path(
            name, kind, current_file_rel, file_index.jcl_name_dict, file_index.jcl_job_file_set,
        )
    elif kind == MAPSET_KIND:
        target_rel = _mapset_path(name, current_file_rel, file_index)
    elif kind == TRANSID_KIND:
        target_rel = _transid_path(name, current_file_rel, file_index, project_dir)
    return target_rel if target_rel != current_file_rel else None


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


def _copy_statement_list(cobol_source: CobolSource) -> list[tuple[str, str]]:
    """Return (copybook name, library name) of each COPY statement and EXEC SQL INCLUDE."""
    return [
        (cobol_import.name, cobol_import.library)
        for cobol_import in cobol_source.import_list if cobol_import.kind == COPY_KIND
    ]


def _is_named_whole(name: str, file_rel: str) -> bool:
    """Return whether a copybook name of a COPY statement is the whole file name of a file.

    Examples:
        ("INCX.inc", "copy/INCX.inc")  -> True
        ("DATETIME", "copy/DATETIME")  -> True
        ("DFHAID", "notes/DFHAID.md")  -> False
    """
    return name.replace("\\", "/").split("/")[-1].upper() == os.path.basename(file_rel).upper()


def _is_cobol_text(file_rel: str, project_dir: str) -> bool:
    """Return whether the text of a file read as COBOL holds a data description or a statement.

    Returns:
        True when the text holds a data item, the description of a file, or a sentence
        of the procedure division the grammar reads a statement in (has_statement).
    """
    try:
        text = read_source(os.path.join(project_dir, file_rel))[0]
        unit_list = split_cobol_source(text).unit_list
    except Exception as e:
        logger.warning(f"{file_rel} is not read as COBOL: {type(e).__name__}: {e}")
        return False
    if any(unit.kind in (ITEM_UNIT, FILE_UNIT) for unit in unit_list):
        return True
    language = EXT_TO_LANGUAGE_DICT[_COPYBOOK_EXT]
    return any(
        unit.kind == PROCEDURE_UNIT and has_statement(unit, language) for unit in unit_list
    )


def _read_source_file(
    project_dir: str, language_file_set: set[str],
) -> tuple[CobolFileIndex, dict[str, list[tuple[str, str]]]]:
    """Parse the COBOL files and BMS sources among project files once and index them.

    Args:
        project_dir: Absolute path to the project root.
        language_file_set: Relative paths of the project files that have a language.

    Returns:
        A (file index of the COBOL files and BMS sources, {relative path of a COBOL file:
        (copybook name, library name) of its COPY statements}) tuple. A file that cannot
        be read or parsed is in neither.
    """
    file_index = CobolFileIndex()
    # Relative path of a COBOL file -> (copybook name, library name) of its COPY statements
    copy_statement_dict: dict[str, list[tuple[str, str]]] = {}
    for file_rel in _source_file_list(project_dir, language_file_set):
        cobol_source = _cobol_source(file_rel, project_dir)
        if cobol_source is None:
            continue
        _add_source(file_index, file_rel, cobol_source)
        if _is_cobol_file(file_rel, project_dir):
            copy_statement_dict[file_rel] = _copy_statement_list(cobol_source)
    return file_index, copy_statement_dict


def _lookup_index(file_index: CobolFileIndex, other_file_set: set[str]) -> CobolFileIndex:
    """Return a file index that finds the files without a language by their file names too.

    Args:
        file_index: The index of the COBOL files and BMS sources. Its file names are
            copied; its programs are shared, not copied.
        other_file_set: Relative paths of the files without a language.

    Returns:
        A CobolFileIndex with the file names and programs only, for _copybook_path.
    """
    lookup_index = CobolFileIndex(
        file_name_dict={
            name: list(file_list) for name, file_list in file_index.file_name_dict.items()
        },
        program_dict=file_index.program_dict,
        program_file_set=file_index.program_file_set,
    )
    for file_rel in other_file_set:
        add_file_name(lookup_index.file_name_dict, file_rel)
    _sort_file_index(lookup_index)
    return lookup_index


def _follow_copy_statement(
    project_dir: str,
    file_index: CobolFileIndex,
    lookup_index: CobolFileIndex,
    copy_statement_dict: dict[str, list[tuple[str, str]]],
    other_file_set: set[str],
    copy_target_ext_dict: dict[str, str],
) -> None:
    """Follow the COPY statements of the COBOL files to the files without a language.

    The files are taken in path order, then each copybook in the order it is recorded. A
    file without a language a COPY statement resolves to is recorded as a copybook when
    the statement names it with its whole file name or its text is read as COBOL
    (_is_cobol_text); otherwise the statement is resolved again without that file, also
    when another statement has recorded it. A recorded copybook is parsed and indexed,
    and its COPY statements are followed too.

    Args:
        project_dir: Absolute path to the project root.
        file_index: The index of the COBOL files and BMS sources; the copybooks are added,
            and to whole_name_copybook_set those whose text is not read as COBOL.
        lookup_index: The index the COPY statements are resolved with (_lookup_index).
        copy_statement_dict: Relative path of a COBOL file -> (copybook name, library
            name) of its COPY statements; the statements of the copybooks are added.
        other_file_set: Relative paths of the files without a language.
        copy_target_ext_dict: {relative path: extension} of the files recorded; each
            copybook is added with "cpy" and recorded with add_copy_target_ext().
    """
    # Relative path of a file without a language -> whether its text is read as COBOL
    is_cobol_text_dict: dict[str, bool] = {}

    def is_copy_target(name: str, file_rel: str) -> bool:
        """Return whether a file without a language is the copybook a COPY statement names."""
        if file_rel not in is_cobol_text_dict:
            is_cobol_text_dict[file_rel] = _is_cobol_text(file_rel, project_dir)
        return _is_named_whole(name, file_rel) or is_cobol_text_dict[file_rel]

    file_queue = sorted(copy_statement_dict)
    while file_queue:
        file_rel = file_queue.pop(0)
        for name, library in copy_statement_dict[file_rel]:
            skip_file_set: set[str] = set()
            copybook_rel = _copybook_path(name, library, file_rel, lookup_index)
            while copybook_rel in other_file_set and not is_copy_target(name, copybook_rel):
                skip_file_set.add(copybook_rel)
                copybook_rel = _copybook_path(name, library, file_rel, lookup_index, skip_file_set)
            if copybook_rel not in other_file_set or copybook_rel in copy_target_ext_dict:
                continue
            copy_target_ext_dict[copybook_rel] = _COPYBOOK_EXT
            add_copy_target_ext(project_dir, copybook_rel, _COPYBOOK_EXT)
            if not is_cobol_text_dict[copybook_rel]:
                file_index.whole_name_copybook_set.add(copybook_rel)
            cobol_source = _cobol_source(copybook_rel, project_dir)
            if cobol_source is not None:
                _add_source(file_index, copybook_rel, cobol_source)
                copy_statement_dict[copybook_rel] = _copy_statement_list(cobol_source)
                file_queue.append(copybook_rel)


def register_copy_target(project_dir: str, file_rel_list: list[str]) -> dict[str, str]:
    """Find the files without a language that are JCL or COBOL copybooks, and record them.

    The JCL files among them are found first (register_jcl_target). A COPY statement or
    EXEC SQL INCLUDE of a COBOL file that resolves to a file without a language
    (_copybook_path, with those files as candidates too) makes that file a COBOL
    copybook, whatever its extension, when the statement names it with its whole file
    name, or when its text read as COBOL holds a data item, the description of a file
    or a statement of the procedure division (_is_cobol_text); otherwise the statement
    is resolved again without that file. A copybook recorded for its whole file name
    alone is a candidate later only for the statements that name it so. The COPY
    statements of such a file are followed as well. The files are recorded with
    set_copy_target_ext() and add_copy_target_ext(), replacing the ones recorded before
    for the project.

    Processing flow:
    1. Record the JCL files among the files without a language
    2. Parse the COBOL files and BMS sources once: index them and keep the COPY
       statements of the COBOL files
    3. Index the files without a language by their file name as well
    4. Follow the COPY statements, recording and parsing each file without a language
       they lead to
    5. Keep the index of the files with a language in file_index_cache_dict

    Args:
        project_dir: Absolute path to the project root.
        file_rel_list: Relative paths of the project files.

    Returns:
        {relative path: "jcl" for a JCL file, "cpy" for a copybook} of the files recorded.
    """
    set_copy_target_ext(project_dir, {})
    other_file_set = {
        file_rel for file_rel in file_rel_list
        if not has_language(os.path.join(project_dir, file_rel))
    }
    if not other_file_set:
        return {}
    language_file_set = set(file_rel_list) - other_file_set

    # == Step 1: JCL files ====================================================
    copy_target_ext_dict = register_jcl_target(project_dir, other_file_set, language_file_set)
    other_file_set -= set(copy_target_ext_dict)
    language_file_set |= set(copy_target_ext_dict)

    # == Step 2: COBOL files and BMS sources ==================================
    file_index, copy_statement_dict = _read_source_file(project_dir, language_file_set)

    # == Step 3: Files without a language =====================================
    lookup_index = _lookup_index(file_index, other_file_set)

    # == Step 4: COPY statements ==============================================
    _follow_copy_statement(
        project_dir, file_index, lookup_index, copy_statement_dict, other_file_set,
        copy_target_ext_dict,
    )

    # == Step 5: Index of the files with a language ===========================
    _finish_file_index(file_index, project_dir, language_file_set)
    file_index_cache_dict[project_dir] = (language_file_set | set(copy_target_ext_dict), file_index)
    return copy_target_ext_dict


@dataclass
class CobolReferenceTarget:
    """The definition one reference of a COBOL, JCL or CSD file resolves to."""

    # Name of the definition: a data name or procedure name as the referring file writes
    # it (after REPLACING), a program, ENTRY, transaction, procedure, step, symbol or
    # resource name as its definition writes it; the name as referred to without a
    # definition
    name: str
    line: int        # Line of the reference (1-based)
    file_rel: str    # Relative path of the file with the definition
    # The definition; None when the program file of a CALL is not read, for the
    # procedure of a JCL file that does not start with a PROC statement, for an INCLUDE
    # member and a mapset, and for a transaction a JCL file defines
    definition: CobolDefinition | None


@dataclass
class _Candidate:
    """A file whose definitions a COBOL file can refer to: itself, or a copybook it includes."""

    file_rel: str
    cobol_source: CobolSource
    # Line of the COPY statement in the referring file; 0 for the referring file itself
    copy_line: int = 0
    # REPLACING operands of the COPY statement
    replacing_list: list[tuple[str, str, str]] = field(default_factory=list)
    # Upper-case local name -> definitions with it other than programs and ENTRY
    # names, built on first use
    definition_dict: dict[str, list[CobolDefinition]] | None = None

    def local_name(self, definition: CobolDefinition) -> str:
        """Return the name of a definition as the referring file writes it."""
        return replace_name(definition.name, self.replacing_list)

    def find_definition_list(self, name: str) -> list[CobolDefinition]:
        """Return the definitions a data name or procedure name can refer to.

        Args:
            name: Upper-case name.

        Returns:
            The definitions whose local name is the name, in line order; a program or
            an ENTRY name is not among them.
        """
        if self.definition_dict is None:
            self.definition_dict = {}
            for definition in self.cobol_source.definition_list:
                if definition.type in CALL_TARGET_TYPE_TUPLE:
                    continue
                local_name = self.local_name(definition).upper()
                self.definition_dict.setdefault(local_name, []).append(definition)
        return self.definition_dict.get(name, [])


def _add_copybook_candidate(
    candidate_list: list[_Candidate],
    holder: _Candidate,
    open_file_set: set[str],
    project_file_set: set[str],
    project_dir: str,
) -> None:
    """Add the copybooks the COPY statements of a file bring in, in line order.

    Each copybook is followed by the copybooks its own COPY statements bring in. A
    copybook of a copybook has the line of the COPY statement of the referring file,
    and the REPLACING operands of its own COPY statement before those of the
    statements that lead to it.

    Args:
        candidate_list: The candidates of the referring file; the copybooks are appended.
        holder: The file whose COPY statements are read: the referring file or a copybook.
        open_file_set: Relative paths of the holder and of the files that lead to it.
        project_file_set: Relative paths of the project files that have a language.
        project_dir: Absolute path to the project root.
    """
    for cobol_import in holder.cobol_source.import_list:
        if cobol_import.kind != COPY_KIND:
            continue
        module = cobol_module(COPY_KIND, cobol_import.name, cobol_import.library)
        copybook_rel = resolve_cobol_module_path(
            module, holder.file_rel, project_file_set, project_dir,
        )
        if not copybook_rel or copybook_rel in open_file_set:
            continue
        copy_source = _cobol_source(copybook_rel, project_dir)
        if copy_source is None:
            continue
        candidate = _Candidate(
            copybook_rel, copy_source, holder.copy_line or cobol_import.line,
            [*cobol_import.replacing_list, *holder.replacing_list],
        )
        candidate_list.append(candidate)
        _add_copybook_candidate(
            candidate_list, candidate, open_file_set | {copybook_rel}, project_file_set,
            project_dir,
        )


def _candidate_list(
    cobol_source: CobolSource, file_rel: str, project_file_set: set[str], project_dir: str,
) -> list[_Candidate]:
    """Return the file itself, then each copybook its COPY statements bring in, in line order.

    The copybooks a copybook brings in come right after it (_add_copybook_candidate).
    """
    candidate_list = [_Candidate(file_rel, cobol_source)]
    _add_copybook_candidate(
        candidate_list, candidate_list[0], {file_rel}, project_file_set, project_dir,
    )
    return candidate_list


def _is_under(
    qualifier: str, definition: CobolDefinition, candidate: _Candidate, own_candidate: _Candidate,
) -> bool:
    """Return whether a qualifier names a definition that holds a definition of a candidate.

    Args:
        qualifier: Upper-case qualifier.
        definition: A definition of the candidate.
        candidate: The file with the definition.
        own_candidate: The referring file.

    Returns:
        True when another definition of the candidate with that local name holds the
        definition's line range, or, for a copybook, a definition of the referring file
        with that name holds the line of the COPY statement.
    """
    if any(
        holder is not definition
        and holder.start_line <= definition.start_line
        and definition.end_line <= holder.end_line
        for holder in candidate.find_definition_list(qualifier)
    ):
        return True
    return bool(candidate.copy_line) and any(
        holder.start_line <= candidate.copy_line <= holder.end_line
        for holder in own_candidate.find_definition_list(qualifier)
    )


def _target_under_qualifier(
    name: str, qualifier_tuple: tuple[str, ...], candidate_list: list[_Candidate],
) -> tuple[_Candidate, CobolDefinition] | None:
    """Return the definition a qualified name (NAME OF Q1 IN Q2 ...) refers to.

    A definition matches when every qualifier names a definition that holds it (_is_under).

    Args:
        name: Upper-case name.
        qualifier_tuple: Upper-case qualifiers.
        candidate_list: The referring file, then its copybooks.

    Returns:
        (candidate, definition) of the first match in candidate_list order, or None.
    """
    own_candidate = candidate_list[0]
    for candidate in candidate_list:
        for definition in candidate.find_definition_list(name):
            if all(
                _is_under(qualifier, definition, candidate, own_candidate)
                for qualifier in qualifier_tuple
            ):
                return candidate, definition
    return None


def _import_target_dict(
    cobol_source: CobolSource, file_rel: str, project_file_set: set[str], project_dir: str,
) -> dict[tuple[str, str, int], tuple[str, str]]:
    """Return the file each CALL statement and EXEC CICS TRANSID of a COBOL file leads to.

    Returns:
        {(CALL_KIND or TRANSID_KIND, upper-case name, line of the statement): (name as
        written, relative path of the file)} of each one that resolves to another file.
    """
    import_target_dict: dict[tuple[str, str, int], tuple[str, str]] = {}
    for cobol_import in cobol_source.import_list:
        if cobol_import.kind not in NAME_KIND_TUPLE:
            continue
        module = cobol_module(cobol_import.kind, cobol_import.name)
        target_rel = resolve_cobol_module_path(module, file_rel, project_file_set, project_dir)
        if target_rel:
            import_target_dict.setdefault(
                (cobol_import.kind, cobol_import.name.upper(), cobol_import.line),
                (cobol_import.name, target_rel),
            )
    return import_target_dict


def _program_definition(cobol_source: CobolSource | None, name: str) -> CobolDefinition | None:
    """Return the first program or ENTRY of a file with an upper-case name, or None."""
    return next(
        (
            definition for definition in _iter_program_definition(cobol_source)
            if definition.name.upper() == name
        ),
        None,
    )


def _first_program_definition(cobol_source: CobolSource | None) -> CobolDefinition | None:
    """Return the first program or ENTRY of a file, or None."""
    return next(_iter_program_definition(cobol_source), None)


def program_definition(file_rel: str, name: str, project_dir: str) -> CobolDefinition | None:
    """Return the program or ENTRY of a program file a program name leads to.

    Args:
        file_rel: Relative path of the program file.
        name: The program name.
        project_dir: Absolute path to the project root.

    Returns:
        The program or ENTRY of that name, else the first program or ENTRY of the file.
        None when the file has neither or cannot be read.
    """
    cobol_source = _cobol_source(file_rel, project_dir)
    return (
        _program_definition(cobol_source, name.upper())
        or _first_program_definition(cobol_source)
    )


def _call_target(
    reference: CobolReference,
    file_rel: str,
    import_target_dict: dict[tuple[str, str, int], tuple[str, str]],
    project_dir: str,
) -> CobolReferenceTarget | None:
    """Resolve the program name of a CALL statement.

    Args:
        reference: A reference of CALL_KIND.
        file_rel: Relative path of the referring file.
        import_target_dict: Return value of _import_target_dict for the file.
        project_dir: Absolute path to the project root.

    Returns:
        The target in the program file the CALL leads to: the program or ENTRY of that
        name, else the first program or ENTRY of the file. Without such a file, the
        program or ENTRY of that name in the referring file. None when neither exists.
        The target has the name of its definition, and the program name as written
        when the program file has no definition.
    """
    program_name, program_file_rel = import_target_dict.get(
        (CALL_KIND, reference.name, reference.line), ("", file_rel),
    )
    program_source = _cobol_source(program_file_rel, project_dir)
    definition = _program_definition(program_source, reference.name)
    if program_file_rel == file_rel and definition is None:
        return None
    if definition is None:
        definition = _first_program_definition(program_source)
    return CobolReferenceTarget(
        definition.name if definition is not None else program_name,
        reference.line, program_file_rel, definition,
    )


def _transid_target(
    reference: CobolReference,
    import_target_dict: dict[tuple[str, str, int], tuple[str, str]],
    project_dir: str,
) -> CobolReferenceTarget | None:
    """Resolve the transaction of an EXEC CICS TRANSID option.

    Args:
        reference: A reference of TRANSID_KIND.
        import_target_dict: Return value of _import_target_dict for the referring file.
        project_dir: Absolute path to the project root.

    Returns:
        The target in the file that defines the transaction: the first DEFINE
        TRANSACTION of that name of a CSD file, no definition for a JCL file. None
        when no file defines the transaction.
    """
    transaction_name, definition_file_rel = import_target_dict.get(
        (TRANSID_KIND, reference.name, reference.line), ("", ""),
    )
    if not definition_file_rel:
        return None
    csd_source = _cobol_source(definition_file_rel, project_dir)
    definition = next(
        (
            definition for definition in (csd_source.definition_list if csd_source else [])
            if definition.type == TRANSACTION_TYPE and definition.name.upper() == reference.name
        ),
        None,
    )
    return CobolReferenceTarget(
        definition.name if definition is not None else transaction_name,
        reference.line, definition_file_rel, definition,
    )


def _name_target(
    reference: CobolReference, candidate_list: list[_Candidate],
) -> CobolReferenceTarget | None:
    """Resolve a data name or procedure name.

    Args:
        reference: A reference of a data name or procedure name.
        candidate_list: Return value of _candidate_list for the referring file.

    Returns:
        The target under the qualifiers (_target_under_qualifier); without one, the first
        definition of the name in the referring file, else in the copybooks in the
        order of the COPY statements. None when no file defines the name.
    """
    match_tuple = None
    if reference.qualifier_tuple:
        match_tuple = _target_under_qualifier(
            reference.name, reference.qualifier_tuple, candidate_list,
        )
    if match_tuple is None:
        match_tuple = next(
            (
                (candidate, candidate.find_definition_list(reference.name)[0])
                for candidate in candidate_list
                if candidate.find_definition_list(reference.name)
            ),
            None,
        )
    if match_tuple is None:
        return None
    candidate, definition = match_tuple
    return CobolReferenceTarget(
        candidate.local_name(definition), reference.line, candidate.file_rel, definition,
    )


def cobol_reference_target_list(
    file_rel: str,
    project_file_set: set[str],
    project_dir: str,
) -> list[CobolReferenceTarget]:
    """Resolve each reference of a COBOL file to the definition it refers to.

    The program name of a CALL statement goes to the program or ENTRY of the program
    file the statement leads to, or to the one of that name in the file itself
    (_call_target). The transaction of an EXEC CICS TRANSID option goes to the DEFINE
    TRANSACTION of the CSD file, or to the JCL file, that defines it (_transid_target).
    A data name or procedure name qualified with OF / IN goes to the definition under
    the named groups in the file itself or in a copybook it includes; any other name,
    and a qualified one no definition matches, goes to the first definition of that
    name in the file itself, else in the copybooks in the order of the COPY statements,
    each followed by the copybooks it includes (_name_target). The names of a copybook
    are those a COPY ... REPLACING statement writes. Names are compared without regard
    to upper and lower case.

    Processing flow:
    1. Return the targets of reference_target_cache_dict when they were resolved with
       the same project file set
    2. Resolve the copybooks, the CALL statements and the EXEC CICS TRANSID options of
       the file
    3. Resolve each reference

    Args:
        file_rel: Relative path of the file.
        project_file_set: Relative paths of the project files that have a language.
        project_dir: Absolute path to the project root.

    Returns:
        One target per reference that resolves, in line order, without duplicates. The
        targets are kept in reference_target_cache_dict until the project file set
        changes or the cache is cleared.
    """
    # == Step 1: Cache ========================================================
    cache_key = os.path.abspath(os.path.join(project_dir, file_rel))
    target_list = project_cache_value(reference_target_cache_dict, cache_key, project_file_set)
    if target_list is not None:
        return target_list

    # == Step 2: Copybooks, CALL statements and TRANSID options ===============
    cobol_source = parse_file(os.path.join(project_dir, file_rel))[0]
    candidate_list = _candidate_list(cobol_source, file_rel, project_file_set, project_dir)
    import_target_dict = _import_target_dict(
        cobol_source, file_rel, project_file_set, project_dir,
    )

    # == Step 3: References ===================================================
    # Two references of one line that write the same name lead to two definitions when
    # they qualify it differently (FLD OF A, FLD OF B)
    target_dict: dict[tuple[str, int, str, int | None], CobolReferenceTarget] = {}
    for reference in cobol_source.reference_list:
        if reference.kind == CALL_KIND:
            target = _call_target(reference, file_rel, import_target_dict, project_dir)
        elif reference.kind == TRANSID_KIND:
            target = _transid_target(reference, import_target_dict, project_dir)
        else:
            target = _name_target(reference, candidate_list)
        if target is not None:
            definition_line = target.definition.start_line if target.definition else None
            target_dict.setdefault(
                (target.name, target.line, target.file_rel, definition_line), target,
            )

    target_list = sorted(target_dict.values(), key=lambda target: (target.line, target.name))
    reference_target_cache_dict[cache_key] = (project_file_set, target_list)
    return target_list
