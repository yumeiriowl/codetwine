import os
from bisect import bisect_left
from dataclasses import dataclass
from codetwine.parsers.ts_parser import parse_file
from codetwine.cobol_file_index import (
    CobolReferenceTarget,
    program_definition,
    reference_target_cache_dict,
    resolve_cobol_module_path,
)
from codetwine.extractors.cobol_source import CALL_KIND, CobolDefinition
from codetwine.extractors.imports import cobol_module
from codetwine.extractors.jcl_source import (
    INCLUDE_KIND,
    INCLUDE_REFERENCE,
    MAPSET_KIND,
    MAPSET_REFERENCE,
    PARAMETER_REFERENCE,
    PROC_KIND,
    PROC_REFERENCE,
    PROC_TYPE,
    PROGRAM_REFERENCE,
    RESOURCE_REFERENCE,
    STEP_REFERENCE,
    STEP_TYPE,
    SYMBOL_REFERENCE,
    SYMBOL_TYPE,
    JclReference,
    JclSource,
)
from codetwine.jcl_member import parse_jcl_file
from codetwine.utils.project_cache import project_cache_value

# Kind of a reference to a file without a definition -> the statement kind it is resolved as
_FILE_REFERENCE_KIND_DICT = {
    INCLUDE_REFERENCE: INCLUDE_KIND,
    MAPSET_REFERENCE:  MAPSET_KIND,
}

# Kind of a reference to a step or a symbol -> type of the definitions it can name
_REFERENCE_TYPE_DICT = {
    STEP_REFERENCE:      STEP_TYPE,
    SYMBOL_REFERENCE:    SYMBOL_TYPE,
    PARAMETER_REFERENCE: SYMBOL_TYPE,
}


class _DefinitionIndex:
    """The definitions of a JclSource by type and upper-case name, in line order."""

    def __init__(self, jcl_source: JclSource) -> None:
        """Index the definitions of a JclSource."""
        # (definition type, upper-case name) -> definitions in line order
        self.definition_dict: dict[tuple[str, str], list[CobolDefinition]] = {}
        # (upper-case name, first line) -> the first definition of that name starting there
        self.line_dict: dict[tuple[str, int], CobolDefinition] = {}
        for definition in jcl_source.definition_list:
            self.definition_dict.setdefault(
                (definition.type, definition.name.upper()), [],
            ).append(definition)
            self.line_dict.setdefault((definition.name.upper(), definition.start_line), definition)

    def first(
        self, name: str, definition_type: str, start_line: int, end_line: int,
    ) -> CobolDefinition | None:
        """Return the first definition of a type with an upper-case name starting between two lines."""
        candidate_list = self.definition_dict.get((definition_type, name), [])
        index = bisect_left(
            candidate_list, start_line, key=lambda definition: definition.start_line,
        )
        if index < len(candidate_list) and candidate_list[index].start_line <= end_line:
            return candidate_list[index]
        return None

    def at_line(self, name: str, start_line: int) -> CobolDefinition | None:
        """Return the first definition with an upper-case name that starts on a line, or None."""
        return self.line_dict.get((name, start_line))


@dataclass
class _ProcTarget:
    """The procedure an EXEC statement calls."""

    name: str                              # Upper-case name the EXEC statement calls it by
    file_rel: str                          # Relative path of the file it is in
    definition_index: _DefinitionIndex     # The definitions of that file
    # Its PROC statement; None for a file whose first definition is no PROC statement
    definition: CobolDefinition | None
    start_line: int                        # First line of the procedure in its file
    end_line: int                          # Last line of the procedure in its file


def _proc_target(
    reference: JclReference,
    definition_index: _DefinitionIndex,
    file_rel: str,
    proc_index_dict: dict[str, _DefinitionIndex],
    project_file_set: set[str],
    project_dir: str,
) -> _ProcTarget | None:
    """Return the procedure a PROC_REFERENCE calls.

    It is the in-stream procedure of the file the reference names, else the procedure of
    the file the name resolves to (resolve_cobol_module_path): the PROC statement its
    first definition is, whatever its name, else the whole file.

    Args:
        reference: The PROC_REFERENCE.
        definition_index: The definitions of the referring file.
        file_rel: Relative path of the referring file.
        proc_index_dict: {relative path of a procedure file: its definitions}; the index
            of a file read here is added.
        project_file_set: Relative paths of the project files that have a language.
        project_dir: Absolute path to the project root.

    Returns:
        The procedure. None when the file has no in-stream procedure of that name on the
        definition_line of the reference, when no other file has the name, and when that
        file cannot be read as JCL.
    """
    if reference.definition_line:
        definition = definition_index.first(
            reference.name, PROC_TYPE, reference.definition_line, reference.definition_line,
        )
        if definition is None:
            return None
        return _ProcTarget(
            reference.name, file_rel, definition_index, definition,
            definition.start_line, definition.end_line,
        )

    module = cobol_module(PROC_KIND, reference.name)
    proc_rel = resolve_cobol_module_path(module, file_rel, project_file_set, project_dir)
    proc_source = parse_jcl_file(proc_rel, project_dir) if proc_rel else None
    if proc_rel is None or proc_source is None:
        return None
    if proc_rel not in proc_index_dict:
        proc_index_dict[proc_rel] = _DefinitionIndex(proc_source)
    proc_index = proc_index_dict[proc_rel]
    definition_list = proc_source.definition_list
    if not definition_list or definition_list[0].type != PROC_TYPE:
        return _ProcTarget(
            reference.name, proc_rel, proc_index, None, 1, len(proc_source.line_list),
        )
    definition = definition_list[0]
    return _ProcTarget(
        reference.name, proc_rel, proc_index, definition,
        definition.start_line, definition.end_line,
    )


def _program_target(
    reference: JclReference, file_rel: str, project_file_set: set[str], project_dir: str,
) -> CobolReferenceTarget | None:
    """Resolve a program a step or CSD command names to the program or ENTRY of its file.

    Returns:
        The target. None when no file defines the program.
    """
    module = cobol_module(CALL_KIND, reference.name)
    program_rel = resolve_cobol_module_path(module, file_rel, project_file_set, project_dir)
    if program_rel is None:
        return None
    definition = program_definition(program_rel, reference.name, project_dir)
    return CobolReferenceTarget(
        definition.name if definition is not None else reference.name,
        reference.line, program_rel, definition,
    )


def _reference_target_list(
    reference: JclReference,
    definition_index: _DefinitionIndex,
    file_rel: str,
    proc_target_dict: dict[int, list[_ProcTarget]],
    project_file_set: set[str],
    project_dir: str,
) -> list[CobolReferenceTarget]:
    """Resolve one reference of a JCL or CSD file.

    Args:
        reference: The reference.
        definition_index: The definitions of the referring file.
        file_rel: Relative path of the referring file.
        proc_target_dict: {line of an EXEC statement: the procedures it calls}.
        project_file_set: Relative paths of the project files that have a language.
        project_dir: Absolute path to the project root.

    Returns:
        The targets: the program a step runs (_program_target), the file of an INCLUDE
        member or a mapset (without a definition), the CICS resource of the file a CSD
        command names, the procedure of that name an EXEC statement calls, the
        definition of the file the reader resolved the name to, or the first step or
        symbolic parameter of that name in each of the procedures the EXEC statement of
        exec_line calls. Empty when the name resolves to no definition and, for a
        procedure, a member or a mapset, to no file.
    """
    if reference.kind == PROGRAM_REFERENCE:
        program_target = _program_target(reference, file_rel, project_file_set, project_dir)
        return [program_target] if program_target is not None else []
    if reference.kind in _FILE_REFERENCE_KIND_DICT:
        module = cobol_module(_FILE_REFERENCE_KIND_DICT[reference.kind], reference.name)
        member_rel = resolve_cobol_module_path(module, file_rel, project_file_set, project_dir)
        if member_rel is None:
            return []
        return [CobolReferenceTarget(reference.name, reference.line, member_rel, None)]
    if reference.kind == RESOURCE_REFERENCE:
        definition = definition_index.at_line(reference.name, reference.definition_line)
        if definition is None:
            return []
        return [CobolReferenceTarget(definition.name, reference.line, file_rel, definition)]

    proc_target_list = proc_target_dict.get(reference.exec_line, [])
    if reference.kind == PROC_REFERENCE:
        proc_target = next(
            (target for target in proc_target_list if target.name == reference.name), None,
        )
        if proc_target is None:
            return []
        return [CobolReferenceTarget(
            proc_target.definition.name if proc_target.definition else reference.name,
            reference.line, proc_target.file_rel, proc_target.definition,
        )]

    definition_type = _REFERENCE_TYPE_DICT[reference.kind]
    if reference.definition_line:
        definition = definition_index.first(
            reference.name, definition_type, reference.definition_line, reference.definition_line,
        )
        if definition is None:
            return []
        return [CobolReferenceTarget(definition.name, reference.line, file_rel, definition)]

    target_list: list[CobolReferenceTarget] = []
    for proc_target in proc_target_list:
        definition = proc_target.definition_index.first(
            reference.name, definition_type, proc_target.start_line, proc_target.end_line,
        )
        if definition is not None:
            target_list.append(CobolReferenceTarget(
                definition.name, reference.line, proc_target.file_rel, definition,
            ))
    return target_list


def jcl_reference_target_list(
    file_rel: str,
    project_file_set: set[str],
    project_dir: str,
) -> list[CobolReferenceTarget]:
    """Resolve each reference of a JCL or CSD file to the definition it refers to.

    A program goes to the program or ENTRY of its program file, an INCLUDE member and a
    mapset to its file (without a definition), a resource of a CSD command to the
    definition of the file that starts on the line the reader gave it, a procedure to
    the PROC statement of the in-stream procedure of the file or of the file the
    procedure is in (the file when it does not start with one), and a step or symbolic
    parameter of a called procedure to the one of that name inside each procedure the
    EXEC statement calls.
    The other steps and symbols go to the definitions of the file the reader resolved
    them to. Names are compared without regard to upper and lower case.

    Processing flow:
    1. Return the targets of reference_target_cache_dict when they were resolved with
       the same project file set
    2. Resolve the procedures the EXEC statements call
    3. Resolve each reference

    Args:
        file_rel: Relative path of the file.
        project_file_set: Relative paths of the project files that have a language.
        project_dir: Absolute path to the project root.

    Returns:
        One target per reference and definition it resolves to, in line order, without
        duplicates. The targets are kept in reference_target_cache_dict until the
        project file set changes or the cache is cleared.
    """
    # == Step 1: Cache ========================================================
    cache_key = os.path.abspath(os.path.join(project_dir, file_rel))
    target_list = project_cache_value(reference_target_cache_dict, cache_key, project_file_set)
    if target_list is not None:
        return target_list

    # == Step 2: Procedures ===================================================
    jcl_source = parse_file(os.path.join(project_dir, file_rel))[0]
    definition_index = _DefinitionIndex(jcl_source)
    # Relative path of a procedure file -> its definitions
    proc_index_dict: dict[str, _DefinitionIndex] = {}
    proc_target_dict: dict[int, list[_ProcTarget]] = {}
    for reference in jcl_source.reference_list:
        if reference.kind != PROC_REFERENCE:
            continue
        proc_target = _proc_target(
            reference, definition_index, file_rel, proc_index_dict, project_file_set,
            project_dir,
        )
        if proc_target is not None:
            proc_target_dict.setdefault(reference.exec_line, []).append(proc_target)

    # == Step 3: References ===================================================
    target_dict: dict[tuple[str, int, str, int | None], CobolReferenceTarget] = {}
    for reference in jcl_source.reference_list:
        for target in _reference_target_list(
            reference, definition_index, file_rel, proc_target_dict, project_file_set, project_dir,
        ):
            definition_line = target.definition.start_line if target.definition else None
            target_dict.setdefault(
                (target.name, target.line, target.file_rel, definition_line), target,
            )

    target_list = sorted(target_dict.values(), key=lambda target: (target.line, target.name))
    reference_target_cache_dict[cache_key] = (project_file_set, target_list)
    return target_list
