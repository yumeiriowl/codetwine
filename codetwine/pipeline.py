import os
import json
import contextlib
import shutil
import logging
from collections import Counter
from codetwine.extractors.dependency_graph import build_project_dependencies, clear_analysis_cache
from codetwine.file_analyzer import get_file_dependencies
from codetwine.output import (
    save_consolidated_json,
    save_dependency_summary,
    save_dependency_graph_as_mermaid,
    build_symbol_level_dep_dict,
    to_output_path,
    build_summary_dict,
)
from codetwine.doc_creator import (
    check_code_summary_setting,
    check_doc_template,
    check_worker_count,
    generate_all_docs,
    sync_md_edit,
)
from codetwine.knowledge_db import save_consolidated_sqlite
from codetwine.llm.client import LLMClient
from codetwine.utils.file_utils import (
    DEPENDENCY_FILE_NAME,
    DOC_JSON_NAME,
    DOC_MD_NAME,
    check_source_encoding,
    compute_file_hash,
    load_doc,
    output_path_to_rel,
    resolve_file_output_dir,
)
from codetwine.config.logger import log_progress
from codetwine.config.settings import (
    MAX_WORKERS,
    ENABLE_LLM_DOC,
    KNOWLEDGE_FORMAT,
    KNOWLEDGE_FORMAT_TUPLE,
    PARSE_CACHE_MAX_FILES,
    has_language,
)

logger = logging.getLogger(__name__)


# File names of the design document in the output directory of a file
_DOC_FILE_TUPLE = (DOC_JSON_NAME, DOC_MD_NAME)

# File names the output directory of a file keeps for what is written there
_OUTPUT_FILE_NAME_SET = {DEPENDENCY_FILE_NAME, *_DOC_FILE_TUPLE}

# KNOWLEDGE_FORMAT value -> file name of the consolidated result in that form
_KNOWLEDGE_FILE_NAME_DICT = {
    "json":   "project_knowledge.json",
    "sqlite": "project_knowledge.sqlite",
}


def _to_internal_dep_list(
    raw_dep_list: list[dict],
) -> list[dict]:
    """Convert the paths of build_project_dependencies' result to relative paths from the project root.

    The result holds paths in "project_name/copy_path" format; the project name prefix is
    removed and copy_path is restored to the original relative path.

    Args:
        raw_dep_list: Return value of build_project_dependencies.

    Returns:
        A dependency list converted to internal path format.
    """
    return [
        {
            "file": output_path_to_rel(dep["file"]),
            "callers": [output_path_to_rel(caller) for caller in dep.get("callers", [])],
            "callees": [output_path_to_rel(callee) for callee in dep.get("callees", [])],
        }
        for dep in raw_dep_list
    ]


def _source_hash_dict(file_list: list[str], project_dir: str) -> dict[str, str]:
    """Return the SHA256 hash of the source of each file (compute_file_hash).

    Args:
        file_list: Relative paths of the files.
        project_dir: Absolute path to the project root.

    Returns:
        A {file relative path: hash} dict. A file that cannot be read has no entry.
    """
    source_hash_dict: dict[str, str] = {}
    for file_rel in file_list:
        with contextlib.suppress(OSError):
            source_hash_dict[file_rel] = compute_file_hash(os.path.join(project_dir, file_rel))
    return source_hash_dict


def _detect_change_file_set(
    all_file_list: list[str],
    source_hash_dict: dict[str, str],
    base_output_dir: str,
) -> set[str]:
    """Detect changed files by comparing source file hashes with the ones recorded in doc.json.

    The following files are returned as "changed":
    - Files whose output has no readable doc.json.
    - Files whose source hash differs from the source_hash recorded in doc.json, or
      whose source has no hash in source_hash_dict.

    Args:
        all_file_list: List of all file relative paths within the project.
        source_hash_dict: {file relative path: hash of its source} (_source_hash_dict).
        base_output_dir: Absolute path to the output root directory.

    Returns:
        A set of relative paths of files where changes were detected.
    """
    change_file_set: set[str] = set()
    for file_rel in all_file_list:
        doc = load_doc(resolve_file_output_dir(base_output_dir, file_rel))
        source_hash = source_hash_dict.get(file_rel)
        if doc is None or source_hash is None or doc.get("source_hash") != source_hash:
            change_file_set.add(file_rel)
    return change_file_set


def _ext_count_line(file_list: list[str]) -> str:
    """Count the files per extension and return it as one line, most common first.

    Examples:
        ["a.py", "b.py", "c.md", "Makefile"] -> "py 2, md 1, (none) 1"

    Args:
        file_list: File paths; only their extensions are looked at.

    Returns:
        "<ext> <count>" pairs joined by ", ". A file without an extension is "(none)".
    """
    ext_count_dict = Counter(
        os.path.splitext(file_path)[1].lstrip(".") or "(none)" for file_path in file_list
    )
    return ", ".join(f"{ext} {count}" for ext, count in ext_count_dict.most_common())


def _to_output_format(file_dep_dict: dict, base_output_dir: str) -> None:
    """Convert the paths of a get_file_dependencies result to "project_name/copy_path" format.

    The file field, callee_usages[].from and caller_usages[].file are rewritten in place.

    Args:
        file_dep_dict: Return value of get_file_dependencies.
        base_output_dir: Absolute path to the output root directory.
    """
    file_dep_dict["file"] = to_output_path(base_output_dir, file_dep_dict["file"])
    for usage in file_dep_dict.get("callee_usages", []):
        if "from" in usage:
            usage["from"] = to_output_path(base_output_dir, usage["from"])
    for usage in file_dep_dict.get("caller_usages", []):
        if "file" in usage:
            usage["file"] = to_output_path(base_output_dir, usage["file"])


def _process_file_dependency(
    file_rel_list: list[str],
    project_dir: str,
    base_output_dir: str,
    language_dep_list: list[dict],
) -> tuple[list[str], dict[str, str]]:
    """Analyze dependency info for each file and save file_dependencies.json
    and a copy of the original file to the output directory. A file named like a file
    written there (file_dependencies.json, doc.json, doc.md) is not copied.

    When the analysis of a file fails, the file_dependencies.json and the copy that a
    previous run left in its output directory are removed. For a file analyzed without
    a language, the design document (doc.json, doc.md) a previous run left is removed.

    Args:
        file_rel_list: List of relative paths of files to process.
        project_dir: Absolute path to the project root.
        base_output_dir: Absolute path to the output root directory.
        language_dep_list: Dependency list (internal path format) of the files that have
            a language. Import resolution looks up dependency targets in these only.

    Returns:
        A (fail list, detected encoding dict) tuple: the relative paths of the files
        whose analysis failed, and {relative path: detected_encoding} of the files whose
        encoding read_source() had to detect.
    """
    log_progress(logger, f"Extracting dependencies for {len(file_rel_list)} files...")

    # Lookups that are the same for every file, built once and passed to each call
    project_file_set = {dep_info["file"] for dep_info in language_dep_list}
    caller_dict = {dep_info["file"]: dep_info.get("callers", []) for dep_info in language_dep_list}

    fail_file_list: list[str] = []
    encoding_dict: dict[str, str] = {}
    for file_rel in file_rel_list:
        file_abs = os.path.join(project_dir, file_rel)
        output_file_dir = resolve_file_output_dir(base_output_dir, file_rel)
        try:
            os.makedirs(output_file_dir, exist_ok=True)

            file_dep_dict = get_file_dependencies(
                file_abs, project_dir,
                project_file_set, caller_dict,
            )
            if file_dep_dict["detected_encoding"] is not None:
                encoding_dict[file_rel] = file_dep_dict["detected_encoding"]

            _to_output_format(file_dep_dict, base_output_dir)

            with open(os.path.join(output_file_dir, DEPENDENCY_FILE_NAME), "w", encoding="utf-8") as f:
                json.dump(file_dep_dict, f, indent=2, ensure_ascii=False)

            # Copy the original file to the output directory, unless it is named like
            # a file written there
            if os.path.basename(file_rel) not in _OUTPUT_FILE_NAME_SET:
                shutil.copy2(file_abs, os.path.join(output_file_dir, os.path.basename(file_rel)))

            # Remove the design document a previous run left for a file without a language
            if file_dep_dict["language"] == "":
                for output_name in _DOC_FILE_TUPLE:
                    with contextlib.suppress(OSError):
                        os.remove(os.path.join(output_file_dir, output_name))

            logger.info(f"  OK: {file_rel}")
        except Exception as e:
            logger.error(f"  FAIL: {file_rel}: {e}")
            fail_file_list.append(file_rel)
            # Remove what a previous run left for this file
            for output_name in (DEPENDENCY_FILE_NAME, os.path.basename(file_rel)):
                with contextlib.suppress(OSError):
                    os.remove(os.path.join(output_file_dir, output_name))

    return fail_file_list, encoding_dict


def _check_setting(max_workers: int, llm_client: LLMClient | None) -> None:
    """Check the settings process_all_files reads.

    Args:
        max_workers: The max_workers argument of process_all_files.
        llm_client: The llm_client argument of process_all_files.

    Raises:
        ValueError: When KNOWLEDGE_FORMAT is not one of KNOWLEDGE_FORMAT_TUPLE,
            SOURCE_ENCODING names an unknown codec or one that is no text encoding,
            PARSE_CACHE_MAX_FILES is negative, or, while ENABLE_LLM_DOC is True,
            max_workers is less than 1, CODE_SUMMARY_PIECE_LINES is less than 1 with
            ENABLE_CODE_SUMMARY True, the template DOC_TEMPLATE_PATH names cannot be
            read or has a wrong form, or llm_client is None.
    """
    if KNOWLEDGE_FORMAT not in KNOWLEDGE_FORMAT_TUPLE:
        raise ValueError(
            f"KNOWLEDGE_FORMAT must be one of "
            f"{' / '.join(KNOWLEDGE_FORMAT_TUPLE)}, but got '{KNOWLEDGE_FORMAT}'. "
            f"Set it in the .env file or your shell."
        )
    check_source_encoding()
    if PARSE_CACHE_MAX_FILES < 0:
        raise ValueError(
            f"PARSE_CACHE_MAX_FILES must be 0 or more, but got {PARSE_CACHE_MAX_FILES}. "
            f"Set it in the .env file or your shell."
        )
    if ENABLE_LLM_DOC:
        check_worker_count(max_workers)
        check_code_summary_setting()
        check_doc_template()
        if llm_client is None:
            raise ValueError(
                "ENABLE_LLM_DOC is True but no LLM client was given. Pass LLMClient() as "
                "llm_client to process_all_files(), or set ENABLE_LLM_DOC=False to run "
                "without design documents."
            )


def _is_same_dir(path: str, other_path: str) -> bool:
    """Return whether two paths lead to the same location (os.path.realpath), existing or not."""
    return (
        os.path.normcase(os.path.realpath(path)) == os.path.normcase(os.path.realpath(other_path))
    )


def _base_output_dir(project_dir: str, output_dir: str) -> str:
    """Return the directory the outputs of a project are written to: output_dir/<project name>.

    Args:
        project_dir: Absolute path to the project root.
        output_dir: Output directory for analysis results, absolute or relative to the
            current directory.

    Returns:
        The absolute path of the directory.

    Raises:
        ValueError: When project_dir is not a directory, has no name (the file system
            root), has a name that cannot be written as UTF-8, is output_dir, or is the
            directory the outputs would be written to (output_dir is its parent). The
            directories are compared by real location (os.path.realpath).
    """
    project_name = os.path.basename(project_dir)
    if not os.path.isdir(project_dir):
        raise ValueError(
            f"The project directory {project_dir} does not exist or is not a directory. "
            f"Pass the root directory of the project to analyze (--project-dir or "
            f"DEFAULT_PROJECT_DIR)."
        )
    if not project_name:
        raise ValueError(
            f"The project directory {project_dir} has no name to write the outputs under. "
            f"Pass a directory below the file system root."
        )
    try:
        project_name.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(
            f"The name of the project directory {project_dir!r} is not valid UTF-8. "
            f"Rename the directory."
        ) from None
    base_output_dir = os.path.join(os.path.abspath(output_dir), project_name)
    if _is_same_dir(output_dir, project_dir):
        raise ValueError(
            f"The output directory {output_dir} is the project directory {project_dir}. "
            f"Pass a directory below the project directory (such as "
            f"{os.path.join(project_dir, 'output')}) or outside it (--output-dir or "
            f"DEFAULT_OUTPUT_DIR)."
        )
    if _is_same_dir(base_output_dir, project_dir):
        raise ValueError(
            f"The outputs of {project_dir} would be written into the project directory "
            f"itself. Pass an output directory other than the parent of the project "
            f"directory (--output-dir or DEFAULT_OUTPUT_DIR)."
        )
    return base_output_dir


def _remove_knowledge_file(knowledge_path: str) -> None:
    """Remove the consolidated result of a form KNOWLEDGE_FORMAT does not select, left by an earlier run."""
    with contextlib.suppress(FileNotFoundError):
        os.remove(knowledge_path)
        logger.info(f"Removed {knowledge_path}: KNOWLEDGE_FORMAT is {KNOWLEDGE_FORMAT}")


async def process_all_files(
    project_dir: str,
    output_dir: str,
    llm_client: LLMClient | None,
    max_workers: int = MAX_WORKERS,
    file_list: list[str] | None = None,
) -> dict:
    """Analyze the entire project and output per-file dependency JSON, design documents,
    and consolidated JSON.

    Processing flow:
    1. Build the project-wide dependency graph over every non-empty text file.
       The output directory is left out when it is inside project_dir.
    2. Extract dependency info for all files, changed or not.
       A file without a language (has_language) gets empty lists.
    3. Detect changed files and generate design documents in topological order
       (regenerate only the impact range of changes). Only files with a language
       get a design document. With ENABLE_LLM_DOC False, the manual edits of the
       doc.md of each file whose doc.json is current are synced to doc.json instead.
    4. Generate dependency graph + summary consolidated JSON.
    5. Generate Mermaid dependency graph diagram.
    6. Generate the consolidated result in the form KNOWLEDGE_FORMAT selects, and remove
       the one of the other form an earlier run left.

    The consolidated outputs (steps 4 to 6) leave out the files whose dependency
    extraction failed, and take the design document of a file only when its doc.json
    was generated from the current source (source_hash).

    Args:
        project_dir: Root directory of the project to analyze, absolute or relative to
            the current directory. Its name is the project name.
        output_dir: Output directory for analysis results, absolute or relative to the
            current directory. The outputs are written to output_dir/<project name>.
        llm_client: LLM client the design documents are written with; None only when
            ENABLE_LLM_DOC is False.
        max_workers: Number of design documents generated in parallel (ENABLE_LLM_DOC).
        file_list: File paths relative to project_dir. When given, only these files
            are analyzed instead of walking project_dir. EXCLUDE_PATTERNS and the
            text file check apply to them as well.

    Returns:
        A dict with the following keys.
            "file_count": Number of files analyzed.
            "dependency_fail_list": Relative paths of the files whose dependency
                extraction failed.
            "doc_count": Number of files that have a complete design document
                (0 when ENABLE_LLM_DOC is False).
            "doc_fail_list": Relative paths of the files left without a complete
                design document (empty when ENABLE_LLM_DOC is False).
            "detected_encoding_dict": {relative path: encoding} of the files that
                neither the codec of a BOM, UTF-8 nor SOURCE_ENCODING decoded without
                error; the encoding is "" for a file read as UTF-8 with invalid bytes
                replaced, and "utf-16" or "utf-32" for a file with that BOM read with
                invalid bytes replaced.

    Raises:
        ValueError: When KNOWLEDGE_FORMAT is not one of KNOWLEDGE_FORMAT_TUPLE,
            SOURCE_ENCODING names an unknown codec or one that is no text encoding,
            PARSE_CACHE_MAX_FILES is negative, or, while ENABLE_LLM_DOC is True,
            max_workers is less than 1, CODE_SUMMARY_PIECE_LINES is less than 1 with
            ENABLE_CODE_SUMMARY True, the template DOC_TEMPLATE_PATH names cannot be
            read or has a wrong form, or llm_client is None; when project_dir is not a
            directory, is the file system root or has a name that is not valid UTF-8;
            and when output_dir is project_dir or its parent.
    """
    # Checked before any analysis runs
    _check_setting(max_workers, llm_client)
    project_dir = os.path.abspath(project_dir)
    base_output_dir = _base_output_dir(project_dir, output_dir)
    os.makedirs(base_output_dir, exist_ok=True)

    # == Step 1: Build the project-wide dependency graph ====================
    log_progress(logger, "Analyzing project dependencies...")
    raw_dep_list = build_project_dependencies(project_dir, file_list, output_dir)
    project_dep_list = _to_internal_dep_list(raw_dep_list)

    all_file_list = [dep_info["file"] for dep_info in project_dep_list]

    # The files with a tree-sitter language: the only ones with definitions,
    # dependency targets and design documents
    language_dep_list = [
        dep_info for dep_info in project_dep_list
        if has_language(os.path.join(project_dir, dep_info["file"]))
    ]
    language_file_list = [dep_info["file"] for dep_info in language_dep_list]

    log_progress(logger, f"Files to analyze: {len(all_file_list)} ({_ext_count_line(all_file_list)})")

    # == Step 2: Extract dependency info for all files ========================
    dependency_fail_list, encoding_dict = _process_file_dependency(
        all_file_list, project_dir, base_output_dir,
        language_dep_list,
    )

    # == Step 3: Generate design documents in topological order ================
    source_hash_dict = _source_hash_dict(language_file_list, project_dir)
    change_file_set = _detect_change_file_set(language_file_list, source_hash_dict, base_output_dir)
    doc_fail_list: list[str] = []
    if ENABLE_LLM_DOC:
        log_progress(logger, f"Change detection: {len(change_file_set)} changed / {len(language_file_list)} total")

        log_progress(logger, "Generating design documents...")
        doc_fail_list = await generate_all_docs(
            base_output_dir, language_dep_list, llm_client, max_workers,
            change_file_set,
        )
    else:
        log_progress(logger, "ENABLE_LLM_DOC=False: skipping design document generation")
        sync_md_edit(
            base_output_dir,
            [file_rel for file_rel in language_file_list if file_rel not in change_file_set],
        )

    # == Step 4: Generate dependency graph + summary consolidated JSON ============
    log_progress(logger, "Generating dependency graph + summary JSON...")

    # The files the consolidated outputs list: those whose dependency extraction succeeded
    dependency_fail_set = set(dependency_fail_list)
    output_file_list = [file_rel for file_rel in all_file_list if file_rel not in dependency_fail_set]

    # Build symbol-level dependencies and the summaries of the current design documents
    # once and share them across the functions writing the consolidated outputs
    symbol_dep_dict = build_symbol_level_dep_dict(base_output_dir, output_file_list)

    summary_dict = build_summary_dict(base_output_dir, output_file_list, source_hash_dict)

    dep_summary_path = os.path.join(base_output_dir, "project_dependency_summary.json")
    save_dependency_summary(
        base_output_dir, output_file_list, dep_summary_path, symbol_dep_dict, summary_dict,
    )

    # == Step 5: Generate Mermaid dependency graph ========================
    mermaid_output_path = os.path.join(base_output_dir, "dependency_graph.md")
    save_dependency_graph_as_mermaid(base_output_dir, mermaid_output_path, symbol_dep_dict)

    # == Step 6: Generate the consolidated result ============================
    knowledge_path = os.path.join(base_output_dir, _KNOWLEDGE_FILE_NAME_DICT["json"])
    if KNOWLEDGE_FORMAT in ("json", "both"):
        log_progress(logger, "Generating consolidated JSON...")
        save_consolidated_json(
            base_output_dir, output_file_list, knowledge_path, symbol_dep_dict, summary_dict,
        )
    else:
        _remove_knowledge_file(knowledge_path)

    knowledge_db_path = os.path.join(base_output_dir, _KNOWLEDGE_FILE_NAME_DICT["sqlite"])
    if KNOWLEDGE_FORMAT in ("sqlite", "both"):
        log_progress(logger, "Generating consolidated SQLite database...")
        save_consolidated_sqlite(
            base_output_dir, output_file_list, knowledge_db_path, symbol_dep_dict, summary_dict,
        )
    else:
        _remove_knowledge_file(knowledge_db_path)

    clear_analysis_cache()

    log_progress(logger, "Analysis complete.")

    doc_count = len(language_file_list) - len(doc_fail_list) if ENABLE_LLM_DOC else 0
    return {
        "file_count":           len(all_file_list),
        "dependency_fail_list": dependency_fail_list,
        "doc_count":            doc_count,
        "doc_fail_list":        doc_fail_list,
        "detected_encoding_dict": encoding_dict,
    }
