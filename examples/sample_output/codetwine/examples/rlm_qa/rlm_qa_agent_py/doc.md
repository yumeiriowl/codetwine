# Design Document: examples/rlm_qa/rlm_qa_agent.py

## Overview & Purpose

# Overview & Purpose

## Purpose and Role

`rlm_qa_agent.py` is the main entry point and orchestration module for an interactive Q&A agent that answers questions about a project's design documents. It exists as a separate file to isolate the agent assembly, configuration, and execution loop concerns from the tool implementations (`qa_tools`) and the underlying DSPy primitives.

Concretely, this module is responsible for:

- Loading `project_knowledge.json` into the shared `qa_tools` module state
- Constructing the natural-language instruction prompt dynamically from the loaded data
- Instantiating and wiring together the `dspy.LM`, `dspy.Signature`, `PythonInterpreter` (Deno/Pyodide sandbox), and `dspy.RLM` components into a ready-to-use agent
- Providing a thin `ask()` wrapper and a `main()` interactive REPL loop for end-user interaction

---

## Public Interface

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `create_qa_agent` | `json_path: str` | `dspy.RLM` | Loads project data, configures the LLM and DSPy defaults, builds the instruction prompt, and assembles the fully wired `dspy.RLM` agent instance |
| `ask` | `rlm: dspy.RLM`, `question: str` | `str` | Invokes the RLM agent with the loaded project data and question, returning the plain answer string |
| `main` | *(none)* | `None` | Validates the JSON path, initializes the agent, runs the interactive question-answer REPL, and shuts down the interpreter on exit |

Internal helpers (prefixed with `_`) are not part of the public interface:

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `_build_doc_schema` | `project_data: dict` | `str` | Extracts the doc section list from the first file entry and formats it as a Markdown table for prompt injection |
| `_load_project` | `json_path: str` | `None` | Reads `project_knowledge.json` from disk and writes `project_data` and `base_dir` into the `qa_tools` module-level variables |
| `_create_interpreter` | *(none)* | `PythonInterpreter` | Locates the DSPy `runner.js`, resolves the Deno cache directory, and constructs a `PythonInterpreter` with scoped `--allow-read` permissions |

---

## Design Decisions

**Dynamic prompt construction via string replacement.** The instruction prompt (`INSTRUCTIONS_TEMPLATE`) uses `<<<DOC_SCHEMA>>>` and `<<<RLM_OUTPUT_LANGUAGE>>>` as inline sentinels replaced at runtime by `str.replace()`. This keeps the template human-readable as a plain string constant while still embedding data-driven content (the actual section table) at agent creation time.

**Shared mutable state through the `qa_tools` module.** `project_data` and `base_dir` are stored as module-level variables on the imported `qa_tools` module rather than passed as arguments. This allows the `dspy.RLM` tool functions (registered by reference) to access project data without requiring the RLM framework to pass it through as parameters.

**Scoped Deno sandbox permissions.** `_create_interpreter` constructs the Deno command with `--allow-read` restricted to only the `runner.js` path and the resolved Deno cache directory, minimising the sandbox's filesystem access surface rather than using blanket `--allow-all`.

**Separation of agent creation from invocation.** `create_qa_agent` and `ask` are deliberately separated so callers can create the agent once and call `ask` multiple times, which matches the interactive REPL pattern in `main()` and avoids re-loading and re-initialising the LLM on every question.

## Definition Design Specifications

# Definition Design Specifications

## Module-Level Constants

**`LLM_MODEL`** (`str`)
Default LLM model identifier in litellm format. Serves as the single configuration point for switching LLM providers without touching logic code.

**`LLM_API_KEY`** (`str`)
API key resolved from the `LLM_API_KEY` environment variable at import time. Defaults to an empty string if the variable is absent, allowing the module to load without raising errors at configuration time.

**`OUTPUT_LANGUAGE`** (`str`)
Natural language specification injected into the prompt template. Centralizes language control so that changing it affects all agent responses uniformly.

**`TARGET_JSON_PATH`** (`str`)
Placeholder path to `project_knowledge.json`. Intentionally left as a sentinel value (`"/path/to/project_knowledge.json"`) that must be replaced by the operator before running; `main()` validates existence and exits early if the path is invalid.

**`INSTRUCTIONS_TEMPLATE`** (`str`)
Prompt template containing two substitution tokens (`<<<DOC_SCHEMA>>>`, `<<<RLM_OUTPUT_LANGUAGE>>>`) that are resolved at agent-creation time rather than at module load time. Keeping schema and language as late-bound variables prevents stale content when different JSON files are loaded in the same process.

---

## `_build_doc_schema(project_data: dict) -> str`

**Arguments**
- `project_data` (`dict`): The fully loaded `project_knowledge.json` structure.

**Returns** (`str`): A Markdown table listing the `id` and `title` of each section found in the first file entry that contains non-empty `doc.sections`, prefixed with a bold heading.

**Responsibility**
Dynamically generates the doc-section reference table that is embedded into the system prompt, so the LLM receives an accurate schema for whichever project is loaded rather than a hardcoded one.

**Design decision**
Only the first file with a non-empty `doc.sections` is used as the representative schema. This works because all files in a given `project_knowledge.json` share the same section structure; iterating every file would be redundant and slow.

**Edge cases / constraints**
- If no file contains `doc.sections`, `sections` remains an empty list and the returned table body is empty (the heading and header row are still emitted).
- Does not validate that all files share an identical section schema; assumes structural homogeneity enforced by the generator.

---

## `_load_project(json_path: str) -> None`

**Arguments**
- `json_path` (`str`): Filesystem path to `project_knowledge.json`.

**Returns**: `None` (side-effect only).

**Responsibility**
Centralizes project data loading and populates the shared `qa_tools` module-level variables (`project_data`, `base_dir`) that all tool functions depend on, keeping the coupling between agent setup and tool execution explicit and in one place.

**Design decision**
`base_dir` is derived from `json_path`'s directory rather than from a separate parameter, because source files referenced inside the JSON are expected to reside relative to that same directory.

**Edge cases / constraints**
- Raises `FileNotFoundError` or `json.JSONDecodeError` on missing or malformed input; no internal error handling is performed—callers are expected to validate the path first (as `main()` does).
- Mutates `qa_tools` module globals; calling this function twice overwrites the previously loaded project.

---

## `_create_interpreter() -> PythonInterpreter`

**Arguments**: None.

**Returns** (`PythonInterpreter`): An interpreter instance configured with a Deno command that restricts read access to the runner script and Deno cache only.

**Responsibility**
Constructs a sandboxed Python execution environment with the minimal Deno permissions required, isolating LLM-generated code from the host filesystem.

**Design decision**
`--node-modules-dir=false` and `--allow-read` scoped to `runner_path` and `deno_dir` are used instead of broad `--allow-all`, following a least-privilege principle for the sandbox. The Deno cache directory is resolved in priority order—`DENO_DIR` env var → `deno info --json` → `~/.cache/deno`—to support non-standard Deno installations without requiring manual configuration.

**Edge cases / constraints**
- If `deno` is not on `PATH`, `subprocess.run` raises `FileNotFoundError`; this is not caught here and will propagate to the caller.
- The `runner.js` path is resolved via `inspect.getfile(PythonInterpreter)`, which means it depends on `dspy`'s installed package layout and breaks if `dspy` is installed in an editable or non-standard form.
- Sets `os.environ["DENO_DIR"]` as a side effect, which affects any subsequent subprocess calls in the same process.

---

## `create_qa_agent(json_path: str) -> dspy.RLM`

**Arguments**
- `json_path` (`str`): Path to `project_knowledge.json`.

**Returns** (`dspy.RLM`): A fully configured reactive language model agent ready to accept `(project_data, question)` calls.

**Responsibility**
Acts as the factory and composition root for the Q&A agent, wiring together data loading, LLM configuration, prompt construction, sandboxed interpreter creation, and tool registration in one place.

**Design decision**
The `dspy.Signature` is built with the string `"project_data, question -> answer"`, which passes the entire `project_data` object as a direct input field. This makes the JSON available inside the interpreter's execution context by name, matching the variable name used in the code examples embedded in the prompt. `max_iterations=10` caps runaway tool-call loops while allowing enough steps for multi-hop reasoning over the dependency graph.

**Edge cases / constraints**
- Calls `dspy.configure(lm=lm)` as a global side effect; this affects any other dspy modules active in the same process.
- Does not validate `LLM_API_KEY`; an empty key will be forwarded to the LLM provider and cause an authentication error at first inference time.

---

## `ask(rlm: dspy.RLM, question: str) -> str`

**Arguments**
- `rlm` (`dspy.RLM`): A configured RLM agent instance, as returned by `create_qa_agent`.
- `question` (`str`): The user's natural language question.

**Returns** (`str`): The `answer` field from the RLM result object.

**Responsibility**
Provides a thin, stable calling interface that hides the `qa_tools.project_data` binding detail from callers, ensuring that `project_data` is always sourced from the module-level variable populated by `_load_project`.

**Edge cases / constraints**
- `qa_tools.project_data` must have been populated before calling this function; if `_load_project` was never called, behavior depends on `qa_tools`'s initial state.
- Empty or whitespace-only `question` strings are not validated here; the interactive loop in `main()` handles that guard.

---

## `main() -> None`

**Arguments**: None.

**Returns**: `None`.

**Responsibility**
Implements the interactive REPL entry point: validates prerequisites, initializes the agent, and drives a question-answer loop until the user exits or an interrupt is received.

**Design decision**
The interpreter shutdown (`rlm._interpreter.shutdown()`) is placed in a `finally` block to ensure the Deno subprocess is terminated even when the loop exits via `KeyboardInterrupt`, preventing zombie processes. The inner `try/except KeyboardInterrupt` breaks only the current iteration rather than bypassing `finally`, which is why the outer `try/finally` and inner `try/except` are nested separately.

**Edge cases / constraints**
- Exits with code `1` via `sys.exit(1)` if `TARGET_JSON_PATH` does not exist, rather than raising an exception, to provide a user-friendly error message.
- `_` accesses `rlm._interpreter` (a private attribute) directly; this is fragile against internal dspy API changes.
- Blank input lines are silently skipped; the exact strings `"exit"`, `"quit"`, and `"q"` (case-insensitive) trigger a clean exit.

## Dependency Description

# Dependency Description

## Dependencies (what this file uses)

- **`qa_tools`** (project-internal module): Used as the central data store and tool provider for the Q&A agent. This file reads and writes `qa_tools.project_data` (the loaded JSON data) and `qa_tools.base_dir` (the base directory path), and registers `qa_tools.read_source_file`, `qa_tools.get_files_using`, and `qa_tools.graph_search` as callable tools supplied to the `dspy.RLM` agent for code-driven knowledge exploration.

## Dependents (what uses this file)

No dependent information available.

---

**Direction of dependency**: Unidirectional — `rlm_qa_agent.py` depends on `qa_tools`, and there is no reverse dependency from `qa_tools` back to this file.

## Data Flow

# Data Flow

## Overall Data Flow

```
project_knowledge.json
        │
        ▼
  _load_project()
        │  json.load() → qa_tools.project_data (dict)
        │  os.path.dirname() → qa_tools.base_dir (str)
        │
        ▼
  create_qa_agent()
        │  _build_doc_schema(project_data) → doc schema text
        │  INSTRUCTIONS_TEMPLATE.replace() → instructions (str)
        │  dspy.Signature("project_data, question -> answer", instructions)
        │  _create_interpreter() → PythonInterpreter (Deno process)
        │
        ▼
     dspy.RLM
        │
        ▼
      ask()
   ┌────────────────────────────────────┐
   │  Input:  project_data (dict)       │
   │          question (str)            │
   │                ↓                  │
   │  RLM executes Python code via      │
   │  PythonInterpreter (Deno/Pyodide) │
   │  + calls qa_tools functions        │
   │                ↓                  │
   │  Output: result.answer (str)       │
   └────────────────────────────────────┘
```

## Input Data

| Source | Format | Description |
|---|---|---|
| `project_knowledge.json` (file) | JSON file | Loaded by `_load_project()`; stored into `qa_tools.project_data` |
| User stdin | `str` | Question entered interactively in `main()` loop |
| `LLM_API_KEY` env var | `str` | API key passed to `dspy.LM` |
| `DENO_DIR` env var | `str` | Deno cache directory path for `PythonInterpreter` |

## Key Data Structures

### `qa_tools.project_data` (dict) — central shared state

```
{
  "project_name": str,
  "project_dependencies": [
    {
      "file":    str,
      "summary": str | null,
      "callers": [str, ...],
      "callees": [str, ...]
    }, ...
  ],
  "files": [
    {
      "file": str,
      "file_dependencies": {
        "definitions": [
          { "name": str, "type": str, "start_line": int,
            "end_line": int, "context": str }
        ],
        "callee_usages": [
          { "lines": [int], "name": str, "from": str,
            "target_context": str }
        ],
        "caller_usages": [
          { "lines": [int], "name": str, "file": str,
            "usage_context": str }
        ]
      },
      "doc": {
        "summary": str,
        "sections": [
          { "id": str, "title": str, "content": str }
        ]
      }
    }, ...
  ]
}
```

This dict is passed directly as the `project_data` argument to `dspy.RLM` at query time and is also accessible to the registered `qa_tools` functions.

### Instructions string (built once at agent creation)

```
INSTRUCTIONS_TEMPLATE (str)
    │
    ├─ replace("<<<DOC_SCHEMA>>>")
    │      ← _build_doc_schema():
    │        scans project_data["files"][0]["doc"]["sections"]
    │        → Markdown table of { id, title }
    │
    └─ replace("<<<RLM_OUTPUT_LANGUAGE>>>")
           ← OUTPUT_LANGUAGE constant (str)
    │
    ▼
instructions (str)  →  dspy.Signature
```

### `dspy.RLM` agent configuration

| Field | Value / Type | Role |
|---|---|---|
| `signature` | `dspy.Signature` | Declares input/output ports and system instructions |
| `max_iterations` | `int` (10) | Maximum code execution rounds |
| `tools` | `[read_source_file, get_files_using, graph_search]` | Extra functions callable by the LLM |
| `interpreter` | `PythonInterpreter` | Deno subprocess that executes generated Python code |
| `verbose` | `bool` (True) | Enables execution trace output |

## Output Data

| Destination | Format | Description |
|---|---|---|
| `ask()` return value | `str` | `result.answer` extracted from `dspy.RLM` response |
| stdout | `str` | Answer printed in `main()` interactive loop |

## Error Handling

# Error Handling

## Overall Strategy

This file adopts a **fail-fast** strategy at startup and a **propagate-upward** approach during agent operation. Critical preconditions (file existence, environment configuration) are validated before the agent is initialized, and failures immediately terminate the process with an informative message. Runtime errors during LLM interaction and code execution are not caught locally and are allowed to propagate to the caller.

## Error Patterns and Handling Policies

| Error Type | Handling | Impact |
|---|---|---|
| `project_knowledge.json` not found at startup | Prints error message and calls `sys.exit(1)` | Process terminates immediately before any initialization |
| `LLM_API_KEY` not set in environment | Defaults to empty string `""` via `os.environ.get` | Agent is constructed but LLM calls are likely to fail at runtime |
| `deno info` command not found or fails | Caught via `FileNotFoundError` and non-zero return code check; falls back to a hardcoded default path `~/.cache/deno` | Interpreter construction continues with the fallback path |
| `KeyboardInterrupt` during interactive loop | Caught at the outer loop level; breaks the loop cleanly | Interactive session ends without a traceback |
| Deno/`PythonInterpreter` process cleanup | `interpreter.shutdown()` is called in a `finally` block | Ensures the subprocess is terminated even if the session ends abnormally |
| Runtime errors from LLM calls or tool execution | Not caught; propagate upward | Exception surfaces to the top level of the interactive loop or terminates the process |

## Design Considerations

- The fail-fast check on `json_path` is intentionally placed at the entry point of `main()`, concentrating precondition validation in one location rather than distributing it across helper functions.
- The `DENO_DIR` fallback chain (environment variable → `deno info` → hardcoded path) reflects a best-effort approach specifically for interpreter setup, which is an exception to the otherwise fail-fast posture.
- Resource cleanup for the Deno subprocess is handled unconditionally via `finally`, treating subprocess lifecycle as a critical concern separate from application-level error handling.
- The empty-string default for `LLM_API_KEY` means misconfiguration is not caught at startup; the error surface is deferred to the first actual LLM invocation.

## Summary

`rlm_qa_agent.py` orchestrates a DSPy-based Q&A agent over project design documents. It loads `project_knowledge.json` into shared `qa_tools` module state, builds a dynamic system prompt embedding a doc-section schema table, and wires together a `dspy.LM`, `dspy.Signature`, sandboxed `PythonInterpreter` (Deno), and `dspy.RLM` with three registered tools (`read_source_file`, `get_files_using`, `graph_search`). Public interface: `create_qa_agent(json_path)` returns a configured `dspy.RLM`; `ask(rlm, question)` returns the answer string; `main()` runs an interactive REPL with clean subprocess shutdown.
