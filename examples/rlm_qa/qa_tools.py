"""Tool functions the RLM QA agent calls to read a codetwine knowledge file.

Each tool reads the module variable store one file at a time and returns plain dicts,
lists and strings. load_project() in rlm_qa_agent.py sets store before the tools run.
"""

import os
from collections import deque
from collections.abc import Callable

from codetwine.utils.file_utils import read_source_text

import knowledge_store

# The knowledge_store.Store the tool functions read, set by load_project().
# It reads either project_knowledge.json or project_knowledge.sqlite.
# The tools query it per file; the sandbox never holds the whole analysis.
store: knowledge_store.Store | None = None

# How many hits search_text returns before it stops scanning
SEARCH_HIT_LIMIT = 40


def _is_inside(path_abs: str, dir_abs: str) -> bool:
    """Return whether an absolute path is the directory itself or lies under it."""
    try:
        return os.path.commonpath([path_abs, dir_abs]) == dir_abs
    except ValueError:
        return False


def read_source_file(path: str) -> str:
    """
    Read a source file copied into the output directory.

    Args:
        path: Path as listed in the JSON file field
              (e.g. "myproject/extract_imports_py/extract_imports.py")

    Returns:
        File content as a string. An error message on read failure and for a path
        outside the output directory.

    Usage:
        # Example: get line numbers of a function definition and extract that portion
        detail = get_file_detail("myproject/extract_imports_py/extract_imports.py")
        defn = [d for d in detail["file_dependencies"]["definitions"] if d["name"] == "extract_imports"][0]
        code = read_source_file(detail["file"])
        lines = code.split("\\n")
        function_code = "\\n".join(lines[defn["start_line"]-1:defn["end_line"]])
        print(function_code)
    """
    if store is None:
        return "Error: store not initialized. Call load_project() first."

    # Strip leading project_name/ from the file field
    project_name = store.project_name
    if project_name and path.startswith(project_name + "/"):
        path = path[len(project_name) + 1:]

    full_path = os.path.realpath(os.path.join(store.base_dir, path))
    if not _is_inside(full_path, os.path.realpath(store.base_dir)):
        return f"Error reading {path}: the path is outside the output directory"

    try:
        return read_source_text(full_path)
    except OSError as e:
        return f"Error reading {path}: {e}"


def get_file_detail(file: str) -> dict:
    """
    Get one file's definitions, dependency usages and design document.

    This is the detail that project_data does not carry. Call it for the files you have
    narrowed down to, not for every file.

    Args:
        file: File path exactly as it appears in project_data["project_dependencies"][]["file"]

    Returns:
        {
            "file": str,
            "file_dependencies": {
                "definitions":      [{"name", "type", "start_line", "end_line", "context"}],
                "callee_usages":    [{"lines", "name", "from", "target_context",
                                      "target_name", "target_start_line"}],
                "same_file_usages": [{"lines", "name", "target_name", "target_start_line"}],
                "caller_usages":    [{"lines", "name", "file", "usage_context"}]
            },
            "doc": {"summary": str, "sections": [{"id", "title", "content"}]}
        }
        "doc" is absent when the file has no design document.
        {"error": str} when the file is not in the project.

    Usage:
        detail = get_file_detail("myproject/config_py/config.py")
        for d in detail["file_dependencies"]["definitions"]:
            print(d["name"], d["start_line"], d["end_line"])
        print(detail.get("doc", {}).get("summary"))
    """
    if store is None:
        return {"error": "store not initialized. Call load_project() first."}
    entry = store.entry(file)
    if entry is None:
        return {"error": f"File '{file}' not found"}
    return entry


def search_text(keyword: str, limit: int = SEARCH_HIT_LIMIT) -> list:
    """
    Search the whole project for a keyword and return where it appears.

    Searches design document summaries and sections, definition source code, and the
    source of the symbols each file depends on. The match is case-insensitive.

    Args:
        keyword: The text to look for
        limit: Maximum number of hits to return (default 40)

    Returns:
        List of {"kind": str, "file": str, "name": str} dicts.
        kind is one of "summary", "section", "definition", "callee", "caller".

    Usage:
        for hit in search_text("retry"):
            print(hit["kind"], hit["file"], hit["name"])
    """
    if store is None:
        return [{"error": "store not initialized. Call load_project() first."}]
    if limit <= 0:
        return []

    keyword_lower = keyword.lower()
    hit_list = []

    def add_hit(kind: str, file: str, name: str) -> bool:
        """Record one hit and report whether the limit has been reached."""
        hit_list.append({"kind": kind, "file": file, "name": name})
        return len(hit_list) >= limit

    for entry in store.iter_entries():
        file_path = entry["file"]
        doc = entry.get("doc") or {}
        dep_dict = entry.get("file_dependencies") or {}

        if keyword_lower in (doc.get("summary") or "").lower():
            if add_hit("summary", file_path, ""):
                return hit_list
        for section in doc.get("sections", []):
            if keyword_lower in section.get("content", "").lower():
                if add_hit("section", file_path, section.get("title", "")):
                    return hit_list
        for definition in dep_dict.get("definitions", []):
            if keyword_lower in (definition.get("context") or "").lower():
                if add_hit("definition", file_path, definition["name"]):
                    return hit_list
        for usage in dep_dict.get("callee_usages", []):
            if keyword_lower in (usage.get("target_context") or "").lower():
                if add_hit("callee", file_path, f"{usage['name']} from {usage['from']}"):
                    return hit_list
        for caller_usage in dep_dict.get("caller_usages", []):
            if keyword_lower in (caller_usage.get("usage_context") or "").lower():
                if add_hit("caller", caller_usage["file"], f"{caller_usage['name']} in {file_path}"):
                    return hit_list

    return hit_list


def get_files_using(target_file: str) -> list:
    """
    Get files that depend on the specified file (dependents).
    Traverses callee_usages of all files and collects entries whose from field partially matches target_file.

    Args:
        target_file: File path to search for (partial match)

    Returns:
        List in [{"file": str, "usage": dict}, ...] format

    Usage:
        users = get_files_using("ts_parser/ts_parser.py")
        for u in users:
            print(f"{u['file']} uses {u['usage']['name']}")
    """
    if store is None:
        return [{"error": "store not initialized. Call load_project() first."}]

    # Traverse callee_usages across all files, collecting entries that partially match target_file
    user_list = []
    for entry in store.iter_entries():
        for usage in (entry.get("file_dependencies") or {}).get("callee_usages", []):
            if target_file in usage.get("from", ""):
                user_list.append({
                    "file": entry["file"],
                    "usage": usage
                })
    return user_list


# Name of the node that stands for the code of a file outside every definition
_MODULE_NODE_NAME = "__module__"


def _node_key(file: str, name: str, start_line: int | None) -> str:
    """Return the key of a graph node.

    Examples:
        ("p/a_py/a.py", "helper", 5)        -> "p/a_py/a.py:helper:5"
        ("p/a_py/a.py", "__module__", None) -> "p/a_py/a.py:__module__"
    """
    if start_line is None:
        return f"{file}:{name}"
    return f"{file}:{name}:{start_line}"


def _find_definition(definition_list: list[dict], name: str, start_line: int | None) -> dict | None:
    """Return the definition with the given name and start line, or None."""
    for definition in definition_list:
        if definition["name"] == name and definition["start_line"] == start_line:
            return definition
    return None


def _owner_of(line: int, definition_list: list[dict]) -> tuple[str, int | None, str]:
    """Return the definition a line belongs to: the narrowest one whose lines contain it.

    Of two definitions with the same line range, the one listed later is chosen.

    Args:
        line: Line number in the file.
        definition_list: Definitions of the file.

    Returns:
        (name, start_line, type) of the definition; (_MODULE_NODE_NAME, None, "") when no
        definition contains the line.
    """
    owner = None
    for definition in definition_list:
        if not definition["start_line"] <= line <= definition["end_line"]:
            continue
        span = definition["end_line"] - definition["start_line"]
        if owner is None or span <= owner["end_line"] - owner["start_line"]:
            owner = definition
    if owner is None:
        return _MODULE_NODE_NAME, None, ""
    return owner["name"], owner["start_line"], owner.get("type", "")


def _owner_list(usage: dict, definition_list: list[dict]) -> list[tuple[str, int | None, str]]:
    """Return the distinct definitions the lines of a usage belong to, in line order.

    Args:
        usage: One callee_usages or same_file_usages entry.
        definition_list: Definitions of the file the usage is written in.

    Returns:
        (name, start_line, type) tuples as _owner_of returns them.
    """
    owner_list = []
    for line in usage.get("lines", []):
        owner = _owner_of(line, definition_list)
        if owner not in owner_list:
            owner_list.append(owner)
    return owner_list


def _outgoing_usage_list(file: str, dep_dict: dict) -> list[tuple[str, dict]]:
    """Return every usage a file writes, with the file of the definition it leads to.

    Args:
        file: File path of the file.
        dep_dict: file_dependencies of the file.

    Returns:
        (target file, usage) tuples for the callee_usages and the same_file_usages entries.
    """
    usage_list = [(usage.get("from", ""), usage) for usage in dep_dict.get("callee_usages", [])]
    usage_list += [(file, usage) for usage in dep_dict.get("same_file_usages", [])]
    return usage_list


def _incoming_usage_list(
    file: str, dep_dict: dict, dep_dict_of: Callable[[str], dict],
) -> list[tuple[str, dict]]:
    """Return every usage that leads to a file, with the file it is written in.

    Args:
        file: File path of the file.
        dep_dict: file_dependencies of the file.
        dep_dict_of: Function returning the file_dependencies of a file path.

    Returns:
        (source file, usage) tuples for the callee_usages entries of the dependent files
        whose "from" is the file, and the file's own same_file_usages entries.
    """
    usage_list = []
    source_file_list = list(dict.fromkeys(
        usage.get("file", "") for usage in dep_dict.get("caller_usages", [])
    ))
    for source_file in source_file_list:
        for usage in dep_dict_of(source_file).get("callee_usages", []):
            if usage.get("from") == file:
                usage_list.append((source_file, usage))
    usage_list += [(file, usage) for usage in dep_dict.get("same_file_usages", [])]
    return usage_list


def _start_definition(name: str) -> dict | None:
    """Return the definition graph_search starts from.

    Args:
        name: Definition name, matched exactly first and partially when nothing matches.

    Returns:
        The first matching {"file", "name", "type", "start_line", "end_line"} dict in the
        order of file path and start line, or None when no definition matches.
    """
    candidate_list = store.find_definitions(name)
    if not candidate_list:
        candidate_list = store.find_definitions(name, partial=True)
    if not candidate_list:
        return None
    return candidate_list[0]


def _walk_graph(
    start_file: str, start_name: str, start_line: int, hops: int, direction: str,
) -> tuple[list[dict], list[dict]]:
    """Walk the definition graph breadth first from one definition.

    A usage is an edge from the definition each of its lines belongs to (_owner_of) to the
    definition its target_name and target_start_line name. A usage without a target_name
    is no edge.

    Args:
        start_file: File path of the start definition.
        start_name: Name of the start definition.
        start_line: Start line of the start definition.
        hops: Number of hops to walk.
        direction: "outgoing", "incoming" or "both".

    Returns:
        (node list, edge list) in the "nodes" / "edges" form graph_search returns.
    """
    # Cache of the file entries this walk reads; each file is read through the store once
    entry_cache_dict: dict[str, dict | None] = {}

    def dep_dict_of(file_path: str) -> dict:
        """Return one file's file_dependencies, reading it through the store once."""
        if file_path not in entry_cache_dict:
            entry_cache_dict[file_path] = store.entry(file_path)
        entry = entry_cache_dict[file_path]
        return (entry or {}).get("file_dependencies", {})

    # BFS state: visited node keys, the (file, name, start_line, hop) queue, and the result lists
    visit_set = {_node_key(start_file, start_name, start_line)}
    node_queue = deque([(start_file, start_name, start_line, 0)])
    node_list = []
    edge_list = []
    edge_id_set = set()

    def record(
        source_key: str, target_key: str, via: str, hop: int,
        node_file: str, node_name: str, node_start_line: int | None, node_type: str,
    ) -> None:
        """Record one edge, and the node at its far end when it has not been visited."""
        edge_id_tuple = (source_key, target_key)
        if edge_id_tuple not in edge_id_set:
            edge_id_set.add(edge_id_tuple)
            edge_list.append({
                "source": source_key,
                "target": target_key,
                "hop": hop
            })

        node_key = _node_key(node_file, node_name, node_start_line)
        if node_key not in visit_set:
            visit_set.add(node_key)
            node_list.append({
                "key": node_key,
                "file": node_file,
                "name": node_name,
                "start_line": node_start_line,
                "type": node_type,
                "hop": hop,
                "via": via
            })
            node_queue.append((node_file, node_name, node_start_line, hop))

    while node_queue:
        current_file, current_name, current_start_line, current_hop = node_queue.popleft()
        if current_hop >= hops:
            continue

        dep_dict = dep_dict_of(current_file)
        if not dep_dict:
            continue

        current_key = _node_key(current_file, current_name, current_start_line)
        definition_list = dep_dict.get("definitions", [])
        next_hop = current_hop + 1

        # Outgoing: definitions the usages written in this definition lead to
        if direction in ("outgoing", "both"):
            for target_file, usage in _outgoing_usage_list(current_file, dep_dict):
                target_name = usage.get("target_name")
                if target_name is None:
                    continue
                if not any(
                    (owner_name, owner_start_line) == (current_name, current_start_line)
                    for owner_name, owner_start_line, _ in _owner_list(usage, definition_list)
                ):
                    continue
                target_start_line = usage.get("target_start_line")
                target_definition = _find_definition(
                    dep_dict_of(target_file).get("definitions", []), target_name, target_start_line
                )
                target_type = target_definition.get("type", "") if target_definition else ""
                record(
                    current_key, _node_key(target_file, target_name, target_start_line),
                    "outgoing", next_hop, target_file, target_name, target_start_line, target_type,
                )

        # Incoming: definitions whose usages lead to this definition
        if direction in ("incoming", "both"):
            for source_file, usage in _incoming_usage_list(current_file, dep_dict, dep_dict_of):
                if (usage.get("target_name"), usage.get("target_start_line")) != (
                    current_name, current_start_line
                ):
                    continue
                source_definition_list = dep_dict_of(source_file).get("definitions", [])
                for source_name, source_start_line, source_type in _owner_list(
                    usage, source_definition_list
                ):
                    record(
                        _node_key(source_file, source_name, source_start_line), current_key,
                        "incoming", next_hop, source_file, source_name, source_start_line,
                        source_type,
                    )

    return node_list, edge_list


def graph_search(name: str, hops: int = 1, direction: str = "both") -> dict:
    """
    BFS search for dependencies within N hops from the specified definition name.
    Treats definitions as nodes and dependencies as edges.

    A usage is an edge from the innermost definition its line is written in. Code outside
    every definition of a file is the node named "__module__" (key "file:__module__").

    Args:
        name: Definition name (exact match search; falls back to partial match if not found;
              of several matches, the first in file path and line order is the start)
        hops: Number of hops to search (1=direct dependencies only, 2=up to dependencies of dependencies)
        direction: "outgoing" (dependencies), "incoming" (dependents), "both"

    Returns:
        {
            "start": "file:name:start_line",  # Start node
            "hops": int,
            "direction": str,
            "nodes": [                  # List of found definitions
                {"key": "file:name:start_line", "file": str, "name": str,
                 "start_line": int|None, "type": str, "hop": int, "via": "outgoing"|"incoming"}
            ],
            "edges": [                  # List of edges
                {"source": "file:name:start_line", "target": "file:name:start_line", "hop": int}
            ]
        }

    Usage:
        # Search direct dependencies and dependents of extract_imports (1 hop)
        result = graph_search("extract_imports", hops=1, direction="both")
        for r in result["nodes"]:
            print(f"  hop {r['hop']}: {r['key']} ({r['via']})")

        # Search only dependents within 2 hops from node_text
        result = graph_search("node_text", hops=2, direction="incoming")
        for r in result["nodes"]:
            print(f"  hop {r['hop']}: {r['name']} in {r['file']}")
    """
    if store is None:
        return {"error": "store not loaded. Call load_project() first."}

    # == Step 1: Find the start definition (exact match -> partial match fallback) =
    start_definition = _start_definition(name)
    if start_definition is None:
        return {"error": f"Definition '{name}' not found"}

    start_file = start_definition["file"]
    start_name = start_definition["name"]
    start_line = start_definition["start_line"]

    # == Step 2: BFS search ========================================================
    node_list, edge_list = _walk_graph(start_file, start_name, start_line, hops, direction)

    # == Step 3: Assemble the result ===============================================
    return {
        "start": _node_key(start_file, start_name, start_line),
        "hops": hops,
        "direction": direction,
        "nodes": node_list,
        "edges": edge_list
    }
