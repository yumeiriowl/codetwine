# codetwine

A CLI tool that analyzes source code with tree-sitter. It extracts definitions and dependencies, writes a design document for each file with an LLM, and gathers everything into one knowledge file.
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
- [📝 Design Documents](#-design-documents)
- [⚠️ Dependency Analysis Limitations](#️-dependency-analysis-limitations)
- [💡 Usage Example: RLM QA Agent](#-usage-example-rlm-qa-agent)
- [🧪 Running the Tests](#-running-the-tests)
- [🏗️ Project Structure](#️-project-structure)
- [🙏 Acknowledgments](#-acknowledgments)
- [📄 License](#-license)

## 🧬 Supported Languages

codetwine extracts definitions and dependencies, and writes design documents, for these languages:

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

The extensions of COBOL, BMS, JCL, CSD and R match in any case.

Some files get their language from their content instead of their extension:

- **JCL**: a file with an extension the table does not list, or none, is read as JCL when its first statement is an upper-case `JOB` / `JOBGROUP` statement. It is also read as JCL when its first statement is an upper-case JCL statement and an `EXEC` or `INCLUDE` statement names it.
- **COBOL copybooks**: a file without a language is read as a copybook when a `COPY` or `EXEC SQL INCLUDE` statement names it. The statement must name its whole file name, or the file must hold COBOL code.
- **No language**: a `.jcl` / `.prc` / `.proc` file whose first statement is not JCL gets no language. So do a `.csd` file that does not start with a DFHCSDUP command and a `.Rmd` / `.qmd` file without an `{r}` chunk. The two rules above still apply to them.

Every other non-empty text file (`.md`, `.yaml`, `Makefile`, ...) is listed in the outputs and copied to the output directory. It gets no definitions, usages, summary or design document. Empty files, binary files, symbolic links, named pipes, sockets and devices are skipped. Linked directories are not entered.

## 🚀 Quick Start

### Prerequisites

- Python 3.11 or later
- [uv](https://github.com/astral-sh/uv) 0.11.8 or later
- An API key for an LLM provider (Anthropic, OpenAI, Google, ...). You do not need one for local models such as Ollama, or when design documents are turned off.

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

If the machine cannot reach `raw.githubusercontent.com` (offline, GitHub blocked, or a required proxy not set), also set `LITELLM_LOCAL_MODEL_COST_MAP=True`. Without it, codetwine can stop at start-up with an `ImportError` from litellm. With it, litellm uses the model table it ships with. That table may not know models newer than the installed litellm.

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

`main.py` checks the directories and settings before it analyzes anything. If one is wrong, it logs the error and exits with status 1. It checks that:

- the project directory exists, is not the file system root and has a valid UTF-8 name;
- the output directory is neither the project directory nor its parent;
- `KNOWLEDGE_FORMAT` is `json`, `sqlite` or `both`, every name in `SOURCE_ENCODING` is a known text codec, and `PARSE_CACHE_MAX_FILES` is 0 or more;
- with `ENABLE_LLM_DOC=True`: `LLM_MODEL` is set, `MAX_RETRIES` is 0 or more, `DOC_EFFORT` is a known value, `MAX_WORKERS` is 1 or more, `CODE_SUMMARY_PIECE_LINES` is 1 or more (with `ENABLE_CODE_SUMMARY=True`), and the template can be read and has the right form (see [Template](#template)).

The log goes to `logs/codetwine.log` under the repository root (1 MB per file, five old files kept). The console shows progress, warnings and errors.

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
| `llm_client` | `LLMClient()` with `ENABLE_LLM_DOC=True` (it reads the model, key, base URL and effort from `.env`). `None` with `ENABLE_LLM_DOC=False` |
| `file_list` | Optional. Paths relative to the project root to analyze, instead of walking the whole project. `EXCLUDE_PATTERNS` and the file checks still apply. Paths that do not exist or lead outside the project are skipped with a warning |
| `max_workers` | Optional. Number of design documents written in parallel (1 or more; default `MAX_WORKERS`) |

| Return key | Type | Description |
|------------|------|-------------|
| `file_count` | int | Number of files analyzed |
| `dependency_fail_list` | string[] | Files whose dependency extraction failed. Their outputs are removed and they are left out of the whole-project files. Other files' `callee_usages[].from` and `caller_usages[].file` can still name them |
| `doc_count` | int | Files with a complete design document (`0` with `ENABLE_LLM_DOC=False`) |
| `doc_fail_list` | string[] | Files left without a complete design document |
| `detected_encoding_dict` | {string: string} | Files decoded with a detected encoding, and that encoding (see `detected_encoding` in [docs/output-schema.md](docs/output-schema.md#file_dependenciesjson)) |

The checks listed under [Usage](#3-usage) raise `ValueError`. `LLMClient()` checks `LLM_MODEL`, `MAX_RETRIES` and `DOC_EFFORT`. `process_all_files()` checks the rest. It also raises when `ENABLE_LLM_DOC=True` and `llm_client` is `None`.

## ⚙️ Configuration Options

Set all options in `.env` or in the environment.

### LLM Settings

| Variable | Description | Default |
|----------|-------------|---------|
| `LLM_API_KEY` | API key of the LLM provider | Not set |
| `LLM_MODEL` | Model name in litellm format (required with `ENABLE_LLM_DOC=True`) | Not set |
| `LLM_API_BASE` | API base URL, for non-standard endpoints (Ollama, Azure, self-hosted) | Not set |
| `OUTPUT_LANGUAGE` | Language of the design documents | `English` |
| `DOC_MAX_TOKENS` | Token limit of one reply, thinking included. It is lowered to the model's output limit in litellm's model table. A reply cut at the limit is saved as it is, with a warning. A reply cut before any text counts as a failed call. For a model the table does not know (e.g. one served by vLLM), the limit is halved when the server refuses a call for the context window, with a warning. It is not halved below `4096` | `16384` |
| `DOC_EFFORT` | How much the model thinks while writing. `off`: no thinking (`low` on a model that always thinks or refuses `off`). `low` / `medium` / `high`: adaptive thinking at that effort. If a model refuses these parameters, it is called without them from then on, with a warning. Some providers' litellm translations (e.g. Gemini, Magistral) drop or rewrite them without a warning | `low` |

Ollama does not refuse a prompt longer than its context (`num_ctx`). It drops the start of the prompt instead. Set `OLLAMA_CONTEXT_LENGTH` on the server, or `PARAMETER num_ctx` in the Modelfile, large enough for the prompt plus `DOC_MAX_TOKENS`.

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
| `KNOWLEDGE_FORMAT` | Form of the knowledge file: `json` (`project_knowledge.json`), `sqlite` (`project_knowledge.sqlite`) or `both`. The per-file JSON files are always written. A knowledge file of the form not selected is removed | `json` |

### Analysis Settings

| Variable | Description | Default |
|----------|-------------|---------|
| `ENABLE_LLM_DOC` | Write design documents (`True` / `False`) | `True` |
| `SUMMARY_MAX_CHARS` | Characters the LLM is asked to keep a file summary within. A longer summary is kept | `600` |
| `ENABLE_CODE_SUMMARY` | Summarize large code with the LLM when a prompt exceeds the context window (`True` / `False`). With `False`, the prompt is only shortened by dropping context | `True` |
| `CODE_SUMMARY_TRIGGER_LINES` | Line count above which a definition or dependency symbol is summarized. A line over 80 characters counts as one line per 80 characters | `40` |
| `CODE_SUMMARY_PIECE_LINES` | Lines summarized together when a file still exceeds the context window after its large definitions are summarized (1 or more) | `200` |
| `CODE_SUMMARY_MAX_CHARS` | Characters the LLM is asked to keep one code summary within. A longer summary is kept | `400` |
| `EXCLUDE_PATTERNS` | Directory and file name patterns to exclude (comma-separated, fnmatch). They are added to `__pycache__`, `.git`, `.github`, `.venv` and `node_modules`, which are always excluded. Every text file not excluded is copied to the output, so add secrets, data files and lock files here | Not set |
| `SOURCE_ENCODING` | Encodings to try, in order, on a file without a BOM, or with a UTF-8 BOM but not valid UTF-8 (comma-separated Python codec names, e.g. `cp1252`). The first one that decodes the file is used, so list narrower encodings first | Not set |

## 🔄 Processing Flow

1. **Build the dependency graph**: collect every non-empty text file. For each file with a language, link its imports and references to the project files they lead to.
2. **Extract each file's dependencies**: its definitions, the definitions it uses from other files (callees), and the uses of its definitions by other files (callers), with line numbers and source code.
3. **Write design documents** (files with a language only): one section at a time from the template, using the source code, the dependency information and the summaries of the files it depends on. Only the documents that need it are rewritten (see [Re-runs](#re-runs)). A prompt over the context window is retried with less context, or with summaries of its large code (`ENABLE_CODE_SUMMARY`).
4. **Save the outputs** to `<output directory>/<project name>/`.

Note: LLM calls happen only in step 3. Each code summary is one more call. If a provider silently cuts a long prompt, the document is written from the cut prompt.

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

`<filename>` is the file's path relative to the project root, with the last `.` of its name turned into `_`. For example, `codetwine/import_to_path.py` becomes `codetwine/import_to_path_py/`. `Makefile` and `.env` keep their names. File paths in the JSON outputs are the paths of these copies, with the project name in front (`<project name>/codetwine/import_to_path_py/import_to_path.py`); `file` in `doc.json` is the exception ([Output schema](docs/output-schema.md)).

The whole-project files list only the files analyzed in that run. The `<filename>/` directories of files not analyzed (removed, excluded, or not in `file_list`) are left as they are.

The fields of each file, the SQLite tables and the Python helpers that read the database are described in [docs/output-schema.md](docs/output-schema.md).

## 📝 Design Documents

### Re-runs

Dependencies are extracted again for every file on each run. A design document is rewritten only when:

- the file's content changed since its `doc.json` was written (`source_hash`), or a file it depends on changed, directly or through other files. A C/C++ header also counts as changed when the implementation file of the same name in its directory (`.cpp`, `.c`, `.cc`, `.cxx`) changed;
- the document has missing sections, has an empty summary while the template has a `summary_prompt`, no longer matches the template's sections, or its `doc.json` cannot be read.

A file whose content did not change keeps its document when a file it depended on is removed, or when a name in it now leads to another unchanged file.

### Template

`doc_template.json` sets the sections and the instructions given to the LLM. To use another file, set `DOC_TEMPLATE_PATH`.

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

Each section needs a unique, non-empty `id`, a non-empty `title` and a string `prompt`. `summary_prompt` is optional. Without it, no summary is written, and the documents of dependent files are written without this file's summary. With `ENABLE_LLM_DOC=True`, a template that is not valid JSON or does not have this form stops the run with a `ValueError` before any file is analyzed.

`examples/` holds two other templates: `doc_template_search.json` (one section written in the words a reader would search for) and `doc_template_python.json` (a fuller specification for Python).

### Editing doc.md

You can edit `output/<project name>/<filename>/doc.md`. On the next run, a `doc.md` newer than its `doc.json` is applied to `doc.json`.

- Keep the `# Section heading` lines. If one is missing, the sections next to it keep their `doc.json` text, and a warning names them. A line `# <title of the next section>` added inside a section, or `# Summary` in the last section, is taken as that heading.
- Save `doc.md` as UTF-8. Otherwise `doc.json` is left as it is, with a warning.
- When a document is rewritten, its `doc.md` is replaced and your edits are lost. A warning says so.

### Running without design documents

Set `ENABLE_LLM_DOC=False`. No API key or model is needed, and all other outputs are still written. A design document from an earlier run, with its `doc.md` edits, is kept and included as long as its file's content has not changed.

## ⚠️ Dependency Analysis Limitations

Dependencies come from static analysis of the source text, so some are missing or incomplete. The main cases are below. [docs/dependency-analysis-limitations.md](docs/dependency-analysis-limitations.md) lists them per language.

- **Names built at run time**: `importlib.import_module(name)`, `import(variable)`, `Class.forName("...")`, a COBOL `CALL` of a run-time name, R `get("x")`.
- **Method calls on values**: a method call is linked to a class only when the variable's class is visible. In Python and JavaScript/TypeScript, the variable must be assigned an instance, or annotated, in the same function (`e = Engine(); e.run()`). In Java, Kotlin, Rust and C/C++, it must be declared with the type (`User u; u.getName()`). Such calls are not linked in C# (except extension methods) or R. Function results, call chains and fields (`self.engine.run()`) are not followed.
- **Inherited members**: a member inherited from a class in another file is not linked by its name alone, or through `self` / `this` (except in C++ through `#include`).
- **Build configuration**: CMake / Makefile include paths, `PYTHONPATH`, `.csproj` references, `package.json` workspaces and `JCLLIB ORDER` are not read.
- **Preprocessors and generated code**: `#if` / `#ifdef` / `#[cfg]` are not evaluated; every branch is analyzed. Code produced by macros and source generators is not analyzed. A COBOL copybook is analyzed as its own file, not expanded into the files that copy it.
- **Case and namespaces**: SQL names are compared as written. C++ names are linked without their namespaces, so equal names in different namespaces are not told apart.
- **Unsupported syntax**: some Kotlin, SQL, C# and COBOL syntax is not parsed. Definitions after it can be missing (in Kotlin, up to the end of the file).
- **Detected encodings**: a file that neither UTF-8 nor `SOURCE_ENCODING` decodes is decoded with a detected encoding, which can be wrong. Set `SOURCE_ENCODING` for such files.
- **File paths**: a file whose path is not valid UTF-8 is not analyzed. A warning names it.

## 💡 Usage Example: RLM QA Agent

`examples/rlm_qa/` is a sample agent that answers questions about a knowledge file with dspy's RLM. By default it reads `examples/sample_output/`. That is the output of codetwine run on itself with `examples/doc_template_search.json`. It is a snapshot and is not kept in step with the source.

The sample also needs the [Deno](https://deno.land/) runtime and dspy (`uv sync --extra examples`).

```bash
uv run python examples/rlm_qa/rlm_qa_agent.py
```

- To use your own output, set `TARGET_KNOWLEDGE_PATH` in `rlm_qa_agent.py`. A path ending in `.sqlite` is read as `project_knowledge.sqlite`; any other path is read as `project_knowledge.json`.
- The agent uses the `LLM_MODEL`, `SUB_LLM_MODEL` and `LLM_API_BASE` constants at the top of `rlm_qa_agent.py`, not `.env`. It reads the API key from the `LLM_API_KEY` environment variable.

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
├── docs/                   # Output schema, dependency analysis limitations per language
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
