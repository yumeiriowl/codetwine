# codetwine

A CLI tool that analyzes source code with tree-sitter, extracts definitions and dependencies, writes a design document for each file with an LLM, and gathers everything into one knowledge file.
The knowledge file can be used as input for LLM-based code search and Q&A, such as RLM, GraphRAG and agent search.

- Dependencies at the symbol level (functions, classes)
- One design document per file, written with its dependencies in view
- Knowledge file as JSON, SQLite or both (`KNOWLEDGE_FORMAT`)
- Dependency graph in Mermaid format

## Table of Contents

- [🧬 Supported Languages](#-supported-languages)
- [🚀 Quick Start](#-quick-start)
  - [Prerequisites](#prerequisites)
  - [1. Installation](#1-installation)
  - [2. Configuration](#2-configuration)
  - [3. Usage](#3-usage)
  - [Calling from Python](#calling-from-python)
- [⚙️ Configuration Options](#️-configuration-options)
- [🔄 Processing Flow](#-processing-flow)
- [📁 Output Files](#-output-files)
- [📋 Output Schema](#-output-schema)
- [📝 Design Documents](#-design-documents)
- [⚠️ Dependency Analysis Limitations](#️-dependency-analysis-limitations)
- [💡 Usage Example: RLM QA Agent](#-usage-example-rlm-qa-agent)
- [🧪 Running the Tests](#-running-the-tests)
- [🏗️ Project Structure](#️-project-structure)
- [🙏 Acknowledgments](#-acknowledgments)
- [📄 License](#-license)

## 🧬 Supported Languages

Definitions, dependencies and design documents are extracted for these languages:

| Language | Extensions |
|----------|------------|
| Python | `py` |
| Java | `java` |
| Kotlin | `kt`, `kts` |
| JavaScript | `js`, `jsx`, `mjs`, `cjs` |
| TypeScript | `ts`, `tsx`, `mts`, `cts` |
| C | `c` |
| C++ | `cpp`, `cc`, `cxx`, `h`, `hpp`, `hh`, `hxx` |
| SQL | `sql` |
| Rust | `rs` |
| C# | `cs` |
| COBOL | `cbl`, `cob`, `cpy` |
| BMS (CICS map definitions) | `bms` |
| JCL (jobs, procedures and `INCLUDE` members) | `jcl`, `prc`, `proc` |
| CSD (CICS resource definitions, DFHCSDUP input) | `csd` |
| R (scripts, and the `{r}` chunks of R Markdown and Quarto) | `r`, `rmd`, `qmd` |

The extensions of COBOL, BMS, JCL, CSD and R match in any case. Some files get their language from their content:

- **JCL**: a file with an extension the table does not list, or none, is read as JCL when its first statement is an upper-case `JOB` / `JOBGROUP` statement, or a JCL statement that an `EXEC` / `INCLUDE` statement names.
- **COBOL copybooks**: a file without a language that a `COPY` or `EXEC SQL INCLUDE` statement names is read as a copybook when the statement names its whole file name or the file holds COBOL code (unless it is read as JCL).
- **No language**: a `.jcl` / `.prc` / `.proc` file whose first statement is not JCL, a `.csd` file that does not start with a DFHCSDUP command, and a `.Rmd` / `.qmd` file without an `{r}` chunk get no language, unless one of the two rules above applies.

Every other non-empty text file (`.md`, `.yaml`, `Makefile`, ...) is listed in the outputs and copied to the output directory, without definitions, usages, summary or design document. Empty files, binary files, symbolic links, named pipes, sockets and devices are skipped, and linked directories are not entered.

## 🚀 Quick Start

### Prerequisites

- Python 3.11 or later
- [uv](https://github.com/astral-sh/uv) 0.11.8 or later
- An API key for an LLM provider (Anthropic, OpenAI, Google, ...). Not needed for local models such as Ollama, or when design documents are off.

### 1. Installation

```bash
git clone https://github.com/yumeiriowl/codetwine.git
cd codetwine
uv sync
```

### 2. Configuration

```bash
cp .env.example .env
```

Set at least these values in `.env`:

```bash
# API key (can be omitted for providers without authentication, e.g. Ollama)
LLM_API_KEY=your-api-key-here

# Model in litellm format: anthropic/<model>, openai/<model>, gemini/<model>, ollama/<model>
LLM_MODEL=anthropic/claude-sonnet-4-6

# Root directory of the project to analyze (absolute path)
DEFAULT_PROJECT_DIR=/path/to/your/project

# Language of the design documents
OUTPUT_LANGUAGE=English
```

On a machine that cannot reach `raw.githubusercontent.com` (offline, GitHub blocked, or a required proxy not set), also set `LITELLM_LOCAL_MODEL_COST_MAP=True`. Without it, codetwine can stop at start-up with an `ImportError` from litellm. With it, litellm uses the model table it ships with, which may not know models newer than the installed litellm.

### 3. Usage

```bash
uv run main.py
```

This analyzes `DEFAULT_PROJECT_DIR` and writes the results under `DEFAULT_OUTPUT_DIR` (default `output/`).

```bash
uv run main.py --project-dir /path/to/your/project --output-dir /path/to/output
```

| Argument | Description | Default |
|----------|-------------|---------|
| `--project-dir` | Root directory of the project to analyze | `DEFAULT_PROJECT_DIR` |
| `--output-dir` | Output directory | `DEFAULT_OUTPUT_DIR`; `output/` when only `--project-dir` is given |

When a directory or a setting is wrong, `main.py` logs the error and exits with status 1 before it analyzes anything. It checks that:

- the project directory exists, is not the file system root and has a valid UTF-8 name, and the output directory is neither the project directory nor its parent;
- `KNOWLEDGE_FORMAT` is `json`, `sqlite` or `both`, every name in `SOURCE_ENCODING` is a known text codec, and `PARSE_CACHE_MAX_FILES` is 0 or more;
- with `ENABLE_LLM_DOC=True`: `LLM_MODEL` is set, `MAX_RETRIES` is 0 or more, `DOC_EFFORT` is a known value, `MAX_WORKERS` is 1 or more, `CODE_SUMMARY_PIECE_LINES` is 1 or more (with `ENABLE_CODE_SUMMARY=True`), and the template can be read and has the right form (see [Template](#template)).

`main.py` writes its log to `logs/codetwine.log` under the repository root (1 MB per file, five old files kept). The console shows progress, warnings and errors.

### Calling from Python

```python
import asyncio
from codetwine.llm.client import LLMClient
from codetwine.pipeline import process_all_files

run_result = asyncio.run(process_all_files(
    "/path/to/your/project", "/path/to/output", llm_client=LLMClient(),
    file_list=["src/main.py", "src/utils.py"],
))
```

| Argument | Description |
|----------|-------------|
| `project_dir`, `output_dir` | The project and the output directory. Results go to `<output_dir>/<name of project_dir>/`. An output directory inside the project is not analyzed |
| `llm_client` | `LLMClient()` (model, key, base URL and effort from `.env`) with `ENABLE_LLM_DOC=True`; `None` with `False` |
| `file_list` | Optional. Paths relative to the project root to analyze instead of walking the project. `EXCLUDE_PATTERNS` and the file checks still apply; paths that do not exist or lead outside the project are skipped with a warning |
| `max_workers` | Optional. Number of design documents written in parallel (1 or more; default `MAX_WORKERS`) |

| Return key | Type | Description |
|------------|------|-------------|
| `file_count` | int | Number of files analyzed |
| `dependency_fail_list` | string[] | Files whose dependency extraction failed. Their outputs are removed and they are left out of the whole-project files; other files' `callee_usages[].from` and `caller_usages[].file` can still name them |
| `doc_count` | int | Files with a complete design document (`0` with `ENABLE_LLM_DOC=False`) |
| `doc_fail_list` | string[] | Files left without a complete design document |
| `detected_encoding_dict` | {string: string} | Files decoded with a detected encoding, and that encoding (see `detected_encoding` in [file_dependencies.json](#file_dependenciesjson)) |

The checks listed under [Usage](#3-usage) raise `ValueError`: `LLMClient()` checks `LLM_MODEL`, `MAX_RETRIES` and `DOC_EFFORT`, and `process_all_files()` checks the rest, and also raises when `ENABLE_LLM_DOC=True` and `llm_client` is `None`.

## ⚙️ Configuration Options

All options are set in `.env` (or the environment).

### LLM Settings

| Variable | Description | Default |
|----------|-------------|---------|
| `LLM_API_KEY` | API key of the LLM provider | Not set |
| `LLM_MODEL` | Model name in litellm format (required with `ENABLE_LLM_DOC=True`) | Not set |
| `LLM_API_BASE` | API base URL, for non-standard endpoints (Ollama, Azure, self-hosted) | Not set |
| `OUTPUT_LANGUAGE` | Language of the design documents | `English` |
| `DOC_MAX_TOKENS` | Token limit of one reply, thinking included. Lowered to the model's output limit in litellm's model table. A reply cut at the limit is saved as it is, with a warning; a reply cut before any text counts as a failed call. For a model the table does not know (e.g. served by vLLM), the limit is halved, not below `4096`, when the server refuses a call for the context window, with a warning | `16384` |
| `DOC_EFFORT` | How much the model thinks while writing: `off` (no thinking; `low` on a model that always thinks or refuses `off`), `low` / `medium` / `high` (adaptive thinking at that effort). A model that refuses these parameters is called without them from then on, with a warning. Some providers' litellm translations (e.g. Gemini, Magistral) drop or rewrite them without a warning | `low` |

Ollama does not refuse a prompt longer than its context (`num_ctx`): it drops the start of the prompt. Set `OLLAMA_CONTEXT_LENGTH` on the server, or `PARAMETER num_ctx` in the Modelfile, large enough for the prompt plus `DOC_MAX_TOKENS`.

### Path Settings

| Variable | Description | Default |
|----------|-------------|---------|
| `DEFAULT_PROJECT_DIR` | Root directory of the project to analyze | Repository root |
| `DEFAULT_OUTPUT_DIR` | Output directory | `output/` |
| `DOC_TEMPLATE_PATH` | Design document template (JSON) | `doc_template.json` |

### Performance Settings

| Variable | Description | Default |
|----------|-------------|---------|
| `MAX_WORKERS` | Design documents written in parallel (1 or more) | `4` |
| `MAX_RETRIES` | Retries of an LLM call after a rate limit error (0 or more; `0`: no retry) | `3` |
| `RETRY_WAIT` | Seconds between retries (fractions allowed) | `2` |
| `PARSE_CACHE_MAX_FILES` | Files whose parse results are kept in memory at once (0 or more; `0`: no limit) | `200` |

### Output Settings

| Variable | Description | Default |
|----------|-------------|---------|
| `KNOWLEDGE_FORMAT` | Knowledge file form: `json` (`project_knowledge.json`), `sqlite` (`project_knowledge.sqlite`) or `both`. Per-file JSON files are always written; the file of the form not selected is removed | `json` |

### Analysis Settings

| Variable | Description | Default |
|----------|-------------|---------|
| `ENABLE_LLM_DOC` | Write design documents (`True` / `False`) | `True` |
| `SUMMARY_MAX_CHARS` | Characters the LLM is asked to keep a file summary within (a longer summary is kept) | `600` |
| `ENABLE_CODE_SUMMARY` | Summarize large code with the LLM when a prompt exceeds the context window (`True` / `False`). With `False`, the prompt is only reduced by dropping context | `True` |
| `CODE_SUMMARY_TRIGGER_LINES` | Line count above which a definition or dependency symbol is summarized. A line over 80 characters counts as one line per 80 characters | `40` |
| `CODE_SUMMARY_PIECE_LINES` | Lines summarized together when a file still exceeds the context window after its large definitions are summarized (1 or more) | `200` |
| `CODE_SUMMARY_MAX_CHARS` | Characters the LLM is asked to keep one code summary within (a longer summary is kept) | `400` |
| `EXCLUDE_PATTERNS` | Directory and file name patterns to exclude (comma-separated, fnmatch), in addition to `__pycache__`, `.git`, `.github`, `.venv` and `node_modules`. Every text file not excluded is copied to the output: add secrets, data files and lock files | Not set |
| `SOURCE_ENCODING` | Encodings tried in order on a file without a BOM, or with a UTF-8 BOM but not valid UTF-8 (comma-separated Python codec names, e.g. `cp1252`). The first that decodes the file is used, so list narrower encodings first | Not set |

## 🔄 Processing Flow

1. **Build the dependency graph**: collect every non-empty text file and resolve the imports and references of each file with a language to the project files they lead to.
2. **Extract each file's dependencies**: its definitions, the definitions of other files it uses (callees) and the uses of its definitions by other files (callers), with line numbers and source code.
3. **Write design documents** (files with a language only): section by section from the template, using the source code, the dependency information and the summaries of the files it depends on. Only documents that need it are rewritten (see [Re-runs](#re-runs)). A prompt over the context window is retried with less context, or with summaries of its large code (`ENABLE_CODE_SUMMARY`).
4. **Save the outputs** to `<output directory>/<project name>/`.

Note: LLM calls happen only in step 3; each code summary is one more call. A provider that silently cuts a long prompt produces a document from the cut prompt.

## 📁 Output Files

Files are written to `<output directory>/<project name>/` (default `output/<project name>/`).

| File | Description |
|------|-------------|
| `project_knowledge.json` | Knowledge file: all dependencies and design documents (`KNOWLEDGE_FORMAT=json` / `both`) |
| `project_knowledge.sqlite` | The same content as a SQLite database (`KNOWLEDGE_FORMAT=sqlite` / `both`) |
| `project_dependency_summary.json` | Dependency graph and per-file summaries |
| `dependency_graph.md` | Dependency graph in Mermaid format |
| `<filename>/file_dependencies.json` | Definitions and dependencies of one file |
| `<filename>/doc.json` | Design document of one file (JSON) |
| `<filename>/doc.md` | Design document of one file (Markdown) |
| `<filename>/<original filename>` | Byte-for-byte copy of the file. Not written for a file named `file_dependencies.json`, `doc.json` or `doc.md` |

`<filename>` is the file's path relative to the project root with the last `.` of its name turned into `_` (`codetwine/import_to_path.py` → `codetwine/import_to_path_py/`; `Makefile` and `.env` keep their names). File paths in the JSON outputs are the paths of these copies with the project name in front (`<project name>/codetwine/import_to_path_py/import_to_path.py`), except `doc.json`'s `file`, which is relative to the project root. All JSON is UTF-8.

The whole-project files list only the files analyzed in that run. The `<filename>/` directories of files not analyzed (removed, excluded, or not in `file_list`) are left as they are.

## 📋 Output Schema

### project_knowledge.json

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
| `project_dependencies[].summary` | string\|null | File summary; `null` without a `doc.json` written from the current content, and for files without a language |
| `project_dependencies[].callers` | string[] | Paths of files that depend on it |
| `project_dependencies[].callees` | string[] | Paths of files it depends on |
| `files[].file` | string | Path of the copied file |
| `files[].file_dependencies` | object | `file_dependencies.json` without its `file` field |
| `files[].doc` | object | `doc.json` without its `file` field. Absent without a `doc.json` written from the current content |

`files[]` holds the same files as `project_dependencies[]`.

### project_dependency_summary.json

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

The fields are those of `project_dependencies[]` above.

### file_dependencies.json

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
| `language` | string | Lower-case extension whose language the file is analyzed as (`py`, `cbl`, ...; `cpy` for a copybook, `jcl` for JCL with another extension). `""` without a language |
| `detected_encoding` | string\|null | Encoding the file was decoded with when neither its BOM's codec, UTF-8 nor `SOURCE_ENCODING` decoded it. `""` when read as UTF-8 with invalid bytes replaced; `utf-16` / `utf-32` when read with that BOM's codec and invalid bytes replaced. `null` otherwise and without a language |
| `definitions[].name` | string | Definition name |
| `definitions[].type` | string | tree-sitter node type, per language (`function_definition`, `class_declaration`, `create_table`, `job_statement`, `define_transaction`, ...) |
| `definitions[].start_line` | int | Start line. Lines count from 1 and end at `\n`, `\r\n` or a lone `\r` |
| `definitions[].end_line` | int | End line |
| `definitions[].name_line` | int | Line the name is on (COBOL, BMS, JCL and CSD only) |
| `definitions[].level` | int | Level number (COBOL and BMS data items only) |
| `definitions[].is_group` | bool | Whether the data item is a group item (COBOL and BMS data items only) |
| `definitions[].context` | string | Full source of the definition |
| `callee_usages[].name` | string | Name as the file writes it (`helper.process`, `Settings::new`). A member used on a variable declared with a project type is named after the type (`User.getName`) |
| `callee_usages[].from` | string | Path of the dependency file |
| `callee_usages[].target_context` | string\|null | Full source of the definition the name leads to; `null` when it leads to no definition of that file (a module used as a value) |
| `callee_usages[].target_name` | string\|null | `definitions[].name` of that definition (`helper` for `hp` after `const { helper: hp } = require(...)`) |
| `callee_usages[].target_start_line` | int\|null | `definitions[].start_line` of that definition |
| `callee_usages[].lines` | int[] | Lines of use in this file. One entry per dependency file, name and definition (two overloads give two entries) |
| `same_file_usages[].name` | string | Name of a symbol defined and used in this file |
| `same_file_usages[].target_name` | string\|null | `definitions[].name` of the definition it leads to (`new` for `Point::new`) |
| `same_file_usages[].target_start_line` | int\|null | `definitions[].start_line` of that definition |
| `same_file_usages[].lines` | int[] | Lines of use outside the definition itself (JCL and CSD: every use) |
| `caller_usages[].name` | string | Name of the symbol used |
| `caller_usages[].file` | string | Path of the dependent file |
| `caller_usages[].usage_context` | string | Source at the place of use. Absent when the dependent file cannot be read |
| `caller_usages[].lines` | int[] | Lines of use in the dependent file. One entry per dependent file and name |

Members declared inside a class, struct, interface, enum or namespace are listed as their own entries besides the enclosing definition. Functions defined inside a function body are not listed.

### doc.json

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
| `summary` | string | File summary (`""` when the template has no `summary_prompt`) |
| `sections[].id` | string | Section `id` from the template |
| `sections[].title` | string | Section heading |
| `sections[].content` | string | Section body (Markdown). With this repository's templates, each definition starts with a level-2 heading holding only its name in backticks (`` ## `parse_args` ``) |
| `source_hash` | string | SHA256 of the source the document was written from |

### project_knowledge.sqlite

Written with `KNOWLEDGE_FORMAT=sqlite` or `both`; holds the same content as `project_knowledge.json`.

| Table | Columns (type) | Description |
|-------|----------------|-------------|
| `meta` | `key` (TEXT), `value` (TEXT) | Keys `project_name`, `schema_version`, `created_at` |
| `files` | `file` (TEXT), `summary` (TEXT), `file_dependencies` (TEXT), `doc` (TEXT) | One row per file. `file_dependencies` and `doc` are JSON text; `summary` and `doc` are `NULL` when there is none |
| `file_edges` | `file` (TEXT), `direction` (TEXT), `other` (TEXT) | Dependency graph. `direction` is `caller` or `callee` as seen from `file` |
| `definitions` | `file` (TEXT), `name` (TEXT), `type` (TEXT), `start_line` (INTEGER), `end_line` (INTEGER) | Every definition, indexed by name and by file |

`codetwine/knowledge_db.py` reads it:

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

## 📝 Design Documents

### Re-runs

Dependencies are extracted again for every file on each run. Design documents are rewritten only for:

- files whose content changed since their `doc.json` was written (`source_hash`), and the files that depend on them, directly or through other files. A C/C++ header also counts as changed when the implementation file of the same name in its directory (`.cpp`, `.c`, `.cc`, `.cxx`) changed;
- documents with missing sections, an empty summary while the template has a `summary_prompt`, a section structure that no longer matches the template, or a `doc.json` that cannot be read.

A file whose content did not change keeps its document when a file it depended on is removed, or a name in it now leads to another unchanged file.

### Template

`doc_template.json` sets the sections and the instructions given to the LLM. Use another file with `DOC_TEMPLATE_PATH`.

```json
{
  "sections": [
    {
      "id": "overview",
      "title": "Section heading",
      "prompt": "Instruction text for the LLM"
    }
  ],
  "summary_prompt": "Instruction for generating the overall summary"
}
```

Each section needs a unique, non-empty `id`, a non-empty `title` and a string `prompt`. `summary_prompt` is optional; without it no summary is written, and the documents of dependent files are written without this file's summary. With `ENABLE_LLM_DOC=True`, a template that cannot be read as JSON or does not have this form stops the run with a `ValueError` before any file is analyzed.

`examples/` holds two other templates: `doc_template_search.json` (one section written in the words a reader would search for) and `doc_template_python.json` (a fuller specification for Python).

### Editing doc.md

Edit `output/<project name>/<filename>/doc.md`. On the next run, a `doc.md` newer than its `doc.json` is applied to `doc.json`.

- Keep the `# Section heading` lines. When one is missing, the sections next to it keep their `doc.json` text and a warning names them. A line `# <title of the next section>` added inside a section, or `# Summary` in the last section, is taken as that heading.
- Save `doc.md` as UTF-8. Otherwise `doc.json` is left as it is, with a warning.
- When a document is rewritten, its `doc.md` is replaced and the edits are lost, with a warning.

### Running without design documents

Set `ENABLE_LLM_DOC=False`. No API key or model is needed. All other outputs are still written. A design document from an earlier run, with its `doc.md` edits, is kept and included while its file's content has not changed.

## ⚠️ Dependency Analysis Limitations

Dependencies come from static analysis of the source text, so some are missing or incomplete. The main cases are below; [docs/dependency-analysis-limitations.md](docs/dependency-analysis-limitations.md) lists them per language.

- **Names built at run time**: `importlib.import_module(name)`, `import(variable)`, `Class.forName("...")`, a COBOL `CALL` of a run-time name, R `get("x")`.
- **Method calls on values**: linked to a class only when the variable's class is visible: Python and JavaScript/TypeScript when it is assigned an instance or annotated in the same function (`e = Engine(); e.run()`); Java, Kotlin, Rust and C/C++ when it is declared with the type (`User u; u.getName()`). Not linked in C# (except extension methods) and R. Function results, call chains and fields (`self.engine.run()`) are not followed.
- **Inherited members**: a member inherited from a class of another file is not linked by its name alone or through `self` / `this` (except C++ through `#include`).
- **Build configuration**: CMake / Makefile include paths, `PYTHONPATH`, `.csproj` references, `package.json` workspaces and `JCLLIB ORDER` are not read.
- **Preprocessors and generated code**: `#if` / `#ifdef` / `#[cfg]` are not evaluated (every branch is analyzed). Code produced by macros and source generators is not analyzed. A COBOL copybook is analyzed as its own file, not expanded into the files that copy it.
- **Case and namespaces**: SQL names are compared as written. C++ names are linked without their namespaces, so equal names in different namespaces are not told apart.
- **Unsupported syntax**: some Kotlin, SQL, C# and COBOL syntax is not parsed; definitions after it can be missing (in Kotlin, to the end of the file).
- **Detected encodings**: a file that neither UTF-8 nor `SOURCE_ENCODING` decodes is decoded with a detected encoding, which can be wrong. Set `SOURCE_ENCODING` for such files; they are listed in `detected_encoding_dict` and carry `detected_encoding`.
- **File paths**: a file whose path is not valid UTF-8 is not analyzed; a warning names it.

## 💡 Usage Example: RLM QA Agent

`examples/rlm_qa/` is a sample that answers questions about a knowledge file with dspy's RLM. By default it reads `examples/sample_output/`, the output of codetwine run on itself with `examples/doc_template_search.json` (a snapshot, not kept in step with the source).

It also needs the [Deno](https://deno.land/) runtime and dspy (`uv sync --extra examples`).

```bash
uv run python examples/rlm_qa/rlm_qa_agent.py
```

- To use your own output, set `TARGET_KNOWLEDGE_PATH` in `rlm_qa_agent.py`. A path ending in `.sqlite` is read as `project_knowledge.sqlite`, any other as `project_knowledge.json`.
- The agent uses the `LLM_MODEL`, `SUB_LLM_MODEL` and `LLM_API_BASE` constants at the top of `rlm_qa_agent.py`, not `.env`, and reads the API key from the `LLM_API_KEY` environment variable.

## 🧪 Running the Tests

```bash
uv sync --extra dev
uv run pytest
```

The tests make no LLM calls and need no API key.

## 🏗️ Project Structure

```
codetwine/
├── main.py                 # CLI entry point
├── doc_template.json       # Design document template
├── .env.example            # Environment variable template
├── codetwine/              # Pipeline, per-language resolution, output
│   ├── config/             # Settings, per-language settings, logging
│   ├── extractors/         # Definitions, imports, usages, dependency graph, per-language readers
│   ├── parsers/            # tree-sitter parser, node helpers, source-format readers
│   ├── llm/                # LLM client (litellm)
│   └── utils/              # File and cache utilities
├── docs/                   # Dependency analysis limitations per language
├── tests/                  # Tests (pytest)
└── examples/
    ├── doc_template_*.json # Other design document templates
    ├── rlm_qa/             # RLM QA agent sample
    └── sample_output/      # Sample output (codetwine analyzed with itself)
```

## 🙏 Acknowledgments

- [tree-sitter](https://tree-sitter.github.io/tree-sitter/) - Source code parsing
- [tree-sitter-language-pack](https://github.com/xberg-io/tree-sitter-language-pack) - COBOL grammar ([tree-sitter-cobol](https://github.com/yutaro-sakamoto/tree-sitter-cobol)) and R grammar ([tree-sitter-r](https://github.com/r-lib/tree-sitter-r))
- [litellm](https://github.com/BerriAI/litellm) - One interface for many LLM providers
- [charset-normalizer](https://github.com/jawah/charset_normalizer) - Encoding detection for files that are not UTF-8

## 📄 License

MIT License. See [LICENSE](LICENSE).
