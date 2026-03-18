# Design Document: examples/rlm_qa/rlm_qa_agent.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Instantiates and operates a `dspy.RLM`-based interactive Q&A agent that answers natural language questions about a project by executing Python code against a loaded `project_knowledge.json`.

## 2. When to Use This Module

- **Run as a script directly** (`python rlm_qa_agent.py`): Launches an interactive REPL where a developer types questions and receives answers derived from the project knowledge graph.
- **Call `create_qa_agent(json_path)`** to obtain a configured `dspy.RLM` instance: Use this when embedding the Q&A capability into another program, passing the path to `project_knowledge.json` to initialize the agent with the correct LLM, tools, and sandbox interpreter.
- **Call `ask(rlm, question)`** to submit a single question: Use this after obtaining an `rlm` instance from `create_qa_agent` to programmatically retrieve an answer string without managing the interactive loop.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `create_qa_agent` | `json_path: str` | `dspy.RLM` | Loads project data, configures the LLM and Deno-backed `PythonInterpreter`, builds the instruction prompt with the doc schema, and assembles the `dspy.RLM` agent with `read_source_file`, `get_files_using`, and `graph_search` as tools. |
| `ask` | `rlm: dspy.RLM`, `question: str` | `str` | Invokes the RLM agent with the loaded `project_data` and the given question, returning the answer string from the result. |
| `main` | *(none)* | `None` | Validates the target JSON path, initialises the agent via `create_qa_agent`, and runs the interactive question-answer loop until the user exits, shutting down the `PythonInterpreter` on exit. |
| `LLM_MODEL` | — | `str` | Constant specifying the litellm-format model name used by `dspy.LM`. |
| `OUTPUT_LANGUAGE` | — | `str` | Constant specifying the natural language in which answers are written. |
| `TARGET_JSON_PATH` | — | `str` | Constant specifying the default path to `project_knowledge.json`. |

## 4. Design Decisions

- **Instruction prompt built at runtime from actual data**: Rather than using a static prompt, `_build_doc_schema` inspects the first file entry in the loaded `project_knowledge.json` to extract the actual section list and embeds it into `INSTRUCTIONS_TEMPLATE` via string replacement. This ensures the schema description in the prompt always reflects the concrete document structure of the target project rather than a generic placeholder.
- **Deno sandbox with explicit permission flags**: `_create_interpreter` constructs a `deno run` command with `--node-modules-dir=false` and `--allow-read` restricted to only the runner script and the Deno cache directory, preventing the sandboxed code from reading arbitrary host files while still allowing the interpreter to function.
- **`qa_tools` module as shared mutable state**: `project_data` and `base_dir` are set as module-level variables on the imported `qa_tools` module rather than passed as parameters. This allows the tool functions (`read_source_file`, `get_files_using`, `graph_search`) registered with the RLM agent to access the loaded data without requiring the RLM framework to pass it explicitly on each tool call.

## Definition Design Specifications

# Definition Design Specifications

---

## Module-Level Constants

| Name | Type | Value / Purpose |
|---|---|---|
| `LLM_MODEL` | `str` | Default LLM model identifier in litellm format (`"anthropic/claude-sonnet-4-6"`). Modify to switch providers. |
| `LLM_API_KEY` | `str` | API key read from the `LLM_API_KEY` environment variable; empty string if unset. |
| `OUTPUT_LANGUAGE` | `str` | Natural language for agent-generated answers (`"English"`). Embedded into the instruction prompt. |
| `TARGET_JSON_PATH` | `str` | Absolute path to `project_knowledge.json`, resolved relative to this file's directory. |
| `INSTRUCTIONS_TEMPLATE` | `str` | Multi-line prompt template for the dspy `Signature`. Contains two placeholder tokens—`<<<DOC_SCHEMA>>>` and `<<<RLM_OUTPUT_LANGUAGE>>>`—that are replaced at agent creation time. Defines the agent's behavioral rules, the JSON schema it may navigate, and Python code examples. |

---

## Functions

### `_build_doc_schema(project_data: dict) -> str`

**Responsibility:** Extracts the actual `doc.sections` list from loaded project data and formats it as a Markdown table, producing a project-specific schema description to embed in the agent's instructions.

**When to use:** Called once during agent creation to customize the static instruction template with the concrete section IDs and titles present in the loaded `project_knowledge.json`.

**Design decisions:**
- Iterates `project_data["files"]` and stops at the first file entry that has a non-empty `sections` list. The assumption is that all files share the same section structure, so sampling the first available file is sufficient.
- Returns a fully formed Markdown table string (with header and separator rows) rather than a data structure, so the caller can perform a direct string replacement.

**Constraints & edge cases:**
- If no file has a non-empty `sections` list, `sections` remains an empty list and the returned table body is empty (header row only). No error is raised.
- Assumes all files have a uniform section schema; does not merge or deduplicate sections across files.

---

### `_load_project(json_path: str) -> None`

**Responsibility:** Reads `project_knowledge.json` from disk and writes the parsed data into the shared `qa_tools` module-level variables (`project_data` and `base_dir`), making the data available to all tool functions.

**When to use:** Called once at the start of `create_qa_agent` before any agent or tool is constructed.

**Design decisions:**
- Mutates `qa_tools.project_data` and `qa_tools.base_dir` directly rather than returning values, because the tool functions (`read_source_file`, `get_files_using`, `graph_search`) read those module-level variables at call time.
- `base_dir` is set to the directory containing the JSON file so that `read_source_file` can resolve relative source file paths.

**Constraints & edge cases:**
- Raises `FileNotFoundError` if `json_path` does not exist (no internal guard; the caller in `main` checks existence before calling).
- After this call, `qa_tools.project_data` and `qa_tools.base_dir` are global state; calling this function a second time with a different path overwrites the previous data.

---

### `_create_interpreter() -> PythonInterpreter`

**Responsibility:** Constructs and returns a `PythonInterpreter` instance that uses the Deno runtime with a restricted permission set, ensuring sandboxed execution of LLM-generated Python code.

**When to use:** Called once inside `create_qa_agent` to supply the RLM agent with its code execution backend.

**Design decisions:**
- Locates `runner.js` by inspecting the file path of the `PythonInterpreter` class itself, making the path resolution portable across installation layouts.
- Determines the Deno cache directory via a `deno info --json` subprocess call rather than hard-coding it, with a fallback to `~/.cache/deno`. This is necessary because `--allow-read` must explicitly include the Deno cache for Pyodide to function under Deno 2.x's stricter permission model.
- Sets `DENO_DIR` in the process environment as a side effect so that the spawned Deno process inherits the correct cache location.
- The `--allow-read` flag is scoped to exactly two paths (`runner_path` and `deno_dir`), limiting filesystem access from within the sandbox.

**Constraints & edge cases:**
- If `deno` is not on `PATH`, the `subprocess.run` call raises `FileNotFoundError`, which is caught; in that case the fallback path `~/.cache/deno` is used, and Deno invocation itself will fail later at runtime.
- `DENO_DIR` is written to the current process environment as a side effect.

---

### `create_qa_agent(json_path: str) -> dspy.RLM`

**Responsibility:** Orchestrates the full initialization sequence—loading project data, configuring the global dspy LM, building the instruction prompt, and assembling the RLM agent—returning a ready-to-use `dspy.RLM` instance.

**When to use:** Called once by `main` (or by external callers) to obtain an agent before entering the question-answering loop.

**Design decisions:**
- Calls `dspy.configure(lm=lm)` as a global side effect, which sets the default LM for all subsequent dspy operations in the process.
- The `dspy.Signature` is constructed with the string shorthand `"project_data, question -> answer"` and the fully resolved instruction string, keeping input/output field naming explicit.
- The RLM is given three tools from `qa_tools` (`read_source_file`, `get_files_using`, `graph_search`) and a maximum of 10 iterations.
- `verbose=True` is set on the RLM, causing intermediate steps to be printed to stdout during inference.

**Constraints & edge cases:**
- `LLM_API_KEY` is passed directly; if it is an empty string (environment variable not set), the LM constructor may raise an error depending on the provider.
- Calling this function more than once replaces the global dspy LM configuration and overwrites `qa_tools.project_data`/`qa_tools.base_dir`.

---

### `ask(rlm: dspy.RLM, question: str) -> str`

**Responsibility:** Invokes the RLM agent with the current project data and a user question, returning the plain-text answer string.

**When to use:** Called on each iteration of the interactive loop in `main`, or by external code that holds a `dspy.RLM` instance and wants a single answer.

**Constraints & edge cases:**
- Passes `qa_tools.project_data` at call time; if `_load_project` has not been called, `qa_tools.project_data` is `None` and the agent will receive `None` as input.
- Returns only `result.answer`; any other fields produced by the RLM (intermediate reasoning, tool outputs) are discarded.

---

### `main() -> None`

**Responsibility:** Entry point for interactive use; validates the JSON path, initializes the agent, and runs a REPL loop that reads user questions from stdin and prints answers.

**When to use:** Executed when the module is run directly (`__name__ == "__main__"`) or via `uv run python`.

**Design decisions:**
- Exits with `sys.exit(1)` if the target JSON file does not exist, providing a clear error before any LLM or Deno initialization occurs.
- The outer `try/finally` guarantees that `rlm._interpreter.shutdown()` is called even if the loop exits via `KeyboardInterrupt`, preventing orphaned Deno processes.
- An inner `try/except KeyboardInterrupt` handles Ctrl-C during `input()` cleanly, breaking the loop and allowing the `finally` block to run.

**Constraints & edge cases:**
- Accesses `rlm._interpreter` directly (a private attribute of `dspy.RLM`); this is a dependency on the internal API of the dspy library.
- Empty input lines are skipped silently (`continue`).
- The commands `"exit"`, `"quit"`, and `"q"` (case-insensitive) terminate the loop.

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

**`rlm_qa_agent.py` → `examples/rlm_qa/qa_tools.py`**

This file depends on `qa_tools` for all project knowledge data access and tool functions exposed to the RLM agent.

| Symbol | Purpose |
|---|---|
| `qa_tools.project_data` | Read/write module-level variable; populated by `_load_project()` and subsequently passed to the RLM agent as the `project_data` input field |
| `qa_tools.base_dir` | Write-only from this file; set in `_load_project()` to the directory containing `project_knowledge.json`, enabling `read_source_file` to resolve relative paths |
| `qa_tools.read_source_file` | Registered as a tool in the `dspy.RLM` instance, allowing the agent to read raw source files from the output directory |
| `qa_tools.get_files_using` | Registered as a tool in the `dspy.RLM` instance, allowing the agent to look up which files depend on a specified file |
| `qa_tools.graph_search` | Registered as a tool in the `dspy.RLM` instance, allowing the agent to perform BFS traversal of the dependency graph by definition name |

The relationship is a **data initialization + tool delegation** pattern: `rlm_qa_agent.py` owns the lifecycle (loading JSON, setting `qa_tools.project_data` and `qa_tools.base_dir`), then hands off the tool functions directly to the RLM agent without wrapping them.

---

## Dependents (modules that import this file)

No dependent information is available. `rlm_qa_agent.py` is an entry-point script (its `main()` function is invoked directly via `uv run python`), and no project-internal module is shown to import it.

---

## Dependency Direction

| Relationship | Direction |
|---|---|
| `rlm_qa_agent.py` → `examples/rlm_qa/qa_tools.py` | **Unidirectional** — `rlm_qa_agent.py` imports and mutates `qa_tools`; `qa_tools` has no reference back to `rlm_qa_agent.py` |

## Data Flow

# Data Flow

## 1. Inputs

| Source | Format | Description |
|--------|--------|-------------|
| `json_path` (argument / `TARGET_JSON_PATH`) | String (file path) | Path to `project_knowledge.json` on disk |
| `project_knowledge.json` | JSON file | Serialized project knowledge graph containing `project_name`, `project_dependencies`, and `files` arrays |
| `LLM_MODEL` | String constant | litellm-format model identifier (e.g. `"anthropic/claude-sonnet-4-6"`) |
| `LLM_API_KEY` | String from `os.environ` | API key for the LLM provider |
| `OUTPUT_LANGUAGE` | String constant | Natural language for generated answers (e.g. `"English"`) |
| `question` (stdin / argument) | Plain string | User question entered interactively or passed to `ask()` |
| `dspy.RLM` result | `dspy.Prediction` object | Structured result returned by the RLM agent after code execution |

---

## 2. Transformation Overview

```
project_knowledge.json
        │
        ▼
[Stage 1: Load & inject into qa_tools]
  json.load() → dict
  → qa_tools.project_data = dict
  → qa_tools.base_dir = directory of json_path

        │
        ▼
[Stage 2: Build dynamic instructions]
  _build_doc_schema(project_data)
  → extracts first file's doc.sections list
  → renders Markdown table of section ids/titles
  → INSTRUCTIONS_TEMPLATE
       .replace("<<<DOC_SCHEMA>>>", section_table)
       .replace("<<<RLM_OUTPUT_LANGUAGE>>>", OUTPUT_LANGUAGE)
  → instructions: str

        │
        ▼
[Stage 3: Assemble dspy components]
  dspy.LM(LLM_MODEL, api_key=LLM_API_KEY)
  → dspy.configure(lm=lm)
  dspy.Signature("project_data, question -> answer", instructions)
  _create_interpreter()
  → PythonInterpreter(deno_command=[...])
  dspy.RLM(signature, tools=[read_source_file, get_files_using, graph_search],
           interpreter=interpreter)
  → rlm: dspy.RLM

        │
        ▼
[Stage 4: Per-question inference]
  user question: str
  rlm(project_data=qa_tools.project_data, question=question)
  → LLM generates Python code snippets
  → PythonInterpreter (Deno/Pyodide sandbox) executes code
     against project_data, optionally calling tool functions
  → LLM iterates (up to max_iterations=10) until answer is produced
  → result: dspy.Prediction

        │
        ▼
[Stage 5: Answer extraction]
  result.answer → str
  → printed to stdout
```

The `_create_interpreter()` sub-pipeline discovers the Deno cache directory (via `deno info --json` subprocess or `DENO_DIR` env var / default path) and builds a `deno run` command with restricted `--allow-read` permissions before constructing `PythonInterpreter`.

---

## 3. Outputs

| Output | Format | Description |
|--------|--------|-------------|
| `qa_tools.project_data` | `dict` (side effect) | Module-level variable in `qa_tools` populated with the full JSON graph; persists for the process lifetime and is re-used on every `ask()` call |
| `qa_tools.base_dir` | `str` (side effect) | Directory path of `project_knowledge.json`; used by `read_source_file` to resolve relative paths |
| `rlm` (return value of `create_qa_agent`) | `dspy.RLM` instance | Configured agent ready to accept questions |
| `answer` (return value of `ask`) | `str` | Natural-language answer extracted from `result.answer` |
| stdout | Plain text | Agent answer printed per question; verbose RLM iteration output printed during inference |

---

## 4. Key Data Structures

### `project_data` (top-level dict loaded from JSON)

| Field / Key | Type | Purpose |
|---|---|---|
| `project_name` | `str` | Name of the analysed project |
| `project_dependencies` | `list[dict]` | File-level dependency graph nodes (callers/callees) |
| `files` | `list[dict]` | Per-file detailed records (dependencies + design docs) |

### `project_dependencies[]` entry

| Field / Key | Type | Purpose |
|---|---|---|
| `file` | `str` | File path |
| `summary` | `str \| null` | File summary |
| `callers` | `list[str]` | Files that depend on this file |
| `callees` | `list[str]` | Files this file depends on |

### `files[]` entry

| Field / Key | Type | Purpose |
|---|---|---|
| `file` | `str` | File path |
| `file_dependencies` | `dict` | Definitions, callee usages, caller usages |
| `doc` | `dict` | Design document (summary + sections) |

### `file_dependencies` dict

| Field / Key | Type | Purpose |
|---|---|---|
| `definitions` | `list[dict]` | Functions/classes defined in this file (name, type, start_line, end_line, context) |
| `callee_usages` | `list[dict]` | External symbols this file calls (lines, name, from, target_context) |
| `caller_usages` | `list[dict]` | Locations in other files that call into this file (lines, name, file, usage_context) |

### `doc` dict

| Field / Key | Type | Purpose |
|---|---|---|
| `summary` | `str` | Human-readable file summary |
| `sections` | `list[dict]` | Ordered design-doc sections, each with `id`, `title`, `content` |

### `deno_command` list (constructed inside `_create_interpreter`)

| Element | Type | Purpose |
|---|---|---|
| `"deno"` | `str` | Deno executable |
| `"run"` | `str` | Deno subcommand |
| `"--node-modules-dir=false"` | `str` | Disables npm node_modules directory |
| `f"--allow-read=..."` | `str` | Restricts filesystem read access to `runner.js` and the Deno cache |
| `runner_path` | `str` | Absolute path to `runner.js` (entry point for Pyodide sandbox) |

## Error Handling

# Error Handling

## 1. Overall Strategy

The file follows a **fail-fast with selective graceful degradation** strategy. Critical initialization failures (missing JSON file, environment misconfiguration) terminate the process immediately with an informative message. Runtime errors during interactive Q&A are absorbed at the tool layer (delegated to `qa_tools`), allowing the interactive session to continue. Resource cleanup is guaranteed via a `finally` block regardless of how the session ends.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Missing `project_knowledge.json` | `TARGET_JSON_PATH` does not exist at startup | Prints error message and calls `sys.exit(1)` | No | Process terminates before initialization |
| Missing `LLM_API_KEY` | `LLM_API_KEY` environment variable is not set | Falls back to empty string `""` silently | Yes (partially) | LLM call will likely fail downstream with an auth error |
| Deno binary not found | `deno` command is absent when running `subprocess.run(["deno", "info", "--json"])` | `FileNotFoundError` is caught; `deno_dir` falls back to `~/.cache/deno` | Yes | Deno may fail at execution time if the path is wrong |
| `deno info` non-zero exit | `deno info --json` returns a non-zero return code | Return code is checked; result is silently ignored, fallback path used | Yes | Same as above |
| Source file read failure | File path not found or unreadable inside `read_source_file` | Returns an error message string (handled in `qa_tools`) | Yes | Single tool call returns an error string; agent session continues |
| `KeyboardInterrupt` during interactive loop | User presses Ctrl+C during input or processing | Caught by the outer `try/except KeyboardInterrupt`; breaks the loop | Yes | Session ends cleanly; interpreter shutdown still executes |
| Interpreter not initialized | `rlm._interpreter` is `None` at shutdown | Guarded by `is not None` check before calling `shutdown()` | Yes | No crash; shutdown skipped safely |

---

## 3. Design Notes

- **Separation of error ownership**: Errors that occur within tool functions (`read_source_file`, `get_files_using`, `graph_search`) are handled entirely within `qa_tools`, returning error strings or structured error dicts rather than raising exceptions. This keeps `rlm_qa_agent.py` free of per-tool error logic and allows the RLM agent loop to continue across individual tool failures.
- **Silent fallback for Deno directory**: The Deno cache directory resolution degrades through three levels (environment variable → `deno info` → hardcoded default) without surfacing any warning to the user, prioritizing uninterrupted startup over strict correctness signaling.
- **No retry logic**: There is no retry or backoff mechanism at the agent level. If an LLM call or interpreter execution fails, the error propagates directly to the caller (`ask()`), meaning the interactive loop will surface it as an unhandled exception for that question without terminating the overall session only if the exception type is not `KeyboardInterrupt`.
- **Resource cleanup as a hard guarantee**: The `finally` block ensuring `interpreter.shutdown()` is unconditional with respect to normal exits and `KeyboardInterrupt`, preventing orphaned Deno processes, but it does not cover unhandled exceptions that escape the interactive loop entirely.

## Summary

**rlm_qa_agent.py** — Entry-point script that initializes and operates a `dspy.RLM` Q&A agent answering natural language questions about a project using `project_knowledge.json`.

Public API: `create_qa_agent(json_path: str) -> dspy.RLM`; `ask(rlm: dspy.RLM, question: str) -> str`; `main() -> None`.

Consumes `project_data` (`dict` with `project_name`, `files[]`, `project_dependencies[]`) loaded from JSON. Registers `qa_tools` functions (`read_source_file`, `get_files_using`, `graph_search`) as RLM tools. Sets `qa_tools.project_data` and `qa_tools.base_dir` as shared module-level state.
