import os
import json
import shutil
import logging
from codetwine.parsers.ts_parser import parse_cache
from codetwine.extractors.dependency_graph import build_project_dependencies
from codetwine.file_analyzer import get_file_dependencies
from codetwine.output import (
    save_consolidated_json,
    save_dependency_summary,
    save_dependency_graph_as_mermaid,
    build_symbol_level_deps,
    to_output_path,
    build_summary_map,
)
from codetwine.doc_creator import generate_all_docs
from codetwine.llm.client import LLMClient
from codetwine.utils.file_utils import (
    copy_path_to_rel,
    is_file_unchanged,
    resolve_file_output_dir,
)
from codetwine.config.settings import (
    MAX_WORKERS,
    ENABLE_LLM_DOC,
)

logger = logging.getLogger(__name__)


def _convert_dep_list_to_internal_paths(
    project_dep_list_raw: list[dict],
    project_name: str,
) -> list[dict]:
    """Convert paths from project_dependencies.json to relative paths for internal pipeline use.

    project_dependencies.json stores paths in "project_name/copy_path" format.
    Since the pipeline internally processes using only relative paths from the project root,
    this performs project name prefix removal + copy_path to original relative path restoration.

    Args:
        project_dep_list_raw: Return value of save_project_dependencies.
        project_name: The project name (e.g. "my-project").

    Returns:
        A dependency list converted to internal path format.
    """
    prefix = f"{project_name}/"

    def to_internal(path: str) -> str:
        stripped = path[len(prefix):] if path.startswith(prefix) else path
        return copy_path_to_rel(stripped)

    return [
        {
            "file": to_internal(dep["file"]),
            "callers": [to_internal(c) for c in dep.get("callers", [])],
            "callees": [to_internal(c) for c in dep.get("callees", [])],
        }
        for dep in project_dep_list_raw
    ]


def _detect_changed_files(
    all_file_list: list[str],
    project_dir: str,
    base_output_dir: str,
) -> set[str]:
    """Detect changed files by comparing source file hashes with output copies.

    The following files are returned as "changed":
    - Files whose source hash differs from the output copy.
    - Files without a file_dependencies.json in the output
      (to recover from an incomplete state where previous processing failed midway).

    Args:
        all_file_list: List of all file relative paths within the project.
        project_dir: Absolute path to the project root.
        base_output_dir: Absolute path to the output root directory.

    Returns:
        A set of relative paths of files where changes were detected.
    """
    changed: set[str] = set()
    for file_rel in all_file_list:
        file_abs = os.path.join(project_dir, file_rel)
        output_file_dir = resolve_file_output_dir(base_output_dir, file_rel)
        copied_path = os.path.join(output_file_dir, os.path.basename(file_rel))
        deps_json_path = os.path.join(output_file_dir, "file_dependencies.json")

        if not is_file_unchanged(file_abs, copied_path) or not os.path.exists(deps_json_path):
            changed.add(file_rel)
    return changed


def _process_file_dependencies(
    files_to_process: list[str],
    project_dir: str,
    base_output_dir: str,
    project_dep_list: list[dict],
) -> None:
    """Analyze dependency info for each file and save file_dependencies.json
    and a copy of the original file to the output directory.

    Args:
        files_to_process: List of relative paths of files to process.
        project_dir: Absolute path to the project root.
        base_output_dir: Absolute path to the output root directory.
        project_dep_list: Project-wide dependency list (internal path format).
    """
    print(f"Extracting dependencies for {len(files_to_process)} files...")
    logger.info(f"Extracting dependencies for {len(files_to_process)} files...")

    for file_rel in files_to_process:
        try:
            file_abs = os.path.join(project_dir, file_rel)
            output_file_dir = resolve_file_output_dir(base_output_dir, file_rel)

            os.makedirs(output_file_dir, exist_ok=True)

            dep_result = get_file_dependencies(
                file_abs, project_dir, project_dep_list,
            )

            # Convert paths to output format (project_name/copy_path)
            dep_result["file"] = to_output_path(base_output_dir, dep_result["file"])
            for usage in dep_result.get("callee_usages", []):
                if "from" in usage:
                    usage["from"] = to_output_path(base_output_dir, usage["from"])
            for usage in dep_result.get("caller_usages", []):
                if "file" in usage:
                    usage["file"] = to_output_path(base_output_dir, usage["file"])

            with open(os.path.join(output_file_dir, "file_dependencies.json"), "w", encoding="utf-8") as f:
                json.dump(dep_result, f, indent=2, ensure_ascii=False)

            # Copy the original file to the output directory
            shutil.copy2(file_abs, os.path.join(output_file_dir, os.path.basename(file_rel)))

            logger.info(f"  OK: {file_rel}")
        except Exception as e:
            logger.error(f"  FAIL: {file_rel}: {e}")


async def process_all_files(
    project_dir: str,
    output_dir: str,
    llm_client: LLMClient | None,
    max_workers: int = MAX_WORKERS,
) -> None:
    """Analyze the entire project and output per-file dependency JSON, design documents,
    and consolidated JSON.

    Processing flow:
    1. Build the project-wide dependency graph.
    1.5. Detect changed files (for Stage 4 impact range identification).
    2. Extract dependency info for all files (always process all for consistency).
    3. Generate design documents in topological order (regenerate only the impact range of changes).
    3.5. Generate dependency graph + summary consolidated JSON.
    4. Generate Mermaid dependency graph diagram.
    5. Generate consolidated JSON.

    Args:
        project_dir: Root directory of the project to analyze.
        output_dir: Output directory for analysis results.
        llm_client: LLM summary generation client.
        max_workers: Maximum number of files to process concurrently.
    """
    project_name = os.path.basename(project_dir)
    base_output_dir = os.path.join(output_dir, project_name)
    os.makedirs(base_output_dir, exist_ok=True)

    # == Step 1: Build the project-wide dependency graph ====================
    print("Analyzing project dependencies...")
    logger.info("Analyzing project dependencies...")
    project_dep_list_raw = build_project_dependencies(project_dir)
    project_dep_list = _convert_dep_list_to_internal_paths(project_dep_list_raw, project_name)

    all_file_list = [info["file"] for info in project_dep_list]

    # Exclude empty files from processing
    empty_files = set()
    for file_rel in all_file_list:
        file_abs = os.path.join(project_dir, file_rel)
        try:
            with open(file_abs, "r", encoding="utf-8") as f:
                if not f.read().strip():
                    empty_files.add(file_rel)
        except (OSError, UnicodeDecodeError):
            pass
    if empty_files:
        project_dep_list = [info for info in project_dep_list if info["file"] not in empty_files]
        all_file_list = [f for f in all_file_list if f not in empty_files]
        print(f"Excluded {len(empty_files)} empty files: {sorted(empty_files)}")
        logger.info(f"Excluded {len(empty_files)} empty files: {sorted(empty_files)}")

    print(f"Files to analyze: {len(all_file_list)}")
    logger.info(f"Files to analyze: {len(all_file_list)}")

    # == Step 1.5: Detect changed files ==============================
    changed_files = _detect_changed_files(all_file_list, project_dir, base_output_dir)
    print(f"Change detection: {len(changed_files)} changed / {len(all_file_list)} total")
    logger.info(f"Change detection: {len(changed_files)} changed / {len(all_file_list)} total")

    # == Step 2: Extract dependency info for all files ========================
    _process_file_dependencies(
        all_file_list, project_dir, base_output_dir,
        project_dep_list,
    )

    # == Step 3: Generate design documents in topological order ================
    if ENABLE_LLM_DOC:
        print("Generating design documents...")
        logger.info("Generating design documents...")
        await generate_all_docs(
            base_output_dir, project_dep_list, llm_client, max_workers,
            changed_files,
        )
    else:
        print("ENABLE_LLM_DOC=False: skipping design document generation")
        logger.info("ENABLE_LLM_DOC=False: skipping design document generation")

    # == Step 3.5: Generate dependency graph + summary consolidated JSON ==========
    print("Generating dependency graph + summary JSON...")
    logger.info("Generating dependency graph + summary JSON...")

    # Build symbol-level dependencies once and share across the 3 subsequent functions
    symbol_deps = build_symbol_level_deps(base_output_dir, all_file_list)

    summary_map = build_summary_map(base_output_dir, all_file_list)

    dep_summary_path = os.path.join(base_output_dir, "project_dependency_summary.json")
    save_dependency_summary(base_output_dir, all_file_list, dep_summary_path, symbol_deps, summary_map)

    # == Step 4: Generate Mermaid dependency graph ========================
    mermaid_output_path = os.path.join(base_output_dir, "dependency_graph.md")
    save_dependency_graph_as_mermaid(base_output_dir, mermaid_output_path, symbol_deps)

    # == Step 5: Generate consolidated JSON ==================================
    print("Generating consolidated JSON...")
    logger.info("Generating consolidated JSON...")
    knowledge_path = os.path.join(base_output_dir, "project_knowledge.json")
    save_consolidated_json(base_output_dir, all_file_list, knowledge_path, symbol_deps, summary_map)

    # Clear parse result cache to free memory
    parse_cache.clear()

    print("Analysis complete.")
    logger.info("Analysis complete.")
