import os
import fnmatch
import logging
from codetwine.parsers.ts_parser import blank_macro_cache, parse_cache, parse_file
from codetwine.extractors.definition_source import clear_definition_source_cache
from codetwine.extractors.imports import extract_imports
from codetwine.cobol_file_index import (
    file_index_cache,
    reference_target_cache,
    register_copy_target,
)
from codetwine.csharp_namespace_index import csharp_target_cache, namespace_index_cache
from codetwine.import_binding import clear_import_binder_cache, import_binder
from codetwine.import_reference import clear_import_reference_cache
from codetwine.rust_module_tree import module_tree_cache
from codetwine.parsers.r_markdown import has_r_chunk
from codetwine.path_config import path_config_cache
from codetwine.package_path import clear_package_path_cache
from codetwine.alias_path import clear_alias_path_cache
from codetwine.r_name_index import r_import_file_list, r_name_index_cache, r_target_cache
from codetwine.reference_target import reference_kind, reference_target_list
from codetwine.import_to_path import (
    clear_import_path_cache,
    resolve_module_to_project_path,
    get_import_params,
)
from codetwine.utils.file_utils import is_text_file, lone_cr_to_lf, read_source, rel_to_copy_path
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


def _collect_text_file_list(project_dir: str) -> list[str]:
    """Walk the project and return the absolute paths of its non-empty text files.

    Directories and files matching EXCLUDE_PATTERNS are left out, and so are symbolic
    links (_is_own_file) and empty, binary and unreadable files (is_text_file). Linked
    directories are not entered. The number of skipped files is logged.

    Args:
        project_dir: Root directory of the project to analyze.

    Returns:
        Absolute file paths in os.walk order.
    """
    text_file_list: list[str] = []
    skip_count = 0
    for dir_path, dir_name_list, file_name_list in os.walk(project_dir):
        # Remove directories matching exclude patterns from the traversal targets
        # Modifying dir_names in-place causes os.walk to skip those subtrees
        dir_name_list[:] = [d for d in dir_name_list if not any(fnmatch.fnmatch(d, p) for p in EXCLUDE_PATTERNS)]
        for file_name in file_name_list:
            if any(fnmatch.fnmatch(file_name, p) for p in EXCLUDE_PATTERNS):
                continue
            file_path = os.path.join(dir_path, file_name)
            if not _is_own_file(project_dir, file_path):
                continue
            if is_text_file(file_path):
                text_file_list.append(file_path)
            else:
                skip_count += 1
    if skip_count:
        logger.info(f"Skipped {skip_count} empty, binary or unreadable files")
    return text_file_list


def _filter_text_file_list(project_dir: str, file_list: list[str]) -> list[str]:
    """Return the absolute paths of the non-empty text files among the given files.

    The same files are left out as in _collect_text_file_list: a path with a directory
    or file name matching EXCLUDE_PATTERNS, symbolic links and paths under a linked
    directory (_is_own_file), and empty, binary and unreadable files (is_text_file). A
    path outside project_dir is left out too. The number of skipped files is logged.

    Args:
        project_dir: Root directory of the project to analyze.
        file_list: File paths relative to project_dir.

    Returns:
        Absolute file paths in the order given, without duplicates.
    """
    text_file_list: list[str] = []
    known_path_set: set[str] = set()
    skip_count = 0
    for file_rel in file_list:
        file_path = os.path.normpath(os.path.join(project_dir, file_rel))
        part_list = os.path.relpath(file_path, project_dir).replace("\\", "/").split("/")
        if part_list[0] == ".." or file_path in known_path_set:
            continue
        known_path_set.add(file_path)
        if any(fnmatch.fnmatch(part, p) for part in part_list for p in EXCLUDE_PATTERNS):
            continue
        if not _is_own_file(project_dir, file_path):
            continue
        if is_text_file(file_path):
            text_file_list.append(file_path)
        else:
            skip_count += 1
    if skip_count:
        logger.info(f"Skipped {skip_count} empty, binary or unreadable files")
    return text_file_list


def _to_rel(path: str, project_dir: str) -> str:
    """Return a path relative to project_dir with "/" separators."""
    return os.path.relpath(path, project_dir).replace("\\", "/")


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
        if os.path.splitext(file_path)[1].lstrip(".").lower() not in R_MARKDOWN_EXT_SET:
            continue
        try:
            content = lone_cr_to_lf(read_source(file_path)[0]).encode("utf-8")
        except OSError:
            continue
        if not has_r_chunk(content):
            document_list.append(_to_rel(file_path, project_dir))
    return document_list


def _import_callee_rel_set(
    file_path: str, file_rel: str, project_file_set: set[str], project_dir: str,
) -> set[str]:
    """Return the project files the import statements of a file resolve to.

    For a file whose references are resolved through its import statements these are
    the files its ImportBinder gives; for a COBOL file, the files its COPY and CALL
    statements resolve to.

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
    language, import_query_str = get_import_params(file_ext)
    if not language or file_ext not in EXT_TO_IMPORT_RESOLVE_DICT:
        return set()
    root_node = parse_file(file_path)[0]
    callee_rel_set: set[str] = set()
    for import_info in extract_imports(root_node, language, import_query_str):
        resolved = resolve_module_to_project_path(
            import_info.module, file_rel, project_file_set, None, project_dir,
        )
        if resolved:
            callee_rel_set.add(resolved)
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
    the file (COBOL). Both are read for one file before the next file is read, so the
    syntax tree of the file is read while it is in parse_cache.

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
        file_rel = _to_rel(file_path, project_dir)
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
        return f"{project_name}/{rel_to_copy_path(_to_rel(path, project_dir))}"

    file_info_list = []
    for file_path in all_file_list:
        abs_path = os.path.abspath(file_path)
        file_info_list.append({
            "file":    to_output(abs_path),
            "callers": sorted(to_output(caller) for caller in file_caller_dict.get(abs_path, [])),
            "callees": sorted(to_output(callee) for callee in file_callee_dict.get(abs_path, ())),
        })
    return file_info_list


def build_project_dependencies(
    project_dir: str,
    file_list: list[str] | None = None,
) -> list[dict]:
    """Analyze inter-file dependencies within the project and build a dependency graph in memory.

    Return value structure (array):
        [
          {
            "file":    "project_name/src/foo.py/foo.py",
            "callers": ["project_name/src/bar.py/bar.py"],
            "callees": ["project_name/src/baz.py/baz.py"],
          },
          ...
        ]

    Paths use the "project_name/copy_path" format.
    Every non-empty text file that passes EXCLUDE_PATTERNS is listed. Import analysis
    runs on the files with a language (has_language); every other file is listed with
    empty callers and callees. A file without a language that a COBOL COPY statement
    names is analyzed as a COBOL copybook, and keeps that language until the next call
    for the project (register_copy_target). An R Markdown or Quarto file without an R
    code chunk is analyzed without a language, until the next call for the project
    (set_no_language_file).
    The indexes built from the files of a project and the references resolved with
    them are cleared: the Rust module trees (module_tree_cache), the C# namespace
    indexes (namespace_index_cache, csharp_target_cache), the R name indexes
    (r_name_index_cache, r_target_cache), the COBOL file indexes (file_index_cache,
    reference_target_cache) and the names and references resolved through import
    statements (clear_import_reference_cache, clear_import_binder_cache,
    clear_import_path_cache), the definitions read for a definition's source
    (clear_definition_source_cache) and the path settings of the JS/TS config files
    (path_config_cache, the package and alias caches). The parse results (parse_cache) are kept.

    Args:
        project_dir: Root directory of the project to analyze.
        file_list: File paths relative to project_dir. When given, only these files
            are analyzed instead of walking project_dir.

    Returns:
        A list of file dependency information dicts.
    """
    # == Step 1: Collect every non-empty text file ==============================
    if file_list is None:
        all_file_list = _collect_text_file_list(project_dir)
    else:
        all_file_list = _filter_text_file_list(project_dir, file_list)

    # Forget the syntax trees, the indexes and the resolved references of an earlier analysis
    parse_cache.clear()
    blank_macro_cache.clear()
    module_tree_cache.clear()
    file_index_cache.clear()
    namespace_index_cache.clear()
    r_name_index_cache.clear()
    reference_target_cache.clear()
    csharp_target_cache.clear()
    r_target_cache.clear()
    clear_import_reference_cache()
    clear_import_binder_cache()
    clear_import_path_cache()
    clear_definition_source_cache()
    path_config_cache.clear()
    clear_package_path_cache()
    clear_alias_path_cache()

    # R Markdown and Quarto files without an R code chunk are analyzed without a language,
    # and files without a language that COBOL COPY statements name are analyzed as COBOL
    set_no_language_file(project_dir, _no_chunk_document_list(project_dir, all_file_list))
    register_copy_target(project_dir, [_to_rel(f, project_dir) for f in all_file_list])

    # Only the files with a language take part in import resolution and dependency edges
    language_file_list = [f for f in all_file_list if has_language(f)]

    # == Step 2: Build the set of relative paths for project files ============
    # A lookup set used to determine whether a module is within the project during import resolution
    project_file_set = {_to_rel(file_path, project_dir) for file_path in language_file_list}

    # == Step 3: Collect the files each file imports and refers to (callees) ==
    file_callee_dict = _collect_callee_dict(language_file_list, project_dir, project_file_set)

    # == Step 4: Build the callers (reverse lookup) index ==================
    file_caller_dict: dict[str, list[str]] = {os.path.abspath(f): [] for f in language_file_list}
    for caller_path, callee_set in file_callee_dict.items():
        for callee_path in callee_set:
            if callee_path in file_caller_dict:
                file_caller_dict[callee_path].append(caller_path)

    # == Step 5: Convert to "project_name/copy_path" paths ====================
    return _to_output_entry_list(all_file_list, project_dir, file_caller_dict, file_callee_dict)
