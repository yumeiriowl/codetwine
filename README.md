# codetwine

A CLI tool that analyzes source code with tree-sitter, extracts key definitions and dependencies, generates design documents via LLM, and consolidates everything into a single knowledge file.
The knowledge file can be used as input material for LLM-powered code search and Q&A, such as RLM, GraphRAG, and agent search.

- Dependencies are extracted at the symbol level (functions, classes)
- Design documents are generated per file, taking dependencies into account
- The consolidated result is written as a single JSON, a SQLite database, or both (`KNOWLEDGE_FORMAT`)
- Outputs dependency graphs in Mermaid format

## Table of Contents

- [codetwine](#codetwine)
  - [Table of Contents](#table-of-contents)
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
    - [R](#r)
  - [♻️ Incremental Processing](#️-incremental-processing)
  - [🗄️ SQLite Output](#️-sqlite-output)
  - [📋 Output JSON Schema](#-output-json-schema)
    - [project\_knowledge.json](#project_knowledgejson)
    - [project\_dependency\_summary.json](#project_dependency_summaryjson)
    - [file\_dependencies.json](#file_dependenciesjson)
    - [doc.json](#docjson)
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

Definitions, dependencies and design documents are extracted for the following languages
(extensions `py`, `java`, `kt`, `kts`, `js`, `jsx`, `mjs`, `cjs`, `ts`, `tsx`, `mts`, `cts`, `c`, `cpp`,
`cc`, `cxx`, `h`, `hpp`, `hh`, `hxx`, `sql`, `rs`, `cs`, and `cbl`, `cob`, `cpy`, `bms`, `r`, `rmd`, `qmd` in any case):

- Python
- Java
- JavaScript
- TypeScript 
- C
- C++
- Kotlin
- SQL
- Rust
- C#
- COBOL (a file with another extension, or none, that a `COPY` statement names is read as a copybook)
- BMS (CICS map definitions: the names of the symbolic map a COBOL program copies)
- R (scripts, and the `{r}` code chunks of R Markdown and Quarto files; a `.Rmd` / `.qmd` file without such a chunk is a file without a language)

Every other non-empty text file (`.md`, `.yaml`, `.toml`, `Makefile`, ...) is also listed in the
outputs and copied to the output directory, with empty `definitions`, `callee_usages`,
`same_file_usages` and `caller_usages`, a `null` summary and no design document. Empty files, binary files and
symbolic links are skipped; a linked directory is not entered.

## 🚀 Quick Start

### Prerequisites

- Python 3.11 or higher
- [uv](https://github.com/astral-sh/uv) package manager
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

Analyzes the project set in `DEFAULT_PROJECT_DIR` in `.env` and outputs results to the `output/` directory.

#### Specifying Project and Output Directories

```bash
uv run main.py --project-dir /path/to/your/project --output-dir /path/to/output
```

| Argument | Description | Default |
|------|------|------------|
| `--project-dir` | Root directory of the project to analyze | `DEFAULT_PROJECT_DIR` from `.env` |
| `--output-dir` | Output directory for analysis results | `DEFAULT_OUTPUT_DIR` from `.env` (defaults to `output/` if not set). When only `--project-dir` is specified, `DEFAULT_OUTPUT_DIR` is ignored and `output/` is used |

#### Calling from Python

```python
import asyncio
from codetwine.pipeline import process_all_files

run_result = asyncio.run(process_all_files(
    "/path/to/your/project", "/path/to/output", llm_client=None,
    file_list=["src/main.py", "src/utils.py"],
))
```

`file_list` (optional) holds file paths relative to the project root. When given, only these files are analyzed instead of walking the project directory; `EXCLUDE_PATTERNS`, the empty / binary file check and the symbolic link check still apply. `llm_client` is an `LLMClient()` when `ENABLE_LLM_DOC=True`.

| Return key | Type | Description |
|-----------|-----|------|
| `file_count` | int | Number of files analyzed |
| `dependency_fail_list` | string[] | Files whose dependency extraction failed. Their `file_dependencies.json` and copy are removed from the output directory, and they have no dependencies in the dependency graph; the other files are analyzed |
| `doc_count` | int | Files with a complete design document (`0` when `ENABLE_LLM_DOC=False`) |
| `doc_fail_list` | string[] | Files left without a complete design document |
| `detected_encoding_dict` | {string: string} | Files of a supported language whose encoding was detected (see **Detected encodings**), with that encoding; `""` for a file read as UTF-8 with invalid bytes replaced |

## ⚙️ Configuration Options

The following options can be configured in the `.env` file.

### LLM Settings

| Variable | Description | Default |
|--------|------|------------|
| `LLM_API_KEY` | API key for the LLM provider | None |
| `LLM_MODEL` | Model name in litellm format (required when `ENABLE_LLM_DOC=True`) | None |
| `LLM_API_BASE` | API base URL (set when using non-standard endpoints, e.g. Ollama, Azure) | Not set |
| `OUTPUT_LANGUAGE` | Output language for design documents | `English` |
| `DOC_MAX_TOKENS` | Token limit for LLM output. Output cut at this limit is saved as it is and a warning is logged | `16384` |

### Path Settings

| Variable | Description | Default |
|--------|------|------------|
| `DEFAULT_PROJECT_DIR` | Root directory of the project to analyze | Repository root |
| `DEFAULT_OUTPUT_DIR` | Output directory for analysis results | `output/` |
| `DOC_TEMPLATE_PATH` | Path to the design document template JSON file | `doc_template.json` |

### Performance Settings

| Variable | Description | Default |
|--------|------|------------|
| `MAX_WORKERS` | Number of parallel workers for document generation | `4` |
| `MAX_RETRIES` | Number of retries of an LLM API call after a rate limit error (`0`: one call, no retry) | `3` |
| `RETRY_WAIT` | Wait time in seconds between retries | `2` |
| `PARSE_CACHE_MAX_FILES` | Number of files whose parse results are kept in memory at once (`0`: no limit) | `200` |

### Output Settings

| Variable | Description | Default |
|--------|------|------------|
| `KNOWLEDGE_FORMAT` | Form of the whole-project result: `json` (`project_knowledge.json`), `sqlite` (`project_knowledge.sqlite`), or `both` | `json` |

The per-file JSON files are written in every case.

### Analysis Settings

| Variable | Description | Default |
|--------|------|------------|
| `ENABLE_LLM_DOC` | Enable/disable LLM design document generation (`True` / `False`) | `True` |
| `SUMMARY_MAX_CHARS` | Maximum character count for summaries | `600` |
| `ENABLE_CODE_SUMMARY` | Summarize large code with the LLM when a prompt exceeds the model context window (`True` / `False`). When `False`, such a prompt is only reduced by dropping caller/callee context | `True` |
| `CODE_SUMMARY_TRIGGER_LINES` | Line span above which a definition or dependency symbol is summarized | `40` |
| `CODE_SUMMARY_MAX_CHARS` | Character limit for a single code behavior summary | `400` |
| `EXCLUDE_PATTERNS` | Directory and file name patterns to exclude (comma-separated, fnmatch format). Every text file that is not excluded is collected and copied to the output: add secrets, data files, lock files and an output directory inside the project | `__pycache__,.git,.github,.venv,node_modules` |
| `SOURCE_ENCODING` | Encodings tried, in order, on a source file that has no BOM and is not valid UTF-8 (comma-separated Python codec names). The first one that decodes the file is used; list `euc_jp` before `cp932` | None |

## 🔄 High-Level Processing Flow

1. **Build the project-wide dependency graph**
   - Collects every non-empty text file from the target directory
   - Resolves the imports and references of each file of a supported language to the project files they lead to
2. **Extract dependency information for each file**
   - Extracts definitions (functions, classes, etc.) with tree-sitter
   - Extracts the usages of other files' definitions (callees) and the usages of this file's definitions by other files (callers), with line numbers and source code
3. **Generate design documents via LLM** (files of a supported language only)
   - Regenerates only the files changed since their previous design document, and the files that depend on them
   - Processes files in dependency order, from files with no dependencies toward dependent files
   - Generates each document section by section according to the template (`doc_template.json`), from the source code, the dependency information and the summaries of the files it depends on
   - Generates a summary for each file
   - A prompt that exceeds the model context window is retried with less context (see `ENABLE_CODE_SUMMARY`)
4. **Save all outputs**
   - Saved to `<output directory>/<project name>`
   - All dependencies and design documents are consolidated into a single JSON (`project_knowledge.json`), a SQLite database (`project_knowledge.sqlite`), or both, as `KNOWLEDGE_FORMAT` selects
   - Dependency graphs and design documents are also output as Markdown

Note: LLM API calls are only made in step 3. No LLM is used in other steps.

## 📁 Output Files

Running the tool generates the following files in `<output directory>/<project name>/` (default: `output/<project name>/`).

| File | Description |
|----------|------|
| `project_knowledge.json` | Consolidated JSON of all file dependencies and design documents (`KNOWLEDGE_FORMAT=json` / `both`) |
| `project_knowledge.sqlite` | Consolidated SQLite database with the same content (`KNOWLEDGE_FORMAT=sqlite` / `both`) |
| `project_dependency_summary.json` | Consolidated JSON of the dependency graph + per-file summaries |
| `dependency_graph.md` | Dependency graph in Mermaid format |
| `<filename>/file_dependencies.json` | Per-file definition and dependency information |
| `<filename>/doc.json` | Per-file design document (JSON format) |
| `<filename>/doc.md` | Per-file design document (Markdown format) |
| `<filename>/<original filename>` | Copy of the original file, byte for byte in its own encoding |

All JSON output is UTF-8.

## ⚠️ Dependency Analysis Limitations

Dependencies are extracted by static analysis of the source text. They may be missing or incomplete in the following cases.

### Common to All Languages

- **Dynamic imports**: Patterns that construct module names as strings at runtime cannot be detected
  - Python: `importlib.import_module(name)`, `__import__(name)`
  - JavaScript/TypeScript: `import(variable)`
  - Java: `Class.forName("com.example.Foo")`
- **Detected encodings**: A file that has no BOM, is not valid UTF-8 and is not decoded by `SOURCE_ENCODING` is decoded with a detected encoding, which can be wrong: comments and strings may be garbled and definitions may be lost. Such files are listed in `detected_encoding_dict` and carry `detected_encoding` in their `file_dependencies.json`. Set `SOURCE_ENCODING` for them
- **Local names**: A name bound inside a function (a parameter, a local variable, an inner function) is not linked to an imported or file-level name of that name
- **`self` / `this`**: `self.name`, `cls.name` and `this.name` are linked to the definition `name` of the same file, whatever class it is written in (Python, JavaScript/TypeScript, Java, Kotlin, C++, Rust)

### Python

- **Import roots**: A module is looked up from the project root, the directory of the file and a `src/` directory; a module written with two or more parts is also matched against the end of the project file paths (`from app.models import User` leads to `backend/app/models.py`). Roots added through `PYTHONPATH` or `sys.path` are not read
- **Modules of the same name**: A module that both a project file and an installed package provide (`import utils` with `utils.py` in the project) is linked to the project file
- **`__all__`**: `from m import *` brings in every top-level name of `m`; `__all__` is not read
- **Method calls on values**: `e = Engine(); e.run()` is not linked to `Engine.run`

### JavaScript / TypeScript

- **Path aliases**: A module that is not relative (`import { x } from "app/store"`) is resolved through `paths` and `baseUrl` of the `tsconfig.json` or `jsconfig.json` in the directory of the file or the nearest one above it; `extends` is followed when it is a relative path. A module such a config file does not map is not linked. Without a config file the module is looked up from the project root and a `src/` directory. Aliases of bundlers (Webpack, Vite), `imports` and workspaces of `package.json`, and `include` / `exclude` / `references` of the config file are not read
- **Imports not bound to a name**: A `require()` or `import()` whose result the statement does not bind to a name (`require("./m").run()`, `import("./m").then(...)`) adds the dependency on the file, but the names used from it are not linked
- **Method calls on values**: `const s = new Store(); s.get()` is not linked to `Store.get`
- **Definitions**: The declarations inside a callback (`describe("...", function () { ... })`) are not listed as definitions; the ones inside a function called where it is written (`(function () { ... })()`) are. An unnamed default export (`export default { ... }`) is listed as the definition `default`

### Java / Kotlin

- **Packages**: A dependency on a file of a wildcard import or of the same package is added when one of its names is used. A file without a `package` statement sees only the files of its directory that have none either
- **Method calls**: A method called on a variable is linked to the type the variable is declared with (`User u; u.getName()` is listed as `User.getName`). A variable whose type is not written (`var`, `val u = ...`) and a chain of calls are not followed
- **Kotlin class bodies on one line**: A class, object or interface whose body with members is written on one line (`class A { fun f() {} }`) is not parsed by the grammar. Surrounding code is still analyzed

### C / C++

- **Include paths**: `#include` is looked up from the project root and the directory of the file, then as the nearest project file whose path ends with the included path (`#include "list.h"` leads to `include/list.h`). Include paths added via CMake or Makefile `-I` options are not read
- **Declarations and definitions**: A function a header declares is linked to the header and to each file that includes the header and defines it; when several files define it (per-platform sources), all of them are linked. A variable declared `extern` is linked to the header only
- **Namespaces**: `using namespace` is not evaluated, and a name is linked without its namespaces (`geo::Shape::count()` is listed as `Shape::count`), so equal names of different namespaces are not told apart
- **Preprocessor**: `#if` / `#ifdef` are not evaluated; every branch is analyzed. Definitions produced by macro expansion are not analyzed, and a declaration written with a macro the grammar does not read (`EXPORT int f(void);`) may be listed without its type

### SQL

- **Object references**: A dependency is added when a table, view, function, type or sequence created in another `.sql` file of the project is referenced. Names are compared as written, so a reference that differs in case or quoting from the `CREATE` statement is not detected
- **Unsupported syntax**: PostgreSQL-style `CREATE PROCEDURE`, `CALL`, `GRANT` and psql `\i` are not parsed by the grammar. Surrounding statements are still analyzed

### Rust

- **Module paths**: A path starting with a crate name is resolved when that crate's `Cargo.toml` is in the project. A `mod` declaration inside an inline module (`mod a { mod b; }`) and `#[cfg_attr(..., path = "...")]` are not resolved. A name a module only re-exports from outside the project (`pub use std::collections::HashMap;`) is not linked
- **Inline modules**: The names of the whole file are linked inside an inline module, also without `use super::*`
- **Impl blocks in other files**: `Type::member` is linked to the file that defines the type and to every other file whose `impl` block of the type defines the member
- **`Self`**: `Self::new()` inside an `impl` block is listed as `Type::new`; `Self` inside a `trait` is listed as written
- **Macros**: Code generated by macros (`macro_rules!`, procedural macros, `include!`) is not analyzed. In the arguments of a macro call only the paths written with `::` and the plain names are linked. A macro exported with `#[macro_export]` is not linked to the file that defines it
- **Method calls on values**: `value.method()` is not linked to the type of `value`. A call written with the type (`Type::method()`) is
- **Patterns**: A name bound by a pattern of `match` / `if let` / `while let` is not told apart from the names of the whole file
- **Conditional compilation**: `#[cfg(...)]` is not evaluated. Every alternative module is a dependency

### C#

- **Usage names**: A usage is listed as the file writes it, without its namespaces (`App.Models.Customer.Parse()` is `Customer.Parse`); an attribute is listed with its class (`AuditAttribute`) and a name written with an alias of a type with that type
- **Overloads**: A called method is linked by its name and its number of arguments; the types of the arguments are not read. When the number fits no declaration or several, `target_context` is the first declaration. The lines of one name that lead to one file share the `target_context` of the first line
- **Extension methods**: `value.Method()` is linked to every extension method of that name and number of arguments the line sees, whatever the type of `value` is
- **Method calls on values**: Other than extension methods, `value.Method()` is not linked to the type of `value`. A call written with the type (`Type.Method()`) is. A member inherited from a base type is not linked by its name alone
- **Types in several files**: A `partial` type is linked to every file that declares it, or to the files that define the member when one is named. A type of the same name and namespace declared under several `.csproj` directories is linked to the one under the `.csproj` directory of the referring file, when there is one
- **Project files**: `global using` directives count for the files under the nearest `.csproj` directory above the file they are written in. `ProjectReference`, `<Using Include="..." />` and `ImplicitUsings` of a `.csproj` file are not read
- **Conditional compilation**: `#if` is not evaluated; the code of every branch is analyzed. A directive that splits a declaration or statement is not parsed by the grammar; the code around it is still analyzed
- **Unsupported syntax**: `extension` blocks, `allows ref struct` and null-conditional assignment (`a?.b = c`) are not parsed by the grammar. Surrounding code is still analyzed
- **Generated code**: Code produced by source generators, and `.razor` / `.cshtml` files, are not analyzed

### COBOL

- **Definitions**: Programs (`PROGRAM-ID`), `ENTRY` names, sections, paragraphs, data items, index names (`INDEXED BY`) and file descriptions (`FD` / `SD`) are definitions. The items of the `SCREEN` and `REPORT` sections, `FUNCTION-ID`, `CLASS-ID` and `METHOD-ID` are not, and neither is a data item whose name has other text joined to it (`(PFX)-ID`). A group item whose items come from a `COPY` statement after items of its own is not told apart from a `COPY` of other records
- **Names**: Names are compared without regard to upper and lower case. A name is linked to its definition in the file itself, else to the first copybook, in the order of the `COPY` statements, that defines it. A name qualified with `OF` / `IN` that matches no definition is linked as an unqualified one. Of an `EXEC SQL` block only the host variables are read; of any other `EXEC` block every word is read as a name
- **COPY**: `COPY name` and `EXEC SQL INCLUDE name` lead to the project file named `name`, with or without its extension; a name with a directory part is looked up by its file name. The text of a copybook is not expanded into the file that includes it: a copybook is analyzed as a file of its own. With `REPLACING`, only an operand whose texts are one word each (`==:TAG:== BY ==WS==`, `OLD-NAME BY NEW-NAME`, `LEADING` / `TRAILING`) is applied to the names
- **BMS**: `COPY name` leads to the `.bms` file of that name or of that mapset name, unless a COBOL file of that name (a generated symbolic map) is in the project
- **CALL**: `CALL "name"` and `EXEC CICS ... PROGRAM("name")` lead to the file that defines a program or an `ENTRY` of that name, or to the program file named `name`. A `CALL` of a data item leads to the programs named by the literals the item is given in the same file (`VALUE` clause, `MOVE "name" TO item`); a name built at run time is not resolved
- **Source format**: Fixed-format and free-format source are read. Without a `>>SOURCE` / `$SET SOURCEFORMAT` directive, the format of a file and whether its code runs past column 72 are judged from its lines
- **Statements the grammar does not read**: In a statement the grammar does not read, every word that is a name of a definition counts as a usage, and a paragraph that starts in the middle of a sentence (no period before it) is not a definition
- **Debug lines and compiler directives**: Lines with `D` in the indicator column, `>>` directives, `REPLACE` statements and conditional compilation are not evaluated

### R

- **Names**: A name is linked to a top-level definition of the file itself, of the scripts it reads with `source()` or `box::use`, of the files that read it, of its Shiny app (`global.R`, `R/`), of the `testthat` helper scripts, of its package (`R/` beside a `DESCRIPTION` file) and of the project packages it attaches with `library()`. Scripts that share only a directory do not see each other. A name defined in several of those files is linked to all of them. A usage is listed under the name of the definition (`pkg::f` and `mod$f` are `f`)
- **Definitions**: `assign("name", ...)`, a name assigned inside `local()` and S7 `method(generic, class) <- ...` are not definitions
- **`source()`**: A path built at run time is not followed
- **`box::use`**: `#' @export` is not read: a module gives every top-level name
- **Bare column names**: A column written as a bare name (`count(df, n)`, `DT[, sum(x), by = month]`) is linked when a definition of that name is in sight
- **S3**: A call of a generic (`print(x)`) is not linked to its methods
- **S4, R6 and Reference Classes**: `object$method()` and `object@slot` are not linked to the class of `object`
- **Names in strings**: `do.call("f", ...)` and `match.fun("f")` are linked. `get("x")`, `exists("x")` and names built at run time are not
- **R Markdown and Quarto**: Only the `{r}` code chunks are analyzed. A chunk whose code does not end inside the chunk (an open `{`, an operator at its end) is not analyzed, and neither are chunks of other languages, inline code (`` `r x` ``), chunks read with `knitr::read_chunk()` and `child` documents
- **Not analyzed**: `import::from()`, `NAMESPACE` files, and the link between `.Call()` / Rcpp and C or C++ code

## ♻️ Incremental Processing

On subsequent runs, design documents are regenerated only where needed.

- **Dependency information**: Extracted again for every file on each run
- **Design documents**: Regenerated for the files whose content changed since their `doc.json` was generated (`source_hash`) and for the files that depend on them; all others are reused
- **Incomplete documents**: A `doc.json` with missing sections or an empty summary (e.g. after an LLM API failure) is regenerated

## 🗄️ SQLite Output

With `KNOWLEDGE_FORMAT=sqlite` or `both`, the same content as `project_knowledge.json`
is written to `project_knowledge.sqlite`.

| Table | Columns | Description |
|-------|---------|-------------|
| `meta` | `key`, `value` | `project_name`, `schema_version`, `created_at` |
| `files` | `file`, `summary`, `file_dependencies`, `doc` | One row per file. `file_dependencies` and `doc` hold the JSON bodies as text; both are `NULL` when the file has none |
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

## 📋 Output JSON Schema

### project_knowledge.json

Consolidated JSON integrating all file dependencies and design documents.

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
| `project_dependencies[].summary` | string\|null | Summary of the file (null when the design document is not generated, and always for a file without a supported language) |
| `project_dependencies[].callers` | string[] | Paths of dependent files copied to the output directory |
| `project_dependencies[].callees` | string[] | Paths of dependency files copied to the output directory |
| `files[].file` | string | Path of the source file copied to the output directory |
| `files[].file_dependencies` | object | Same structure as file_dependencies.json (excluding `file` field) |
| `files[].doc` | object | Same structure as doc.json (excluding `file` field) |

### project_dependency_summary.json

Consolidated JSON of the dependency graph and per-file summaries.

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
| `language` | string | Extension whose language settings the file is analyzed with, in lower case (`py`, `cbl`, ...; `cpy` for a copybook of another extension or without one that a `COPY` statement names). `""` for a file without a language, and for a `.Rmd` / `.qmd` file without an R code chunk |
| `detected_encoding` | string\|null | Encoding the file was decoded with when it had no BOM, was not valid UTF-8 and was not decoded by `SOURCE_ENCODING` (the encoding charset-normalizer detects; `""` when it was read as UTF-8 with invalid bytes replaced). `null` otherwise, and for a file without a language |
| `definitions[].name` | string | Function/class name |
| `definitions[].type` | string | Definition type (tree-sitter node type, varies by language. Python: `function_definition`, `class_definition` / Java: `class_declaration`, `method_declaration` / JS/TS: `function_declaration`, `class_declaration` / SQL: `create_table`, `create_view` / Rust: `function_item`, `struct_item`, `impl_item` / C#: `class_declaration`, `method_declaration`, `property_declaration` / R: `function_definition`, `binary_operator`, `call`, `argument`, etc.) |
| `definitions[].start_line` | int | Start line number. Every line number counts from 1, and a line ends at `\n`, `\r\n` or a lone `\r` |
| `definitions[].end_line` | int | End line number |
| `definitions[].name_line` | int | Line the name is written on (COBOL and BMS only) |
| `definitions[].level` | int | Level number of a data item (COBOL and BMS data items only) |
| `definitions[].is_group` | bool | Whether a data item is a group item (COBOL and BMS data items only) |
| `definitions[].context` | string | Full source code of the definition |
| `callee_usages[].name` | string | Name of the used symbol as the file writes it (`helper.process`, `Settings::new`). A member used on a variable declared with a type of the project is named after the type (`User.getName`, `node_t.value`) |
| `callee_usages[].from` | string | Path of the dependency file copied to the output directory |
| `callee_usages[].target_context` | string\|null | Full source code of the definition the name leads to. `null` when it leads to no definition of that file (a module used as a value) |
| `callee_usages[].target_name` | string\|null | `definitions[].name` of that definition in the dependency file (`helper` for a usage written `hp` after `const { helper: hp } = require(...)`). `null` with `target_context` |
| `callee_usages[].target_start_line` | int\|null | `definitions[].start_line` of that definition. `null` with `target_context` |
| `callee_usages[].lines` | int[] | Line numbers of usage within this file |
| `same_file_usages[].name` | string | Name of a symbol defined in this file and used in it |
| `same_file_usages[].target_name` | string\|null | `definitions[].name` of the definition the name leads to (`new` for `Point::new`). `null` when it leads to no definition |
| `same_file_usages[].target_start_line` | int\|null | `definitions[].start_line` of that definition |
| `same_file_usages[].lines` | int[] | Line numbers of usage within this file, outside the definition the name refers to |
| `caller_usages[].name` | string | Name of the symbol being used |
| `caller_usages[].file` | string | Path of the dependent file copied to the output directory |
| `caller_usages[].usage_context` | string | Source code of the usage location in the dependent |
| `caller_usages[].lines` | int[] | Line numbers of usage in the dependent file |

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
| `file` | string | Path of the source file copied to the output directory |
| `summary` | string | Summary of the file |
| `sections[].id` | string | Section identifier (corresponds to id in doc_template.json) |
| `sections[].title` | string | Section heading |
| `sections[].content` | string | Section body (Markdown format) |
| `source_hash` | string | SHA256 hash of the source file the document was generated from |

In the `definitions` section, each definition starts with a level-2 heading holding only the definition name in backticks (`` ## `parse_args` ``).

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
| Use a different template | Specify the path in `DOC_TEMPLATE_PATH` in `.env` |

When you modify the template sections, existing design documents whose section structure no longer matches the template are automatically regenerated on the next run.

`examples/` holds two other templates: `doc_template_search.json` (one section written in the words a reader would search for, any language) and `doc_template_python.json` (a fuller specification for Python).

## ✏️ Manual Editing of Design Documents

You can manually edit the output `doc.md` and have it automatically reflected in `doc.json` on the next run.

1. Edit `output/<project name>/<filename>/doc.md` with a text editor
2. On the next `uv run main.py` execution, if `doc.md` has a newer timestamp than `doc.json`, the Markdown section content is parsed and applied to `doc.json`

Notes for editing:

- Do not delete or rename `## Section heading` lines
- The body text below section headings can be freely edited

## ⏭️ Running Without Design Document Generation

To output only dependency information without generating LLM design documents, set `ENABLE_LLM_DOC=False` in `.env`.

```bash
ENABLE_LLM_DOC=False
```

Dependency information (`file_dependencies.json` and file copies) is still generated for each file, along with `project_knowledge.json`, `project_dependency_summary.json`, and `dependency_graph.md`. No API key or model configuration is needed. The next run with `ENABLE_LLM_DOC=True` regenerates the design documents of every file changed since they were generated.

## 💡 Usage Example: RLM QA Agent

`examples/rlm_qa/` contains a sample that performs interactive Q&A against a knowledge file. It uses dspy's RLM and PythonInterpreter to generate answers by manipulating data with Python code.

The agent receives the file graph and one summary per file, and fetches definitions, source code and design documents per file through tools (`get_file_detail`, `search_text`, `read_source_file`, `get_files_using`, `graph_search`).

### Sample Output

`examples/sample_output/` contains sample output produced by analyzing the codetwine repository itself with the template `examples/doc_template_search.json`. It is a snapshot and does not reflect the current source. `rlm_qa_agent.py` references it by default, so you can try out RLM QA without running any analysis.

> **Note:** The `file` field paths in `project_knowledge.json` refer to sources copied into the output directory and differ from the original source tree paths (e.g. `codetwine/import_to_path.py` → `codetwine/import_to_path_py/import_to_path.py`).

### Additional Prerequisites

- [Deno](https://deno.land/) runtime
- dspy package (install with `uv sync --extra examples`)

### How to Run

```bash
uv run python examples/rlm_qa/rlm_qa_agent.py
```

To use your own project's output, edit `TARGET_KNOWLEDGE_PATH` in `rlm_qa_agent.py`. A path ending in `.sqlite` is read as `project_knowledge.sqlite`, any other path as `project_knowledge.json`.

## 🏗️ Project Structure

```
codetwine/
├── main.py                 # CLI entry point
├── doc_template.json       # Design document section template
├── .env.example            # Environment variable template
├── codetwine/              # Pipeline, module and reference resolution per language, output
│   ├── config/             # Environment variables, per-language settings, logging
│   ├── extractors/         # Definitions, imports, usages and the dependency graph
│   ├── parsers/            # tree-sitter parser, COBOL and R Markdown readers
│   ├── llm/                # LLM API client via litellm
│   └── utils/              # File and cache utilities
└── examples/
    ├── rlm_qa/             # RLM QA agent sample
    └── sample_output/      # Sample output (codetwine analyzed against itself)
```

## 🙏 Acknowledgments

This project uses the following libraries:

- [tree-sitter](https://tree-sitter.github.io/tree-sitter/) - Source code syntax analysis
- [tree-sitter-language-pack](https://github.com/xberg-io/tree-sitter-language-pack) - COBOL grammar ([tree-sitter-cobol](https://github.com/yutaro-sakamoto/tree-sitter-cobol)) and R grammar ([tree-sitter-r](https://github.com/r-lib/tree-sitter-r))
- [litellm](https://github.com/BerriAI/litellm) - Unified interface for multiple LLM providers

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.
