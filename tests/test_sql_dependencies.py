"""SQL files depend on each other through the objects they create, without import statements.

A table, view, function, type or sequence created in one .sql file is visible from every
other .sql file of the project. build_project_dependencies() adds an edge when a file
references such an object, and get_file_dependencies() lists the CREATE statement as the
dependency context. The same lookup gives a C function its body and finds a Java class
whose name differs from its file name.
"""

import logging
import os

from codetwine.extractors.definition_source import extract_callee_source
from codetwine.extractors.dependency_graph import (
    build_project_dependencies,
)
from codetwine.file_analyzer import get_file_dependencies
from codetwine.utils.file_utils import copy_path_to_rel

SCHEMA_SQL = """\
CREATE TABLE customers (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TYPE order_status AS ENUM ('new', 'paid');

CREATE TABLE orders (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER REFERENCES customers(id),
    status order_status
);

CREATE SEQUENCE order_seq;
"""

VIEWS_SQL = """\
CREATE VIEW customer_totals AS
SELECT c.id, COUNT(o.id) AS n
FROM customers c
JOIN orders o ON o.customer_id = c.id
GROUP BY c.id;

CREATE VIEW big_customers AS
SELECT * FROM app.customer_totals WHERE n > 10;
"""

FUNCS_SQL = """\
CREATE FUNCTION order_total(p_customer INTEGER) RETURNS NUMERIC AS $$
    SELECT SUM(total) FROM orders WHERE customer_id = p_customer;
$$ LANGUAGE SQL;

CREATE TRIGGER orders_audit AFTER INSERT ON orders
FOR EACH ROW EXECUTE FUNCTION order_total(1);
"""

MIGRATION_SQL = """\
ALTER TABLE orders ADD COLUMN note TEXT;
UPDATE orders SET note = order_total(customer_id);
SELECT * FROM big_customers;
"""


def _make_sql_project(tmp_path) -> str:
    """Create a SQL project whose files reference objects created in other files."""
    project = tmp_path / "shop"
    (project / "migrations").mkdir(parents=True)
    (project / "schema.sql").write_text(SCHEMA_SQL)
    (project / "views.sql").write_text(VIEWS_SQL)
    (project / "funcs.sql").write_text(FUNCS_SQL)
    (project / "migrations" / "001.sql").write_text(MIGRATION_SQL)
    return str(project)


def _graph(project_dir: str) -> dict[str, dict]:
    """Build the dependency graph keyed by the original relative path."""
    project_name = os.path.basename(project_dir)
    graph = {}
    for dep in build_project_dependencies(project_dir):
        rel = copy_path_to_rel(dep["file"][len(project_name) + 1:])
        graph[rel] = {
            "callers": sorted(copy_path_to_rel(p[len(project_name) + 1:]) for p in dep["callers"]),
            "callees": sorted(copy_path_to_rel(p[len(project_name) + 1:]) for p in dep["callees"]),
        }
    return graph


def _analyze(project_dir: str, rel: str) -> dict:
    """Run the per-file analysis of one file with the caller map from the graph."""
    graph = _graph(project_dir)
    project_file_set = set(graph)
    caller_map = {f: g["callers"] for f, g in graph.items()}
    return get_file_dependencies(
        os.path.join(project_dir, rel), project_dir, project_file_set,
        caller_map,
    )


def test_sql_edges_follow_object_references(tmp_path):
    graph = _graph(_make_sql_project(tmp_path))
    assert graph["views.sql"]["callees"] == ["schema.sql"]
    assert graph["funcs.sql"]["callees"] == ["schema.sql"]
    assert graph["migrations/001.sql"]["callees"] == ["funcs.sql", "schema.sql", "views.sql"]
    assert graph["schema.sql"]["callees"] == []
    assert graph["schema.sql"]["callers"] == ["funcs.sql", "migrations/001.sql", "views.sql"]


def test_sql_definitions_are_the_created_objects(tmp_path):
    result = _analyze(_make_sql_project(tmp_path), "schema.sql")
    assert [(d["name"], d["type"]) for d in result["definitions"]] == [
        ("customers", "create_table"),
        ("order_status", "create_type"),
        ("orders", "create_table"),
        ("order_seq", "create_sequence"),
    ]
    assert result["definitions"][0]["context"].startswith("CREATE TABLE customers (")
    assert result["definitions"][0]["end_line"] == 4


def test_sql_callee_usages_carry_the_create_statement(tmp_path):
    result = _analyze(_make_sql_project(tmp_path), "views.sql")
    usages = {u["name"]: u for u in result["callee_usages"]}
    assert usages["customers"]["from"] == "schema.sql"
    assert usages["customers"]["lines"] == [3]
    assert usages["customers"]["target_context"] == SCHEMA_SQL.split(";")[0].strip()
    assert usages["orders"]["lines"] == [4]
    assert "customer_totals" not in usages  # defined in the same file
    assert set(usages) == {"customers", "orders"}


def test_sql_schema_qualified_reference_and_callers(tmp_path):
    project = _make_sql_project(tmp_path)
    migration = _analyze(project, "migrations/001.sql")
    by_name = {u["name"]: u for u in migration["callee_usages"]}
    assert by_name["orders"]["lines"] == [1, 2]
    assert by_name["order_total"]["from"] == "funcs.sql"
    assert by_name["order_total"]["target_context"].startswith("CREATE FUNCTION order_total(")
    assert by_name["big_customers"]["from"] == "views.sql"

    views = _analyze(project, "views.sql")
    callers = {(u["file"], u["name"]): u["lines"] for u in views["caller_usages"]}
    assert callers[("migrations/001.sql", "big_customers")] == [3]

    schema = _analyze(project, "schema.sql")
    callers = {(u["file"], u["name"]): u["lines"] for u in schema["caller_usages"]}
    assert callers[("views.sql", "customers")] == [3]
    assert callers[("funcs.sql", "orders")] == [2, 5]


def test_c_function_context_includes_the_body(tmp_path):
    project = tmp_path / "cproj"
    project.mkdir()
    (project / "util.h").write_text("int add(int a, int b);\n")
    (project / "util.c").write_text('#include "util.h"\nint add(int a, int b) {\n    return a + b;\n}\n')
    assert extract_callee_source("util.c", "add", str(project)) == (
        "int add(int a, int b) {\n    return a + b;\n}"
    )
    assert extract_callee_source("util.h", "add", str(project)) == "int add(int a, int b);"


def test_java_same_package_class_named_unlike_its_file(tmp_path):
    project = tmp_path / "jproj"
    pkg = project / "com" / "shop"
    pkg.mkdir(parents=True)
    (pkg / "Models.java").write_text(
        "package com.shop;\n\nclass Customer {\n    String name;\n}\n\nclass Order {\n    int id;\n}\n"
    )
    (pkg / "Report.java").write_text(
        "package com.shop;\n\npublic class Report {\n    Customer customer = new Customer();\n}\n"
    )
    graph = _graph(str(project))
    assert graph["com/shop/Report.java"]["callees"] == ["com/shop/Models.java"]
    assert graph["com/shop/Models.java"]["callees"] == []

    result = _analyze(str(project), "com/shop/Report.java")
    usages = {u["name"]: u for u in result["callee_usages"]}
    assert usages["Customer"]["from"] == "com/shop/Models.java"
    assert usages["Customer"]["target_context"].startswith("class Customer {")


def test_a_file_whose_definitions_cannot_be_read_adds_no_edge(tmp_path, monkeypatch, caplog):
    from codetwine import import_binding
    from codetwine.extractors import dependency_graph
    project = tmp_path / "db"
    project.mkdir()
    (project / "a.sql").write_text("CREATE TABLE items (id INT);\n")
    (project / "b.sql").write_text("SELECT id FROM items;\n")
    (project / "c.sql").write_text("CREATE TABLE other (id INT);\n")
    real_top_level_definition_name_list = import_binding.top_level_definition_name_list
    call_file_list = []

    def fail_on_c(file_rel, project_dir):
        call_file_list.append(file_rel)
        if file_rel == "c.sql":
            raise ValueError("unexpected node")
        return real_top_level_definition_name_list(file_rel, project_dir)

    monkeypatch.setattr(import_binding, "top_level_definition_name_list", fail_on_c)
    with caplog.at_level(logging.WARNING, logger="codetwine.import_binding"):
        graph = {
            os.path.basename(dep["file"]): dep
            for dep in dependency_graph.build_project_dependencies(str(project))
        }

    assert "c.sql" in call_file_list
    assert sorted(graph) == ["a.sql", "b.sql", "c.sql"]
    assert [os.path.basename(p) for p in graph["b.sql"]["callees"]] == ["a.sql"]
    assert graph["c.sql"]["callees"] == []
    assert any(
        "c.sql" in record.getMessage() and "ValueError" in record.getMessage()
        for record in caplog.records
    )
