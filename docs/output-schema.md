# Output Schema

This page describes the fields of the files codetwine writes. The list of files and where they go is in the README under [Output Files](../README.md#-output-files).

All JSON files are UTF-8. A path in the JSON outputs is the path of the file's copy in the output directory, with the project name in front (`my-project/src/main_py/main.py`). The one exception is `file` in `doc.json`, which is relative to the project root.

## project_knowledge.json

```json
{
  "project_name": "string",
  "project_dependencies": [
    {
      "file": "string",
      "summary": "string|null",
      "callers": ["string"],
      "callees": ["string"]
    }
  ],
  "files": [
    {
      "file": "string",
      "file_dependencies": {},
      "doc": {}
    }
  ]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `project_name` | string | Project name |
| `project_dependencies[].file` | string | Path of the copied file |
| `project_dependencies[].summary` | string\|null | File summary. `null` for a file without a language, and for a file whose `doc.json` was not written from its current content |
| `project_dependencies[].callers` | string[] | Paths of the files that depend on it |
| `project_dependencies[].callees` | string[] | Paths of the files it depends on |
| `files[].file` | string | Path of the copied file |
| `files[].file_dependencies` | object | The file's `file_dependencies.json`, without its `file` field |
| `files[].doc` | object | The file's `doc.json`, without its `file` field. Absent when the `doc.json` was not written from the current content |

`files[]` holds the same files as `project_dependencies[]`.

## project_dependency_summary.json

```json
{
  "project_name": "string",
  "files": [
    {
      "file": "string",
      "summary": "string|null",
      "callers": ["string"],
      "callees": ["string"]
    }
  ]
}
```

The fields are the same as in `project_dependencies[]` above.

## file_dependencies.json

```json
{
  "file": "string",
  "language": "string",
  "detected_encoding": "string|null",
  "definitions": [
    {
      "name": "string",
      "type": "string",
      "start_line": 0,
      "end_line": 0,
      "name_line": 0,
      "level": 0,
      "is_group": false,
      "context": "string"
    }
  ],
  "callee_usages": [
    {
      "name": "string",
      "from": "string",
      "target_context": "string|null",
      "target_name": "string|null",
      "target_start_line": 0,
      "lines": [0]
    }
  ],
  "same_file_usages": [
    {
      "name": "string",
      "target_name": "string|null",
      "target_start_line": 0,
      "lines": [0]
    }
  ],
  "caller_usages": [
    {
      "name": "string",
      "file": "string",
      "usage_context": "string",
      "lines": [0]
    }
  ]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `file` | string | Path of the copied file |
| `language` | string | Lower-case extension of the language the file is analyzed as (`py`, `cbl`, ...). A copybook found by its content gets `cpy`, and JCL with another extension gets `jcl`. `""` for a file without a language |
| `detected_encoding` | string\|null | The encoding used when the file's BOM codec, UTF-8 and `SOURCE_ENCODING` all failed to decode it. `""` means the file was read as UTF-8 with invalid bytes replaced. `utf-16` / `utf-32` means it was read with that BOM's codec and invalid bytes replaced. `null` for every other file |
| `definitions[].name` | string | Definition name |
| `definitions[].type` | string | tree-sitter node type, which differs per language (`function_definition`, `class_declaration`, `create_table`, `job_statement`, `define_transaction`, ...) |
| `definitions[].start_line` | int | Start line. Lines count from 1 and end at `\n`, `\r\n` or a lone `\r` |
| `definitions[].end_line` | int | End line |
| `definitions[].name_line` | int | Line the name is on (COBOL, BMS, JCL and CSD only) |
| `definitions[].level` | int | Level number (COBOL and BMS data items only) |
| `definitions[].is_group` | bool | Whether the data item is a group item (COBOL and BMS data items only) |
| `definitions[].context` | string | Full source of the definition |
| `callee_usages[].name` | string | The name as the file writes it (`helper.process`, `Settings::new`). A member used on a variable declared with a project type is named after the type (`User.getName`) |
| `callee_usages[].from` | string | Path of the file the name comes from |
| `callee_usages[].target_context` | string\|null | Full source of the definition the name leads to. `null` when it leads to no definition of that file, such as a module used as a value |
| `callee_usages[].target_name` | string\|null | `definitions[].name` of that definition (`helper` for `hp` after `const { helper: hp } = require(...)`) |
| `callee_usages[].target_start_line` | int\|null | `definitions[].start_line` of that definition |
| `callee_usages[].lines` | int[] | Lines in this file where the name is used. There is one entry per dependency file, name and definition, so two overloads give two entries |
| `same_file_usages[].name` | string | Name of a symbol defined and used in this file |
| `same_file_usages[].target_name` | string\|null | `definitions[].name` of the definition it leads to (`new` for `Point::new`) |
| `same_file_usages[].target_start_line` | int\|null | `definitions[].start_line` of that definition |
| `same_file_usages[].lines` | int[] | Lines of use outside the definition itself. For JCL and CSD, every line of use |
| `caller_usages[].name` | string | Name of the symbol used |
| `caller_usages[].file` | string | Path of the file that uses it |
| `caller_usages[].usage_context` | string | Source at the place of use. Absent when that file cannot be read |
| `caller_usages[].lines` | int[] | Lines of use in that file. There is one entry per using file and name |

Members declared inside a class, struct, interface, enum or namespace are listed as their own entries, in addition to the enclosing definition. Functions defined inside a function body are not listed.

## doc.json

```json
{
  "file": "string",
  "summary": "string",
  "sections": [
    {
      "id": "string",
      "title": "string",
      "content": "string"
    }
  ],
  "source_hash": "string"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `file` | string | Path relative to the project root |
| `summary` | string | File summary. `""` when the template has no `summary_prompt` |
| `sections[].id` | string | Section `id` from the template |
| `sections[].title` | string | Section heading |
| `sections[].content` | string | Section body (Markdown). With the templates in this repository, each definition starts with a level-2 heading that holds only its name in backticks (`` ## `parse_args` ``) |
| `source_hash` | string | SHA256 of the source the document was written from |

## project_knowledge.sqlite

Written with `KNOWLEDGE_FORMAT=sqlite` or `both`. It holds the same content as `project_knowledge.json`.

| Table | Columns (type) | Description |
|-------|----------------|-------------|
| `meta` | `key` (TEXT), `value` (TEXT) | Keys `project_name`, `schema_version` and `created_at` |
| `files` | `file` (TEXT), `summary` (TEXT), `file_dependencies` (TEXT), `doc` (TEXT) | One row per file. `file_dependencies` and `doc` are JSON text. `summary` and `doc` are `NULL` when there is none |
| `file_edges` | `file` (TEXT), `direction` (TEXT), `other` (TEXT) | The dependency graph. `direction` is `caller` or `callee`, as seen from `file` |
| `definitions` | `file` (TEXT), `name` (TEXT), `type` (TEXT), `start_line` (INTEGER), `end_line` (INTEGER) | Every definition, indexed by name and by file |

`codetwine/knowledge_db.py` reads the database:

```python
from codetwine import knowledge_db

conn = knowledge_db.open_knowledge("output/my-project/project_knowledge.sqlite")

# Entries shaped like project_knowledge.json's "files"
for entry in knowledge_db.iter_files(conn):
    print(entry["file"])

# Entries shaped like project_knowledge.json's "project_dependencies"
for dep in knowledge_db.iter_dependencies(conn):
    print(dep["file"], dep["summary"])

entry = knowledge_db.get_file(conn, "my-project/src/main_py/main.py")
callees = knowledge_db.callees_of(conn, entry["file"])
callers = knowledge_db.callers_of(conn, entry["file"])
hits = knowledge_db.find_definitions(conn, "parse_file")
partial_hits = knowledge_db.find_definitions(conn, "parse", partial=True)  # case-insensitive substring
```
