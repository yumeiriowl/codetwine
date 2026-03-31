# Design Document: examples/rlm_qa/rlm_qa_agent.py

## Overview & Purpose

## 1. Module Summary

Initializes and operates an interactive Q&A agent that answers natural language questions about a parsed project knowledge base by combining `dspy.RLM` (a code-executing LLM agent) with a sandboxed Python interpreter (Deno/Pyodide) and project-specific tool functions.

## 2. When to Use This Module

- **Running an interactive Q&A session against a project**: Execute `main()` (or run the script directly) to launch a REPL loop where a developer enters natural language questions and receives answers derived from `project_knowledge.json`.
- **Programmatically asking a single question**: Call `create_qa_agent(json_path)` to obtain a configured `dspy.RLM` instance, then pass it to `ask(rlm, question)` to get a string answer. Use this when embedding Q&A capability into another script or test harness.
- **Reusing the agent across multiple questions**: Call `create_qa_agent(json_path)` once to pay the initialization cost, then call `ask(rlm, question)` repeatedly with the same `rlm` instance.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `build_doc_schema` | `project_data` (dict) | `str` | Extracts the `doc.sections` list from loaded project data and returns a formatted Markdown table for embedding in LLM instructions. |
| `load_project` | `json_path` (str) | `None` | Loads `project_knowledge.json` from the given path and sets `qa_tools.project_data` and `qa_tools.base_dir` for use by tool functions. |
| `create_interpreter` | — | `PythonInterpreter` | Constructs a `PythonInterpreter` configured with the Deno 2.x command and appropriate `--allow-read` permissions for the sandbox. |
| `create_qa_agent` | `json_path` (str) | `dspy.RLM` | Loads the project, configures the primary and sub-LLMs, builds the instruction prompt, and assembles a fully initialized `dspy.RLM` agent with the `qa_tools` tool set. |
| `ask` | `rlm` (dspy.RLM), `question` (str) | `str` | Invokes the RLM agent with the loaded project data and question, returning the answer string. |
| `main` | — | `None` | Entry point: validates the JSON path, initializes the agent, and runs an interactive question-answering loop until the user exits. |

### Configuration Constants

| Name | Type | Responsibility |
|---|---|---|
| `LLM_MODEL` | `str` | LiteLLM-format model name for the primary (outer) LLM. |
| `SUB_LLM_MODEL` | `str` | LiteLLM-format model name for the sub-LLM used inside the RLM sandbox. |
| `LLM_API_KEY` | `str` | API key read from the `LLM_API_KEY` environment variable. |
| `LLM_API_BASE` | `str \| None` | Optional custom API base URL (e.g., for Ollama or Azure endpoints). |
| `OUTPUT_LANGUAGE` | `str` | Natural language in which all answers are written. |
| `TARGET_JSON_PATH` | `str` | Default filesystem path to `project_knowledge.json`. |

## 4. Design Decisions

- **Instruction prompt built at runtime**: `INSTRUCTIONS_TEMPLATE` is a static string with `<<<DOC_SCHEMA>>>` and `<<<RLM_OUTPUT_LANGUAGE>>>` placeholders. These are resolved at agent creation time via `str.replace()` using `build_doc_schema()` output and `OUTPUT_LANGUAGE`, so the LLM receives a prompt tailored to the actual project's section structure rather than a generic schema.
- **Stateful tool module (`qa_tools`)**: Rather than passing `project_data` and `base_dir` as arguments to each tool call, the module sets them as globals on `qa_tools` in `load_project()`. This allows the tool functions (passed as bare callables to `dspy.RLM`) to access project state without requiring the RLM sandbox to manage it explicitly.
- **Deno permission scoping**: `create_interpreter()` restricts `--allow-read` to exactly two paths (the `runner.js` script and the resolved Deno cache directory), minimizing sandbox filesystem exposure rather than using a permissive `--allow-read` flag.
- **Explicit interpreter shutdown**: `main()` calls `rlm._interpreter.shutdown()` in a `finally` block to ensure the background Deno process is terminated even if the session ends via `KeyboardInterrupt`.

## Definition Design Specifications

---

## Module-Level Constants

| Name | Type | Value/Source | Purpose |
|---|---|---|---|
| `LLM_MODEL` | `str` | `"anthropic/claude-opus-4-6"` | Primary LLM model name in litellm format used by `dspy.LM` |
| `SUB_LLM_MODEL` | `str` | `"anthropic/claude-sonnet-4-6"` | Secondary LLM model name used within the RLM sandbox for `llm_query`/`llm_query_batched` |
| `LLM_API_KEY` | `str` | `os.environ.get("LLM_API_KEY", "")` | API key read from environment; empty string if unset |
| `LLM_API_BASE` | `None` | `None` | API base URL override; `None` means the provider default endpoint is used |
| `OUTPUT_LANGUAGE` | `str` | `"English"` | Natural language in which answers are generated |
| `TARGET_JSON_PATH` | `str` | Relative to `__file__` | Absolute path to `project_knowledge.json` resolved at import time |
| `INSTRUCTIONS_TEMPLATE` | `str` | Multi-line string literal | Signature instruction template containing placeholder tokens `<<<DOC_SCHEMA>>>` and `<<<RLM_OUTPUT_LANGUAGE>>>` that are substituted at agent creation time |

---

## Functions

---

### `build_doc_schema`

**Signature:**
```python
def build_doc_schema(project_data: dict) -> str
```

**Responsibility:**  
Extracts the doc section list from the first file entry that has sections in the loaded `project_knowledge.json` and formats it as a Markdown table for injection into the instructions template.

**When to use:**  
Called once during agent creation to produce project-specific schema documentation to embed in the LLM system prompt.

**Design decisions:**
- Takes the sections from the first file entry found to have a non-empty `sections` list, on the assumption that all files share the same section schema.
- Returns a fully formatted Markdown table string rather than a data structure, allowing direct string substitution into `INSTRUCTIONS_TEMPLATE`.

**Constraints & edge cases:**
- If no file entry contains a non-empty `sections` list, `sections` remains an empty list and the returned table body is empty.
- Does not validate that section entries contain both `id` and `title` keys.

---

### `load_project`

**Signature:**
```python
def load_project(json_path: str) -> None
```

**Responsibility:**  
Loads `project_knowledge.json` from disk and populates the shared module-level state variables `qa_tools.project_data` and `qa_tools.base_dir` required by all `qa_tools` tool functions.

**When to use:**  
Called once before any `qa_tools` functions are invoked; typically the first step inside `create_qa_agent`.

**Design decisions:**
- Sets `qa_tools.base_dir` to the directory containing the JSON file so that `qa_tools.read_source_file` can resolve relative source paths against the same output directory.
- Mutates external module state (`qa_tools`) directly rather than returning a value, keeping the loaded data centralized.

**Constraints & edge cases:**
- Raises `FileNotFoundError` if `json_path` does not exist.
- `qa_tools.read_source_file` will malfunction if called before `load_project` sets `qa_tools.base_dir`.

---

### `create_interpreter`

**Signature:**
```python
def create_interpreter() -> PythonInterpreter
```

**Responsibility:**  
Constructs a `PythonInterpreter` instance with a Deno 2.x-compatible command that restricts filesystem access to the runner script and the Deno cache directory.

**When to use:**  
Called once during agent creation to provide the sandboxed execution environment for RLM-generated Python code.

**Design decisions:**
- Locates `runner.js` dynamically via `inspect.getfile(PythonInterpreter)` to remain decoupled from installation paths.
- Resolves `DENO_DIR` through a priority chain: environment variable → `deno info --json` → hardcoded fallback `~/.cache/deno`. This ensures the allow-read permission covers the Deno module cache regardless of the installation.
- Uses `--node-modules-dir=false` and a narrowly scoped `--allow-read` flag to minimize sandbox surface area.
- Sets `os.environ["DENO_DIR"]` as a side effect so Deno itself can locate its cache at runtime.

**Constraints & edge cases:**
- If `deno` is not on `PATH` and `DENO_DIR` is not set, falls back to `~/.cache/deno`, which may be incorrect on some systems.
- The `subprocess.run` call for `deno info` uses `check=False`; a non-zero exit code silently triggers the fallback.

---

### `create_qa_agent`

**Signature:**
```python
def create_qa_agent(json_path: str) -> dspy.RLM
```

**Responsibility:**  
Orchestrates the full initialization sequence—loading project data, constructing both LLM instances, building the instruction string, and assembling a `dspy.RLM` agent with the qa_tools functions registered as tools.

**When to use:**  
Called once at application startup with the path to `project_knowledge.json` to obtain a ready-to-use Q&A agent.

**Design decisions:**
- The instruction string is built by string replacement on `INSTRUCTIONS_TEMPLATE` rather than a templating engine, keeping the dependency footprint minimal.
- Uses a two-model setup: `lm` (the primary model) is set on the RLM module via `set_lm`, while `sub_lm` (a lighter model) is passed to the RLM constructor for use within the sandbox.
- The `dspy.Signature` is constructed with the string shorthand `"project_data, question -> answer"` plus the instruction text.
- Registers exactly three tools: `read_source_file`, `get_files_using`, and `graph_search` from `qa_tools`.

**Constraints & edge cases:**
- Requires `LLM_API_KEY` to be non-empty; an empty string is passed to `dspy.LM` without validation.
- `load_project` is called as a side effect, mutating `qa_tools` module state.
- `LLM_API_BASE` being `None` is passed directly to `dspy.LM`; behavior depends on the dspy/litellm implementation for `None`.

---

### `ask`

**Signature:**
```python
def ask(rlm: dspy.RLM, question: str) -> str
```

**Responsibility:**  
Invokes the RLM agent with the loaded project data and a user question, returning the plain-text answer string.

**When to use:**  
Called each time the user submits a question in the interactive loop or in programmatic usage.

**Design decisions:**
- Reads `qa_tools.project_data` directly rather than accepting it as a parameter, relying on the module-level state set by `load_project`.
- Extracts only the `.answer` attribute from the RLM result, discarding any other fields in the prediction.

**Constraints & edge cases:**
- Requires `load_project` to have been called beforehand; otherwise `qa_tools.project_data` is `None`.
- No timeout or retry logic; long-running LLM calls block indefinitely.

---

### `main`

**Signature:**
```python
def main() -> None
```

**Responsibility:**  
Entry point that validates the JSON path, initializes the agent, and runs an interactive read-eval-print loop accepting questions from stdin.

**When to use:**  
Executed when the script is run directly (`__name__ == "__main__"`).

**Design decisions:**
- Catches `KeyboardInterrupt` inside the outer `try/finally` to ensure `rlm._interpreter.shutdown()` is always called, preventing orphaned Deno processes.
- Uses a nested `try/except KeyboardInterrupt` inside the loop so that Ctrl-C during `input()` breaks the loop cleanly rather than raising to the top level abruptly.
- Exits with `sys.exit(1)` if the JSON file is missing, providing an explicit non-zero status code.
- Commands `"exit"`, `"quit"`, and `"q"` (case-insensitive) all terminate the loop.

**Constraints & edge cases:**
- `rlm._interpreter` is accessed directly (private attribute); behavior depends on `dspy.RLM` internals.
- Shutdown is skipped if `rlm._interpreter` is `None`; no other cleanup is performed.
- Blank input lines are silently skipped without sending to the agent.

## Dependency Description

## Dependencies (modules this file imports)

**`rlm_qa_agent` → `examples/rlm_qa/qa_tools.py` : project knowledge state and tool functions**

This file depends on `qa_tools` for the following symbols:

- `qa_tools.project_data` — Reads and writes the module-level variable to store the loaded JSON data. `load_project()` assigns the parsed JSON content to this variable, and `ask()` passes it as the `project_data` argument to the RLM agent at invocation time.
- `qa_tools.base_dir` — Writes the module-level variable to record the base directory path of the loaded JSON file, enabling `read_source_file` within the sandbox to resolve relative file paths.
- `qa_tools.read_source_file` — Registered as a tool passed to `dspy.RLM`, allowing the agent to retrieve full source file content by path.
- `qa_tools.get_files_using` — Registered as a tool passed to `dspy.RLM`, allowing the agent to look up which files depend on a specified target file.
- `qa_tools.graph_search` — Registered as a tool passed to `dspy.RLM`, allowing the agent to perform BFS traversal over the dependency graph from a named definition.

The relationship is one of initialization and delegation: `rlm_qa_agent` sets the shared state (`project_data`, `base_dir`) on the `qa_tools` module and then hands the tool functions to the RLM agent to invoke during reasoning.

## Dependents (modules that import this file)

No dependent information is available.

## Dependency Direction

- **`rlm_qa_agent` → `qa_tools`**: Unidirectional. `rlm_qa_agent` imports and mutates state in `qa_tools`, and registers its functions as RLM tools. `qa_tools` has no reference back to `rlm_qa_agent`.

## Data Flow

## 1. Inputs

| Source | Format | Description |
|---|---|---|
| `TARGET_JSON_PATH` (config constant) | File path string | Resolved path to `project_knowledge.json`, derived from `__file__` at module load time |
| `project_knowledge.json` | JSON file on disk | Loaded via `json.load()`; becomes the central `project_data` dict shared with `qa_tools` module |
| `LLM_API_KEY` | Environment variable (`LLM_API_KEY`) | API key string for authenticating with the LLM provider |
| `LLM_MODEL`, `SUB_LLM_MODEL`, `LLM_API_BASE`, `OUTPUT_LANGUAGE` | Module-level constants | Configuration values controlling model selection, endpoint, and response language |
| `question` | String entered interactively via `input()` | Natural language question from the user at runtime |
| `INSTRUCTIONS_TEMPLATE` | Module-level string constant | Template text containing `<<<DOC_SCHEMA>>>` and `<<<RLM_OUTPUT_LANGUAGE>>>` placeholders |

---

## 2. Transformation Overview

```
project_knowledge.json
        │
        ▼
[load_project()]
  ├─ Parses JSON → dict
  ├─ Assigns to qa_tools.project_data (shared module state)
  └─ Assigns to qa_tools.base_dir (base directory for source file reads)
        │
        ▼
[build_doc_schema(project_data)]
  ├─ Extracts first non-empty doc.sections list from project_data["files"]
  └─ Renders a Markdown table of section ids/titles → doc_schema string
        │
        ▼
[INSTRUCTIONS_TEMPLATE.replace()]
  ├─ Substitutes <<<DOC_SCHEMA>>> with doc_schema string
  └─ Substitutes <<<RLM_OUTPUT_LANGUAGE>>> with OUTPUT_LANGUAGE constant
        └─ Produces: instructions string (final system prompt)
        │
        ▼
[dspy.Signature("project_data, question -> answer", instructions)]
  └─ Produces: typed signature object describing agent I/O and behavior
        │
        ▼
[create_interpreter()]
  ├─ Locates PythonInterpreter's runner.js path via inspect
  ├─ Resolves Deno cache directory (DENO_DIR env → deno info --json → ~/.cache/deno)
  └─ Constructs Deno subprocess command with restricted --allow-read permissions
        └─ Produces: PythonInterpreter instance (Deno/Pyodide sandbox)
        │
        ▼
[dspy.RLM(signature, tools=[...], sub_lm=sub_lm, interpreter=interpreter)]
  ├─ Binds qa_tools functions as callable tools inside the sandbox
  │   (read_source_file, get_files_using, graph_search)
  ├─ Binds sub_lm for LLM calls originating inside generated code
  └─ Produces: rlm agent instance, with lm set via rlm.set_lm(lm)

        ── Interactive loop ──

user input (question string)
        │
        ▼
[ask(rlm, question)]
  ├─ Calls rlm(project_data=qa_tools.project_data, question=question)
  ├─ RLM agent generates Python code, executes it inside PythonInterpreter sandbox
  ├─ Sandbox code may call qa_tools tools (read_source_file / get_files_using / graph_search)
  │   which access qa_tools.project_data and qa_tools.base_dir (shared module state)
  └─ LLM synthesizes final natural language answer from code execution results
        │
        ▼
result.answer (string) → printed to stdout
```

---

## 3. Outputs

| Output | Format | Description |
|---|---|---|
| `result.answer` | String printed to stdout | Natural language answer to the user's question, written in `OUTPUT_LANGUAGE` |
| `qa_tools.project_data` | Side effect — module-level dict | Set by `load_project()`; persists for the lifetime of the process and is read by all tool functions |
| `qa_tools.base_dir` | Side effect — module-level string | Set by `load_project()`; used by `read_source_file` to resolve file paths |
| `os.environ["DENO_DIR"]` | Side effect — environment variable | Written by `create_interpreter()` to configure Deno's cache directory |
| PythonInterpreter shutdown | Side effect — subprocess termination | `rlm._interpreter.shutdown()` is called in the `finally` block to cleanly terminate the Deno process |

---

## 4. Key Data Structures

### `project_data` (dict — top-level structure of `project_knowledge.json`)

| Field / Key | Type | Purpose |
|---|---|---|
| `project_name` | `str` | Name of the analyzed project |
| `project_dependencies` | `list[dict]` | Per-file dependency graph nodes (callers/callees) |
| `files` | `list[dict]` | Per-file detailed records containing dependencies and design docs |

### `project_dependencies[]` (dict)

| Field / Key | Type | Purpose |
|---|---|---|
| `file` | `str` | File path |
| `summary` | `str \| None` | Short summary of the file |
| `callers` | `list[str]` | Files that depend on this file |
| `callees` | `list[str]` | Files this file depends on |

### `files[]` (dict)

| Field / Key | Type | Purpose |
|---|---|---|
| `file` | `str` | File path |
| `file_dependencies` | `dict` | Definitions, callee usages, caller usages |
| `doc` | `dict` | Design document with summary and sections |

### `file_dependencies` (dict)

| Field / Key | Type | Purpose |
|---|---|---|
| `definitions` | `list[dict]` | Function/class definitions with name, type, line range, and full source context |
| `callee_usages` | `list[dict]` | Dependencies used by this file (with source context of the dependency) |
| `caller_usages` | `list[dict]` | Dependents using this file (with source context of usage sites) |

### `doc` (dict)

| Field / Key | Type | Purpose |
|---|---|---|
| `summary` | `str` | Human-readable summary of the file |
| `sections` | `list[dict]` | Design document sections, each with `id`, `title`, and `content` |

### Deno command list (constructed in `create_interpreter()`)

| Element | Type | Purpose |
|---|---|---|
| `"deno"`, `"run"` | `str` | Invokes the Deno runtime |
| `"--node-modules-dir=false"` | `str` | Disables automatic node_modules resolution |
| `f"--allow-read={runner_path},{deno_dir}"` | `str` | Restricts filesystem read access to runner.js and Deno cache only |
| `runner_path` | `str` | Path to `runner.js` inside the `dspy.primitives` package directory |

## Error Handling

## 1. Overall Strategy

The file adopts a **fail-fast on initialization, best-effort on runtime** strategy. Critical setup steps (missing JSON file, missing API key environment variable) terminate the process immediately with an informative message. Once the agent is running in the interactive loop, errors are surfaced to the user without crashing the process, allowing the session to continue. Cleanup of external resources (the Deno subprocess) is guaranteed via a `finally` block regardless of how the loop exits.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Missing `project_knowledge.json` | `TARGET_JSON_PATH` does not exist at startup | Prints error message and calls `sys.exit(1)` | No | Process terminates before initialization |
| Missing `LLM_API_KEY` | `LLM_API_KEY` environment variable is not set | Defaults to empty string `""`; no explicit abort | Yes (deferred) | Failure deferred to first LLM API call |
| `deno info` subprocess failure | Deno is not installed or returns non-zero exit code | `returncode` check suppresses the error; falls back to `~/.cache/deno` as default | Yes | Deno cache path may be incorrect, potentially causing sandbox startup failure |
| `FileNotFoundError` from `deno info` | `deno` binary is not on `PATH` | Caught silently; falls back to `~/.cache/deno` | Yes | Same as above |
| `KeyboardInterrupt` during interactive loop | User presses Ctrl+C | Caught at the outer `try/except` block; exits the loop cleanly | Yes (graceful exit) | Session ends; `finally` cleanup still executes |
| Deno/PythonInterpreter shutdown | Normal or abnormal exit from the interactive loop | `finally` block calls `rlm._interpreter.shutdown()` if interpreter is not `None` | N/A | Ensures the Deno subprocess is not left orphaned |
| File read error in `read_source_file` | Target source file cannot be opened | Returns an error string (handled inside `qa_tools`) | Yes | Agent receives an error message string instead of file content |

---

## 3. Design Notes

- **Deferred API key validation**: The empty-string default for `LLM_API_KEY` means a missing environment variable does not cause an immediate abort. The error is deferred to the point of the first actual LLM API call, which shifts the failure surface away from startup and into runtime, where the error message may be less obvious.
- **Silent Deno path fallback**: The failure to resolve the Deno cache directory via `deno info` is treated as non-fatal. The hardcoded fallback path (`~/.cache/deno`) is a best-effort assumption and may not be valid on all platforms or installations, but the design prioritizes continued execution over strict validation.
- **Resource cleanup as a first-class concern**: The explicit `finally` guard around `rlm._interpreter.shutdown()` reflects that the Deno process is an external resource requiring deterministic cleanup, independent of whether the session ended normally or via interrupt.
- **Error handling in tools is delegated**: File I/O errors within `read_source_file` are handled inside `qa_tools.py` and returned as string messages to the agent, keeping error handling for tool execution outside the scope of this file.

## Summary

**rlm_qa_agent.py** initializes and runs an interactive Q&A agent over a parsed project knowledge base.

**Public functions:** `build_doc_schema(project_data:dict)->str`, `load_project(json_path:str)->None`, `create_interpreter()->PythonInterpreter`, `create_qa_agent(json_path:str)->dspy.RLM`, `ask(rlm:dspy.RLM, question:str)->str`, `main()->None`.

**Key data:** consumes `project_knowledge.json` (dict with `files`, `project_dependencies` lists); sets `qa_tools.project_data` (dict) and `qa_tools.base_dir` (str); registers `read_source_file`, `get_files_using`, `graph_search` as RLM tools.
