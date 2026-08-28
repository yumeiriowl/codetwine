"""SQLite form of the whole-project analysis result.

The database is built from the per-file JSON files in the output directory, one file at
a time, so the consolidated result is never held in memory as a whole. It carries the
same content as project_knowledge.json:

    project_knowledge.json "files"[]                -> files table (one row per file)
    project_knowledge.json "project_dependencies"[] -> files.summary + file_edges

callers and callees come from two separate analyses and do not always mirror each other.
file_edges holds each direction as it was analyzed; neither is derived from the other.
"""

import json
import logging
import os
import sqlite3
from collections.abc import Iterator
from datetime import datetime, timezone

from codetwine.output import build_file_entry, to_output_path

logger = logging.getLogger(__name__)

# Bumped whenever the table layout below changes
SCHEMA_VERSION = "1"

_SCHEMA = """
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE files (
    file              TEXT PRIMARY KEY,   -- "project_name/copy_path"
    summary           TEXT,               -- doc.json summary, NULL when there is none
    file_dependencies TEXT,               -- file_dependencies.json body as JSON text
    doc               TEXT                -- doc.json body as JSON text, NULL when absent
);

CREATE TABLE file_edges (
    file      TEXT NOT NULL,
    direction TEXT NOT NULL,              -- "caller" or "callee", as seen from file
    other     TEXT NOT NULL,
    PRIMARY KEY (file, direction, other)
);
CREATE INDEX idx_file_edges_other ON file_edges(direction, other);

CREATE TABLE definitions (
    file       TEXT NOT NULL,
    name       TEXT NOT NULL,
    type       TEXT,
    start_line INTEGER,
    end_line   INTEGER
);
CREATE INDEX idx_definitions_name ON definitions(name);
CREATE INDEX idx_definitions_file ON definitions(file);
"""


def _definition_rows(file_path: str, file_deps: dict) -> Iterator[tuple]:
    """Yield the definitions table rows for one file.

    Args:
        file_path: The file's path in "project_name/copy_path" format.
        file_deps: The file_dependencies member of a consolidated entry.

    Yields:
        A (file, name, type, start_line, end_line) tuple per definition.
    """
    for definition in file_deps.get("definitions", []):
        yield (
            file_path,
            definition.get("name"),
            definition.get("type"),
            definition.get("start_line"),
            definition.get("end_line"),
        )


def save_consolidated_sqlite(
    base_output_dir: str,
    all_file_list: list[str],
    output_path: str,
    symbol_deps: dict[str, dict[str, set[str]]],
    summary_map: dict[str, str | None],
) -> None:
    """Write the entire project's analysis results to a SQLite database.

    Any existing database at output_path is replaced. The per-file JSON files are the
    source of truth and the database is rebuilt from them on every run.

    Each file's analysis results are read, inserted and released before the next file is
    read, so only one file is held in memory at a time.

    Args:
        base_output_dir: Base output directory for file_dependencies.
        all_file_list: List of relative paths of files to analyze.
        output_path: Output file path for the SQLite database.
        symbol_deps: Return value of build_symbol_level_deps (symbol-level dependency info).
        summary_map: Return value of build_summary_map (file relative path -> summary text or None).
    """
    project_name = os.path.basename(base_output_dir)

    if os.path.exists(output_path):
        os.remove(output_path)

    written_count = 0
    connection = sqlite3.connect(output_path)
    try:
        connection.executescript(_SCHEMA)
        connection.executemany(
            "INSERT INTO meta (key, value) VALUES (?, ?)",
            [
                ("project_name", project_name),
                ("schema_version", SCHEMA_VERSION),
                ("created_at", datetime.now(timezone.utc).isoformat()),
            ],
        )

        # One row per file, plus its definitions index
        for file_rel in all_file_list:
            entry = build_file_entry(base_output_dir, file_rel)
            if entry is None:
                continue
            file_deps = entry.get("file_dependencies")
            doc = entry.get("doc")
            connection.execute(
                "INSERT INTO files (file, summary, file_dependencies, doc) "
                "VALUES (?, ?, ?, ?)",
                (
                    entry["file"],
                    summary_map.get(file_rel),
                    json.dumps(file_deps, ensure_ascii=False) if file_deps else None,
                    json.dumps(doc, ensure_ascii=False) if doc else None,
                ),
            )
            if file_deps:
                connection.executemany(
                    "INSERT INTO definitions "
                    "(file, name, type, start_line, end_line) VALUES (?, ?, ?, ?, ?)",
                    _definition_rows(entry["file"], file_deps),
                )
            written_count += 1

        # Both directions, each as symbol_deps holds it
        for file_rel in all_file_list:
            file_path = to_output_path(base_output_dir, file_rel)
            for direction in ("caller", "callee"):
                connection.executemany(
                    "INSERT OR IGNORE INTO file_edges (file, direction, other) "
                    "VALUES (?, ?, ?)",
                    [(file_path, direction, to_output_path(base_output_dir, other))
                     for other in sorted(symbol_deps[file_rel][direction + "s"])],
                )

        connection.commit()
    finally:
        connection.close()

    logger.info(
        f"Consolidated SQLite output: {output_path} "
        f"(files: {written_count}/{len(all_file_list)})"
    )


def open_knowledge(path: str) -> sqlite3.Connection:
    """Open a knowledge database for reading.

    Args:
        path: Path to the SQLite database.

    Returns:
        A read-only connection whose rows come back as sqlite3.Row.

    Raises:
        FileNotFoundError: When there is no database at that path.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"no knowledge database: {path}")
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def get_project_name(connection: sqlite3.Connection) -> str | None:
    """Return the analyzed project's name.

    Args:
        connection: An open knowledge database connection.

    Returns:
        The project name, or None when the meta table does not carry one.
    """
    row = connection.execute(
        "SELECT value FROM meta WHERE key = 'project_name'"
    ).fetchone()
    return row["value"] if row else None


def _row_to_entry(row: sqlite3.Row) -> dict:
    """Turn one files row back into a consolidated JSON entry.

    Args:
        row: A row of the files table.

    Returns:
        A dict with a "file" key plus "file_dependencies" and "doc" where the row
        carries them.
    """
    entry: dict = {"file": row["file"]}
    if row["file_dependencies"] is not None:
        entry["file_dependencies"] = json.loads(row["file_dependencies"])
    if row["doc"] is not None:
        entry["doc"] = json.loads(row["doc"])
    return entry


def iter_files(connection: sqlite3.Connection) -> Iterator[dict]:
    """Yield every file entry, one at a time, in insertion order.

    The entries have the same structure as the consolidated JSON's "files" elements.

    Args:
        connection: An open knowledge database connection.

    Yields:
        One consolidated entry per file.
    """
    for row in connection.execute("SELECT * FROM files ORDER BY rowid"):
        yield _row_to_entry(row)


def iter_dependencies(connection: sqlite3.Connection) -> Iterator[dict]:
    """Yield every file's summary and dependency lists, one at a time, in insertion order.

    The entries have the same structure as the consolidated JSON's
    "project_dependencies" elements.

    Args:
        connection: An open knowledge database connection.

    Yields:
        A dict with {"file", "summary", "callers", "callees"} keys.
    """
    for row in connection.execute(
            "SELECT file, summary FROM files ORDER BY rowid"):
        yield {
            "file": row["file"],
            "summary": row["summary"],
            "callers": callers_of(connection, row["file"]),
            "callees": callees_of(connection, row["file"]),
        }


def get_file(connection: sqlite3.Connection, file: str) -> dict | None:
    """Return one file's entry.

    Args:
        connection: An open knowledge database connection.
        file: The file path in "project_name/copy_path" format.

    Returns:
        The consolidated entry, or None when the database holds no such file.
    """
    row = connection.execute(
        "SELECT * FROM files WHERE file = ?", (file,)
    ).fetchone()
    return _row_to_entry(row) if row else None


def callees_of(connection: sqlite3.Connection, file: str) -> list[str]:
    """Return the files that a file depends on, sorted.

    Args:
        connection: An open knowledge database connection.
        file: The file path in "project_name/copy_path" format.

    Returns:
        The dependency target file paths.
    """
    rows = connection.execute(
        "SELECT other FROM file_edges WHERE file = ? AND direction = 'callee' "
        "ORDER BY other",
        (file,),
    )
    return [row["other"] for row in rows]


def callers_of(connection: sqlite3.Connection, file: str) -> list[str]:
    """Return the files recorded as depending on a file, sorted.

    Args:
        connection: An open knowledge database connection.
        file: The file path in "project_name/copy_path" format.

    Returns:
        The dependent file paths.
    """
    rows = connection.execute(
        "SELECT other FROM file_edges WHERE file = ? AND direction = 'caller' "
        "ORDER BY other",
        (file,),
    )
    return [row["other"] for row in rows]


def find_definitions(connection: sqlite3.Connection, name: str) -> list[dict]:
    """Return every definition with a given name, without reading any file body.

    Args:
        connection: An open knowledge database connection.
        name: The definition name to look for (exact match).

    Returns:
        A list of {"file", "name", "type", "start_line", "end_line"} dicts.
    """
    rows = connection.execute(
        "SELECT file, name, type, start_line, end_line FROM definitions "
        "WHERE name = ? ORDER BY file, start_line",
        (name,),
    )
    return [dict(row) for row in rows]
