import os
from collections import deque

# Module variables set by load_project() (referenced by tool functions)
project_data = None  # Entire project_knowledge.json
base_dir = None      # Base directory for source files


def read_source_file(path: str) -> str:
    """
    Read a source file copied into the output directory.

    Args:
        path: Path as listed in the JSON file field
              (e.g. "code_anarizer/extract_imports/extract_imports.py")

    Returns:
        File content as a string. An error message on read failure.

    Usage:
        # Example: get line numbers of a function definition from JSON and extract that portion
        file_entry = [f for f in project_data["files"] if "extract_imports" in f["file"]][0]
        defn = [d for d in file_entry["file_dependencies"]["definitions"] if d["name"] == "extract_imports"][0]
        code = read_source_file(file_entry["file"])
        lines = code.split("\\n")
        function_code = "\\n".join(lines[defn["start_line"]-1:defn["end_line"]])
        print(function_code)
    """
    if base_dir is None:
        return "Error: base_dir not initialized. Call load_project() first."

    # Strip leading project_name/ from the file field
    project_name = project_data.get("project_name", "")
    if project_name and path.startswith(project_name + "/"):
        path = path[len(project_name) + 1:]

    full_path = os.path.join(base_dir, path)

    try:
        with open(full_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Error reading {path}: {e}"


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
    # Traverse callee_usages across all files, collecting entries that partially match target_file
    results = []
    for file_entry in project_data["files"]:
        for usage in file_entry.get("file_dependencies", {}).get("callee_usages", []):
            if target_file in usage.get("from", ""):
                results.append({
                    "file": file_entry["file"],
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
            "results": [                # List of found definitions
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
        for r in result["results"]:
            print(f"  hop {r['hop']}: {r['key']} ({r['via']})")

        # Search only dependents within 2 hops from node_text
        result = graph_search("node_text", hops=2, direction="incoming")
        for r in result["results"]:
            print(f"  hop {r['hop']}: {r['name']} in {r['file']}")
    """
    if project_data is None:
        return {"error": "project_data not loaded. Call load_project() first."}

    # File data index (fast lookup by file path)
    file_index = {f["file"]: f for f in project_data["files"]}

    # Search for start definition (exact match -> partial match fallback)
    starts = []
    for f in project_data["files"]:
        for d in f.get("file_dependencies", {}).get("definitions", []):
            if d["name"] == name:
                starts.append({"file": f["file"], "definition": d})
    if not starts:
        for f in project_data["files"]:
            for d in f.get("file_dependencies", {}).get("definitions", []):
                if name.lower() in d["name"].lower():
                    starts.append({"file": f["file"], "definition": d})
    if not starts:
        return {"error": f"Definition '{name}' not found"}

    start_file = starts[0]["file"]
    start_def = starts[0]["definition"]
    start_key = f"{start_file}:{start_def['name']}"

    # BFS search
    visited = {start_key}
    queue = deque([(start_key, start_file, start_def["name"], 0)])
    results = []
    edges = []
    seen_edges = set()

    while queue:
        current_key, current_file, current_name, current_hop = queue.popleft()
        if current_hop >= hops:
            continue

        file_data = file_index.get(current_file)
        if not file_data:
            continue

        file_deps = file_data.get("file_dependencies", {})

        # Get line range of the current definition
        current_def = None
        for d in file_deps.get("definitions", []):
            if d["name"] == current_name:
                current_def = d
                break

        next_hop = current_hop + 1

        # Outgoing: other definitions used by this definition (callee_usages)
        if direction in ("outgoing", "both"):
            for usage in file_deps.get("callee_usages", []):
                # Check if usage lines are within the current definition's line range
                if current_def:
                    in_range = any(
                        current_def["start_line"] <= line <= current_def["end_line"]
                        for line in usage.get("lines", [])
                    )
                elif current_name == "__module__":
                    all_defs = file_deps.get("definitions", [])
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
                target_file_data = file_index.get(target_file)
                if target_file_data:
                    for d in target_file_data.get("file_dependencies", {}).get("definitions", []):
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
                    results.append({
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
            for caller in file_deps.get("caller_usages", []):
                if caller.get("name") != current_name:
                    continue

                source_file = caller.get("file", "")
                source_file_data = file_index.get(source_file)

                # Identify which definition in the source file is using it
                source_name = "__module__"
                source_type = ""
                if source_file_data:
                    source_deps = source_file_data.get("file_dependencies", {})
                    for line in caller.get("lines", []):
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
                    results.append({
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
        "results": results,
        "edges": edges
    }
