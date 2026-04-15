# Design Document: examples/rlm_qa/rlm_qa_agent.py

# Overview & Purpose

## 1. Module Summary

Orchestrates an interactive, code-executing Q&A agent over a `project_knowledge.json` file by configuring and wiring together a `dspy.RLM` instance with a sandboxed Python interpreter and project-specific tools.

## 2. When to Use This Module

- **Run as a CLI entrypoint** (`uv run python examples/rlm_qa/rlm_qa_agent.py`): launches an interactive REPL where a developer types natural language questions and receives answers grounded in the loaded project knowledge JSON.
- **Call `create_qa_agent(json_path)`** when programmatically constructing a pre-configured `dspy.RLM` agent bound to a specific `project_knowledge.json`; returns a ready-to-use agent without needing to manage LM initialization, interpreter setup, or tool registration manually.
- **Call `ask(rlm, question)`** to submit a single natural language question to an already-created `dspy.RLM` agent and receive a plain string answer, suitable for integration into scripts or test harnesses.
- **Call `load_project(json_path)`** when only the side effect of populating `qa_tools.project_data` and `qa_tools.base_dir` is needed, independently of agent construction.
- **Call `create_interpreter()`** when a standalone `PythonInterpreter` with the correct Deno 2.x flags (`--node-modules-dir=false`, `--allow-read`) is needed outside the full agent setup.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `build_doc_schema` | `project_data: dict` | `str` | Extracts the doc section list from the first file entry in `project_data` and formats it as a Markdown table for embedding in the agent's instruction prompt. |
| `load_project` | `json_path: str` | `None` | Loads `project_knowledge.json` from disk and sets `qa_tools.project_data` and `qa_tools.base_dir` as module-level state. |
| `create_interpreter` | *(none)* | `PythonInterpreter` | Constructs a `PythonInterpreter` with Deno 2.x-compatible flags, resolving the Deno cache directory automatically via `deno info` or environment variable fallback. |
| `create_qa_agent` | `json_path: str` | `dspy.RLM` | Loads the project, builds the instruction prompt, and assembles a fully configured `dspy.RLM` agent with two LMs, a sandboxed interpreter, and the three `qa_tools` tool functions. |
| `ask` | `rlm: dspy.RLM`, `question: str` | `str` | Invokes the RLM agent with the loaded `project_data` and the given question, returning the `.answer` field from the result. |
| `main` | *(none)* | `None` | Entry point: validates the JSON path, initializes the agent, runs an interactive question loop, and shuts down the interpreter on exit. |

**Module-level constants:**

| Name | Type | Responsibility |
|---|---|---|
| `LLM_MODEL` | `str` | Primary LLM model name (litellm format) used by the top-level `dspy.LM`. |
| `SUB_LLM_MODEL` | `str` | Secondary LLM model name used inside the RLM sandbox for tool-calling steps. |
| `LLM_API_KEY` | `str` | API key read from the `LLM_API_KEY` environment variable. |
| `LLM_API_BASE` | `str \| None` | Optional base URL for non-standard LLM endpoints. |
| `OUTPUT_LANGUAGE` | `str` | Natural language in which the agent writes its answers. |
| `TARGET_JSON_PATH` | `str` | Default filesystem path to `project_knowledge.json`. |
| `INSTRUCTIONS_TEMPLATE` | `str` | System prompt template containing JSON schema documentation and code examples, with `<<<DOC_SCHEMA>>>` and `<<<RLM_OUTPUT_LANGUAGE>>>` placeholders replaced at agent creation time. |

## 4. Design Decisions

- **Two-LM split**: `create_qa_agent` wires a primary `lm` (set via `rlm.set_lm`) for top-level reasoning and a `sub_lm` passed directly to `dspy.RLM` for sandbox-internal tool invocations, allowing different models to be used for orchestration versus code generation without coupling either to the other.
- **Dynamic prompt construction via string replacement**: Rather than using a templating library, `INSTRUCTIONS_TEMPLATE` uses literal `<<<...>>>` placeholders replaced with `.replace()` at agent creation time; this keeps the template readable as plain text while allowing runtime values (`build_doc_schema` output and `OUTPUT_LANGUAGE`) to be injected.
- **Deno cache auto-resolution**: `create_interpreter` attempts to resolve the Deno cache directory by shelling out to `deno info --json` before falling back to `~/.cache/deno`, making the setup portable across environments without requiring manual configuration.
- **Side-effect-based state sharing**: `load_project` writes directly into `qa_tools.project_data` and `qa_tools.base_dir` (module-level variables), which the three tool functions read at call time; this avoids passing project data through the RLM call chain while keeping the tools independently usable.

# Definition Design Specifications

---

## Module-Level Constants

| Name | Type | Value / Purpose |
|---|---|---|
| `LLM_MODEL` | `str` | litellm-format model identifier for the primary DSPy LM (outer agent). |
| `SUB_LLM_MODEL` | `str` | litellm-format model identifier for the sub-LM used inside the RLM sandbox. |
| `LLM_API_KEY` | `str` | API key read from the `LLM_API_KEY` environment variable; defaults to empty string if unset. |
| `LLM_API_BASE` | `str \| None` | Optional custom API base URL; `None` means use the provider default. |
| `OUTPUT_LANGUAGE` | `str` | Natural language in which answers are written. |
| `TARGET_JSON_PATH` | `str` | Absolute path to `project_knowledge.json`, derived from this file's directory at import time. |
| `INSTRUCTIONS_TEMPLATE` | `str` | Prompt template string containing two placeholder tokens (`<<<DOC_SCHEMA>>>`, `<<<RLM_OUTPUT_LANGUAGE>>>`) that are substituted at agent construction time. |

---

## Functions

---

### `build_doc_schema`

**Signature:**
```python
def build_doc_schema(project_data: dict) -> str
```

**Responsibility:** Inspects the first file entry in `project_data` that has non-empty `doc.sections` and renders its section list as a Markdown table, producing a dynamic fragment to embed in the system prompt.

**When to use:** Called once during agent creation to inject project-specific doc section metadata into the instructions.

**Design decisions:**
- Uses the first file entry with sections rather than aggregating across all files, on the assumption that section schemas are uniform across the project.
- Output is a two-column Markdown table (`id` | `title`) prefixed with a descriptive header line.

**Constraints & edge cases:**
- If no file entry has non-empty sections, `sections` remains `[]` and the table body is empty (only the header row is emitted).
- Does not validate that every file uses the same section schema.

---

### `load_project`

**Signature:**
```python
def load_project(json_path: str) -> None
```

**Responsibility:** Reads `project_knowledge.json` from disk and populates the two module-level state variables in `qa_tools` (`project_data` and `base_dir`) that all tool functions depend on.

**When to use:** Called once before any tool or agent invocation to initialise the shared project state.

**Design decisions:**
- `qa_tools.base_dir` is set to the directory containing the JSON file, so that `read_source_file` can resolve relative paths against the same output directory.
- Prints a confirmation line to stdout including the project name and file count.

**Constraints & edge cases:**
- Assumes `json_path` points to a valid, UTF-8-encoded JSON file; no error handling for malformed JSON.
- Mutates global state in an external module (`qa_tools`), so concurrent calls are not safe.

---

### `create_interpreter`

**Signature:**
```python
def create_interpreter() -> PythonInterpreter
```

**Responsibility:** Constructs a `PythonInterpreter` whose underlying Deno command is restricted to the minimum read permissions required for the sandbox runner, preventing broad filesystem access.

**When to use:** Called once during agent creation to supply the sandboxed code-execution backend to `dspy.RLM`.

**Design decisions:**
- Resolves the `runner.js` path dynamically from `PythonInterpreter`'s own source location to stay robust against package installation layout changes.
- Locates the Deno cache directory by running `deno info --json`; falls back to `~/.cache/deno` if the subprocess call fails or `DENO_DIR` is not set.
- The `--allow-read` flag is scoped to exactly two paths (`runner.js` and the Deno cache directory) rather than granting broad read access.
- `--node-modules-dir=false` disables node_modules resolution, which is appropriate for a pure Pyodide/Deno sandbox.

**Constraints & edge cases:**
- Requires Deno to be installed and on `PATH`; if not found, `subprocess.run` raises `FileNotFoundError` (caught silently) and the fallback cache path is used.
- Sets `DENO_DIR` as a side effect on the current process's environment.

---

### `create_qa_agent`

**Signature:**
```python
def create_qa_agent(json_path: str) -> dspy.RLM
```

**Responsibility:** Assembles and returns a fully configured `dspy.RLM` agent by loading project data, constructing the prompt signature with substituted instructions, instantiating both LMs, and wiring in the tool set and interpreter.

**When to use:** Called once at application startup to produce the agent instance that will handle all subsequent questions.

**Design decisions:**
- The instructions string is built by string replacement on `INSTRUCTIONS_TEMPLATE` rather than a templating library, keeping the dependency surface small.
- Three tools from `qa_tools` are registered: `read_source_file`, `get_files_using`, and `graph_search`.
- The primary LM is set via `rlm.set_lm(lm)` after construction; the sub-LM is passed as a constructor argument, reflecting separate roles (outer reasoning vs. sandboxed code execution).

**Constraints & edge cases:**
- Depends on `load_project` being called internally; after this function returns, `qa_tools.project_data` and `qa_tools.base_dir` are populated.
- `LLM_API_KEY` must be non-empty for authenticated providers; an empty string may cause API errors at call time rather than here.
- `LLM_API_BASE` is passed directly to `dspy.LM`; `None` means use the provider default.

---

### `ask`

**Signature:**
```python
def ask(rlm: dspy.RLM, question: str) -> str
```

**Responsibility:** Invokes the RLM agent with the loaded project data and the user's question, and extracts the `answer` field from the result.

**When to use:** Called once per user question inside the interactive loop or from any caller that already holds an initialised `dspy.RLM` instance.

**Design decisions:**
- Passes `qa_tools.project_data` as the `project_data` input field rather than re-reading the file, relying on the already-loaded module state.
- Returns only the `answer` string, discarding any other output fields from the RLM result.

**Constraints & edge cases:**
- Assumes `qa_tools.project_data` has already been populated by `load_project`; calling before that will pass `None` as the input.
- No timeout or retry logic; long-running agent calls block the caller.

---

### `main`

**Signature:**
```python
def main() -> None
```

**Responsibility:** Entry point for the interactive command-line session; validates that the target JSON exists, initialises the agent, and runs a `question → answer` REPL until the user exits.

**When to use:** Invoked when the script is run directly (`__name__ == "__main__"`).

**Design decisions:**
- Exits with code 1 via `sys.exit` if the JSON file is missing, providing a clear actionable error message.
- The `finally` block unconditionally shuts down the `PythonInterpreter` (Deno process) via `rlm._interpreter.shutdown()` to avoid orphaned subprocesses, guarded by a `None` check.
- The inner `try/except KeyboardInterrupt` allows `Ctrl-C` mid-input to break out of the loop cleanly, while the outer `try/finally` ensures cleanup regardless of how the loop exits.
- Empty input lines are silently skipped; the exit keywords checked are `"exit"`, `"quit"`, and `"q"` (case-insensitive).

**Constraints & edge cases:**
- `rlm._interpreter` accesses a private attribute of `dspy.RLM`; this is fragile against DSPy internal API changes.
- The interactive loop is single-threaded; only one question is processed at a time.

# Dependency Description

## Dependencies (modules this file imports)

**`examples/rlm_qa/rlm_qa_agent.py` → `examples/rlm_qa/qa_tools.py`**

This file depends on `qa_tools` for two distinct purposes:

- **Shared state initialization** — imports `qa_tools.project_data` and `qa_tools.base_dir` (module-level variables) and assigns them directly after loading `project_knowledge.json`. This allows the tool functions in `qa_tools` to operate against the loaded data without requiring it to be passed as arguments.

- **Tool function registration** — imports `qa_tools.read_source_file`, `qa_tools.get_files_using`, and `qa_tools.graph_search` and passes them as the `tools` list to the `dspy.RLM` agent. These functions serve as the agent's callable tools for source file reading, dependent-file lookup, and BFS graph traversal over the project dependency graph respectively.

## Dependents (modules that import this file)

No dependent information available.

## Dependency Direction

- **`rlm_qa_agent.py` → `qa_tools.py`** : Unidirectional. `rlm_qa_agent.py` imports from and writes to `qa_tools.py` (setting module-level state), while `qa_tools.py` has no reference back to `rlm_qa_agent.py`.

# Data Flow

## 1. Inputs

| Source | Format | Description |
|--------|--------|-------------|
| `TARGET_JSON_PATH` (config constant) | File path string | Resolved path to `project_knowledge.json` relative to the script's directory |
| `project_knowledge.json` | JSON file on disk | Full project knowledge graph including files, dependencies, doc sections, and source code contexts |
| `LLM_MODEL`, `SUB_LLM_MODEL` | String constants | LiteLLM-format model identifiers for the primary and subordinate LLMs |
| `LLM_API_KEY` | Environment variable `LLM_API_KEY` | API key for the LLM provider |
| `LLM_API_BASE` | Config constant (or `None`) | Optional API base URL override |
| `OUTPUT_LANGUAGE` | Config constant string | Natural language for generated answers |
| `INSTRUCTIONS_TEMPLATE` | Multi-line string constant | Signature instruction template containing `<<<DOC_SCHEMA>>>` and `<<<RLM_OUTPUT_LANGUAGE>>>` placeholders |
| User input | String via `input()` | Interactive question typed at the terminal prompt |

---

## 2. Transformation Overview

### Stage 1: Project Data Loading
`load_project(json_path)` reads `project_knowledge.json` from disk and deserializes it into a Python dict, which is stored in `qa_tools.project_data`. The `qa_tools.base_dir` is set to the directory containing the JSON file, enabling subsequent file reads by `qa_tools.read_source_file`.

### Stage 2: Instruction Assembly
`build_doc_schema(qa_tools.project_data)` traverses the first file entry's `doc.sections` array to extract section IDs and titles, producing a Markdown table string. This table, along with `OUTPUT_LANGUAGE`, is substituted into `INSTRUCTIONS_TEMPLATE` via `.replace()` to produce the final `instructions` string. The result is used to construct a `dspy.Signature("project_data, question -> answer", instructions)`.

### Stage 3: Infrastructure Initialization
`create_interpreter()` locates the `runner.js` file from the `dspy` package internals and determines the Deno cache directory (via `deno info --json` or a default path). It constructs a Deno CLI command array and instantiates a `PythonInterpreter` configured to run code in an isolated Deno/Pyodide sandbox. Two `dspy.LM` instances (primary and sub-LLM) are created with the configured credentials.

### Stage 4: Agent Assembly
A `dspy.RLM` instance is assembled from the signature, three tool callables (`qa_tools.read_source_file`, `qa_tools.get_files_using`, `qa_tools.graph_search`), the `PythonInterpreter`, and the sub-LLM. The primary LLM is attached via `rlm.set_lm(lm)`.

### Stage 5: Interactive Question–Answer Loop
In `main()`, user input is read from stdin. Each non-empty, non-exit question is passed to `ask(rlm, question)`, which calls `rlm(project_data=qa_tools.project_data, question=question)`. Internally, `dspy.RLM` drives the primary LLM to generate Python code that manipulates `project_data` inside the Deno sandbox, optionally calling the registered tool functions. The agent iterates—generating code, executing it, inspecting output—until it can produce a final `answer` field. The answer string is extracted from `result.answer` and printed to stdout.

### Stage 6: Shutdown
On loop exit or `KeyboardInterrupt`, `rlm._interpreter.shutdown()` terminates the Deno subprocess if it is still running.

---

## 3. Outputs

| Output | Format | Description |
|--------|--------|-------------|
| `qa_tools.project_data` (side effect) | Python `dict` | Populated in-memory project knowledge graph; persists for the process lifetime |
| `qa_tools.base_dir` (side effect) | String | Directory path used by `read_source_file` to resolve relative file paths |
| `answer` string | Plain text printed to stdout | Natural-language answer to the user's question, written in `OUTPUT_LANGUAGE` |
| Deno subprocess lifecycle (side effect) | OS process | A Deno process is spawned by `PythonInterpreter` and shut down on exit |

---

## 4. Key Data Structures

### `project_data` (top-level dict from `project_knowledge.json`)

| Field / Key | Type | Purpose |
|-------------|------|---------|
| `project_name` | `str` | Name of the analyzed project |
| `project_dependencies` | `list[dict]` | File-level dependency graph (callers/callees per file) |
| `files` | `list[dict]` | Per-file detailed records (dependencies, doc, source) |

### `project_dependencies[]` entry

| Field / Key | Type | Purpose |
|-------------|------|---------|
| `file` | `str` | File path |
| `summary` | `str \| None` | Optional file summary |
| `callers` | `list[str]` | Files that depend on this file |
| `callees` | `list[str]` | Files this file depends on |

### `files[]` entry

| Field / Key | Type | Purpose |
|-------------|------|---------|
| `file` | `str` | File path |
| `file_dependencies` | `dict` | Contains `definitions`, `callee_usages`, `caller_usages` arrays |
| `doc` | `dict` | Contains `summary` string and `sections` array |

### `file_dependencies` sub-dict

| Field / Key | Type | Purpose |
|-------------|------|---------|
| `definitions` | `list[dict]` | Function/class definitions with name, type, line range, and source context |
| `callee_usages` | `list[dict]` | External symbols used by this file, with source file and full source |
| `caller_usages` | `list[dict]` | Locations in other files that use symbols from this file |

### `doc` sub-dict

| Field / Key | Type | Purpose |
|-------------|------|---------|
| `summary` | `str` | File-level summary text |
| `sections` | `list[dict]` | Design document sections, each with `id`, `title`, `content` |

### `deno_command` (list constructed in `create_interpreter`)

| Element | Type | Purpose |
|---------|------|---------|
| `"deno"` | `str` | Deno runtime executable |
| `"run"` | `str` | Deno subcommand |
| `"--node-modules-dir=false"` | `str` | Disables node_modules resolution |
| `f"--allow-read=..."` | `str` | Grants read access only to `runner.js` and the Deno cache directory |
| `runner_path` | `str` | Absolute path to `runner.js` from the `dspy` package |

### `result` (return value of `rlm(...)`)

| Field / Key | Type | Purpose |
|-------------|------|---------|
| `answer` | `str` | The final natural-language answer extracted and printed to the user |

# Error Handling

## 1. Overall Strategy

The file adopts a **fail-fast** strategy for critical initialization failures combined with **best-effort / silent-failure** for non-critical runtime operations. Fatal conditions (missing JSON file, missing environment variable) terminate the process immediately with an error message. Non-critical failures (Deno path detection, file reads inside the sandbox) return error strings or fall back to defaults rather than raising exceptions, allowing the agent loop to continue.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Missing `project_knowledge.json` | `TARGET_JSON_PATH` does not exist at startup | Prints error message and calls `sys.exit(1)` | No | Process terminates |
| Missing `LLM_API_KEY` | `LLM_API_KEY` environment variable is not set | Defaults to empty string `""` silently | Yes (if provider accepts it) | LLM calls will likely fail at runtime |
| Deno binary not found | `deno info --json` subprocess raises `FileNotFoundError` | Caught silently; falls back to `~/.cache/deno` as the default `DENO_DIR` | Yes | Deno may fail to run if the fallback path is wrong |
| Deno info command failure | `deno info --json` returns non-zero exit code | `deno_dir` remains `None`; falls back to `~/.cache/deno` | Yes | Same impact as above |
| Source file read failure inside sandbox | `read_source_file` called with an invalid or inaccessible path | Returns an error string (defined in `qa_tools.py`) | Yes | Agent receives error text instead of file content |
| `KeyboardInterrupt` during interactive loop | User presses Ctrl+C | Caught by outer `try/except`; breaks the loop | Yes | Session ends; interpreter shutdown still executes |
| Interpreter shutdown | Normal or interrupted exit from the interactive loop | `finally` block calls `rlm._interpreter.shutdown()` if interpreter is not `None` | N/A | Ensures Deno subprocess is cleaned up |

---

## 3. Design Notes

- **Startup validation is strict**: The absence of `project_knowledge.json` is treated as an unrecoverable precondition failure, reflecting that the agent has no meaningful fallback without its data source.
- **Environment variable defaults are lenient**: `LLM_API_KEY` silently defaults to an empty string rather than failing at startup, deferring the failure to the actual LLM call. This is a pragmatic trade-off for developer convenience but offers no early warning.
- **Subprocess errors are absorbed**: The Deno path detection treats both `FileNotFoundError` and non-zero return codes the same way—silent fallback—keeping interpreter creation non-fatal even in uncertain environments.
- **Guaranteed cleanup via `finally`**: The Deno subprocess is always shut down via the `finally` block, regardless of whether the session ended normally or via `KeyboardInterrupt`, preventing zombie processes.
- **Sandbox errors surface as data**: File read errors within the agent's sandbox are returned as human-readable error strings rather than raised exceptions, consistent with the agent's iterative code-execution model where the LLM can observe and react to failure output.

# Summary

**rlm_qa_agent.py** — Orchestrates an interactive Q&A agent over `project_knowledge.json` using `dspy.RLM`.

**Public functions:**
- `build_doc_schema(project_data: dict) → str`
- `load_project(json_path: str) → None`
- `create_interpreter() → PythonInterpreter`
- `create_qa_agent(json_path: str) → dspy.RLM`
- `ask(rlm: dspy.RLM, question: str) → str`
- `main() → None`

**Key data:** consumes `project_knowledge.json` (dict with `files`, `project_dependencies`); populates `qa_tools.project_data` (dict) and `qa_tools.base_dir` (str); registers tools `read_source_file`, `get_files_using`, `graph_search`.
