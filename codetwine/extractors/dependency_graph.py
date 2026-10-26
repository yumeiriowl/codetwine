import os
import fnmatch
import logging
from codetwine.parsers.ts_parser import (
    blank_macro_cache,
    detect_encoding_cache_dict,
    parse_cache_dict,
    parse_file,
)
from codetwine.extractors.definition_source import clear_definition_source_cache
from codetwine.extractors.imports import extract_imports
from codetwine.cobol_file_index import (
    file_index_cache_dict,
    reference_target_cache_dict,
    register_copy_target,
)
from codetwine.csharp_namespace_index import csharp_target_cache_dict, namespace_index_cache_dict
from codetwine.import_binding import clear_import_binder_cache, import_binder
from codetwine.import_reference import clear_import_reference_cache
from codetwine.jcl_member import no_csd_file_list, no_jcl_file_list
from codetwine.rust_module_tree import module_tree_cache_dict
from codetwine.parsers.r_markdown import has_r_chunk
from codetwine.path_config import path_config_cache_dict
from codetwine.package_path import clear_package_path_cache
from codetwine.alias_path import clear_alias_path_cache
from codetwine.r_name_index import r_import_file_list, r_name_index_cache_dict, r_target_cache_dict
from codetwine.reference_target import reference_kind, reference_target_list
from codetwine.import_to_path import (
    clear_import_path_cache,
    resolve_module_to_project_path,
    get_import_param_tuple,
)
from codetwine.utils.file_utils import (
    is_text_file,
    lone_cr_to_lf,
    lower_ext,
    path_to_rel,
    read_source,
    rel_to_copy_path,
)
from codetwine.config.settings import (
    EXCLUDE_PATTERNS,
    EXT_TO_IMPORT_RESOLVE_DICT,
    R_EXT_SET,
    R_MARKDOWN_EXT_SET,
    has_language,
    language_ext,
    set_no_language_file,
)

logger = logging.getLogger(__name__)


def _is_own_file(project_dir: str, file_path: str) -> bool:
    """Return whether a path is a file of the project itself rather than a link to one.

    Args:
        project_dir: Root directory of the project to analyze.
        file_path: Absolute path of a file under project_dir.

    Returns:
        False for a symbolic link and for a path under a linked directory of the
        project, True otherwise.
    """
    rel_path = os.path.relpath(os.path.abspath(file_path), os.path.abspath(project_dir))
    own_path = os.path.join(os.path.realpath(project_dir), rel_path)
    return os.path.normcase(os.path.realpath(file_path)) == os.path.normcase(own_path)


def _is_exclude_name(name: str) -> bool:
    """Return whether a file or directory name matches one of EXCLUDE_PATTERNS."""
    return any(fnmatch.fnmatch(name, pattern) for pattern in EXCLUDE_PATTERNS)


def _is_under(path: str, dir_path: str | None) -> bool:
    """Return whether an absolute path is dir_path or a path under it.

    False when dir_path is None.
    """
    if dir_path is None:
        return False
    return path == dir_path or path.startswith(dir_path.rstrip(os.sep) + os.sep)


def _own_text_file_list(project_dir: str, file_path_list: list[str]) -> list[str]:
    """Return the non-empty text files among paths of the project, without links to files.

    Symbolic links and paths under a linked directory (_is_own_file) are left out, and
    so are empty, binary and unreadable files (is_text_file). The number of files left
    out by is_text_file is logged. A path relative to project_dir that cannot be written
    as UTF-8 is left out with a warning.

    Args:
        project_dir: Root directory of the project to analyze.
        file_path_list: Absolute file paths under project_dir.

    Returns:
        The paths kept, in the order given.
    """
    text_file_list: list[str] = []
    skip_count = 0
    for file_path in file_path_list:
        if not _is_own_file(project_dir, file_path):
            continue
        file_rel = path_to_rel(file_path, project_dir)
        try:
            file_rel.encode("utf-8")
        except UnicodeEncodeError:
            logger.warning(f"Skipped {file_rel!r}: its path is not valid UTF-8")
            continue
        if is_text_file(file_path):
            text_file_list.append(file_path)
        else:
            skip_count += 1
    if skip_count:
        logger.info(f"Skipped {skip_count} empty, binary or unreadable files")
    return text_file_list


def _collect_text_file_list(project_dir: str, exclude_dir: str | None) -> list[str]:
    """Walk the project and return the absolute paths of its non-empty text files.

    Directories and files matching EXCLUDE_PATTERNS are left out, and so are exclude_dir,
    symbolic links (_is_own_file) and empty, binary and unreadable files (is_text_file).
    Linked directories are not entered. The number of skipped files is logged.

    Args:
        project_dir: Absolute path of the root directory of the project to analyze.
        exclude_dir: Absolute path of a directory whose files are not collected, or None.

    Returns:
        Absolute file paths in os.walk order.
    """
    if _is_under(project_dir, exclude_dir):
        return []
    file_path_list: list[str] = []
    for dir_path, dir_name_list, file_name_list in os.walk(project_dir):
        # os.walk does not enter the directories removed from dir_name_list in place
        dir_name_list[:] = [
            dir_name for dir_name in dir_name_list
            if not _is_exclude_name(dir_name)
            and not _is_under(os.path.join(dir_path, dir_name), exclude_dir)
        ]
        file_path_list.extend(
            os.path.join(dir_path, file_name)
            for file_name in file_name_list if not _is_exclude_name(file_name)
        )
    return _own_text_file_list(project_dir, file_path_list)


def _filter_text_file_list(
    project_dir: str, file_list: list[str], exclude_dir: str | None,
) -> list[str]:
    """Return the absolute paths of the non-empty text files among the given files.

    The same files are left out as in _collect_text_file_list: a path with a directory
    or file name matching EXCLUDE_PATTERNS, a path under exclude_dir, symbolic links and
    paths under a linked directory (_is_own_file), and empty, binary and unreadable files
    (is_text_file). A path outside project_dir and a path that does not exist are left
    out too, and named in a warning. The number of skipped files is logged.

    Args:
        project_dir: Absolute path of the root directory of the project to analyze.
        file_list: File paths relative to project_dir.
        exclude_dir: Absolute path of a directory whose files are not collected, or None.

    Returns:
        Absolute file paths in the order given, without duplicates.
    """
    file_path_list: list[str] = []
    visit_path_set: set[str] = set()
    outside_list: list[str] = []
    missing_list: list[str] = []
    for file_rel in file_list:
        file_path = os.path.normpath(os.path.join(project_dir, file_rel))
        part_list = path_to_rel(file_path, project_dir).split("/")
        if part_list[0] == "..":
            outside_list.append(file_rel)
            continue
        if file_path in visit_path_set or _is_under(file_path, exclude_dir):
            continue
        visit_path_set.add(file_path)
        if any(_is_exclude_name(part) for part in part_list):
            continue
        if not os.path.lexists(file_path):
            missing_list.append(file_rel)
            continue
        file_path_list.append(file_path)

    # Name the given paths that are no file of the project
    for reason, path_list in (
        ("outside the project directory", outside_list), ("no such file", missing_list),
    ):
        if path_list:
            logger.warning(f"Left out of file_list ({reason}): {', '.join(path_list)}")
    return _own_text_file_list(project_dir, file_path_list)


def _exclude_path(project_dir: str, exclude_dir: str | None) -> str | None:
    """Return the part of the project an output directory leaves out, as a walk gives it.

    The outputs are written to exclude_dir/<name of project_dir>. When exclude_dir is
    project_dir or under it, exclude_dir is left out; otherwise the output location is
    left out when it is project_dir or under it, and nothing when it is outside the
    project. The real locations (os.path.realpath) are compared, so a path through a
    symbolic link names the directory the link leads to.

    Examples:
        ("/work/proj", "/work/link_to_proj/output") -> "/work/proj/output"
        ("/work/proj", "/work/proj")                -> "/work/proj"
        ("/work/proj", "/work")                     -> "/work/proj"
        ("/work/proj", "/")                         -> None
        ("/work/proj", "/work/output")              -> None

    Args:
        project_dir: Absolute path of the root directory of the project to analyze.
        exclude_dir: The output directory, absolute or relative to the current
            directory, or None.

    Returns:
        project_dir joined with the path of the directory left out relative to it
        (project_dir itself when that is the whole project), or None when nothing is
        left out.
    """
    if exclude_dir is None:
        return None
    real_project_dir = os.path.realpath(project_dir)
    project_key = os.path.normcase(real_project_dir)
    real_leave_dir = os.path.realpath(exclude_dir)
    if not _is_under(os.path.normcase(real_leave_dir), project_key):
        # The output location
        real_leave_dir = os.path.realpath(
            os.path.join(real_leave_dir, os.path.basename(project_dir))
        )
        if not _is_under(os.path.normcase(real_leave_dir), project_key):
            return None
    return os.path.normpath(
        os.path.join(project_dir, os.path.relpath(real_leave_dir, real_project_dir))
    )


def _no_chunk_document_list(project_dir: str, all_file_list: list[str]) -> list[str]:
    """Return the R Markdown and Quarto files that have no R code chunk.

    Args:
        project_dir: Root directory of the project to analyze.
        all_file_list: Absolute paths of the text files of the project.

    Returns:
        Paths relative to project_dir. A file that cannot be read is not returned.
    """
    document_list: list[str] = []
    for file_path in all_file_list:
        if lower_ext(file_path) not in R_MARKDOWN_EXT_SET:
            continue
        try:
            content = lone_cr_to_lf(read_source(file_path)[0]).encode("utf-8")
        except OSError:
            continue
        if not has_r_chunk(content):
            document_list.append(path_to_rel(file_path, project_dir))
    return document_list


def _import_callee_rel_set(
    file_path: str, file_rel: str, project_file_set: set[str], project_dir: str,
) -> set[str]:
    """Return the project files the import statements of a file resolve to.

    For a file whose references are resolved through its import statements these are
    the files its ImportBinder gives; for a COBOL file, the files its COPY and CALL
    statements and EXEC CICS TRANSID options resolve to; for a JCL file, the files of
    the programs its steps run, the procedures its EXEC statements call and its INCLUDE
    members; for a CSD file (and the CSD commands of a JCL file), the files of the
    programs and mapsets it names.

    Args:
        file_path: Absolute path of the file.
        file_rel: Relative path of the file.
        project_file_set: Relative paths of the files that have a language.
        project_dir: Root directory of the project to analyze.

    Returns:
        Relative paths. Empty for a file without import statements, and for a file of
        a language without module resolve settings (EXT_TO_IMPORT_RESOLVE_DICT), which
        is not parsed.
    """
    if reference_kind(file_path) == "import":
        return import_binder(project_dir, project_file_set).import_file_set(file_rel)

    file_ext = language_ext(file_path)
    language, import_query_str = get_import_param_tuple(file_ext)
    if not language or file_ext not in EXT_TO_IMPORT_RESOLVE_DICT:
        return set()
    root_node = parse_file(file_path)[0]
    callee_rel_set: set[str] = set()
    for import_info in extract_imports(root_node, language, import_query_str):
        callee_rel = resolve_module_to_project_path(
            import_info.module, file_rel, project_file_set, None, project_dir,
        )
        if callee_rel:
            callee_rel_set.add(callee_rel)
    return callee_rel_set


def _log_graph_failure(file_rel: str, error: Exception) -> None:
    """Log that a file is analyzed without its dependencies in the dependency graph."""
    logger.warning(
        f"{file_rel} has no dependencies in the dependency graph: "
        f"{type(error).__name__}: {error}"
    )


def _reference_callee_rel_list(
    file_path: str, file_rel: str, project_file_set: set[str], project_dir: str,
) -> list[str]:
    """Return the files the references of a file resolve to.

    For an R file the scripts it reads with source() and box::use are returned as well,
    whether or not it uses a name of them.

    Args:
        file_path: Absolute path of the file.
        file_rel: Relative path of the file.
        project_file_set: Relative paths of the files that have a language.
        project_dir: Root directory of the project to analyze.

    Returns:
        Relative paths, the file itself among them when it refers to its own
        definitions.
    """
    callee_rel_list = [
        target.file_rel
        for target in reference_target_list(file_rel, project_file_set, project_dir)
    ]
    if language_ext(file_path) in R_EXT_SET:
        callee_rel_list += r_import_file_list(file_rel, project_file_set, project_dir)
    return callee_rel_list


def _collect_callee_dict(
    language_file_list: list[str],
    project_dir: str,
    project_file_set: set[str],
) -> dict[str, set[str]]:
    """Map each file to the project files it depends on.

    These are the files its import statements resolve to, and the files whose
    definitions it uses: through an import statement, without one (Java / Kotlin: same
    package, SQL: whole project), by name (C#, R), or through a copybook that includes
    the file (COBOL). Both are read for one file, while its syntax tree is in
    parse_cache_dict, before the next file is read.

    Args:
        language_file_list: Absolute paths of the files that have a language.
        project_dir: Root directory of the project to analyze.
        project_file_set: Relative paths of the files that have a language.

    Returns:
        A {file absolute path: set of callee absolute paths} dict, one entry per file.
        A file whose import statements raise an exception has no callees of them, and
        one whose references raise an exception none of those; the exception is logged.
    """
    file_callee_dict: dict[str, set[str]] = {}
    for file_path in language_file_list:
        file_rel = path_to_rel(file_path, project_dir)
        callee_rel_set: set[str] = set()
        try:
            callee_rel_set.update(_import_callee_rel_set(
                file_path, file_rel, project_file_set, project_dir,
            ))
        except Exception as e:
            _log_graph_failure(file_rel, e)
        try:
            callee_rel_set.update(_reference_callee_rel_list(
                file_path, file_rel, project_file_set, project_dir,
            ))
        except Exception as e:
            _log_graph_failure(file_rel, e)
        file_callee_dict[os.path.abspath(file_path)] = {
            os.path.abspath(os.path.join(project_dir, callee_rel))
            for callee_rel in callee_rel_set if callee_rel != file_rel
        }
    return file_callee_dict


def _to_output_entry_list(
    all_file_list: list[str],
    project_dir: str,
    file_caller_dict: dict[str, list[str]],
    file_callee_dict: dict[str, set[str]],
) -> list[dict]:
    """Build one {"file", "callers", "callees"} entry per file with "project_name/copy_path" paths.

    copy_path is the {parent_dir}/{stem}_{ext}/{filename} path of the file in the output
    directory. A file without a language has no entry in the two dicts and gets empty lists.

    Args:
        all_file_list: Absolute paths of every analyzed file, in output order.
        project_dir: Root directory of the project to analyze.
        file_caller_dict: {file absolute path: caller absolute paths}.
        file_callee_dict: {file absolute path: callee absolute paths}.

    Returns:
        The entries in the order of all_file_list.
    """
    project_name = os.path.basename(project_dir)

    def to_output(path: str) -> str:
        """Convert an absolute path to "project_name/copy_path"."""
        return f"{project_name}/{rel_to_copy_path(path_to_rel(path, project_dir))}"

    file_info_list = []
    for file_path in all_file_list:
        abs_path = os.path.abspath(file_path)
        file_info_list.append({
            "file":    to_output(abs_path),
            "callers": sorted(to_output(caller) for caller in file_caller_dict.get(abs_path, [])),
            "callees": sorted(to_output(callee) for callee in file_callee_dict.get(abs_path, ())),
        })
    return file_info_list


def clear_analysis_cache() -> None:
    """Forget the syntax trees, the indexes and the resolved references of an analysis.

    The indexes built from the files of a project and the references resolved with
    them are cleared: the Rust module trees (module_tree_cache_dict), the C# namespace
    indexes (namespace_index_cache_dict, csharp_target_cache_dict), the R name indexes
    (r_name_index_cache_dict, r_target_cache_dict), the COBOL, JCL and CSD file indexes
    (file_index_cache_dict, reference_target_cache_dict) and the names and references
    resolved through import statements (clear_import_reference_cache, clear_import_binder_cache,
    clear_import_path_cache), the definitions read for a definition's source
    (clear_definition_source_cache) and the path settings of the JS/TS config files
    (path_config_cache_dict, the package and alias caches), and so are the parse results
    (parse_cache_dict, blank_macro_cache) and the encodings detected for the files read
    (detect_encoding_cache_dict).
    """
    parse_cache_dict.clear()
    blank_macro_cache.clear()
    detect_encoding_cache_dict.clear()
    module_tree_cache_dict.clear()
    file_index_cache_dict.clear()
    namespace_index_cache_dict.clear()
    r_name_index_cache_dict.clear()
    reference_target_cache_dict.clear()
    csharp_target_cache_dict.clear()
    r_target_cache_dict.clear()
    clear_import_reference_cache()
    clear_import_binder_cache()
    clear_import_path_cache()
    clear_definition_source_cache()
    path_config_cache_dict.clear()
    clear_package_path_cache()
    clear_alias_path_cache()


def _set_file_language(project_dir: str, all_file_list: list[str]) -> None:
    """Set which files of the project are analyzed without a language, as JCL or as COBOL.

    R Markdown and Quarto files without an R code chunk and files with a JCL or CSD
    extension that hold no JCL or CSD commands are analyzed without a language
    (set_no_language_file), and files without a language that are JCL or that COBOL
    COPY statements name are analyzed as JCL or COBOL (register_copy_target).

    Args:
        project_dir: Root directory of the project to analyze.
        all_file_list: Absolute paths of the text files of the project.
    """
    all_file_rel_list = [path_to_rel(file_path, project_dir) for file_path in all_file_list]
    set_no_language_file(project_dir, [
        *_no_chunk_document_list(project_dir, all_file_list),
        *no_jcl_file_list(project_dir, all_file_rel_list),
        *no_csd_file_list(project_dir, all_file_rel_list),
    ])
    register_copy_target(project_dir, all_file_rel_list)


def build_project_dependencies(
    project_dir: str,
    file_list: list[str] | None = None,
    exclude_dir: str | None = None,
) -> list[dict]:
    """Analyze inter-file dependencies within the project and build a dependency graph in memory.

    Return value structure (array):
        [
          {
            "file":    "project_name/src/foo_py/foo.py",
            "callers": ["project_name/src/bar_py/bar.py"],
            "callees": ["project_name/src/baz_py/baz.py"],
          },
          ...
        ]

    Paths use the "project_name/copy_path" format.
    Every non-empty text file that passes EXCLUDE_PATTERNS, is not left out by
    exclude_dir and has a path relative to project_dir that can be written as UTF-8 is
    listed. Import analysis runs on the files with a language (has_language); every
    other file is listed with empty callers and callees. A file without a language whose
    first statement is a JOB or JOBGROUP statement in upper case, or that an EXEC or
    INCLUDE statement of a JCL file names and whose first statement is a JCL statement
    in upper case, is analyzed as JCL (register_jcl_target), and one that a COBOL COPY
    statement names as a COBOL copybook; each keeps that language until the next call
    for the project (register_copy_target). An R Markdown or Quarto file without an R
    code chunk, a file with a JCL extension whose first statement is no JCL statement,
    and a file with a CSD extension whose first line that is no blank or comment is no
    DFHCSDUP command, is analyzed without a language, until the next call for the
    project (set_no_language_file).
    The indexes, the resolved references and the parse results of an earlier call are
    cleared (clear_analysis_cache).

    Processing flow:
    1. Collect every non-empty text file.
    2. Clear the caches of an earlier analysis.
    3. Set the files analyzed without a language, as JCL or as COBOL.
    4. Build the set of relative paths of the files with a language.
    5. Collect the files each file imports and refers to (callees).
    6. Build the callers (reverse lookup) index.
    7. Convert to "project_name/copy_path" paths.

    Args:
        project_dir: Root directory of the project to analyze, absolute or relative to
            the current directory. Its name is the project name of the paths.
        file_list: File paths relative to project_dir. When given, only these files
            are analyzed instead of walking project_dir.
        exclude_dir: The output directory, absolute or relative to the current
            directory, or None. The files under it are not analyzed (also when
            file_list names them) when it is project_dir or under it, and the files
            under exclude_dir/<name of project_dir> when that is project_dir or under
            it; otherwise nothing is left out. The paths are compared by real location
            (_exclude_path).

    Returns:
        A list of file dependency information dicts.
    """
    project_dir = os.path.abspath(project_dir)
    exclude_path = _exclude_path(project_dir, exclude_dir)

    # == Step 1: Collect every non-empty text file ==============================
    if file_list is None:
        all_file_list = _collect_text_file_list(project_dir, exclude_path)
    else:
        all_file_list = _filter_text_file_list(project_dir, file_list, exclude_path)

    # == Step 2: Clear the caches of an earlier analysis ========================
    clear_analysis_cache()

    # == Step 3: Set the files analyzed without a language, as JCL or as COBOL ==
    _set_file_language(project_dir, all_file_list)

    # == Step 4: Build the set of relative paths of the files with a language ===
    # Only the files with a language take part in import resolution and dependency
    # edges; the set tells whether a module is within the project
    language_file_list = [file_path for file_path in all_file_list if has_language(file_path)]
    project_file_set = {path_to_rel(file_path, project_dir) for file_path in language_file_list}

    # == Step 5: Collect the files each file imports and refers to (callees) ====
    file_callee_dict = _collect_callee_dict(language_file_list, project_dir, project_file_set)

    # == Step 6: Build the callers (reverse lookup) index =======================
    file_caller_dict: dict[str, list[str]] = {
        os.path.abspath(file_path): [] for file_path in language_file_list
    }
    for caller_path, callee_set in file_callee_dict.items():
        for callee_path in callee_set:
            if callee_path in file_caller_dict:
                file_caller_dict[callee_path].append(caller_path)

    # == Step 7: Convert to "project_name/copy_path" paths ======================
    return _to_output_entry_list(all_file_list, project_dir, file_caller_dict, file_callee_dict)
