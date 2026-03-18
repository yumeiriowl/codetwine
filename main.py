"""codetwine

Usage:
    uv run main.py [--project-dir DIR] [--output-dir DIR]

Description:
    - Analyzes source code under the specified project and extracts inter-file dependencies
    - Generates detailed design documents for each file via LLM
    - Saves dependency information, design documents, consolidated JSON, and Mermaid graphs to the output directory
"""

import argparse
import asyncio
import os
from codetwine.pipeline import process_all_files
from codetwine.llm.client import LLMClient
from codetwine.config.settings import (
    DEFAULT_PROJECT_DIR,
    DEFAULT_OUTPUT_DIR,
    ENABLE_LLM_DOC,
    REPO_ROOT,
)
from codetwine.config.logger import setup_logging


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        argparse.Namespace: Parsed argument object.
    """
    parser = argparse.ArgumentParser(
        description="Automatically generate inter-file dependencies and design documents from project source code, output as JSON",
    )
    parser.add_argument(
        "--project-dir",
        help="Root directory of the project to analyze (defaults to DEFAULT_PROJECT_DIR from .env)",
    )
    parser.add_argument(
        "--output-dir",
        help="Output directory for analysis results (defaults to DEFAULT_OUTPUT_DIR from .env)",
    )
    return parser.parse_args()


def resolve_dirs(args: argparse.Namespace) -> tuple[str, str]:
    """Determine project_dir and output_dir from CLI arguments and .env settings.

    When only --project-dir is specified and --output-dir is omitted,
    DEFAULT_OUTPUT_DIR from .env is ignored and the default value ({REPO_ROOT}/output) is used.

    Args:
        args: Command-line arguments obtained from parse_args().

    Returns:
        tuple[str, str]: (project_dir, output_dir) tuple.
    """
    project_dir = args.project_dir or DEFAULT_PROJECT_DIR

    if args.output_dir:
        output_dir = args.output_dir
    elif args.project_dir:
        output_dir = os.path.join(REPO_ROOT, "output")
    else:
        output_dir = DEFAULT_OUTPUT_DIR

    return project_dir, output_dir


def main() -> None:
    """Entry point for dependency analysis and design document generation."""
    setup_logging()
    args = parse_args()
    project_dir, output_dir = resolve_dirs(args)

    llm_client = LLMClient() if ENABLE_LLM_DOC else None
    asyncio.run(process_all_files(project_dir, output_dir, llm_client))


if __name__ == "__main__":
    main()
