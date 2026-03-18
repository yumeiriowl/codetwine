# codetwine

A CLI tool that analyzes source code with tree-sitter, extracts key definitions and dependencies, generates design documents via LLM, and consolidates everything into a single unified JSON.
The unified JSON can be used as input material for LLM-powered code search and Q&A, such as RLM, GraphRAG, and agent search.

- Dependencies are extracted at the symbol level (functions, classes)
- Design documents are generated per file, taking dependencies into account
- Outputs dependency graphs in Mermaid format

## Table of Contents

- [codetwine](#codetwine)
  - [Table of Contents](#table-of-contents)
  - [Supported Languages](#supported-languages)
  - [Quick Start](#quick-start)
    - [Prerequisites](#prerequisites)
    - [1. Installation](#1-installation)
    - [2. Configuration](#2-configuration)
    - [3. Usage](#3-usage)
      - [Basic Execution](#basic-execution)
      - [Specifying Project and Output Directories](#specifying-project-and-output-directories)
  - [Configuration Options](#configuration-options)
    - [LLM Settings](#llm-settings)
    - [Path Settings](#path-settings)
    - [Performance Settings](#performance-settings)
    - [Analysis Settings](#analysis-settings)
  - [High-Level Processing Flow](#high-level-processing-flow)
  - [Dependency Analysis Limitations](#dependency-analysis-limitations)
    - [Common to All Languages](#common-to-all-languages)
    - [JavaScript / TypeScript](#javascript--typescript)
    - [Java / Kotlin](#java--kotlin)
    - [C / C++](#c--c)
  - [Output Files](#output-files)
  - [Incremental Processing](#incremental-processing)
  - [Output JSON Schema](#output-json-schema)
    - [project\_knowledge.json](#project_knowledgejson)
    - [project\_dependency\_summary.json](#project_dependency_summaryjson)
    - [file\_dependencies.json](#file_dependenciesjson)
    - [doc.json](#docjson)
  - [Customizing the Design Document Template](#customizing-the-design-document-template)
  - [Manual Editing of Design Documents](#manual-editing-of-design-documents)
  - [Running Without Design Document Generation](#running-without-design-document-generation)
  - [Usage Example: RLM QA Agent](#usage-example-rlm-qa-agent)
    - [Sample Output](#sample-output)
    - [Additional Prerequisites](#additional-prerequisites)
    - [How to Run](#how-to-run)
  - [Project Structure](#project-structure)
  - [Acknowledgments](#acknowledgments)
  - [License](#license)

## Supported Languages

- Python
- Java
- JavaScript
- TypeScript 
- C
- C++
- Kotlin

## Quick Start

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
| `--output-dir` | Output directory for analysis results | `DEFAULT_OUTPUT_DIR` from `.env` (defaults to `output/` if not set) |

## Configuration Options

The following options can be configured in the `.env` file.

### LLM Settings

| Variable | Description | Default |
|--------|------|------------|
| `LLM_API_KEY` | API key for the LLM provider | None |
| `LLM_MODEL` | Model name in litellm format (required when `ENABLE_LLM_DOC=True`) | None |
| `LLM_API_BASE` | API base URL (set when using non-standard endpoints, e.g. Ollama, Azure) | Not set |
| `OUTPUT_LANGUAGE` | Output language for design documents | `English` |
| `DOC_MAX_TOKENS` | Token limit for LLM output | `8192` |

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
| `MAX_RETRIES` | Number of retries for LLM API calls | `3` |
| `RETRY_WAIT` | Wait time in seconds between retries | `2` |

### Analysis Settings

| Variable | Description | Default |
|--------|------|------------|
| `ENABLE_LLM_DOC` | Enable/disable LLM design document generation (`True` / `False`) | `True` |
| `SUMMARY_MAX_CHARS` | Maximum character count for summaries | `600` |
| `EXCLUDE_PATTERNS` | Patterns to exclude during file traversal (comma-separated, fnmatch format) | `__pycache__,.git,.github,.venv,node_modules` |

## High-Level Processing Flow

1. **Build the project-wide dependency graph**
   - Traverses source files in the target directory, analyzes import statements, and identifies inter-file dependencies
2. **Detect changed files**
   - Compares source file hashes with the previous output to identify changed files for reprocessing
3. **Extract dependency information for each file**
   - Generates a syntax tree with tree-sitter and extracts definitions (functions, classes, etc.) at the symbol level
   - Based on the dependency graph built in step 1, extracts callee and caller symbol usage locations with their source code
   - Saves extraction results as `file_dependencies.json`
4. **Generate design documents via LLM**
   - Processes files starting from those with the deepest dependencies (topological sort). Design document summaries generated earlier are included as input for subsequent files, enabling dependency-aware document generation
   - Passes each file's source code, dependency information, and callee design document summaries to the LLM, generating content section by section according to the template (`doc_template.json`)
5. **Output consolidated JSON and Mermaid graph**
   - Consolidates all file dependencies and design documents into a single JSON
   - Outputs the dependency graph in Mermaid format as Markdown

Note: LLM API calls are only made in step 4. No LLM is used in other steps.

## Dependency Analysis Limitations

Dependency extraction is performed through static syntax analysis with tree-sitter, parsing import statements (including `#include`) in source code to identify inter-file dependencies. Dependencies may not be detected or may be incomplete in the following cases.

### Common to All Languages

- **Dynamic imports**: Patterns that construct module names as strings at runtime cannot be detected
  - Python: `importlib.import_module(name)`, `__import__(name)`
  - JavaScript/TypeScript: `import(variable)`
  - Java: `Class.forName("com.example.Foo")`

### JavaScript / TypeScript

- **Build tool path aliases**: Path aliases such as `@/`, `~/` defined in Webpack, Vite, tsconfig, etc. cannot be resolved. Imports using aliases are not recognized as project files and are missing from dependencies

### Java / Kotlin

- **Wildcard imports**: `import com.example.*` is detected as an import statement, but individual class files cannot be resolved, so they are not recognized as dependencies
- **Implicit same-package references**: In Java/Kotlin, classes in the same package can be referenced without imports. Detection uses regex matching with the assumption that file names match class names, so cases with multiple classes in one file may be missed

### C / C++

- **Build system include paths**: Include paths added via CMake or Makefile `-I` options are not considered. Headers that cannot be resolved from the project root or current directory as relative paths are not detected as dependencies

## Output Files

Running the tool generates the following files in `<output directory>/<project name>/` (default: `output/<project name>/`).

| File | Description |
|----------|------|
| `project_knowledge.json` | Consolidated JSON of all file dependencies and design documents |
| `project_dependency_summary.json` | Consolidated JSON of the dependency graph + per-file summaries |
| `dependency_graph.md` | Dependency graph in Mermaid format |
| `<filename>/file_dependencies.json` | Per-file definition and dependency information |
| `<filename>/doc.json` | Per-file design document (JSON format) |
| `<filename>/doc.md` | Per-file design document (Markdown format) |
| `<filename>/<original filename>` | Copy of the original source code |

## Incremental Processing

On subsequent runs, only the changed files and their affected scope have their design documents regenerated.

- **Change detection**: Compares SHA256 hashes of source files with the copies from the previous output to detect changed files
- **Dependency information**: Re-extracted for all files every run to ensure consistency
- **Design documents**: Only the changed files and files that depend on them (dependents) are regenerated; all others reuse previous results
- **Completeness check**: Even for unchanged files, if the existing `doc.json` has missing sections or an empty summary (e.g. due to a previous LLM API failure), it is treated as incomplete and regenerated

## Output JSON Schema

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
| `project_dependencies[].file` | string | File path |
| `project_dependencies[].summary` | string\|null | Summary of the file (null when design document is not generated) |
| `project_dependencies[].callers` | string[] | List of dependent file paths |
| `project_dependencies[].callees` | string[] | List of dependency file paths |
| `files[].file` | string | File path |
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
| `files[].file` | string | File path |
| `files[].summary` | string\|null | Summary of the file |
| `files[].callers` | string[] | List of dependent file paths |
| `files[].callees` | string[] | List of dependency file paths |

### file_dependencies.json

Per-file definition and dependency information.

```json
{
  "file": "string",
  "definitions": [
    {
      "name": "string",
      "type": "string",
      "start_line": 0,
      "end_line": 0,
      "context": "string"
    }
  ],
  "callee_usages": [
    {
      "name": "string",
      "from": "string",
      "target_context": "string",
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
| `file` | string | File path |
| `definitions[].name` | string | Function/class name |
| `definitions[].type` | string | Definition type (tree-sitter node type, varies by language. Python: `function_definition`, `class_definition` / Java: `class_declaration`, `method_declaration` / JS/TS: `function_declaration`, `class_declaration`, etc.) |
| `definitions[].start_line` | int | Start line number |
| `definitions[].end_line` | int | End line number |
| `definitions[].context` | string | Full source code of the definition |
| `callee_usages[].name` | string | Name of the used symbol |
| `callee_usages[].from` | string | Dependency file path |
| `callee_usages[].target_context` | string | Full source code of the dependency symbol |
| `callee_usages[].lines` | int[] | Line numbers of usage within this file |
| `caller_usages[].name` | string | Name of the symbol being used |
| `caller_usages[].file` | string | Dependent file path |
| `caller_usages[].usage_context` | string | Source code of the usage location in the dependent |
| `caller_usages[].lines` | int[] | Line numbers of usage in the dependent file |

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
  ]
}
```

| Field | Type | Description |
|-----------|-----|------|
| `file` | string | File path |
| `summary` | string | Summary of the file |
| `sections[].id` | string | Section identifier (corresponds to id in doc_template.json) |
| `sections[].title` | string | Section heading |
| `sections[].content` | string | Section body (Markdown format) |

## Customizing the Design Document Template

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

## Manual Editing of Design Documents

You can manually edit the output `doc.md` and have it automatically reflected in `doc.json` on the next run.

1. Edit `output/<project name>/<filename>/doc.md` with a text editor
2. On the next `uv run main.py` execution, if `doc.md` has a newer timestamp than `doc.json`, the Markdown section content is parsed and applied to `doc.json`

Notes for editing:

- Do not delete or rename `## Section heading` lines. The parser uses them as section delimiters, and without headings, parsing will not work correctly
- The body text below section headings can be freely edited

## Running Without Design Document Generation

To output only dependency information without generating LLM design documents, set `ENABLE_LLM_DOC=False` in `.env`.

```bash
ENABLE_LLM_DOC=False
```

The design document generation step is skipped. Dependency information (`file_dependencies.json` and source file copies) is still generated for each file, along with `project_knowledge.json`, `project_dependency_summary.json`, and `dependency_graph.md`. Since no LLM is used, it can run without API keys or model configuration.

## Usage Example: RLM QA Agent

`examples/rlm_qa/` contains a sample that performs interactive Q&A against `project_knowledge.json` as a usage example for the consolidated JSON. It uses dspy's RLM and PythonInterpreter to generate answers by manipulating the JSON with Python code.

### Sample Output

`examples/sample_output/` contains sample output produced by analyzing the codetwine repository itself. This output was generated using a Python-specific template (`examples/doc_template_python.json`) created for this sample. `rlm_qa_agent.py` references this output by default, so you can try out RLM QA immediately without running any analysis.

### Additional Prerequisites

- [Deno](https://deno.land/) runtime
- dspy package (install with `uv sync --extra examples`)

### How to Run

```bash
uv run python examples/rlm_qa/rlm_qa_agent.py
```

By default, the sample output in `examples/sample_output/` is used. To use your own project's output, edit `TARGET_JSON_PATH` in `rlm_qa_agent.py`.

## Project Structure

```
codetwine/
├── README.md
├── CHANGELOG.md                # Changelog
├── LICENSE                     # License (MIT)
├── pyproject.toml              # Package configuration and dependencies
├── main.py                     # CLI entry point
├── doc_template.json           # Design document section template definition
├── .env.example                # Environment variable template
├── codetwine/
│   ├── pipeline.py             # Main pipeline (dependency graph building → document generation → output)
│   ├── file_analyzer.py        # Per-file dependency analysis
│   ├── doc_creator.py          # Design document generation via LLM
│   ├── import_to_path.py       # Import statement to file path resolution
│   ├── output.py               # JSON and Mermaid output processing
│   ├── config/
│   │   ├── settings.py         # Environment variables and per-language settings management
│   │   └── logger.py           # Logging configuration
│   ├── extractors/
│   │   ├── definitions.py      # Definition extraction (functions, classes, etc.)
│   │   ├── imports.py          # Import statement extraction
│   │   ├── usages.py           # Symbol usage location extraction
│   │   ├── usage_analysis.py   # Usage location analysis
│   │   └── dependency_graph.py # Project-wide dependency graph construction
│   ├── parsers/
│   │   └── ts_parser.py        # Source code parser using tree-sitter
│   ├── llm/
│   │   └── client.py           # LLM API client via litellm
│   └── utils/
│       └── file_utils.py       # File operation utilities
└── examples/
    ├── doc_template_python.json  # Python-optimized design document template
    ├── rlm_qa/                   # RLM QA agent sample
    └── sample_output/            # Sample output (codetwine analyzed against itself)
```

## Acknowledgments

This project uses the following libraries:

- [tree-sitter](https://tree-sitter.github.io/tree-sitter/) - Source code syntax analysis
- [litellm](https://github.com/BerriAI/litellm) - Unified interface for multiple LLM providers

## License

MIT License. See [LICENSE](LICENSE) for details.
