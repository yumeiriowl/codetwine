import os
from collections import deque

# Module variable set by load_project() (referenced by tool functions).
# A KnowledgeStore reading either project_knowledge.json or project_knowledge.sqlite.
# The tools query it per file, so the whole analysis is never held in the sandbox.
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

    kw = keyword.lower()
    hits = []

    def add(kind: str, file: str, name: str) -> bool:
        """Record one hit and report whether the limit has been reached."""
        hits.append({"kind": kind, "file": file, "name": name})
        return len(hits) >= limit

    for entry in store.iter_entries():
        file_path = entry["file"]
        doc = entry.get("doc") or {}
        deps = entry.get("file_dependencies") or {}

        if kw in (doc.get("summary") or "").lower():
            if add("summary", file_path, ""):
                return hits
        for section in doc.get("sections", []):
            if kw in section.get("content", "").lower():
                if add("section", file_path, section.get("title", "")):
                    return hits
        for d in deps.get("definitions", []):
            if kw in (d.get("context") or "").lower():
                if add("definition", file_path, d["name"]):
                    return hits
        for u in deps.get("callee_usages", []):
            if kw in (u.get("target_context") or "").lower():
                if add("callee", file_path, f"{u['name']} from {u['from']}"):
                    return hits
        for c in deps.get("caller_usages", []):
            if kw in (c.get("usage_context") or "").lower():
                if add("caller", c["file"], f"{c['name']} in {file_path}"):
                    return hits

    return hits


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
    results = []
    for entry in store.iter_entries():
        for usage in (entry.get("file_dependencies") or {}).get("callee_usages", []):
            if target_file in usage.get("from", ""):
                results.append({
                    "file": entry["file"],
                    "usage": usage
                })
    return results


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
    candidates = store.find_definitions(name)
    if not candidates:
        candidates = store.find_definitions(name, partial=True)
    if not candidates:
        return {"error": f"Definition '{name}' not found"}

    start_file = candidates[0]["file"]
    start_name = candidates[0]["name"]
    start_key = f"{start_file}:{start_name}"

    # BFS search
    visited = {start_key}
    queue = deque([(start_key, start_file, start_name, 0)])
    nodes = []
    edges = []
    seen_edges = set()

    while queue:
        current_key, current_file, current_name, current_hop = queue.popleft()
        if current_hop >= hops:
            continue

        deps = deps_of(current_file)
        if not deps:
            continue

        # Get line range of the current definition
        current_def = None
        for d in deps.get("definitions", []):
            if d["name"] == current_name:
                current_def = d
                break

        next_hop = current_hop + 1

        # Outgoing: other definitions used by this definition (callee_usages)
        if direction in ("outgoing", "both"):
            for usage in deps.get("callee_usages", []):
                # Check if usage lines are within the current definition's line range
                if current_def:
                    in_range = any(
                        current_def["start_line"] <= line <= current_def["end_line"]
                        for line in usage.get("lines", [])
                    )
                elif current_name == "__module__":
                    all_defs = deps.get("definitions", [])
                    in_range = any(
                        not any(d["start_line"] <= line <= d["end_line"] for d in all_defs)
                        for line in usage.get("lines", [])
                    )
                else:
                    in_range = False

                if not in_range:
                    continue

                target_file = usage.get("from", "")
                target_name = usage.get("name", "")
                target_key = f"{target_file}:{target_name}"

                # Get the type of the target definition
                target_type = ""
                for d in deps_of(target_file).get("definitions", []):
                    if d["name"] == target_name:
                        target_type = d.get("type", "")
                        break

                edge_id = (current_key, target_key, "outgoing")
                if edge_id not in seen_edges:
                    seen_edges.add(edge_id)
                    edges.append({
                        "source": current_key,
                        "target": target_key,
                        "hop": next_hop
                    })

                if target_key not in visited:
                    visited.add(target_key)
                    nodes.append({
                        "key": target_key,
                        "file": target_file,
                        "name": target_name,
                        "type": target_type,
                        "hop": next_hop,
                        "via": "outgoing"
                    })
                    queue.append((target_key, target_file, target_name, next_hop))

        # Incoming: other definitions that use this definition (caller_usages)
        if direction in ("incoming", "both"):
            for usage in deps.get("caller_usages", []):
                if usage.get("name") != current_name:
                    continue

                source_file = usage.get("file", "")
                source_deps = deps_of(source_file)

                # Identify which definition in the source file is using it
                source_name = "__module__"
                source_type = ""
                if source_deps:
                    for line in usage.get("lines", []):
                        for d in source_deps.get("definitions", []):
                            if d["start_line"] <= line <= d["end_line"]:
                                source_name = d["name"]
                                source_type = d.get("type", "")
                                break
                        if source_name != "__module__":
                            break

                source_key = f"{source_file}:{source_name}"

                edge_id = (source_key, current_key, "incoming")
                if edge_id not in seen_edges:
                    seen_edges.add(edge_id)
                    edges.append({
                        "source": source_key,
                        "target": current_key,
                        "hop": next_hop
                    })

                if source_key not in visited:
                    visited.add(source_key)
                    nodes.append({
                        "key": source_key,
                        "file": source_file,
                        "name": source_name,
                        "type": source_type,
                        "hop": next_hop,
                        "via": "incoming"
                    })
                    queue.append((source_key, source_file, source_name, next_hop))

    return {
        "start": start_key,
        "hops": hops,
        "direction": direction,
        "nodes": nodes,
        "edges": edges
    }
