import os
import logging
from dataclasses import dataclass, field
from codetwine.parsers.ts_parser import parse_file
from codetwine.extractors.cobol_source import (
    CALL_KIND,
    CALL_TARGET_TYPE_TUPLE,
    COPY_KIND,
    CobolDefinition,
    CobolReference,
    CobolSource,
    has_statement,
)
from codetwine.extractors.imports import cobol_module, cobol_module_part
from codetwine.parsers.cobol_format import (
    FILE_UNIT,
    ITEM_UNIT,
    PROCEDURE_UNIT,
    split_cobol_source,
)
from codetwine.utils.file_utils import read_source
from codetwine.utils.project_cache import project_cache_value
from codetwine.config.settings import (
    BMS_EXT_SET,
    COBOL_EXT_SET,
    EXT_TO_LANGUAGE_DICT,
    has_language,
    language_ext,
    set_copy_target_ext,
)

logger = logging.getLogger(__name__)

# Extension of a copybook (lower case, without ".")
_COPYBOOK_EXT = "cpy"

# Cache of file indexes: project_dir -> (project file set the index was built from, index)
file_index_cache: dict[str, tuple[set[str], "CobolFileIndex"]] = {}

# Cache of the resolved references of COBOL files: absolute path -> (project file set
# they were resolved with, targets)
reference_target_cache: dict[str, tuple[set[str], list["CobolReferenceTarget"]]] = {}


@dataclass
class CobolFileIndex:
    """The names the COBOL files and BMS sources of a project are found by."""

    # Upper-case file name, with and without its extension, and the names in the file's
    # copy_name_list (BMS: mapset names) -> files in path order
    file_name_dict: dict[str, list[str]] = field(default_factory=dict)
    # Upper-case program name or ENTRY name -> files that define it, in path order
    program_dict: dict[str, list[str]] = field(default_factory=dict)
    # Files that define a program
    program_file_set: set[str] = field(default_factory=set)


def _cobol_source(file_rel: str, project_dir: str) -> CobolSource | None:
    """Parse a COBOL file or BMS source of the project and return its CobolSource.

    Returns:
        The CobolSource. None when reading or parsing the file raises an exception;
        the exception is logged.
    """
    try:
        return parse_file(os.path.join(project_dir, file_rel))[0]
    except Exception as e:
        logger.warning(f"{file_rel} is not read as COBOL: {type(e).__name__}: {e}")
        return None


def _add_file_name(file_index: CobolFileIndex, file_rel: str, name_list: list[str]) -> None:
    """Index a file by its file name, with and without its extension, and by other names."""
    file_name = os.path.basename(file_rel).upper()
    for name in dict.fromkeys((file_name, os.path.splitext(file_name)[0], *name_list)):
        file_list = file_index.file_name_dict.setdefault(name, [])
        if file_rel not in file_list:
            file_list.append(file_rel)


def _is_cobol_file(file_rel: str, project_dir: str) -> bool:
    """Return whether a project file is analyzed as COBOL."""
    return language_ext(os.path.join(project_dir, file_rel)) in COBOL_EXT_SET


def _add_source(file_index: CobolFileIndex, file_rel: str, cobol_source: CobolSource) -> None:
    """Index a COBOL file or BMS source by its file name, its copy_name_list and its programs."""
    _add_file_name(file_index, file_rel, cobol_source.copy_name_list)
    for definition in cobol_source.definition_list:
        if definition.type in CALL_TARGET_TYPE_TUPLE:
            file_list = file_index.program_dict.setdefault(definition.name.upper(), [])
            if file_rel not in file_list:
                file_list.append(file_rel)
            file_index.program_file_set.add(file_rel)


def _sort_file_index(file_index: CobolFileIndex) -> None:
    """Put the files of each name of a file index in path order."""
    for file_list in (*file_index.file_name_dict.values(), *file_index.program_dict.values()):
        file_list.sort()


def _source_file_list(project_dir: str, project_file_set: set[str]) -> list[str]:
    """Return the COBOL files and BMS sources among project files, in path order."""
    return sorted(
        file_rel for file_rel in project_file_set
        if language_ext(os.path.join(project_dir, file_rel)) in COBOL_EXT_SET | BMS_EXT_SET
    )


def _build_file_index(project_dir: str, project_file_set: set[str]) -> CobolFileIndex:
    """Parse the COBOL files and BMS sources of a project and build their index.

    Args:
        project_dir: Absolute path to the project root.
        project_file_set: Relative paths of the project files that have a language.

    Returns:
        The CobolFileIndex of the COBOL files and BMS sources in project_file_set. A file
        that cannot be read or parsed is not in it.
    """
    file_index = CobolFileIndex()
    for file_rel in _source_file_list(project_dir, project_file_set):
        cobol_source = _cobol_source(file_rel, project_dir)
        if cobol_source is not None:
            _add_source(file_index, file_rel, cobol_source)
    _sort_file_index(file_index)
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
    file_index = project_cache_value(file_index_cache, project_dir, project_file_set)
    if file_index is not None:
        return file_index

    file_index = _build_file_index(project_dir, project_file_set)
    file_index_cache[project_dir] = (project_file_set, file_index)
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
    and lower case are not told apart. Among several the first of this order is taken: a
    file whose path, with or without its extension, ends with the copybook name, a file
    in a directory named like the library, a file with a COBOL extension, a BMS source,
    a file in the directory of the current file, a file with the copybook extension, a
    file that defines no program, the first in path order.

    Args:
        name: Name of the copybook (e.g. "CUSTREC", "ERRCODES.cpy", "copy/ERRCODES.cpy").
        library: Library name of COPY ... OF library; "" without one.
        current_file_rel: Relative path of the file the statement is written in.
        file_index: The file index of the project.
        skip_file_set: Relative paths of files that are not candidates.

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
        if file_rel != current_file_rel and file_rel not in (skip_file_set or ())
    ]
    if not candidate_list:
        return None

    def order(file_rel: str) -> tuple[bool, bool, bool, bool, bool, bool, bool, str]:
        """Return the sort key of a candidate: the first in this order is taken."""
        directory = os.path.dirname(file_rel)
        ext = os.path.splitext(file_rel)[1].lstrip(".").lower()
        upper_path = "/" + file_rel.upper()
        is_named_path = any(
            path.endswith(name_path) for path in (upper_path, os.path.splitext(upper_path)[0])
        )
        return (
            not is_named_path,
            not library or os.path.basename(directory).upper() != library.upper(),
            ext not in COBOL_EXT_SET,
            ext not in BMS_EXT_SET,
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


def _copy_name_list(cobol_source: CobolSource) -> list[tuple[str, str]]:
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


def register_copy_target(project_dir: str, file_rel_list: list[str]) -> dict[str, str]:
    """Find the files without a language that COPY statements name, and record them as COBOL.

    A COPY statement or EXEC SQL INCLUDE of a COBOL file that resolves to a file without
    a language (_copybook_path, with those files as candidates too) makes that file a
    COBOL copybook, whatever its extension, when the statement names it with its whole
    file name, or when its text read as COBOL holds a data item, the description of a
    file or a statement of the procedure division (_is_cobol_text); otherwise the
    statement is resolved again without that file. The COPY statements of such a file
    are followed as well. The files are recorded with set_copy_target_ext(), replacing
    the ones recorded before for the project.

    Processing flow:
    1. Parse the COBOL files and BMS sources once: index them and keep the COPY
       statements of the COBOL files
    2. Index the files without a language by their file name as well
    3. Follow the COPY statements, recording and parsing each file without a language
       they lead to
    4. Keep the index of the files with a language in file_index_cache

    Args:
        project_dir: Absolute path to the project root.
        file_rel_list: Relative paths of the project files.

    Returns:
        {relative path: "cpy"} of the files recorded.
    """
    set_copy_target_ext(project_dir, {})
    other_file_set = {
        file_rel for file_rel in file_rel_list
        if not has_language(os.path.join(project_dir, file_rel))
    }
    if not other_file_set:
        return {}
    language_file_set = set(file_rel_list) - other_file_set

    # == Step 1: COBOL files and BMS sources ==================================
    file_index = CobolFileIndex()
    # Relative path of a COBOL file -> (copybook name, library name) of its COPY statements
    copy_name_dict: dict[str, list[tuple[str, str]]] = {}
    for file_rel in _source_file_list(project_dir, language_file_set):
        cobol_source = _cobol_source(file_rel, project_dir)
        if cobol_source is None:
            continue
        _add_source(file_index, file_rel, cobol_source)
        if _is_cobol_file(file_rel, project_dir):
            copy_name_dict[file_rel] = _copy_name_list(cobol_source)

    # == Step 2: Files without a language =====================================
    lookup_index = CobolFileIndex(
        file_name_dict={
            name: list(file_list) for name, file_list in file_index.file_name_dict.items()
        },
        program_dict=file_index.program_dict,
        program_file_set=file_index.program_file_set,
    )
    for file_rel in other_file_set:
        _add_file_name(lookup_index, file_rel, [])
    _sort_file_index(lookup_index)

    # == Step 3: COPY statements ==============================================
    copy_target_ext_dict: dict[str, str] = {}
    # Relative path of a file without a language -> whether its text is read as COBOL
    is_cobol_text_dict: dict[str, bool] = {}

    def is_copy_target(name: str, file_rel: str) -> bool:
        """Return whether a file without a language is the copybook a COPY statement names."""
        if file_rel not in is_cobol_text_dict:
            is_cobol_text_dict[file_rel] = _is_cobol_text(file_rel, project_dir)
        return _is_named_whole(name, file_rel) or is_cobol_text_dict[file_rel]

    queue = sorted(copy_name_dict)
    while queue:
        file_rel = queue.pop(0)
        for name, library in copy_name_dict[file_rel]:
            skip_file_set: set[str] = set()
            resolved = _copybook_path(name, library, file_rel, lookup_index)
            while (
                resolved in other_file_set
                and resolved not in copy_target_ext_dict
                and not is_copy_target(name, resolved)
            ):
                skip_file_set.add(resolved)
                resolved = _copybook_path(name, library, file_rel, lookup_index, skip_file_set)
            if resolved not in other_file_set or resolved in copy_target_ext_dict:
                continue
            copy_target_ext_dict[resolved] = _COPYBOOK_EXT
            set_copy_target_ext(project_dir, copy_target_ext_dict)
            cobol_source = _cobol_source(resolved, project_dir)
            if cobol_source is not None:
                _add_source(file_index, resolved, cobol_source)
                copy_name_dict[resolved] = _copy_name_list(cobol_source)
                queue.append(resolved)

    # == Step 4: Index of the files with a language ===========================
    _sort_file_index(file_index)
    file_index_cache[project_dir] = (language_file_set | set(copy_target_ext_dict), file_index)
    return copy_target_ext_dict


@dataclass
class CobolReferenceTarget:
    """The definition one reference of a COBOL file resolves to."""

    # Name of the definition: a data name or procedure name as the referring file writes
    # it (after REPLACING), a program or ENTRY name as its definition writes it
    name: str
    line: int        # Line of the reference (1-based)
    file_rel: str    # Relative path of the file with the definition
    # The definition; None when the program file of a CALL is not read
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
        resolved = resolve_cobol_module_path(
            module, holder.file_rel, project_file_set, project_dir,
        )
        if not resolved or resolved in open_file_set:
            continue
        copy_source = _cobol_source(resolved, project_dir)
        if copy_source is None:
            continue
        candidate = _Candidate(
            resolved, copy_source, holder.copy_line or cobol_import.line,
            [*cobol_import.replacing_list, *holder.replacing_list],
        )
        candidate_list.append(candidate)
        _add_copybook_candidate(
            candidate_list, candidate, open_file_set | {resolved}, project_file_set, project_dir,
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
    qualifier: str, definition: CobolDefinition, candidate: _Candidate, own: _Candidate,
) -> bool:
    """Return whether a qualifier names a definition that holds a definition of a candidate.

    Args:
        qualifier: Upper-case qualifier.
        definition: A definition of the candidate.
        candidate: The file with the definition.
        own: The referring file.

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
        for holder in own.find_definition_list(qualifier)
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
    own = candidate_list[0]
    for candidate in candidate_list:
        for definition in candidate.find_definition_list(name):
            if all(
                _is_under(qualifier, definition, candidate, own) for qualifier in qualifier_tuple
            ):
                return candidate, definition
    return None


def _program_target_dict(
    cobol_source: CobolSource, file_rel: str, project_file_set: set[str], project_dir: str,
) -> dict[tuple[str, int], tuple[str, str]]:
    """Return the program file each CALL statement of a COBOL file leads to.

    Returns:
        {(upper-case program name, line of the statement): (program name as written,
        relative path of the file)} of each CALL that resolves to another file.
    """
    program_target_dict: dict[tuple[str, int], tuple[str, str]] = {}
    for cobol_import in cobol_source.import_list:
        if cobol_import.kind != CALL_KIND:
            continue
        module = cobol_module(CALL_KIND, cobol_import.name)
        resolved = resolve_cobol_module_path(module, file_rel, project_file_set, project_dir)
        if resolved:
            program_target_dict.setdefault(
                (cobol_import.name.upper(), cobol_import.line), (cobol_import.name, resolved),
            )
    return program_target_dict


def _program_definition(cobol_source: CobolSource | None, name: str) -> CobolDefinition | None:
    """Return the first program or ENTRY of a file with an upper-case name, or None."""
    if cobol_source is None:
        return None
    return next(
        (
            definition for definition in cobol_source.definition_list
            if definition.type in CALL_TARGET_TYPE_TUPLE and definition.name.upper() == name
        ),
        None,
    )


def _first_program_definition(cobol_source: CobolSource | None) -> CobolDefinition | None:
    """Return the first program or ENTRY of a file, or None."""
    if cobol_source is None:
        return None
    return next(
        (
            definition for definition in cobol_source.definition_list
            if definition.type in CALL_TARGET_TYPE_TUPLE
        ),
        None,
    )


def _call_target(
    reference: CobolReference,
    file_rel: str,
    program_target_dict: dict[tuple[str, int], tuple[str, str]],
    project_dir: str,
) -> CobolReferenceTarget | None:
    """Resolve the program name of a CALL statement.

    Args:
        reference: A reference with is_call.
        file_rel: Relative path of the referring file.
        program_target_dict: Return value of _program_target_dict for the file.
        project_dir: Absolute path to the project root.

    Returns:
        The target in the program file the CALL leads to: the program or ENTRY of that
        name, else the first program or ENTRY of the file. Without such a file, the
        program or ENTRY of that name in the referring file. None when neither exists.
        The target has the name of its definition, and the program name as written
        when the program file has no definition.
    """
    program_name, program_file_rel = program_target_dict.get(
        (reference.name, reference.line), ("", file_rel),
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


def _name_target(
    reference: CobolReference, candidate_list: list[_Candidate],
) -> CobolReferenceTarget | None:
    """Resolve a data name or procedure name.

    Args:
        reference: A reference without is_call.
        candidate_list: Return value of _candidate_list for the referring file.

    Returns:
        The target under the qualifiers (_target_under_qualifier); without one, the first
        definition of the name in the referring file, else in the copybooks in the
        order of the COPY statements. None when no file defines the name.
    """
    match = None
    if reference.qualifier_tuple:
        match = _target_under_qualifier(reference.name, reference.qualifier_tuple, candidate_list)
    if match is None:
        match = next(
            (
                (candidate, candidate.find_definition_list(reference.name)[0])
                for candidate in candidate_list
                if candidate.find_definition_list(reference.name)
            ),
            None,
        )
    if match is None:
        return None
    candidate, definition = match
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
    (_call_target). A
    data name or procedure name qualified with OF / IN goes to the definition under the
    named groups in the file itself or in a copybook it includes; any other name, and a
    qualified one no definition matches, goes to the first definition of that name in
    the file itself, else in the copybooks in the order of the COPY statements, each
    followed by the copybooks it includes (_name_target). The names of a copybook are
    those a COPY ... REPLACING statement writes. Names are compared without regard to upper and lower case.

    Processing flow:
    1. Return the targets of reference_target_cache when they were resolved with the
       same project file set
    2. Resolve the copybooks and the CALL statements of the file
    3. Resolve each reference

    Args:
        file_rel: Relative path of the file.
        project_file_set: Relative paths of the project files that have a language.
        project_dir: Absolute path to the project root.

    Returns:
        One target per reference that resolves, in line order, without duplicates. The
        targets are kept in reference_target_cache until the project file set changes
        or the cache is cleared.
    """
    # == Step 1: Cache ========================================================
    cache_key = os.path.abspath(os.path.join(project_dir, file_rel))
    target_list = project_cache_value(reference_target_cache, cache_key, project_file_set)
    if target_list is not None:
        return target_list

    # == Step 2: Copybooks and CALL statements ================================
    cobol_source = parse_file(os.path.join(project_dir, file_rel))[0]
    candidate_list = _candidate_list(cobol_source, file_rel, project_file_set, project_dir)
    program_target_dict = _program_target_dict(
        cobol_source, file_rel, project_file_set, project_dir,
    )

    # == Step 3: References ===================================================
    target_dict: dict[tuple[str, int, str], CobolReferenceTarget] = {}
    for reference in cobol_source.reference_list:
        if reference.is_call:
            target = _call_target(reference, file_rel, program_target_dict, project_dir)
        else:
            target = _name_target(reference, candidate_list)
        if target is not None:
            target_dict.setdefault((target.name, target.line, target.file_rel), target)

    target_list = sorted(target_dict.values(), key=lambda target: (target.line, target.name))
    reference_target_cache[cache_key] = (project_file_set, target_list)
    return target_list
