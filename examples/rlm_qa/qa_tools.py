import os
from collections import deque

# Module variable set by load_project() (referenced by tool functions).
# A KnowledgeStore reading either project_knowledge.json or project_knowledge.sqlite.
# The tools query it per file; the sandbox never holds the whole analysis.
store = None

# How many hits search_text returns before it stops scanning
SEARCH_HIT_LIMIT = 40


def read_source_file(path: str) -> str:
    """
    Read a source file copied into the output directory.

    Args:
        path: Path as listed in the JSON file field
              (e.g. "code_anarizer/extract_imports/extract_imports.py")

    Returns:
        File content as a string. An error message on read failure.

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

    full_path = os.path.join(store.base_dir, path)

    try:
        with open(full_path, "r", encoding="utf-8") as f:
            return f.read()
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
                "definitions":   [{"name", "type", "start_line", "end_line", "context"}],
                "callee_usages": [{"lines", "name", "from", "target_context"}],
                "caller_usages": [{"lines", "name", "file", "usage_context"}]
            },
            "doc": {"summary": str, "sections": [{"id", "title", "content"}]}
        }
        {"error": str} when the file is not in the project.

    Usage:
        detail = get_file_detail("myproject/config_py/config.py")
        for d in detail["file_dependencies"]["definitions"]:
            print(d["name"], d["start_line"], d["end_line"])
        print(detail["doc"]["summary"])
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

    keyword_lower = keyword.lower()
    hit_list = []

    def add_hit(kind: str, file: str, name: str) -> bool:
        """Record one hit and report whether the limit has been reached."""
        hit_list.append({"kind": kind, "file": file, "name": name})
        return len(hit_list) >= limit

    for entry in store.iter_entries():
        file_path = entry["file"]
        doc = entry.get("doc") or {}
        deps = entry.get("file_dependencies") or {}

        if keyword_lower in (doc.get("summary") or "").lower():
            if add_hit("summary", file_path, ""):
                return hit_list
        for section in doc.get("sections", []):
            if keyword_lower in section.get("content", "").lower():
                if add_hit("section", file_path, section.get("title", "")):
                    return hit_list
        for definition in deps.get("definitions", []):
            if keyword_lower in (definition.get("context") or "").lower():
                if add_hit("definition", file_path, definition["name"]):
                    return hit_list
        for usage in deps.get("callee_usages", []):
            if keyword_lower in (usage.get("target_context") or "").lower():
                if add_hit("callee", file_path, f"{usage['name']} from {usage['from']}"):
                    return hit_list
        for caller_usage in deps.get("caller_usages", []):
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


def _find_definition(definition_list: list[dict], name: str) -> dict | None:
    """Return the first definition with the given name, or None."""
    for definition in definition_list:
        if definition["name"] == name:
            return definition
    return None


def _is_usage_in(
    usage: dict, current_definition: dict | None, current_name: str, definition_list: list[dict],
) -> bool:
    """Return whether a callee usage is written inside the current definition.

    For the pseudo definition "__module__", a usage outside every definition counts.

    Args:
        usage: One callee_usages entry.
        current_definition: The definition searched from, or None when it is not found.
        current_name: Name of the definition searched from.
        definition_list: Definitions of the file searched from.

    Returns:
        True when one of the usage lines is inside the definition's line range.
    """
    line_list = usage.get("lines", [])
    if current_definition:
        return any(
            current_definition["start_line"] <= line <= current_definition["end_line"]
            for line in line_list
        )
    if current_name == "__module__":
        return any(
            not any(
                definition["start_line"] <= line <= definition["end_line"]
                for definition in definition_list
            )
            for line in line_list
        )
    return False


def _enclosing_definition(usage: dict, source_deps: dict) -> tuple[str, str]:
    """Return the definition of the dependent file that contains a caller usage.

    Args:
        usage: One caller_usages entry.
        source_deps: file_dependencies of the dependent file (may be empty).

    Returns:
        (definition name, definition type); ("__module__", "") when no definition
        contains any of the usage lines.
    """
    for line in usage.get("lines", []):
        for definition in source_deps.get("definitions", []):
            if definition["start_line"] <= line <= definition["end_line"]:
                return definition["name"], definition.get("type", "")
    return "__module__", ""


def graph_search(name: str, hops: int = 1, direction: str = "both") -> dict:
    """
    BFS search for dependencies within N hops from the specified definition name.
    Treats definitions as nodes and dependencies as edges.

    Args:
        name: Definition name (exact match search; falls back to partial match if not found)
        hops: Number of hops to search (1=direct dependencies only, 2=up to dependencies of dependencies)
        direction: "outgoing" (dependencies), "incoming" (dependents), "both"

    Returns:
        {
            "start": "file:name",       # Start node
            "hops": int,
            "direction": str,
            "nodes": [                  # List of found definitions
                {"key": "file:name", "file": str, "name": str, "type": str,
                 "hop": int, "via": "outgoing"|"incoming"}
            ],
            "edges": [                  # List of edges
                {"source": "file:name", "target": "file:name", "hop": int}
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

    # Cache of the file entries this search touches, so each file is read at most once
    entry_cache: dict[str, dict | None] = {}

    def deps_of(file_path: str) -> dict:
        """Return one file's file_dependencies, reading it through the store once."""
        if file_path not in entry_cache:
            entry_cache[file_path] = store.entry(file_path)
        entry = entry_cache[file_path]
        return (entry or {}).get("file_dependencies", {})

    # Search for start definition (exact match -> partial match fallback)
    candidate_list = store.find_definitions(name)
    if not candidate_list:
        candidate_list = store.find_definitions(name, partial=True)
    if not candidate_list:
        return {"error": f"Definition '{name}' not found"}

    start_file = candidate_list[0]["file"]
    start_name = candidate_list[0]["name"]
    start_key = f"{start_file}:{start_name}"

    # BFS search
    visit_set = {start_key}
    queue = deque([(start_key, start_file, start_name, 0)])
    node_list = []
    edge_list = []
    edge_id_set = set()

    def record(
        source_key: str, target_key: str, via: str, hop: int,
        node_file: str, node_name: str, node_type: str,
    ) -> None:
        """Record one edge, and the node at its far end when it has not been visited."""
        edge_id = (source_key, target_key, via)
        if edge_id not in edge_id_set:
            edge_id_set.add(edge_id)
            edge_list.append({
                "source": source_key,
                "target": target_key,
                "hop": hop
            })

        node_key = f"{node_file}:{node_name}"
        if node_key not in visit_set:
            visit_set.add(node_key)
            node_list.append({
                "key": node_key,
                "file": node_file,
                "name": node_name,
                "type": node_type,
                "hop": hop,
                "via": via
            })
            queue.append((node_key, node_file, node_name, hop))

    while queue:
        current_key, current_file, current_name, current_hop = queue.popleft()
        if current_hop >= hops:
            continue

        deps = deps_of(current_file)
        if not deps:
            continue

        definition_list = deps.get("definitions", [])
        current_definition = _find_definition(definition_list, current_name)
        next_hop = current_hop + 1

        # Outgoing: other definitions used by this definition (callee_usages)
        if direction in ("outgoing", "both"):
            for usage in deps.get("callee_usages", []):
                if not _is_usage_in(usage, current_definition, current_name, definition_list):
                    continue
                target_file = usage.get("from", "")
                target_name = usage.get("name", "")
                target_definition = _find_definition(
                    deps_of(target_file).get("definitions", []), target_name
                )
                target_type = target_definition.get("type", "") if target_definition else ""
                record(
                    current_key, f"{target_file}:{target_name}", "outgoing", next_hop,
                    target_file, target_name, target_type,
                )

        # Incoming: other definitions that use this definition (caller_usages)
        if direction in ("incoming", "both"):
            for usage in deps.get("caller_usages", []):
                if usage.get("name") != current_name:
                    continue
                source_file = usage.get("file", "")
                source_name, source_type = _enclosing_definition(usage, deps_of(source_file))
                record(
                    f"{source_file}:{source_name}", current_key, "incoming", next_hop,
                    source_file, source_name, source_type,
                )

    return {
        "start": start_key,
        "hops": hops,
        "direction": direction,
        "nodes": node_list,
        "edges": edge_list
    }
