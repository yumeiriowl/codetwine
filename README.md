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
    - [Basic Execution](#basic-execution)
    - [Specifying Project and Output Directories](#specifying-project-and-output-directories)
    - [Calling from Python](#calling-from-python)
- [⚙️ Configuration Options](#️-configuration-options)
  - [LLM Settings](#llm-settings)
  - [Path Settings](#path-settings)
  - [Performance Settings](#performance-settings)
  - [Output Settings](#output-settings)
  - [Analysis Settings](#analysis-settings)
- [🔄 High-Level Processing Flow](#-high-level-processing-flow)
- [📁 Output Files](#-output-files)
- [📋 Output JSON Schema](#-output-json-schema)
  - [project\_knowledge.json](#project_knowledgejson)
  - [project\_dependency\_summary.json](#project_dependency_summaryjson)
  - [file\_dependencies.json](#file_dependenciesjson)
  - [doc.json](#docjson)
- [🗄️ SQLite Output](#️-sqlite-output)
- [⚠️ Dependency Analysis Limitations](#️-dependency-analysis-limitations)
  - [Common to All Languages](#common-to-all-languages)
  - [Python](#python)
  - [JavaScript / TypeScript](#javascript--typescript)
  - [Java / Kotlin](#java--kotlin)
  - [C / C++](#c--c)
  - [SQL](#sql)
  - [Rust](#rust)
  - [C#](#c)
  - [COBOL](#cobol)
  - [JCL](#jcl)
  - [CSD](#csd)
  - [R](#r)
- [♻️ Incremental Processing](#️-incremental-processing)
- [🎨 Customizing the Design Document Template](#-customizing-the-design-document-template)
- [✏️ Manual Editing of Design Documents](#️-manual-editing-of-design-documents)
- [⏭️ Running Without Design Document Generation](#️-running-without-design-document-generation)
- [💡 Usage Example: RLM QA Agent](#-usage-example-rlm-qa-agent)
  - [Sample Output](#sample-output)
  - [Additional Prerequisites](#additional-prerequisites)
  - [How to Run](#how-to-run)
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
| COBOL | `cbl`, `cob`, `cpy` (in any case) |
| BMS (CICS map definitions: the names of the symbolic map a COBOL program copies) | `bms` (in any case) |
| JCL (jobs, procedures and `INCLUDE` members: the programs the steps run, the procedures they call, the members they include, and the CSD commands of a `PGM=DFHCSDUP` step) | `jcl`, `prc`, `proc` (in any case) |
| CSD (CICS resource definitions, DFHCSDUP input: the resources of every type it defines, and the programs and mapsets they name) | `csd` (in any case) |
| R (scripts, and the `{r}` code chunks of R Markdown and Quarto files) | `r`, `rmd`, `qmd` (in any case) |

Some files get their language from their content or from the files that name them:

- **COBOL copybooks**: A file with an extension the table does not list, or none, that a `COPY` statement or `EXEC SQL INCLUDE` names is read as a copybook when the statement names it with its whole file name, or when its text holds a data item, the description of a file or a statement of the procedure division; unless it is read as JCL
- **JCL**: A file with an extension the table does not list, or none, is read as JCL when its first statement is a `JOB` or `JOBGROUP` statement in upper case with a name of 1 to 8 characters, or a JCL statement in upper case whose name field is such a name, two such names joined by `.` (`//STEP1.SYSIN DD`) or none and an `EXEC` / `INCLUDE` statement names it. A `.jcl` / `.prc` / `.proc` file whose first statement is no JCL statement is a file without a language
- **CSD**: A `.csd` file whose first line, apart from blank and comment lines, is no DFHCSDUP command is a file without a language
- **R Markdown and Quarto**: A `.Rmd` / `.qmd` file without an `{r}` code chunk is a file without a language

A file these rules leave without a language can still be read as a copybook by the first bullet, and a `.csd`, `.Rmd` or `.qmd` file as JCL by the second.

Every other non-empty text file (`.md`, `.yaml`, `.toml`, `Makefile`, ...) is also listed in the outputs and copied to the output directory, with empty `definitions`, `callee_usages`, `same_file_usages` and `caller_usages`, a `null` summary and no design document. Empty files, binary files, symbolic links, named pipes, sockets and devices are skipped; a linked directory is not entered.

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

### 3. Usage

#### Basic Execution

```bash
uv run main.py
```

Analyzes the project set in `DEFAULT_PROJECT_DIR` in `.env` and outputs results to `DEFAULT_OUTPUT_DIR` (default: `output/`).

#### Specifying Project and Output Directories

```bash
uv run main.py --project-dir /path/to/your/project --output-dir /path/to/output
```

| Argument | Description | Default |
|------|------|------------|
| `--project-dir` | Root directory of the project to analyze | `DEFAULT_PROJECT_DIR` from `.env` |
| `--output-dir` | Output directory for analysis results | `DEFAULT_OUTPUT_DIR` from `.env` (defaults to `output/` if not set). When only `--project-dir` is specified, `DEFAULT_OUTPUT_DIR` is ignored and `output/` is used |

When a directory or a setting is wrong (the conditions under which `process_all_files()` raises `ValueError` below, and, with `ENABLE_LLM_DOC=True`, an unset `LLM_MODEL`, a negative `MAX_RETRIES` or an unknown `DOC_EFFORT`), `main.py` logs the error and exits with status 1 before it analyzes anything.

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
| `project_dir`, `output_dir` | The project to analyze and the output directory, absolute or relative to the current directory. The results are written to `<output_dir>/<name of project_dir>/`. An output directory inside the project directory is not analyzed |
| `llm_client` | `LLMClient()` (model, API key, base URL and effort from `.env`) when `ENABLE_LLM_DOC=True`; `None` when it is `False`. `None` with `ENABLE_LLM_DOC=True` raises `ValueError` before anything is analyzed |
| `file_list` | Optional. File paths relative to the project root; only these files are analyzed instead of walking the project directory. `EXCLUDE_PATTERNS`, the empty / binary file check and the symbolic link check still apply. Paths that do not exist or lead outside the project directory are left out and named in a warning |
| `max_workers` | Optional. Number of design documents generated in parallel (1 or more; default `MAX_WORKERS`). With `ENABLE_LLM_DOC=True`, a value less than 1 raises `ValueError` before anything is analyzed |

| Return key | Type | Description |
|-----------|-----|------|
| `file_count` | int | Number of files analyzed |
| `dependency_fail_list` | string[] | Files whose dependency extraction failed. Their `file_dependencies.json` and copy are removed from the output directory, and they are left out of `project_knowledge.json`, `project_knowledge.sqlite`, `project_dependency_summary.json` and `dependency_graph.md`, also as callers and callees of the other files; the other files are analyzed, and the `callee_usages[].from` and `caller_usages[].file` of their `file_dependencies.json` can still name them |
| `doc_count` | int | Files with a complete design document (`0` when `ENABLE_LLM_DOC=False`) |
| `doc_fail_list` | string[] | Files left without a complete design document |
| `detected_encoding_dict` | {string: string} | Files with a language whose encoding was detected (see **Detected encodings** under [Common to All Languages](#common-to-all-languages)), with that encoding; `""` for a file read as UTF-8 with invalid bytes replaced, and `utf-16` or `utf-32` for a file with that BOM read with invalid bytes replaced |

`process_all_files()` raises `ValueError` before it analyzes anything when `project_dir` does not exist, is not a directory, is the file system root or has a name that is not valid UTF-8, and when `output_dir` is `project_dir` itself or its parent (`<output_dir>/<name of project_dir>/` is the project directory itself). A path through a symbolic link counts as the directory the link leads to.

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
| `KNOWLEDGE_FORMAT` | Form of the knowledge file: `json` (`project_knowledge.json`), `sqlite` (`project_knowledge.sqlite`), or `both` | `json` |

The per-file JSON files are written in every case. The file of the form not selected is removed from the output directory.

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

1. **Build the project-wide dependency graph**
   - Collects every non-empty text file from the target directory
   - Resolves the imports and references of each file with a language to the project files they lead to
2. **Extract dependency information for each file**
   - Extracts definitions (functions, classes, etc.)
   - Extracts the usages of other files' definitions (callees) and the usages of this file's definitions by other files (callers), with line numbers and source code
3. **Generate design documents via LLM** (files with a language only)
   - Generates each document section by section according to the template (`doc_template.json`), from the source code, the dependency information and the summaries of the files it depends on
   - Documents the files of a dependency cycle in steps, those that depend on the fewest other files of the cycle first; a file gets the summaries of the files of the cycle documented in the steps before its own
   - Generates a summary for each file (when the template has a `summary_prompt`)
   - Regenerates only the documents that need it (see [Incremental Processing](#️-incremental-processing))
   - A prompt that exceeds the model context window is retried with less context (see `ENABLE_CODE_SUMMARY`). The sections of such a file are written from summaries of its large definitions, or of runs of its lines
4. **Save all outputs**
   - Saved to `<output directory>/<project name>` (see [Output Files](#-output-files))

Note: LLM API calls are only made in step 3. Each code summary is one more call: one per large definition, and one per run of `CODE_SUMMARY_PIECE_LINES` lines. A provider that cuts a long prompt without an error gives a document written from the cut prompt.

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
| `<filename>/<original filename>` | Copy of the original file, byte for byte in its own encoding. A file named `file_dependencies.json`, `doc.json` or `doc.md` is not copied |

`<filename>` is the path of the file relative to the project root with the last `.` of its name turned into `_` (`codetwine/import_to_path.py` → `codetwine/import_to_path_py/`; a file without an extension, or whose dots are all at the start of its name, keeps its name: `Makefile` → `Makefile/`, `.env` → `.env/`). The file paths of the JSON outputs (`file`, `from`, `callers`, `callees`) are the paths of these copies with the project name in front (`<project name>/codetwine/import_to_path_py/import_to_path.py`), except the `file` of `doc.json`, which is the path relative to the project root.

All JSON output is UTF-8.

The whole-project files (`project_knowledge.json`, `project_knowledge.sqlite`, `project_dependency_summary.json`, `dependency_graph.md`) list the files analyzed in the run, except those in `dependency_fail_list`. The `<filename>/` directories of files a run does not analyze (files removed from the project, left out by `EXCLUDE_PATTERNS` or not in `file_list`) are left in the output directory as they are.

`main.py` writes its log to `logs/codetwine.log` under the repository root (up to 1 MB per file, with the five previous files kept). The console shows the progress, warnings and errors only.

## 📋 Output JSON Schema

### project_knowledge.json

The knowledge file in JSON: all file dependencies and design documents.

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
| `project_dependencies[].summary` | string\|null | Summary of the file (`null` when the file has no `doc.json` generated from its current content, and always for a file without a language) |
| `project_dependencies[].callers` | string[] | Paths of dependent files copied to the output directory |
| `project_dependencies[].callees` | string[] | Paths of dependency files copied to the output directory |
| `files[].file` | string | Path of the source file copied to the output directory |
| `files[].file_dependencies` | object | Same structure as `file_dependencies.json` (excluding the `file` field) |
| `files[].doc` | object | Same structure as `doc.json` (excluding the `file` field). Absent when the file has no `doc.json` generated from its current content (`source_hash`) |

`files[]` holds the same files as `project_dependencies[]`.

### project_dependency_summary.json

The dependency graph and per-file summaries in JSON.

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

| Field | Type | Description |
|-----------|-----|------|
| `project_name` | string | Project name |
| `files[].file` | string | Path of the source file copied to the output directory |
| `files[].summary` | string\|null | Summary of the file |
| `files[].callers` | string[] | Paths of dependent files copied to the output directory |
| `files[].callees` | string[] | Paths of dependency files copied to the output directory |

### file_dependencies.json

Per-file definition and dependency information.

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
| `language` | string | Extension whose language settings the file is analyzed with, in lower case (`py`, `cbl`, ...; `cpy` for a copybook of another extension or without one that a `COPY` statement or `EXEC SQL INCLUDE` names, `jcl` for a JCL file of another extension or without one). `""` for a file without a language, including the `.jcl`, `.prc`, `.proc`, `.csd`, `.Rmd` and `.qmd` files the rules of [Supported Languages](#-supported-languages) leave without one |
| `detected_encoding` | string\|null | Encoding the file was decoded with when neither the codec of its BOM, UTF-8 nor `SOURCE_ENCODING` decoded it (`""` when it was read as UTF-8 with invalid bytes replaced, `utf-16` or `utf-32` when it has that BOM and was read with invalid bytes replaced). `null` otherwise, and for a file without a language |
| `definitions[].name` | string | Function/class name |
| `definitions[].type` | string | Definition type (tree-sitter node type, varies by language. Python: `function_definition`, `class_definition` / Java: `class_declaration`, `method_declaration` / JS/TS: `function_declaration`, `class_declaration` / SQL: `create_table`, `create_view` / Rust: `function_item`, `struct_item`, `impl_item` / C#: `class_declaration`, `method_declaration`, `property_declaration` / JCL: `job_statement`, `proc_statement`, `exec_statement`, `symbolic_parameter` / CSD: `define_` + the resource type (`define_transaction`, `define_program`, `define_mapset`, ...) / R: `function_definition`, `binary_operator`, `call`, `argument`, etc.) |
| `definitions[].start_line` | int | Start line number. Every line number counts from 1, and a line ends at `\n`, `\r\n` or a lone `\r` |
| `definitions[].end_line` | int | End line number |
| `definitions[].name_line` | int | Line the name is written on (COBOL, BMS, JCL and CSD only) |
| `definitions[].level` | int | Level number of a data item (COBOL and BMS data items only) |
| `definitions[].is_group` | bool | Whether a data item is a group item (COBOL and BMS data items only) |
| `definitions[].context` | string | Full source code of the definition |
| `callee_usages[].name` | string | Name of the used symbol as the file writes it (`helper.process`, `Settings::new`). A member used on a variable declared with a type of the project is named after the type (`User.getName`, `node_t.value`) |
| `callee_usages[].from` | string | Path of the dependency file copied to the output directory |
| `callee_usages[].target_context` | string\|null | Full source code of the definition the name leads to. `null` when it leads to no definition of that file (a module used as a value) |
| `callee_usages[].target_name` | string\|null | `definitions[].name` of that definition in the dependency file (`helper` for a usage written `hp` after `const { helper: hp } = require(...)`). `null` with `target_context` |
| `callee_usages[].target_start_line` | int\|null | `definitions[].start_line` of that definition. `null` with `target_context` |
| `callee_usages[].lines` | int[] | Line numbers of usage within this file. One entry per dependency file, name and definition: a name that leads to two definitions of that file (two overloads) has two entries |
| `same_file_usages[].name` | string | Name of a symbol defined in this file and used in it |
| `same_file_usages[].target_name` | string\|null | `definitions[].name` of the definition the name leads to (`new` for `Point::new`). `null` when it leads to no definition |
| `same_file_usages[].target_start_line` | int\|null | `definitions[].start_line` of that definition |
| `same_file_usages[].lines` | int[] | Line numbers of usage within this file, outside the definition the name refers to (JCL and CSD: every usage). One entry per name and definition, as in `callee_usages` |
| `caller_usages[].name` | string | Name of the symbol being used |
| `caller_usages[].file` | string | Path of the dependent file copied to the output directory |
| `caller_usages[].usage_context` | string | Source code of the usage location in the dependent. Absent when the dependent file cannot be read |
| `caller_usages[].lines` | int[] | Line numbers of usage in the dependent file. One entry per dependent file and name, whichever definitions of this file the name leads to |

Members declared inside a class, struct, interface, enum or namespace are listed as their own entries in addition to the enclosing definition, so their line range and `context` are contained in the enclosing entry. Functions defined inside a function body are not listed.

### doc.json

Per-file design document.

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
| `sections[].content` | string | Section body (Markdown format) |
| `source_hash` | string | SHA256 hash of the source file the document was generated from |

With the templates of this repository, each definition in a section starts with a level-2 heading holding only the definition name in backticks (`` ## `parse_args` ``).

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

Dependencies are extracted by static analysis of the source text. They may be missing or incomplete in the following cases.

### Common to All Languages

- **Dynamic imports**: Patterns that construct module names as strings at runtime cannot be detected
  - Python: `importlib.import_module(name)`, `__import__(name)`
  - JavaScript/TypeScript: `import(variable)`, ``require(`./${name}`)``
  - Java: `Class.forName("com.example.Foo")`
- **File paths**: A file whose path is not valid UTF-8 is not analyzed; a warning names it
- **Detected encodings**: A file without a BOM or with a UTF-8 BOM that neither UTF-8 nor `SOURCE_ENCODING` decodes is decoded with a detected encoding, which can be wrong: comments and strings may be garbled and definitions may be lost. Set `SOURCE_ENCODING` for them. A file with a UTF-16 or UTF-32 BOM that its codec does not decode is read with that codec, invalid bytes replaced. Such files are listed in `detected_encoding_dict` and carry `detected_encoding` in their `file_dependencies.json`
- **`self` / `this`**: `self.name`, `cls.name` and `this.name` are not linked to a member inherited from a class of another file, except in C++ from a class the file sees through `#include`. Outside a class (`this` in a function assigned to a prototype, in an object literal) they are linked to the definition `name` of the file, whatever class it is written in (Python, JavaScript/TypeScript, Java, Kotlin, C++, Rust)

### Python

- **Import roots**: A module is looked up from the directory of the file when that directory has no `__init__.py`, then from the project root and any `src/` directory of the project; a module written with two or more parts is also matched against the end of the project file paths (`from app.models import User` leads to `backend/app/models.py`). A module whose first part is a name of the standard library of Python 3.11 to 3.14 (`logging`, `email.utils`) is looked up under a `src/` directory and by the end of the paths only in a directory above the file that has no `__init__.py` (`from profile.models import Profile` in `src/accounts/views.py` leads to `src/profile/models.py`). Roots added through `PYTHONPATH` or `sys.path` are not read
- **Modules of the same name**: A module that both a project file and an installed package provide (`import utils` with `utils.py` in the project) is linked to the project file, also when the file is under a `src/` directory of another part of the project
- **Class bodies**: In a class body, a name written after the declaration of a class variable of that name (`DEFAULT = validate` after `validate = ...`) is linked to what the file imports or defines outside the class, not to the class variable
- **`__all__`**: An `__all__` built at run time is not read; `from m import *` then brings in every name of `m` that does not start with `_`
- **Method calls on values**: A method called on a variable is listed under a class (`e = Engine(); e.run()` is `Engine.run`) only when the variable is given an object of the class inside the same function or is annotated with it. The class is the one of the nearest assignment above the line, whichever branch it is in (after `if flag: e = Other()`, `e.run()` is `Other.run`). A variable given the result of a function, an attribute (`self.engine.run()`) and a variable assigned at the top level of the file are not followed

### JavaScript / TypeScript

- **Path aliases**: A module that is not relative is resolved through `paths` and `baseUrl` of the `tsconfig.json` / `jsconfig.json` that covers the file, or from the project root and any `src/` directory of the project without one. `extends` that names a package is not followed, and a module the config file does not map is not linked
- **Modules of the same name**: Without a config file, a module that is not relative and whose package is not listed in `dependencies`, `devDependencies`, `peerDependencies` or `optionalDependencies` of a `package.json` around the file (a Node.js built-in module, a package installed without being listed) is linked to a project file of that path under the project root or a `src/` directory (`import fs from "fs"` with `src/fs.js`)
- **Package entries**: A module that names a package of the project or a directory with a `package.json` is linked, from a TypeScript file, to the declaration file `types` or `typings` names before the file `main` names, and, from a JavaScript file, to a declaration file (`.d.ts`) only when no other file fits (`exports`, `main`, `index.js`)
- **Workspace packages**: `workspaces` and `pnpm-workspace.yaml` are not read. A module is linked to the `package.json` of the project that has its `name`, wherever it is (also a test fixture named like an installed package)
- **Bundler aliases**: `resolve.alias` of `vite.config.*` / `webpack.config.*` is read only when an alias is written as a string that starts with `.` (or with `/` in `vite.config.*`, a path from the directory of the config file), `path.resolve(__dirname, ...)`, `path.join(__dirname, ...)` or `fileURLToPath(new URL("...", import.meta.url))`
- **Imports not bound to a name**: A `require()` or `import()` whose result the statement does not bind to a name adds the dependency on the file only, except `require("./m").run()` and the callback of `import("./m").then((m) => ...)`
- **Method calls on values**: A method called on a variable is listed under a class (`const s = new Store(); s.get()` is `Store.get`) only when the variable is given the object with `new` inside the same function or is annotated with the class in TypeScript. The class is the one of the nearest assignment above the line, whichever branch it is in. A variable given any other value is not followed
- **Constructor parameter properties**: A property declared in the parameters of a TypeScript constructor (`constructor(public r: number)`) is not a definition; `this.r` is not linked
- **Definitions**: The declarations inside a callback (`describe("...", function () { ... })`) are not listed as definitions. An unnamed default export (`export default { ... }`, and `export = { ... }` in TypeScript) is listed as the definition `default`. The entries and methods of an object (`run: () => 1`, `run() {}`) are listed as definitions only in an object the module exports (`export default { ... }`, `module.exports = { ... }`). A function assigned to a member (`Shape.make = function () {}`) is listed as `make`, and `Shape.make()` is linked to `Shape`

### Java / Kotlin

- **Packages**: A file of a wildcard import or of the same package is a dependency only when one of its names is used. A file without a `package` statement sees only the files of its directory that have none either. A `private` top-level declaration of a Kotlin file is linked from the other Kotlin files of its package as well
- **Kotlin files seen from Java**: A Java file sees a Kotlin file as its top-level classes and the class of the file (`StringsKt` for `Strings.kt`, `String_utilsKt` for `string-utils.kt`, or the name `@file:JvmName` gives), whose members are the top-level functions and properties of the file. A class `@file:JvmName` gives to several files (`@JvmMultifileClass`) is linked to one of them only
- **Method calls**: A method called on a variable is listed under the type the variable is declared with (`User u; u.getName()` is `User.getName`). A variable whose type is not written (`var`, `val u = ...`) and a chain of calls are not followed
- **Members written by their name alone**: A member inherited from a class of another file is not linked. A name that neither the class nor a class of the same file it is made from has is linked to the first definition of that name in the file. A call written by its name alone is linked to a method of the class before a function of that name the file imports or sees in its package, unless the method has no parameters and the call passes arguments; other numbers of arguments are not compared. A Kotlin property of a function type called by its name alone (`onClick()`) is linked to such a function, when there is one
- **Constructor properties and record components**: A property declared in a Kotlin primary constructor (`class Car(val engine: Engine)`) and a component of a Java record are not definitions; `car.engine` is linked to `Car`
- **Kotlin extension functions**: `value.name()` and `value.name` are linked to an extension function or property `name` the file sees, whatever the type of `value` is. A name that a class of the project also has as a member is not linked this way
- **Unsupported Kotlin syntax**: A keyword used as a name (`final = true`), `$$"..."` strings and `when` guards are not parsed. The definitions written after such a place, to the end of the file, may be missing, and a definition that holds it may extend to the end of the file
- **Annotations of top-level declarations**: A top-level declaration written after annotations that the grammar reads as an expression (`@Target(TYPE) annotation class Mark`, `@Suppress("unused") enum class Level { LOW }`) starts at its first modifier or keyword, after the annotations

### C / C++

- **Include paths**: `#include "x.h"` is looked up from the directory of the file, then from the project root and any `src/` directory of the project, then as the nearest project file whose path ends with the included path; `#include <x.h>` is looked up in the same way without the directory of the file, and a relative path (`#include "../x.h"`) from the directory of the file only. Include paths added via CMake or Makefile `-I` options are not read
- **Functions of the same name**: A function a header declares is linked to every file that includes the header and defines it (per-platform sources, a test that defines it again). A variable declared `extern` is linked to the header only. A function declared before its definition in the same file (`static int fact(int n);`) is linked to that declaration, also from inside the function
- **Namespaces**: `using namespace` is not evaluated, and a name is linked without its namespaces (`geo::Shape::count()` is listed as `Shape::count`), so equal names of different namespaces are not told apart
- **Members**: A member of a base class written through a `typedef` or a template parameter is not linked. A member written through a `typedef` of a type declared elsewhere (`typedef struct node node_t;` with `struct node { ... };`) is linked to the `typedef`
- **Preprocessor**: `#if` / `#ifdef` are not evaluated; every branch is analyzed. Definitions produced by macro expansion are not analyzed. A declaration written with a macro (`TEST(a, b) { ... }`) may be listed under the name of the macro, except an upper-case macro on a line of its own, next to the type of a declaration (`EXPORT int f(void);`), after a declarator (`void f() LOCKS(mu) { ... }`, `int n GUARDED_BY(mu);`) or between `class`, `struct` or `union` and the name (`class EXPORT Shape { ... };`, `class EXPORT Shape;`). A `struct` or `union` declared with a macro and without a body (`struct EXPORT Shape;`) is listed as a variable of that name, and a variable in upper case followed by a macro (`int COUNT GUARDED_BY(mu);`) under the name of the macro. A macro call written as a statement (`MODULE_INIT(setup);`) is a usage of the macro, not a definition. The declarations of a function whose head is not parsed are not listed as definitions. The lines after a `/* */` comment inside a `#define` of several lines are read as code of the file

### SQL

- **Object references**: Names are compared as written: a reference that differs in case or quoting from the `CREATE` statement is not detected
- **Unsupported syntax**: PostgreSQL-style `CREATE PROCEDURE`, `CALL`, `GRANT` and psql `\i` are not parsed. Surrounding statements are still analyzed, except after a PL/pgSQL function body that is not parsed: the statements that follow it can be missing from the definitions

### Rust

- **Module paths**: A path starting with a crate name is resolved only when that crate's `Cargo.toml` is in the project. A `mod` declaration inside an inline module (`mod a { mod b; }`) and `#[cfg_attr(..., path = "...")]` are not resolved. A name a module only re-exports from outside the project (`pub use std::collections::HashMap;`) is not linked. A glob import of an inline module (`use crate::prelude::*;` of `pub mod prelude { ... }`) does not bind the names written alone (`area()`); a path through it (`Circle::new()`) is resolved. In a file that several crate roots declare as a module (`mod common;` in each file under `tests/`), `crate::` and `super::` lead to the first of those roots in path order
- **Usage names**: `Self::new()` inside an `impl` block is listed as `Type::new`, and `Self::NAME` inside a `trait` as `Trait::NAME`
- **Macros**: Code generated by macros (`macro_rules!`, procedural macros, `include!`) is not analyzed. In the arguments of a macro call only the paths written with `::` and the plain names are linked; a method called on a value there (`assert!(v.len() > 0)`) is linked only after `self`. A macro exported with `#[macro_export]` is not linked to the file that defines it
- **Method calls on values**: A method called on a parameter or a `let` variable is listed under the type it is declared with (`c: &Circle; c.area()` is `Circle.area`). A variable whose type is not written and a field (`self.shape.area()`) are not followed
- **Patterns**: A name bound by `if let` / `while let` counts as bound in the whole `if` / `while` expression, its `else` branch included
- **Conditional compilation**: `#[cfg(...)]` is not evaluated. Every alternative module is a dependency

### C#

- **Usage names**: A usage is listed as the file writes it, without its namespaces (`App.Models.Customer.Parse()` is `Customer.Parse`); an attribute is listed with its class (`AuditAttribute`) and a name written with an alias of a type with that type
- **Overloads**: A called method is linked by its name, its number of arguments and, where the file tells them, the types of the arguments. The type of an argument written as a name is taken from the parameters and variables around the call and the fields, properties and primary constructor parameters (`record Point(int X, int Y)`) of the types around it, not from a base type. A method declared in several files of a partial type is linked to the file of the declaration that fits. When no single declaration fits, `target_context` is the first declaration
- **Extension methods**: `value.Method()` is linked to every extension method of that name and number of arguments the line sees, whatever the type of `value` is. `Name.Method()` is taken as such a call too when `Name` is neither a type or member of the project nor an alias of a using directive (a type from outside the project such as `Math.Max(a, b)`, or a member of a base type, also one named like a namespace); a call written after a namespace and a type (`System.IO.File.Exists(path)`) or after an alias is not
- **Method calls on values**: Other than extension methods, `value.Method()` is not linked to the type of `value`. A call written with the type (`Type.Method()`) is. A member inherited from a base type is not linked by its name alone
- **Project files**: `ProjectReference`, `<Using Include="..." />` and `ImplicitUsings` of a `.csproj` file are not read
- **Conditional compilation**: `#if` is not evaluated; the code of every branch is analyzed. A directive that splits a declaration or statement is not parsed; the code around it is still analyzed
- **Unsupported syntax**: `extension` blocks, `allows ref struct` and null-conditional assignment (`a?.b = c`) are not parsed. Surrounding code is still analyzed
- **Generated code**: Code produced by source generators, and `.razor` / `.cshtml` files, are not analyzed

### COBOL

- **Definitions**: The items of the `SCREEN` and `REPORT` sections, `FUNCTION-ID`, `CLASS-ID` and `METHOD-ID` are not definitions, and neither is a data item whose name has other text joined to it (`(PFX)-ID`). A group item whose items come from a `COPY` statement after items of its own is not told apart from a `COPY` of other records
- **Names**: A name several copybooks define is linked to the first of them in the order of the `COPY` statements. A name qualified with `OF` / `IN` that matches no definition is linked as an unqualified one. Of an `EXEC` block other than `EXEC SQL`, every word is read as a name
- **COPY**: The text of a copybook is not expanded into the file that includes it: a copybook is analyzed as a file of its own. A name with a directory part is looked up by its file name. With `REPLACING`, only an operand whose texts are one word each (`==:TAG:== BY ==WS==`, `OLD-NAME BY NEW-NAME`, `LEADING` / `TRAILING`) is applied to the names
- **CALL**: A `CALL` of a data item leads only to the programs named by the literals the item is given in the same file (`VALUE` clause, `MOVE "name" TO item`); a name built at run time is not resolved
- **CICS**: Of the options of an `EXEC CICS` command, only `PROGRAM(...)` and `TRANSID(...)` are linked. `PROGRAM(...)` leads to the program as a `CALL` does. `TRANSID(...)` leads to the `DEFINE TRANSACTION` of a CSD file, or to a JCL file whose `PGM=DFHCSDUP` step defines the transaction (`target_context` `null`). A data item leads only to the literals it is given in the same file, as for `CALL`
- **Several definitions**: When several files fit a `COPY` statement, a program (`CALL`, `PROGRAM(...)`) or a transaction (`TRANSID(...)`), the one nearest to the COBOL file is taken: the one whose directory has the most leading directories in common with that of the COBOL file, then the one with the fewest other directories. For a `COPY` statement, these come before the nearest, in this order: a file whose path ends with the name written, a file in a directory whose path ends with the library of `OF` / `IN`, a file with a COBOL extension (`.cbl`, `.cpy`, ...), a BMS source and a file that defines no program; of files equally near, a `.cpy` file comes first. For a program, a program the COBOL file defines itself is taken before the others
- **Source format**: Without a `>>SOURCE` / `$SET SOURCEFORMAT` directive, the format of a file and whether its code runs past column 72 are inferred. In free format, an indented line that starts with `*` is read as code only when the line before it ends with an operand
- **Unparsed statements**: In a statement that is not parsed, every word that is a name of a definition counts as a usage, and a paragraph that starts in the middle of a sentence (no period before it) is not a definition
- **Debug lines and compiler directives**: Lines with `D` in the indicator column, `>>` directives, `REPLACE` statements and conditional compilation are not evaluated

### JCL

- **Programs**: Only `PGM=`, `RUN PROGRAM(...)` in the in-stream `SYSTSIN` data of a TSO batch step (`IKJEFT01`, `IKJEFT1A`, `IKJEFT1B`) or of a step that calls a procedure, the program in the `PARM` of an IMS batch step (`DFSRRC00`), and the CSD commands in the in-stream `SYSIN` data of a `PGM=DFHCSDUP` step lead to programs. A program a procedure is given for another purpose (the source member of a compile procedure) is not linked. Of several COBOL files that define a program, the nearest to the JCL file is taken (see [COBOL](#cobol))
- **Symbols**: A symbol (`&NAME`) is replaced only with the values written in the same file (`SET`, the defaults of `PROC`, the `EXEC` statements that call an in-stream procedure); the value an `EXEC` statement of another file gives a procedure is not used, and a value with a symbol among other text is not replaced
- **Procedures and members**: `JCLLIB ORDER` is not read: a procedure or `INCLUDE` member is looked up by its file name in the whole project. Of several files, a `.prc` / `.proc` file comes first for a procedure, then the nearest to the JCL file as for a program (see [COBOL](#cobol)). The statements of an `INCLUDE` member are not read into the file that includes it: its steps are not found by `COND`, `IF`, referbacks or `DD` overrides
- **Definitions**: A step or a `PROC` statement without a name is not a definition. A call of a procedure whose file does not start with a named `PROC` statement, an `INCLUDE` statement, and a mapset of the CSD commands of a `PGM=DFHCSDUP` step have `target_context` `null`
- **Not analyzed**: DD names (they are not linked to `SELECT ... ASSIGN` of COBOL), data set names, in-stream data other than `SYSTSIN` and the `SYSIN` of a `PGM=DFHCSDUP` step, JES statements, and JES2 job group statements (`GJOB`, `AFTER`, `SCHEDULE`, ...)

### CSD

- **Dependencies**: Only `DEFINE PROGRAM` and the `PROGRAM` of `DEFINE TRANSACTION` (to COBOL programs), and `DEFINE MAPSET` (to the BMS source that defines the mapset, else the one named like it) lead to other files. A mapset has `target_context` `null`. Of several COBOL files that define a program and of several BMS sources, the nearest to the CSD file is taken (see [COBOL](#cobol))
- **Commands**: Only `DEFINE` and `USERDEFINE` are read, written out or shortened to at least three letters (`DEF`); `LIST`, `ADD`, `ALTER`, `DELETE` and the other commands are not. A command whose first keyword besides `GROUP` is no resource type or its abbreviation (`TRANS`, `PROG`, `TD`, ...) defines nothing
- **Resources**: An attribute named like a resource type or its abbreviation (`PROGRAM`, `PROF`, ...; not `TYPE`, `JOURNAL`, `PARTITIONS` and `JVM`), `TRANSID`, `REMOTESYSTEM`, `TRPROF`, `PRINTER`, `ALTPRINTER`, `CONVERTER`, `URM`, `EXITPGM`, the `ENTRY` of `DB2TRAN` and the `INDIRECTNAME` of `TDQUEUE` are linked to a resource only when the same file defines it: the one of the same `GROUP`, else the first one. Other attributes are not linked
- **Files**: CSD commands are read from `.csd` files and from the in-stream `SYSIN` data of a `PGM=DFHCSDUP` step of a JCL file, not from a step that calls a procedure running DFHCSDUP. The resources of that `SYSIN` data are no definitions of the JCL file, and their attributes are not linked
- **COBOL**: Of the names of a COBOL program, only `EXEC CICS ... TRANSID(...)` is linked to a resource of a CSD file or of the CSD commands of a JCL file (see [COBOL](#cobol))

### R

- **Names**: Scripts that only share a directory, with no `source()`, `box::use`, package (`DESCRIPTION`), Shiny app or `testthat` helper tying them, do not see each other. A name defined in several files in sight is linked to all of them. A usage is listed under the name of the definition (`pkg::f` and `mod$f` are `f`)
- **Names assigned in a function**: A name a function, or a top-level call such as `test_that("...", { })`, assigns is its own from its first assignment in the text on. A read written before it (`cfg <- modifyList(cfg, extra)`) is linked to the definition outside, also when a loop runs it again after the assignment; so is a read outside the functions of a file before the file assigns the name (`data <- subset(data, x > 0)`). The code given to `on.exit()` or `withr::defer()` and the default values of the parameters see every name the function assigns. A call of the name after the assignment (`load_data(path)` after `load_data <- load_data(path)`) is not linked to the function outside
- **Definitions**: `assign("name", ...)`, a name assigned inside the arguments of a top-level call (`test_that("...", { x <- 1 })`, `local({ })`, also `tryCatch({ x <- 1 })`) and S7 `method(generic, class) <- ...` are not definitions
- **`source()`**: A path built at run time is not followed
- **`box::use`**: `#' @export` is not read: a module gives every top-level name
- **Bare column names**: A column written as a bare name (`count(df, n)`, `DT[, sum(x), by = month]`) is linked when a definition of that name is in sight
- **S3**: A call of a generic (`print(x)`) is not linked to its methods
- **S4, R6 and Reference Classes**: `object$method()` and `object@slot` are not linked to the class of `object`
- **Names in strings**: `get("x")`, `exists("x")` and names built at run time are not linked
- **R Markdown and Quarto**: Only the `{r}` code chunks are analyzed. A chunk whose code does not end inside the chunk (an open `{`, an operator at its end), chunks of other languages, inline code (`` `r x` ``), chunks read with `knitr::read_chunk()` and `child` documents are not
- **Not analyzed**: `import::from()`, `NAMESPACE` files, and the link between `.Call()` / Rcpp and C or C++ code

## ♻️ Incremental Processing

On subsequent runs, design documents are regenerated only where needed.

- **Dependency information**: Extracted again for every file on each run
- **Design documents**: Regenerated for the files whose content changed since their `doc.json` was generated (`source_hash`) and for the files that depend on them, directly or through other files; all others are reused. A C/C++ header (`.h`, `.hpp`, `.hh`, `.hxx`) also counts as changed when its implementation file changed: the file of the same name in the same directory with the extension `.cpp`, `.c`, `.cc` or `.cxx` (the first one found, in this order)
- **Incomplete documents**: A `doc.json` with missing sections or an empty summary while the template has a `summary_prompt` (e.g. after an LLM API failure), and a `doc.json` that cannot be read as a design document, are regenerated
- **Removed or moved dependencies**: A file whose content did not change keeps its document when a file it depended on is removed, or when a name of it now resolves to another file that did not change. A C/C++ header whose content did not change keeps its document when its implementation file is removed

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

When you modify the template sections, existing design documents whose section structure no longer matches the template are automatically regenerated on the next run.

Each section needs an `id` of its own and a `title` (non-empty strings) and a `prompt` (a string); `summary_prompt` is optional. With `ENABLE_LLM_DOC=True`, a template file that cannot be read as JSON or does not have this form stops the run with a `ValueError` before any file is analyzed.

`examples/` holds two other templates: `doc_template_search.json` (one section written in the words a reader would search for, any language) and `doc_template_python.json` (a fuller specification for Python).

## ✏️ Manual Editing of Design Documents

You can manually edit the output `doc.md` and have it automatically reflected in `doc.json` on the next run.

1. Edit `output/<project name>/<filename>/doc.md` with a text editor
2. On the next `uv run main.py` execution, if `doc.md` has a newer timestamp than `doc.json`, the Markdown section content is parsed and applied to `doc.json`. With `ENABLE_LLM_DOC=False`, this is done for the files whose content has not changed since their design document was generated

Notes for editing:

- Do not delete or rename `# Section heading` lines. When a heading is missing, the sections next to it keep their `doc.json` text, a warning names them, and `doc.md` is left as it is
- The body text below section headings can be freely edited, except that a line `# <title of the next section>` added to a section, or `# Summary` added to the last section of a document with a summary, is taken for that heading
- Save `doc.md` as UTF-8. A `doc.md` that cannot be read as UTF-8 leaves `doc.json` as it is, and a warning names the file
- A file whose design document is regenerated (see [Incremental Processing](#️-incremental-processing)) gets a new `doc.md`, and the edits of its `doc.md` are replaced; a warning names the file

## ⏭️ Running Without Design Document Generation

To output only dependency information without generating LLM design documents, set `ENABLE_LLM_DOC=False` in `.env`.

```bash
ENABLE_LLM_DOC=False
```

Dependency information (`file_dependencies.json` and file copies) is still generated for each file, along with `project_dependency_summary.json`, `dependency_graph.md` and the knowledge file in the form `KNOWLEDGE_FORMAT` selects (`project_knowledge.json` by default). No API key or model configuration is needed. A design document an earlier run generated is included in the whole-project files, with the edits of its `doc.md` (see [Manual Editing of Design Documents](#️-manual-editing-of-design-documents)), while the content of its file has not changed since.

## 💡 Usage Example: RLM QA Agent

`examples/rlm_qa/` contains a sample that performs interactive Q&A against a knowledge file with dspy's RLM.

### Sample Output

`examples/sample_output/` contains sample output produced by analyzing the codetwine repository itself with the template `examples/doc_template_search.json`. It is a snapshot and does not reflect the current source. `rlm_qa_agent.py` reads it by default.

### Additional Prerequisites

- [Deno](https://deno.land/) runtime
- dspy package (install with `uv sync --extra examples`)

### How to Run

```bash
uv run python examples/rlm_qa/rlm_qa_agent.py
```

To use your own project's output, edit `TARGET_KNOWLEDGE_PATH` in `rlm_qa_agent.py`. A path ending in `.sqlite` is read as `project_knowledge.sqlite`, any other path as `project_knowledge.json`.

The agent calls the models set in the `LLM_MODEL` and `SUB_LLM_MODEL` constants at the top of `rlm_qa_agent.py` (with `LLM_API_BASE`), not the `LLM_MODEL` of `.env`. It reads the API key from the `LLM_API_KEY` environment variable: set the constants to models of the provider of that key.

## 🏗️ Project Structure

```
codetwine/
├── main.py                 # CLI entry point
├── doc_template.json       # Design document section template
├── .env.example            # Environment variable template
├── codetwine/              # Pipeline, module and reference resolution per language, output
│   ├── config/             # Environment variables, per-language settings, logging
│   ├── extractors/         # Definitions, imports, usages, the dependency graph, and the COBOL, BMS, JCL, CSD, C# and R readers
│   ├── parsers/            # tree-sitter parser and node helpers, and source-format readers (COBOL format, R Markdown, C macros, Kotlin, statement operands, CSD commands)
│   ├── llm/                # LLM API client via litellm
│   └── utils/              # File and cache utilities
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
