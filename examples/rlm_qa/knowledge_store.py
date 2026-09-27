"""Read access to a codetwine knowledge file, from either output form.

The agent tools ask a store for one file at a time. SqliteStore queries the database per
file; JsonStore has to parse the whole file first and keeps it in memory. The two answer
the same questions.

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

    def __init__(self, path: str) -> None:
        """Read the whole knowledge file.

        Args:
            path: File path to project_knowledge.json.
        """
        with open(path, "r", encoding="utf-8") as f:
            self._knowledge_dict = json.load(f)
        self.project_name = self._knowledge_dict.get("project_name", "")
        self.base_dir = os.path.dirname(path)
        self._entry_dict = {entry["file"]: entry for entry in self._knowledge_dict.get("files", [])}

    def dependencies(self) -> list[dict]:
        """Return one {"file", "summary", "callers", "callees"} entry per file."""
        return self._knowledge_dict.get("project_dependencies", [])

    def entry(self, file: str) -> dict | None:
        """Return one file's {"file", "file_dependencies", "doc"} entry, or None."""
        return self._entry_dict.get(file)

    def iter_entries(self) -> Iterator[dict]:
        """Yield every file's entry, one at a time."""
        return iter(self._knowledge_dict.get("files", []))

    def find_definitions(self, name: str, partial: bool = False) -> list[dict]:
        """Return every definition matching a name, as {"file", "name", "type", lines}."""
        definition_list = []
        for entry in self.iter_entries():
            for definition in entry.get("file_dependencies", {}).get("definitions", []):
                is_match = (name.lower() in definition["name"].lower()) if partial else (definition["name"] == name)
                if is_match:
                    definition_list.append({"file": entry["file"], "name": definition["name"],
                                  "type": definition.get("type", ""),
                                  "start_line": definition["start_line"],
                                  "end_line": definition["end_line"]})
        return definition_list

    def close(self) -> None:
        """Release the data. Present so both stores are used the same way."""
        self._knowledge_dict = {}
        self._entry_dict = {}


class SqliteStore:
    """A store backed by a project_knowledge.sqlite, queried per file."""

    def __init__(self, path: str) -> None:
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


# Either store. The two carry the same methods
Store = JsonStore | SqliteStore


def open_store(knowledge_path: str) -> Store:
    """Open a knowledge file, choosing the store from the path's extension.

    Args:
        knowledge_path: File path to project_knowledge.json or project_knowledge.sqlite.

    Returns:
        A SqliteStore for a ".sqlite" path, a JsonStore otherwise.
    """
    if knowledge_path.endswith(".sqlite"):
        return SqliteStore(knowledge_path)
    return JsonStore(knowledge_path)
