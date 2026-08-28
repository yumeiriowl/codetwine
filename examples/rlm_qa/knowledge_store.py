"""Read access to a codetwine knowledge file, from either output form.

The agent tools go through a store instead of holding the whole analysis, so a
project_knowledge.sqlite is read one file at a time. A project_knowledge.json has to be
parsed as a whole, so JsonStore keeps it in memory; the two answer the same questions.

    store = open_store("output/my-project/project_knowledge.sqlite")
    store.dependencies()        # the file graph and the summaries (small)
    store.entry(path)           # one file's definitions, usages and design document
"""

import json
import os
from collections.abc import Iterator

from codetwine import knowledge_db


class JsonStore:
    """A store backed by a project_knowledge.json held in memory."""

    def __init__(self, path: str):
        """Read the whole knowledge file.

        Args:
            path: File path to project_knowledge.json.
        """
        with open(path, "r", encoding="utf-8") as f:
            self._data = json.load(f)
        self.project_name = self._data.get("project_name", "")
        self.base_dir = os.path.dirname(path)
        self._by_file = {e["file"]: e for e in self._data.get("files", [])}

    def dependencies(self) -> list[dict]:
        """Return one {"file", "summary", "callers", "callees"} entry per file."""
        return self._data.get("project_dependencies", [])

    def entry(self, file: str) -> dict | None:
        """Return one file's {"file", "file_dependencies", "doc"} entry, or None."""
        return self._by_file.get(file)

    def iter_entries(self) -> Iterator[dict]:
        """Yield every file's entry, one at a time."""
        return iter(self._data.get("files", []))

    def find_definitions(self, name: str, partial: bool = False) -> list[dict]:
        """Return every definition matching a name, as {"file", "name", "type", lines}."""
        found = []
        for entry in self.iter_entries():
            for d in entry.get("file_dependencies", {}).get("definitions", []):
                hit = (name.lower() in d["name"].lower()) if partial else (d["name"] == name)
                if hit:
                    found.append({"file": entry["file"], "name": d["name"],
                                  "type": d.get("type", ""),
                                  "start_line": d["start_line"],
                                  "end_line": d["end_line"]})
        return found

    def close(self) -> None:
        """Release the data. Present so both stores are used the same way."""
        self._data = {}
        self._by_file = {}


class SqliteStore:
    """A store backed by a project_knowledge.sqlite, queried per file."""

    def __init__(self, path: str):
        """Open the database. Nothing but the connection is held.

        Args:
            path: File path to project_knowledge.sqlite.
        """
        self._conn = knowledge_db.open_knowledge(path)
        self.project_name = knowledge_db.get_project_name(self._conn) or ""
        self.base_dir = os.path.dirname(path)

    def dependencies(self) -> list[dict]:
        """Return one {"file", "summary", "callers", "callees"} entry per file."""
        return list(knowledge_db.iter_dependencies(self._conn))

    def entry(self, file: str) -> dict | None:
        """Return one file's {"file", "file_dependencies", "doc"} entry, or None."""
        return knowledge_db.get_file(self._conn, file)

    def iter_entries(self) -> Iterator[dict]:
        """Yield every file's entry, one at a time."""
        return knowledge_db.iter_files(self._conn)

    def find_definitions(self, name: str, partial: bool = False) -> list[dict]:
        """Return every definition matching a name, as {"file", "name", "type", lines}."""
        return knowledge_db.find_definitions(self._conn, name, partial=partial)

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()


def open_store(knowledge_path: str) -> JsonStore | SqliteStore:
    """Open a knowledge file, choosing the store from the path's extension.

    Args:
        knowledge_path: File path to project_knowledge.json or project_knowledge.sqlite.

    Returns:
        A SqliteStore for a ".sqlite" path, a JsonStore otherwise.
    """
    if knowledge_path.endswith(".sqlite"):
        return SqliteStore(knowledge_path)
    return JsonStore(knowledge_path)
