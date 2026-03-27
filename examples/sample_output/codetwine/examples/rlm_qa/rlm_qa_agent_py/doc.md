# Design Document: examples/rlm_qa/rlm_qa_agent.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Initializes and operates a `dspy.RLM`-based interactive Q&A agent that answers natural language questions about a project by programmatically manipulating `project_knowledge.json` inside a sandboxed Python interpreter (Deno/Pyodide).

## 2. When to Use This Module

- **Run as a standalone CLI** (`python rlm_qa_agent.py`): Launches an interactive REPL where a developer can type natural language questions and receive answers grounded in the project's `project_knowledge.json`.
- **Call `create_qa_agent(json_path)`** when embedding the Q&A agent into another script: Returns a configured `dspy.RLM` instance ready to answer questions about the project at `json_path`.
- **Call `ask(rlm, question)`** when you have an existing `dspy.RLM` instance and want to submit a single question programmatically: Returns the answer as a plain string.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `build_doc_schema` | `project_data` (dict) | `str` | Extracts the doc section list from loaded project data and returns a formatted Markdown table for embedding in agent instructions. |
| `load_project` | `json_path` (str) | `None` | Loads `project_knowledge.json` from disk and sets the `qa_tools.project_data` and `qa_tools.base_dir` module globals. |
| `create_interpreter` | — | `PythonInterpreter` | Constructs a `PythonInterpreter` configured with the correct Deno 2.x flags (`--node-modules-dir=false`, `--allow-read`). |
| `create_qa_agent` | `json_path` (str) | `dspy.RLM` | Loads the project, builds the agent instructions, and assembles the fully configured `dspy.RLM` instance with tools and LM bindings. |
| `ask` | `rlm` (dspy.RLM), `question` (str) | `str` | Submits a question to the RLM agent and returns the answer string. |
| `LLM_MODEL` | — | `str` | Constant: litellm-format model name for the primary LLM. |
| `SUB_LLM_MODEL` | — | `str` | Constant: litellm-format model name for the sub-LLM used inside the RLM sandbox. |
| `LLM_API_KEY` | — | `str` | Constant: API key read from the `LLM_API_KEY` environment variable. |
| `LLM_API_BASE` | — | `str \| None` | Constant: Optional API base URL for non-standard endpoints. |
| `OUTPUT_LANGUAGE` | — | `str` | Constant: Natural language in which the agent writes answers. |
| `TARGET_JSON_PATH` | — | `str` | Constant: Default filesystem path to `project_knowledge.json`. |

## 4. Design Decisions

- **Instructions built at agent-creation time via string replacement**: `INSTRUCTIONS_TEMPLATE` uses `<<<DOC_SCHEMA>>>` and `<<<RLM_OUTPUT_LANGUAGE>>>` as placeholder tokens, which are substituted with live data from `build_doc_schema()` and `OUTPUT_LANGUAGE` inside `create_qa_agent`. This allows the schema table to reflect the actual sections present in the loaded project without requiring a templating library.
- **Deno sandbox configured per-run**: `create_interpreter` introspects the installed `dspy` package to locate `runner.js` and dynamically resolves the Deno cache directory (via `deno info --json` or a fallback path), then constructs a least-privilege `--allow-read` flag scoped to only those two paths. This avoids hardcoding filesystem paths while keeping sandbox permissions minimal.
- **`qa_tools` globals set as a side effect of `load_project`**: Rather than passing `project_data` and `base_dir` as arguments to the tool functions, they are stored as module-level globals in `qa_tools`. This is required because `dspy.RLM` invokes the tool functions by reference inside the sandbox, where passing state through closures is not feasible.

## Definition Design Specifications

# Definition Design Specifications

---

## Module-Level Constants

| Name | Type | Value / Description |
|---|---|---|
| `LLM_MODEL` | `str` | litellm-format model identifier for the primary LLM used by `dspy.LM` |
| `SUB_LLM_MODEL` | `str` | litellm-format model identifier for the sub-LLM used inside the RLM sandbox |
| `LLM_API_KEY` | `str` | API key read from the `LLM_API_KEY` environment variable; empty string if unset |
| `LLM_API_BASE` | `str \| None` | Base URL override for non-standard LLM endpoints; `None` means use the provider default |
| `OUTPUT_LANGUAGE` | `str` | Natural language in which answers are written |
| `TARGET_JSON_PATH` | `str` | Absolute path to `project_knowledge.json`, anchored to the directory of this script |
| `INSTRUCTIONS_TEMPLATE` | `str` | Multi-line prompt template containing two placeholder tokens (`<<<DOC_SCHEMA>>>`, `<<<RLM_OUTPUT_LANGUAGE>>>`) that are replaced at agent creation time |

---

## Functions

---

### `build_doc_schema`

**Signature:** `build_doc_schema(project_data: dict) -> str`

**Responsibility:** Extracts the doc section list from a loaded `project_knowledge.json` dict and returns a formatted Markdown table suitable for embedding in the agent's instruction prompt, so the LLM knows which named sections exist in this particular project.

**When to use:** Called once during agent initialization, after `project_knowledge.json` has been loaded, to produce the `<<<DOC_SCHEMA>>>` substitution value.

**Design decisions:**
- Only the first file entry whose `doc.sections` is non-empty is used as the representative section list. The assumption is that all files share the same section schema.
- Output is a Markdown table with `id` and `title` columns; `content` is deliberately excluded to keep the prompt compact.

**Constraints & edge cases:**
- If no file has a non-empty `doc.sections`, the returned table body is empty (only the header row is present).
- `project_data` must already be loaded and have the standard `files[].doc.sections[].{id, title}` shape.

---

### `load_project`

**Signature:** `load_project(json_path: str) -> None`

**Responsibility:** Reads `project_knowledge.json` from disk and initializes the two module-level globals in `qa_tools` (`project_data`, `base_dir`) that all tool functions require before they can operate.

**When to use:** Called once at the start of agent creation, before any `qa_tools` function is invoked.

**Design decisions:**
- `qa_tools.base_dir` is set to the directory containing the JSON file, not the project root, so that `read_source_file` can locate copied source files relative to the JSON.
- Prints a confirmation line to stdout indicating the project name and the number of files loaded.

**Constraints & edge cases:**
- Raises a standard file I/O exception if `json_path` does not exist or is not valid JSON.
- Mutates `qa_tools` module globals as a side effect; not thread-safe.

---

### `create_interpreter`

**Signature:** `create_interpreter() -> PythonInterpreter`

**Responsibility:** Constructs and returns a `PythonInterpreter` instance with a Deno 2.x-compatible command line that restricts filesystem access to only the runner script and the Deno cache directory.

**When to use:** Called once inside `create_qa_agent` to provide the sandboxed execution environment for RLM-generated Python code.

**Design decisions:**
- The Deno cache directory is resolved in priority order: `DENO_DIR` environment variable → `deno info --json` → `~/.cache/deno` fallback. This avoids hard-coding a path while remaining resilient when `deno` is not on `PATH`.
- `--node-modules-dir=false` is passed to suppress npm-style module resolution, which is unnecessary and can slow startup.
- `--allow-read` is scoped to exactly two paths (runner script and Deno cache), enforcing a minimal-permission sandbox.
- Sets `os.environ["DENO_DIR"]` as a side effect so the Deno subprocess inherits the resolved cache path.

**Constraints & edge cases:**
- If `deno` is not installed or not on `PATH`, the `deno info` lookup silently fails and the fallback path is used; the subsequent `PythonInterpreter` call will fail at runtime when code execution is attempted.
- The path to `runner.js` is resolved via `inspect.getfile(PythonInterpreter)`, so it depends on the installed layout of the `dspy` package.

---

### `create_qa_agent`

**Signature:** `create_qa_agent(json_path: str) -> dspy.RLM`

**Responsibility:** Orchestrates the full initialization sequence—loading project data, constructing the LLMs, building the instruction prompt, and assembling the `dspy.RLM` agent—and returns a ready-to-call agent instance.

**When to use:** Called once in `main` (or by a caller) to produce the agent before entering the interactive loop.

**Design decisions:**
- Template variable substitution (`<<<DOC_SCHEMA>>>`, `<<<RLM_OUTPUT_LANGUAGE>>>`) is done with plain `.replace()` calls rather than Python format strings to avoid conflicts with the curly-brace-heavy JSON schema examples embedded in the template.
- The `dspy.Signature` is built from the string `"project_data, question -> answer"` with the full instruction block attached, making the input/output contract explicit to the LLM.
- Three tools from `qa_tools` are registered: `read_source_file`, `get_files_using`, and `graph_search`. No other tools are exposed to the sandbox.
- `rlm.set_lm(lm)` sets the primary LLM at the module level on the RLM instance, while `sub_lm` is passed as a constructor argument for use inside the sandbox.

**Constraints & edge cases:**
- `LLM_API_KEY` must be a non-empty string for providers that require authentication.
- `load_project` is called as a side effect, mutating `qa_tools` globals; calling `create_qa_agent` more than once will overwrite those globals.

---

### `ask`

**Signature:** `ask(rlm: dspy.RLM, question: str) -> str`

**Responsibility:** Wraps a single invocation of the RLM agent, passing the loaded project data and a question, and returns only the `answer` field of the result.

**When to use:** Called once per user question inside the interactive loop in `main`.

**Constraints & edge cases:**
- `qa_tools.project_data` must have been populated by `load_project` before this is called, because it is passed directly as the `project_data` argument.
- Returns the raw string from `result.answer`; no post-processing or error handling is performed.

---

### `main`

**Signature:** `main() -> None`

**Responsibility:** Implements the interactive REPL: validates that `project_knowledge.json` exists, initializes the agent, and loops reading questions from stdin until the user exits.

**When to use:** Invoked when the script is executed directly (`if __name__ == "__main__"`).

**Design decisions:**
- `KeyboardInterrupt` (Ctrl-C) is caught at the outer loop level to allow graceful exit without a traceback.
- A `finally` block unconditionally calls `rlm._interpreter.shutdown()` to terminate the background Deno process, preventing resource leaks regardless of how the loop exits.
- Empty input (whitespace-only) is silently skipped rather than forwarded to the agent.

**Constraints & edge cases:**
- Exits with code 1 if `TARGET_JSON_PATH` does not exist.
- Accesses `rlm._interpreter` directly (a private attribute of `dspy.RLM`), which is a dependency on the internal API of the `dspy` library.

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

- **`rlm_qa_agent` → `examples/rlm_qa/qa_tools.py`** : Accesses the module-level globals `project_data` and `base_dir` to load and store the parsed `project_knowledge.json` data and its base directory path. Also registers three tool functions — `read_source_file`, `get_files_using`, and `graph_search` — as callable tools passed to the `dspy.RLM` agent, enabling the agent to perform source file reads and dependency graph traversals at query time.

  Symbols used:
  - `qa_tools.project_data` — read and written during project loading; passed as input to the RLM agent at query time
  - `qa_tools.base_dir` — written during project loading to resolve file paths
  - `qa_tools.read_source_file` — registered as an RLM tool for reading source files
  - `qa_tools.get_files_using` — registered as an RLM tool for finding dependents of a file
  - `qa_tools.graph_search` — registered as an RLM tool for BFS traversal of the dependency graph

## Dependents (modules that import this file)

No dependent information available.

## Dependency Direction

- **`rlm_qa_agent` → `examples/rlm_qa/qa_tools.py`** : Unidirectional. `rlm_qa_agent` imports and mutates `qa_tools`; `qa_tools` has no reference back to `rlm_qa_agent`.

## Data Flow

# Data Flow

## 1. Inputs

| Source | Format | Description |
|---|---|---|
| `TARGET_JSON_PATH` (config constant) | File path string | Resolved at module load time using `__file__` and `../sample_output/codetwine/project_knowledge.json` |
| `project_knowledge.json` | JSON file on disk | The entire project knowledge base read into memory as a Python dict |
| `LLM_MODEL`, `SUB_LLM_MODEL` | String constants | litellm-format model identifiers for the primary and sub LLMs |
| `LLM_API_KEY` | String from environment variable `LLM_API_KEY` | Authentication credential for the LLM provider |
| `LLM_API_BASE` | String constant (or `None`) | Optional non-standard API endpoint |
| `OUTPUT_LANGUAGE` | String constant | Natural language in which answers are rendered |
| `question` | String entered by the user via `input()` | The question to answer in each interactive loop iteration |

---

## 2. Transformation Overview

### Stage 1 — Project data loading (`load_project`)
`project_knowledge.json` is read from disk and deserialized into a Python dict. The result is stored in the module-level globals `qa_tools.project_data` and `qa_tools.base_dir`. From this point, all downstream stages read from `qa_tools.project_data` in memory rather than from disk.

### Stage 2 — Instruction assembly (`build_doc_schema` + template substitution)
`build_doc_schema` inspects `qa_tools.project_data["files"][0]["doc"]["sections"]` to produce a Markdown table of section IDs and titles. `INSTRUCTIONS_TEMPLATE` then has two placeholders replaced — `<<<DOC_SCHEMA>>>` with that table and `<<<RLM_OUTPUT_LANGUAGE>>>` with `OUTPUT_LANGUAGE` — yielding the final instructions string passed to `dspy.Signature`.

### Stage 3 — Agent construction (`create_qa_agent`)
Two `dspy.LM` instances (primary and sub) are created from the model/key/base config values. A `dspy.Signature` is created from the fixed field spec `"project_data, question -> answer"` and the assembled instructions. A `PythonInterpreter` is created with a Deno command that restricts filesystem access to the runner script and the Deno cache directory. All three components are assembled into a `dspy.RLM` instance that also receives the three `qa_tools` tool functions (`read_source_file`, `get_files_using`, `graph_search`). The primary LM is bound to the module via `rlm.set_lm(lm)`.

### Stage 4 — Interactive Q&A loop (`main` → `ask`)
Each user question string is passed to `rlm()` together with `qa_tools.project_data`. Inside the RLM agent, the primary LM generates Python code; that code is executed in the Deno/Pyodide sandbox by `PythonInterpreter`, which has access to `project_data` and the three tool functions. The sandbox may call `read_source_file`, `get_files_using`, or `graph_search` to retrieve additional data from disk or from the in-memory dict. The sub LM (`sub_lm`) is used for any `llm_query`/`llm_query_batched` calls made from within the sandbox. The result object's `.answer` field is extracted and printed to stdout.

### Stage 5 — Shutdown
When the interactive loop exits (user types `exit`/`quit`/`q` or sends `KeyboardInterrupt`), `rlm._interpreter.shutdown()` is called to terminate the Deno subprocess.

---

## 3. Outputs

| Output | Format | Description |
|---|---|---|
| Printed project load status | String to stdout | `"[OK] Loaded N files from project 'X'"` emitted by `load_project` |
| Printed answer | String to stdout | The `.answer` field of the `dspy.RLM` result, one per question |
| `qa_tools.project_data` | Python dict (global side effect) | The deserialized JSON set on the `qa_tools` module; consumed by all tool functions |
| `qa_tools.base_dir` | String (global side effect) | Directory of `project_knowledge.json`; used by `read_source_file` to resolve file paths |

No files are written by this module.

---

## 4. Key Data Structures

### `project_data` (top-level dict from `project_knowledge.json`)
| Field / Key | Type | Purpose |
|---|---|---|
| `project_name` | `str` | Name of the project |
| `project_dependencies` | `list[dict]` | Per-file dependency graph entries for entry-point discovery |
| `files` | `list[dict]` | Per-file detailed records containing dependencies and design docs |

### `project_dependencies[]` entry
| Field / Key | Type | Purpose |
|---|---|---|
| `file` | `str` | File path |
| `summary` | `str \| None` | File summary |
| `callers` | `list[str]` | Files that depend on this file |
| `callees` | `list[str]` | Files this file depends on |

### `files[]` entry
| Field / Key | Type | Purpose |
|---|---|---|
| `file` | `str` | File path |
| `file_dependencies` | `dict` | Definitions, callee usages, and caller usages |
| `doc` | `dict` | Design document with summary and sections |

### `file_dependencies` dict
| Field / Key | Type | Purpose |
|---|---|---|
| `definitions` | `list[dict]` | Functions/classes defined in this file |
| `callee_usages` | `list[dict]` | External symbols this file calls |
| `caller_usages` | `list[dict]` | Locations in other files that call into this file |

### `definitions[]` entry
| Field / Key | Type | Purpose |
|---|---|---|
| `name` | `str` | Symbol name |
| `type` | `str` | e.g. `"function_definition"`, `"class_definition"` |
| `start_line` | `int` | 1-indexed start line |
| `end_line` | `int` | 1-indexed end line |
| `context` | `str` | Full source code of the definition |

### `doc` dict
| Field / Key | Type | Purpose |
|---|---|---|
| `summary` | `str` | File-level summary text |
| `sections` | `list[dict]` | Design document sections; each has `id`, `title`, `content` |

### `dspy.RLM` call result (returned by `rlm()`)
| Field / Key | Type | Purpose |
|---|---|---|
| `.answer` | `str` | The natural-language answer extracted and printed to stdout |

## Error Handling

# Error Handling

## 1. Overall Strategy

The file adopts a **fail-fast** strategy for initialization-time errors (missing JSON file, missing environment variables) combined with **graceful degradation** for runtime errors during the interactive loop. Critical prerequisites are validated before agent construction and result in immediate process termination. Once the agent is running, user-facing errors during the Q&A loop are absorbed by catching `KeyboardInterrupt`, allowing the session to end cleanly rather than crash.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Missing `project_knowledge.json` | `TARGET_JSON_PATH` does not exist at startup | Prints error message and calls `sys.exit(1)` | No | Process terminates before agent is created |
| Missing `LLM_API_KEY` | `LLM_API_KEY` environment variable is not set | Defaults to empty string `""`; no explicit guard | No | Downstream LLM API calls will fail with authentication errors |
| Deno path/version detection failure | `deno info --json` subprocess returns non-zero or `deno` is not found | Falls back to hardcoded default path `~/.cache/deno`; `check=False` suppresses subprocess exception | Yes | Interpreter may fail later if default path is incorrect |
| `KeyboardInterrupt` in interactive loop | User presses Ctrl+C during question input or processing | Caught at the outer loop level; breaks the loop and proceeds to cleanup | Yes (session ends cleanly) | Interactive session terminates; cleanup still runs |
| Interpreter shutdown | Any exit path from the interactive loop (normal or exception) | `finally` block calls `rlm._interpreter.shutdown()` if interpreter is not `None` | N/A | Ensures Deno subprocess is terminated regardless of how the loop exits |

---

## 3. Design Notes

- **Startup validation is strict**: The absence of `project_knowledge.json` is treated as an unrecoverable precondition failure, reflecting that the agent has no meaningful state to operate in without it.
- **`LLM_API_KEY` is silently defaulted**: The empty-string fallback means no explicit error is raised at load time; the failure surface is deferred to the first LLM API call, which is consistent with delegating authentication handling to the underlying `dspy.LM` and litellm layers.
- **Deno detection is best-effort**: Using `check=False` and a hardcoded fallback path treats subprocess failure as non-fatal at configuration time, prioritizing interpreter creation success over strict environment validation.
- **`finally`-based resource cleanup** ensures the Deno child process is always terminated, preventing resource leaks regardless of whether the session ended normally, via user interrupt, or via an unhandled exception propagating out of the loop.

## Summary

**rlm_qa_agent.py** — Initializes and operates a DSPy RLM-based interactive Q&A agent that answers natural language questions about a project using `project_knowledge.json`.

**Public functions:** `build_doc_schema(project_data: dict) -> str`, `load_project(json_path: str)`, `create_interpreter() -> PythonInterpreter`, `create_qa_agent(json_path: str) -> dspy.RLM`, `ask(rlm: dspy.RLM, question: str) -> str`.

**Key data:** Consumes `project_knowledge.json` (dict with `project_name`, `files[]`, `project_dependencies[]`); produces a configured `dspy.RLM` instance and sets `qa_tools.project_data` (dict) and `qa_tools.base_dir` (str) globals.
