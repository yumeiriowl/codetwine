# codetwine

A CLI tool that analyzes source code with tree-sitter, extracts key definitions and dependencies, generates design documents via LLM, and consolidates everything into a single knowledge file.
The knowledge file can be used as input material for LLM-powered code search and Q&A, such as RLM, GraphRAG, and agent search.

- Dependencies are extracted at the symbol level (functions, classes)
- Design documents are generated per file, taking dependencies into account
- The knowledge file is written as JSON, as a SQLite database, or as both (`KNOWLEDGE_FORMAT`)
- Outputs dependency graphs in Mermaid format

## Table of Contents

- [🧬 Supported Languages](#-supported-languages)
- [🚀 Quick Start](#-quick-start)
  - [Prerequisites](#prerequisites)
  - [1. Installation](#1-installation)
  - [2. Configuration](#2-configuration)
  - [3. Usage](#3-usage)
- [⚙️ Configuration Options](#️-configuration-options)
- [🔄 High-Level Processing Flow](#-high-level-processing-flow)
- [📁 Output Files](#-output-files)
- [📋 Output JSON Schema](#-output-json-schema)
- [🗄️ SQLite Output](#️-sqlite-output)
- [⚠️ Dependency Analysis Limitations](#️-dependency-analysis-limitations)
- [♻️ Incremental Processing](#️-incremental-processing)
- [🎨 Customizing the Design Document Template](#-customizing-the-design-document-template)
- [✏️ Manual Editing of Design Documents](#️-manual-editing-of-design-documents)
- [⏭️ Running Without Design Document Generation](#️-running-without-design-document-generation)
- [💡 Usage Example: RLM QA Agent](#-usage-example-rlm-qa-agent)
- [🧪 Running the Tests](#-running-the-tests)
- [🏗️ Project Structure](#️-project-structure)
- [🙏 Acknowledgments](#-acknowledgments)
- [📄 License](#-license)

## 🧬 Supported Languages

Definitions, dependencies and design documents are extracted for the following languages:

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

The extensions of COBOL, BMS, JCL, CSD and R are matched in any case. Some files get their language from their content:

- **JCL**: A file with an extension the table does not list, or none, is read as JCL when its first statement is an upper-case `JOB` / `JOBGROUP` statement, or a JCL statement that an `EXEC` / `INCLUDE` statement names
- **COBOL copybooks**: A file without a language that a `COPY` statement or `EXEC SQL INCLUDE` names is read as a copybook when the statement names its whole file name or its text holds COBOL code, unless it is read as JCL
- **Files without a language**: A `.jcl` / `.prc` / `.proc` file whose first statement is no JCL statement, a `.csd` file that starts with no DFHCSDUP command, and a `.Rmd` / `.qmd` file without an `{r}` chunk. They can still be read as a copybook, and a `.csd`, `.Rmd` or `.qmd` file as JCL, by the rules above

Every other non-empty text file (`.md`, `.yaml`, `Makefile`, ...) is listed in the outputs and copied to the output directory, without definitions, usages, summary or design document. Empty files, binary files, symbolic links, named pipes, sockets and devices are skipped; a linked directory is not entered.

## 🚀 Quick Start

### Prerequisites

- Python 3.11 or higher
- [uv](https://github.com/astral-sh/uv) package manager, 0.11.8 or later
- API key for an LLM provider (Anthropic / OpenAI / Google, etc. Not required when using local LLMs like Ollama)

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

Set the following in the `.env` file:

```bash
# LLM API key (can be omitted for providers that don't require authentication, e.g. Ollama)
LLM_API_KEY=your-api-key-here

# LLM model name (specify the provider as a prefix in litellm format)
#   Anthropic:  anthropic/<model>
#   OpenAI:     openai/<model>
#   Google:     gemini/<model>
#   Ollama:     ollama/<model>
LLM_MODEL=anthropic/claude-sonnet-4-6

# Root directory of the project to analyze (absolute path)
DEFAULT_PROJECT_DIR=/path/to/your/project

# LLM output language (default: English)
OUTPUT_LANGUAGE=English
```

On a machine that cannot reach `raw.githubusercontent.com` (no internet access, GitHub blocked, or a required proxy not set), also set `LITELLM_LOCAL_MODEL_COST_MAP=True` in `.env`. Without it, codetwine can stop with an `ImportError` from litellm when it starts. With it, litellm reads the model information it ships with instead of fetching it, which may not include models newer than the installed litellm.

### 3. Usage

```bash
uv run main.py
```

Analyzes the project set in `DEFAULT_PROJECT_DIR` and writes the results to `DEFAULT_OUTPUT_DIR` (default: `output/`).

```bash
uv run main.py --project-dir /path/to/your/project --output-dir /path/to/output
```

| Argument | Description | Default |
|------|------|------------|
| `--project-dir` | Root directory of the project to analyze | `DEFAULT_PROJECT_DIR` from `.env` |
| `--output-dir` | Output directory for analysis results | `DEFAULT_OUTPUT_DIR` from `.env` (`output/` if not set). When only `--project-dir` is specified, `output/` is used |

When a directory or a setting is wrong (see the errors of `process_all_files()` below, and, with `ENABLE_LLM_DOC=True`, an unset `LLM_MODEL`, a negative `MAX_RETRIES` or an unknown `DOC_EFFORT`), `main.py` logs the error and exits with status 1 before it analyzes anything.

#### Calling from Python

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
| `project_dir`, `output_dir` | The project to analyze and the output directory. The results are written to `<output_dir>/<name of project_dir>/`. An output directory inside the project directory is not analyzed |
| `llm_client` | `LLMClient()` (model, API key, base URL and effort from `.env`) when `ENABLE_LLM_DOC=True`; `None` when it is `False` |
| `file_list` | Optional. File paths relative to the project root to analyze instead of walking the project directory. `EXCLUDE_PATTERNS` and the file checks still apply; paths that do not exist or lead outside the project are left out with a warning |
| `max_workers` | Optional. Number of design documents generated in parallel (1 or more; default `MAX_WORKERS`) |

| Return key | Type | Description |
|-----------|-----|------|
| `file_count` | int | Number of files analyzed |
| `dependency_fail_list` | string[] | Files whose dependency extraction failed. Their `file_dependencies.json` and copy are removed, and they are left out of the whole-project files, also as callers and callees; the other files are analyzed, and their `callee_usages[].from` and `caller_usages[].file` can still name them |
| `doc_count` | int | Files with a complete design document (`0` when `ENABLE_LLM_DOC=False`) |
| `doc_fail_list` | string[] | Files left without a complete design document |
| `detected_encoding_dict` | {string: string} | Files with a language decoded with a detected encoding (see **Detected encodings** under [Dependency Analysis Limitations](#️-dependency-analysis-limitations)), with that encoding; `""` for a file read as UTF-8 with invalid bytes replaced, and `utf-16` or `utf-32` for a file with that BOM read with invalid bytes replaced |

`process_all_files()` raises `ValueError` before it analyzes anything when `project_dir` does not exist, is not a directory, is the file system root or has a name that is not valid UTF-8; when `output_dir` is `project_dir` itself or its parent; when `KNOWLEDGE_FORMAT` is not `json` / `sqlite` / `both`, `SOURCE_ENCODING` names an unknown codec or one that is no text encoding, or `PARSE_CACHE_MAX_FILES` is negative; and, with `ENABLE_LLM_DOC=True`, when `llm_client` is `None`, `max_workers` is less than 1, `CODE_SUMMARY_PIECE_LINES` is less than 1 with `ENABLE_CODE_SUMMARY=True`, or the template cannot be read or has a wrong form (see [Customizing the Design Document Template](#-customizing-the-design-document-template)).

## ⚙️ Configuration Options

The following options can be configured in the `.env` file.

### LLM Settings

| Variable | Description | Default |
|--------|------|------------|
| `LLM_API_KEY` | API key for the LLM provider | Not set |
| `LLM_MODEL` | Model name in litellm format (required when `ENABLE_LLM_DOC=True`) | Not set |
| `LLM_API_BASE` | API base URL (set when using non-standard endpoints, e.g. Ollama, Azure) | Not set |
| `OUTPUT_LANGUAGE` | Output language for design documents | `English` |
| `DOC_MAX_TOKENS` | Token limit for LLM output, the model's thinking included. Output cut at this limit is saved as it is and a warning is logged; a reply cut before any text counts as a failed call | `16384` |
| `DOC_EFFORT` | How much the model thinks while writing the design documents: `off` (thinking off; `low` on a model that always thinks or refuses it), `low` / `medium` / `high` (adaptive thinking at that effort). A model that refuses these parameters is called without them from then on and a warning is logged. On a provider whose litellm translation drops or rewrites these parameters (e.g. Gemini, Magistral), the value may not reach the model and no warning is logged | `low` |

### Path Settings

| Variable | Description | Default |
|--------|------|------------|
| `DEFAULT_PROJECT_DIR` | Root directory of the project to analyze | Repository root |
| `DEFAULT_OUTPUT_DIR` | Output directory for analysis results | `output/` |
| `DOC_TEMPLATE_PATH` | Path to the design document template JSON file | `doc_template.json` |

### Performance Settings

| Variable | Description | Default |
|--------|------|------------|
| `MAX_WORKERS` | Number of parallel workers for document generation (1 or more) | `4` |
| `MAX_RETRIES` | Number of retries of an LLM API call after a rate limit error (0 or more; `0`: no retry) | `3` |
| `RETRY_WAIT` | Wait time in seconds between retries (a fraction such as `0.5` is allowed) | `2` |
| `PARSE_CACHE_MAX_FILES` | Number of files whose parse results are kept in memory at once (0 or more; `0`: no limit) | `200` |

### Output Settings

| Variable | Description | Default |
|--------|------|------------|
| `KNOWLEDGE_FORMAT` | Form of the knowledge file: `json` (`project_knowledge.json`), `sqlite` (`project_knowledge.sqlite`), or `both`. The per-file JSON files are written in every case, and the file of the form not selected is removed | `json` |

### Analysis Settings

| Variable | Description | Default |
|--------|------|------------|
| `ENABLE_LLM_DOC` | Enable/disable LLM design document generation (`True` / `False`) | `True` |
| `SUMMARY_MAX_CHARS` | Character count the LLM is asked to keep the summary of a design document within; a longer summary is kept as it is | `600` |
| `ENABLE_CODE_SUMMARY` | Summarize large code with the LLM when a prompt exceeds the model context window (`True` / `False`). When `False`, such a prompt is only reduced by dropping context | `True` |
| `CODE_SUMMARY_TRIGGER_LINES` | Line count above which a definition or dependency symbol is summarized. A line longer than 80 characters counts as one line per 80 characters | `40` |
| `CODE_SUMMARY_PIECE_LINES` | Lines of source summarized together when a file still exceeds the context window after its large definitions are summarized (1 or more) | `200` |
| `CODE_SUMMARY_MAX_CHARS` | Character count the LLM is asked to keep a single code behavior summary within; a longer summary is kept as it is | `400` |
| `EXCLUDE_PATTERNS` | Directory and file name patterns to exclude besides `__pycache__`, `.git`, `.github`, `.venv` and `node_modules`, which are always excluded (comma-separated, fnmatch format). Every text file that is not excluded is collected and copied to the output: add secrets, data files and lock files | Not set |
| `SOURCE_ENCODING` | Encodings tried, in order, on a source file without a BOM or with a UTF-8 BOM that is not valid UTF-8 (comma-separated Python codec names; the BOM is dropped first). The first one that decodes the file is used; list `euc_jp` before `cp932` | Not set |

## 🔄 High-Level Processing Flow

1. **Build the project-wide dependency graph**: collects every non-empty text file and resolves the imports and references of each file with a language to the project files they lead to
2. **Extract dependency information for each file**: definitions, the usages of other files' definitions (callees) and the usages of this file's definitions by other files (callers), with line numbers and source code
3. **Generate design documents via LLM** (files with a language only): section by section according to the template, from the source code, the dependency information and the summaries of the files it depends on. Only the documents that need it are regenerated (see [Incremental Processing](#️-incremental-processing)). A prompt that exceeds the model context window is retried with less context, or with summaries of its large code (`ENABLE_CODE_SUMMARY`)
4. **Save all outputs** to `<output directory>/<project name>/`

Note: LLM API calls are only made in step 3, and each code summary is one more call. A provider that cuts a long prompt without an error gives a document written from the cut prompt.

## 📁 Output Files

Running the tool generates the following files in `<output directory>/<project name>/` (default: `output/<project name>/`).

| File | Description |
|----------|------|
| `project_knowledge.json` | Knowledge file: all file dependencies and design documents in JSON (`KNOWLEDGE_FORMAT=json` / `both`) |
| `project_knowledge.sqlite` | Knowledge file with the same content as a SQLite database (`KNOWLEDGE_FORMAT=sqlite` / `both`) |
| `project_dependency_summary.json` | Dependency graph and per-file summaries in JSON |
| `dependency_graph.md` | Dependency graph in Mermaid format |
| `<filename>/file_dependencies.json` | Per-file definition and dependency information |
| `<filename>/doc.json` | Per-file design document (JSON format) |
| `<filename>/doc.md` | Per-file design document (Markdown format) |
| `<filename>/<original filename>` | Copy of the original file, byte for byte. A file named `file_dependencies.json`, `doc.json` or `doc.md` is not copied |

`<filename>` is the path of the file relative to the project root with the last `.` of its name turned into `_` (`codetwine/import_to_path.py` → `codetwine/import_to_path_py/`; `Makefile` and `.env` keep their names). The file paths in the JSON outputs are the paths of these copies with the project name in front (`<project name>/codetwine/import_to_path_py/import_to_path.py`), except the `file` of `doc.json`, which is the path relative to the project root. All JSON output is UTF-8.

The whole-project files list the files analyzed in the run. The `<filename>/` directories of files a run does not analyze (removed from the project, excluded, or not in `file_list`) are left as they are.

`main.py` writes its log to `logs/codetwine.log` under the repository root (up to 1 MB per file, with the five previous files kept). The console shows the progress, warnings and errors only.

## 📋 Output JSON Schema

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
|-----------|-----|------|
| `project_name` | string | Project name |
| `project_dependencies[].file` | string | Path of the source file copied to the output directory |
| `project_dependencies[].summary` | string\|null | Summary of the file (`null` when the file has no `doc.json` generated from its current content, and for a file without a language) |
| `project_dependencies[].callers` | string[] | Paths of dependent files |
| `project_dependencies[].callees` | string[] | Paths of dependency files |
| `files[].file` | string | Path of the source file copied to the output directory |
| `files[].file_dependencies` | object | Same structure as `file_dependencies.json` (excluding the `file` field) |
| `files[].doc` | object | Same structure as `doc.json` (excluding the `file` field). Absent when the file has no `doc.json` generated from its current content |

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

The fields are those of `project_dependencies[]` of `project_knowledge.json`.

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
|-----------|-----|------|
| `file` | string | Path of the source file copied to the output directory |
| `language` | string | Extension whose language settings the file is analyzed with, in lower case (`py`, `cbl`, ...; `cpy` for a copybook and `jcl` for a JCL file of another extension). `""` for a file without a language |
| `detected_encoding` | string\|null | Encoding the file was decoded with when neither the codec of its BOM, UTF-8 nor `SOURCE_ENCODING` decoded it (`""` when it was read as UTF-8 with invalid bytes replaced, `utf-16` or `utf-32` when it has that BOM and was read with invalid bytes replaced). `null` otherwise, and for a file without a language |
| `definitions[].name` | string | Function/class name |
| `definitions[].type` | string | Definition type: the tree-sitter node type, which varies by language (`function_definition`, `class_declaration`, `create_table`, `job_statement`, `define_transaction`, ...) |
| `definitions[].start_line` | int | Start line number. Line numbers count from 1, and a line ends at `\n`, `\r\n` or a lone `\r` |
| `definitions[].end_line` | int | End line number |
| `definitions[].name_line` | int | Line the name is written on (COBOL, BMS, JCL and CSD only) |
| `definitions[].level` | int | Level number of a data item (COBOL and BMS data items only) |
| `definitions[].is_group` | bool | Whether a data item is a group item (COBOL and BMS data items only) |
| `definitions[].context` | string | Full source code of the definition |
| `callee_usages[].name` | string | Name of the used symbol as the file writes it (`helper.process`, `Settings::new`). A member used on a variable declared with a type of the project is named after the type (`User.getName`) |
| `callee_usages[].from` | string | Path of the dependency file |
| `callee_usages[].target_context` | string\|null | Full source code of the definition the name leads to. `null` when it leads to no definition of that file (a module used as a value) |
| `callee_usages[].target_name` | string\|null | `definitions[].name` of that definition (`helper` for a usage written `hp` after `const { helper: hp } = require(...)`) |
| `callee_usages[].target_start_line` | int\|null | `definitions[].start_line` of that definition |
| `callee_usages[].lines` | int[] | Line numbers of usage within this file. One entry per dependency file, name and definition (two overloads give two entries) |
| `same_file_usages[].name` | string | Name of a symbol defined in this file and used in it |
| `same_file_usages[].target_name` | string\|null | `definitions[].name` of the definition the name leads to (`new` for `Point::new`) |
| `same_file_usages[].target_start_line` | int\|null | `definitions[].start_line` of that definition |
| `same_file_usages[].lines` | int[] | Line numbers of usage within this file, outside the definition the name refers to (JCL and CSD: every usage) |
| `caller_usages[].name` | string | Name of the symbol being used |
| `caller_usages[].file` | string | Path of the dependent file |
| `caller_usages[].usage_context` | string | Source code of the usage location in the dependent. Absent when the dependent file cannot be read |
| `caller_usages[].lines` | int[] | Line numbers of usage in the dependent file. One entry per dependent file and name |

Members declared inside a class, struct, interface, enum or namespace are listed as their own entries in addition to the enclosing definition. Functions defined inside a function body are not listed.

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
|-----------|-----|------|
| `file` | string | Path of the source file relative to the project root |
| `summary` | string | Summary of the file (`""` when the template has no `summary_prompt`) |
| `sections[].id` | string | Section identifier (corresponds to `id` in `doc_template.json`) |
| `sections[].title` | string | Section heading |
| `sections[].content` | string | Section body (Markdown format). With the templates of this repository, each definition starts with a level-2 heading holding only its name in backticks (`` ## `parse_args` ``) |
| `source_hash` | string | SHA256 hash of the source file the document was generated from |

## 🗄️ SQLite Output

With `KNOWLEDGE_FORMAT=sqlite` or `both`, the same content as `project_knowledge.json` is written to `project_knowledge.sqlite`.

| Table | Columns | Description |
|-------|---------|-------------|
| `meta` | `key`, `value` | `project_name`, `schema_version`, `created_at` |
| `files` | `file`, `summary`, `file_dependencies`, `doc` | One row per file. `file_dependencies` and `doc` hold the JSON bodies as text; `doc` is `NULL` when `project_knowledge.json` has no `doc` for the file |
| `file_edges` | `file`, `direction`, `other` | The dependency graph. `direction` is `caller` or `callee` as seen from `file` |
| `definitions` | `file`, `name`, `type`, `start_line`, `end_line` | Every definition, indexed by name and by file |

`codetwine/knowledge_db.py` provides the read API:

```python
from codetwine import knowledge_db

conn = knowledge_db.open_knowledge("output/my-project/project_knowledge.sqlite")

# One file entry at a time, in the same structure as project_knowledge.json's "files"
for entry in knowledge_db.iter_files(conn):
    print(entry["file"])

# One entry per file, in the structure of project_knowledge.json's "project_dependencies"
for dep in knowledge_db.iter_dependencies(conn):
    print(dep["file"], dep["summary"])

entry = knowledge_db.get_file(conn, "my-project/src/main_py/main.py")
callees = knowledge_db.callees_of(conn, entry["file"])
callers = knowledge_db.callers_of(conn, entry["file"])
hits = knowledge_db.find_definitions(conn, "parse_file")
partial_hits = knowledge_db.find_definitions(conn, "parse", partial=True)
```

## ⚠️ Dependency Analysis Limitations

Dependencies are extracted by static analysis of the source text, so some are missing or incomplete. The main cases are below; [docs/dependency-analysis-limitations.md](docs/dependency-analysis-limitations.md) lists them all, per language.

- **Dynamic imports and names built at run time**: `importlib.import_module(name)`, `import(variable)`, `Class.forName("...")`, a COBOL `CALL` of a name built at run time, R `get("x")`
- **Method calls on values**: A method called on a variable is linked to its class only in these cases: in Python and JavaScript/TypeScript, when the variable is given an object of the class in the same function or annotated with it (`e = Engine(); e.run()`); in Java, Kotlin, Rust and C/C++, when the variable is declared with the type (`User u; u.getName()`). In C# (except extension methods) and R it is not linked. Results of functions, chains of calls and fields (`self.engine.run()`) are not followed
- **Inherited members**: A member inherited from a class of another file is not linked by its name alone, nor through `self` / `this` (except in C++ from a class the file sees through `#include`)
- **Build configuration**: Include paths of CMake / Makefile, `PYTHONPATH`, `.csproj` references, `package.json` workspaces and `JCLLIB ORDER` are not read
- **Preprocessors, macros and generated code**: `#if` / `#ifdef` / `#[cfg]` are not evaluated (every branch is analyzed), and code produced by macros and source generators is not analyzed. A COBOL copybook is analyzed as a file of its own, not expanded into the files that copy it
- **Names that differ in case or namespace**: SQL names are compared as written. C++ names are linked without their namespaces, so equal names of different namespaces are not told apart
- **Unsupported syntax**: Some syntax of Kotlin, SQL, C# and COBOL is not parsed; definitions after it can be missing (in Kotlin, to the end of the file)
- **Detected encodings**: A file that neither UTF-8 nor `SOURCE_ENCODING` decodes is decoded with a detected encoding, which can be wrong. Set `SOURCE_ENCODING` for such files; they are listed in `detected_encoding_dict` and carry `detected_encoding`
- **File paths**: A file whose path is not valid UTF-8 is not analyzed; a warning names it

## ♻️ Incremental Processing

On subsequent runs, design documents are regenerated only where needed.

- **Dependency information**: Extracted again for every file on each run
- **Design documents**: Regenerated for the files whose content changed since their `doc.json` was generated (`source_hash`) and for the files that depend on them, directly or through other files; all others are reused. A C/C++ header also counts as changed when the implementation file of the same name in the same directory (`.cpp`, `.c`, `.cc`, `.cxx`) changed
- **Incomplete documents**: A `doc.json` with missing sections, an empty summary while the template has a `summary_prompt`, or that cannot be read is regenerated
- **Removed or moved dependencies**: A file whose content did not change keeps its document when a file it depended on is removed or a name of it now resolves to another file that did not change

## 🎨 Customizing the Design Document Template

Edit `doc_template.json` to customize the section structure and LLM instructions for design documents.

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

| Operation | Method |
|------|------|
| Add section | Add a new object to the `sections` array |
| Remove section | Remove the corresponding element from the `sections` array |
| Change instructions | Edit the text in the `prompt` field |
| Change summary instructions | Edit the `summary_prompt` field |
| Generate no summary | Remove the `summary_prompt` field. The documents of the files that depend on a file are then written without its summary |
| Use a different template | Specify the path in `DOC_TEMPLATE_PATH` in `.env` |

Each section needs its own `id` and a `title` (non-empty strings) and a `prompt` (a string); `summary_prompt` is optional. With `ENABLE_LLM_DOC=True`, a template that cannot be read as JSON or does not have this form stops the run with a `ValueError` before any file is analyzed. When the sections change, the documents whose section structure no longer matches are regenerated on the next run.

`examples/` holds two other templates: `doc_template_search.json` (one section written in the words a reader would search for, any language) and `doc_template_python.json` (a fuller specification for Python).

## ✏️ Manual Editing of Design Documents

Edit `output/<project name>/<filename>/doc.md`; on the next `uv run main.py`, a `doc.md` newer than its `doc.json` is parsed and applied to `doc.json`. With `ENABLE_LLM_DOC=False`, this is done for the files whose content has not changed since their design document was generated.

- Do not delete or rename the `# Section heading` lines. When a heading is missing, the sections next to it keep their `doc.json` text and a warning names them
- A line `# <title of the next section>` added to a section, or `# Summary` added to the last section, is taken for that heading
- Save `doc.md` as UTF-8; a `doc.md` that cannot be read as UTF-8 leaves `doc.json` as it is, with a warning
- A file whose design document is regenerated gets a new `doc.md`, and the edits of its `doc.md` are replaced, with a warning

## ⏭️ Running Without Design Document Generation

Set `ENABLE_LLM_DOC=False` in `.env` to output only dependency information without generating design documents. No API key or model configuration is needed.

```bash
ENABLE_LLM_DOC=False
```

`file_dependencies.json`, the file copies, `project_dependency_summary.json`, `dependency_graph.md` and the knowledge file are still generated. A design document an earlier run generated is included, with the edits of its `doc.md`, while the content of its file has not changed since.

## 💡 Usage Example: RLM QA Agent

`examples/rlm_qa/` contains a sample that performs interactive Q&A against a knowledge file with dspy's RLM. `examples/sample_output/` holds sample output of the codetwine repository itself, analyzed with `examples/doc_template_search.json` (a snapshot that does not reflect the current source); the agent reads it by default.

Additional prerequisites: the [Deno](https://deno.land/) runtime and the dspy package (`uv sync --extra examples`).

```bash
uv run python examples/rlm_qa/rlm_qa_agent.py
```

- To use your own project's output, edit `TARGET_KNOWLEDGE_PATH` in `rlm_qa_agent.py`. A path ending in `.sqlite` is read as `project_knowledge.sqlite`, any other path as `project_knowledge.json`
- The agent calls the models set in the `LLM_MODEL` and `SUB_LLM_MODEL` constants at the top of `rlm_qa_agent.py` (with `LLM_API_BASE`), not the `LLM_MODEL` of `.env`, and reads the API key from the `LLM_API_KEY` environment variable

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
├── doc_template.json       # Design document section template
├── .env.example            # Environment variable template
├── codetwine/              # Pipeline, module and reference resolution per language, output
│   ├── config/             # Environment variables, per-language settings, logging
│   ├── extractors/         # Definitions, imports, usages, the dependency graph, and the readers of each language
│   ├── parsers/            # tree-sitter parser and node helpers, and source-format readers
│   ├── llm/                # LLM API client via litellm
│   └── utils/              # File and cache utilities
├── docs/                   # Dependency analysis limitations per language
├── tests/                  # Tests (pytest)
└── examples/
    ├── doc_template_*.json # Other design document templates
    ├── rlm_qa/             # RLM QA agent sample
    └── sample_output/      # Sample output (codetwine analyzed against itself)
```

## 🙏 Acknowledgments

This project uses the following libraries:

- [tree-sitter](https://tree-sitter.github.io/tree-sitter/) - Source code syntax analysis
- [tree-sitter-language-pack](https://github.com/xberg-io/tree-sitter-language-pack) - COBOL grammar ([tree-sitter-cobol](https://github.com/yutaro-sakamoto/tree-sitter-cobol)) and R grammar ([tree-sitter-r](https://github.com/r-lib/tree-sitter-r))
- [litellm](https://github.com/BerriAI/litellm) - Unified interface for multiple LLM providers
- [charset-normalizer](https://github.com/jawah/charset_normalizer) - Encoding detection of source files that are not UTF-8

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.
