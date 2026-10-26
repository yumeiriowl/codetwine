"""caller_usages lists a Java/Kotlin file that imports the target from another package
under a source root such as src/main/java/.
"""

import json
import sqlite3

import pytest

from codetwine import pipeline


async def _analyze(tmp_path, monkeypatch, source_by_path_dict: dict[str, str]) -> sqlite3.Connection:
    """Write the sources into a project, run the pipeline and open the database."""
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", False)
    monkeypatch.setattr(pipeline, "KNOWLEDGE_FORMAT", "sqlite")
    project = tmp_path / "proj"
    for path, source in source_by_path_dict.items():
        (project / path).parent.mkdir(parents=True, exist_ok=True)
        (project / path).write_text(source)
    out = tmp_path / "out"
    await pipeline.process_all_files(str(project), str(out), None, max_workers=1)
    return sqlite3.connect(out / "proj" / "project_knowledge.sqlite")


def _file_dependencies(connection: sqlite3.Connection, file: str) -> dict:
    """Return the file_dependencies body of one file."""
    row = connection.execute("SELECT file_dependencies FROM files WHERE file = ?", (file,)).fetchone()
    return json.loads(row[0])


JAVA_SOURCE_DICT = {
    "src/main/java/com/example/app/store/Store.java": (
        "package com.example.app.store;\n\n"
        "public class Store {\n"
        "    public static Store create() {\n        return new Store();\n    }\n"
        "}\n"
    ),
    "src/main/java/com/example/app/Main.java": (
        "package com.example.app;\n\n"
        "import com.example.app.store.Store;\n\n"
        "public class Main {\n"
        "    public static void main(String[] args) {\n        Store st = Store.create();\n    }\n"
        "}\n"
    ),
}

KOTLIN_SOURCE_DICT = {
    "src/main/kotlin/com/example/app/store/Store.kt": (
        "package com.example.app.store\n\n"
        "class Store {\n    fun size(): Int = 0\n}\n"
    ),
    "src/main/kotlin/com/example/app/Main.kt": (
        "package com.example.app\n\n"
        "import com.example.app.store.Store\n\n"
        "fun main() {\n    val st = Store()\n    println(st.size())\n}\n"
    ),
}


@pytest.mark.parametrize("source_by_path_dict, ext", [
    (JAVA_SOURCE_DICT, "java"),
    (KOTLIN_SOURCE_DICT, "kt"),
])
async def test_an_importer_in_another_package_is_a_caller(tmp_path, monkeypatch, source_by_path_dict, ext):
    connection = await _analyze(tmp_path, monkeypatch, source_by_path_dict)
    lang_dir = "java" if ext == "java" else "kotlin"
    store = f"proj/src/main/{lang_dir}/com/example/app/store/Store_{ext}/Store.{ext}"
    main = f"proj/src/main/{lang_dir}/com/example/app/Main_{ext}/Main.{ext}"

    callee_line_list = sorted({
        line
        for usage in _file_dependencies(connection, main)["callee_usages"]
        if usage["name"].split(".")[0] == "Store"
        for line in usage["lines"]
    })
    assert callee_line_list

    caller_usages = _file_dependencies(connection, store)["caller_usages"]
    assert caller_usages
    caller_line_list = sorted({line for usage in caller_usages for line in usage["lines"]})
    assert caller_line_list == callee_line_list

    edge_set = set(connection.execute("SELECT file, direction, other FROM file_edges"))
    assert (store, "caller", main) in edge_set
    assert (main, "callee", store) in edge_set
