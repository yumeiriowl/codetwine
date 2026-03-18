# Design Document: examples/rlm_qa/rlm_qa_agent.py

## Overview & Purpose

# Overview & Purpose

## Purpose and Role

`rlm_qa_agent.py` is the main entry point and orchestration module for an interactive Q&A agent that answers questions about a project's design by reasoning over a `project_knowledge.json` file. It exists as a separate file to encapsulate the full lifecycle of the agent: configuration, initialization, LLM binding, prompt construction, and the interactive REPL loop.

Its specific responsibilities are:
- Loading and validating `project_knowledge.json` into shared state (`qa_tools` module variables)
- Dynamically constructing a DSPy `Signature` by embedding the actual doc section schema and output language into a prompt template at runtime
- Instantiating and configuring the `dspy.RLM` agent with a `PythonInterpreter` (Deno sandbox) and a fixed set of tools from `qa_tools`
- Providing a thin `ask()` wrapper over `dspy.RLM.__call__`
- Running the interactive question-answering loop in `main()`

## Public Interface

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `create_qa_agent(json_path)` | `json_path: str` | `dspy.RLM` | Loads project data, configures the LLM, builds the instruction prompt, and assembles the fully configured `dspy.RLM` agent |
| `ask(rlm, question)` | `rlm: dspy.RLM`, `question: str` | `str` | Invokes the RLM agent with the loaded project data and the user's question, returning the answer string |
| `main()` | — | `None` | Validates the JSON path, initializes the agent, and runs the interactive REPL loop |

Internal helpers (`_build_doc_schema`, `_load_project`, `_create_interpreter`) are module-private (underscore-prefixed) and are not part of the public interface.

## Design Decisions

**Dynamic prompt construction at initialization time:** Rather than using a static system prompt, `_build_doc_schema` inspects the actual `files[].doc.sections` array from the loaded JSON and builds a Markdown table of section IDs and titles. This table is injected into `INSTRUCTIONS_TEMPLATE` via string `.replace()` before the `dspy.Signature` is created, meaning the prompt always accurately reflects the real schema of the target project rather than a hardcoded assumption.

**Shared mutable state via the `qa_tools` module:** `project_data` and `base_dir` are stored directly as module-level variables on `qa_tools` rather than passed through closures or constructor arguments. This allows the tool functions registered with `dspy.RLM` to access project state without requiring the RLM framework to pass extra context into each tool call.

**Deno sandbox for code execution:** `_create_interpreter` constructs a `PythonInterpreter` with an explicit `deno_command` that restricts filesystem access to only the runner script and the Deno cache directory (`--allow-read=<runner_path>,<deno_dir>`, `--node-modules-dir=false`). This is a deliberate security boundary: the LLM-generated Python code runs inside the Deno/Pyodide sandbox with minimal host filesystem permissions.

**Explicit interpreter shutdown in `main()`:** The `finally` block in `main()` calls `rlm._interpreter.shutdown()` to ensure the Deno subprocess is terminated cleanly even if the loop exits via `KeyboardInterrupt`.

## Definition Design Specifications

# Definition Design Specifications

## Module-level Variables

**`LLM_MODEL`** (`str`)
Default LLM model identifier in litellm format. Serves as the single configuration point for switching providers without modifying call sites.

**`LLM_API_KEY`** (`str`)
API key resolved from the `LLM_API_KEY` environment variable at module load time. Defaults to an empty string if the variable is absent.

**`OUTPUT_LANGUAGE`** (`str`)
Controls the natural language used in generated answers. Injected into the prompt template at agent construction time rather than at query time, meaning a single agent instance is bound to one language.

**`TARGET_JSON_PATH`** (`str`)
Default path to `project_knowledge.json`, resolved relative to this file's directory. Allows the script to be invoked from any working directory.

**`INSTRUCTIONS_TEMPLATE`** (`str`)
Prompt template containing placeholder tokens `<<<DOC_SCHEMA>>>` and `<<<RLM_OUTPUT_LANGUAGE>>>` that are substituted at construction time. Keeping the template as a module-level constant separates prompt authoring from agent wiring logic.

---

## `_build_doc_schema(project_data: dict) -> str`

**Arguments:**
- `project_data` (`dict`): The fully loaded `project_knowledge.json` structure.

**Returns:** A Markdown string containing a table of `doc.sections` identifiers and titles, prefixed with a heading line.

**Responsibility:** Extracts the concrete section schema from the actual loaded data so the LLM prompt reflects the real document structure rather than a hardcoded or generic description. This makes the instructions self-consistent with the data being queried.

**Design decisions:** Only the first file entry that contains a non-empty `sections` list is used; the section schema is assumed to be uniform across all files, so inspecting a single representative entry is sufficient.

**Edge cases:** If no file contains a non-empty `sections` list, `sections` remains an empty list and the returned table body is empty but structurally valid.

---

## `_load_project(json_path: str) -> None`

**Arguments:**
- `json_path` (`str`): Filesystem path to the `project_knowledge.json` file to load.

**Returns:** `None`. Side effect: populates `qa_tools.project_data` and `qa_tools.base_dir`.

**Responsibility:** Centralizes JSON loading and the population of shared `qa_tools` module-level state so that tool functions defined in `qa_tools` automatically gain access to the loaded data without requiring it to be threaded through every call.

**Design decisions:** `qa_tools.base_dir` is set to the directory containing the JSON file rather than the file path itself, because tool functions that resolve source file paths need a directory root, not a file reference.

**Edge cases:** Raises `FileNotFoundError` or `json.JSONDecodeError` if the path does not exist or the file is not valid JSON; no explicit error handling is performed here, leaving it to the caller.

---

## `_create_interpreter() -> PythonInterpreter`

**Arguments:** None.

**Returns:** A `PythonInterpreter` instance configured with a Deno command that restricts filesystem read access to the runner script and the Deno cache directory.

**Responsibility:** Encapsulates the non-trivial construction of a security-constrained Deno-backed interpreter so that `create_qa_agent` remains readable and the Deno configuration details are isolated in one place.

**Design decisions:**
- The `--node-modules-dir=false` flag prevents Deno from resolving npm packages, keeping the sandbox minimal.
- `--allow-read` is explicitly scoped to only the runner script path and the Deno cache directory, rather than granting broad read access.
- The Deno cache directory is resolved via `deno info --json` at runtime so the path remains correct across different installation layouts; `~/.cache/deno` is used only as a final fallback.
- `DENO_DIR` is written back into the environment so that the Deno subprocess inherits the resolved value even if it was not originally set.

**Edge cases:** If the `deno` binary is not on `PATH`, `subprocess.run` raises `FileNotFoundError`, which is caught and silently ignored, falling back to the hardcoded path. The `--allow-read` scope is determined at construction time, so if the runner script moves after the interpreter is created the restriction will reference a stale path.

---

## `create_qa_agent(json_path: str) -> dspy.RLM`

**Arguments:**
- `json_path` (`str`): Path to the `project_knowledge.json` file the agent will query.

**Returns:** A fully configured `dspy.RLM` instance ready to answer questions about the project.

**Responsibility:** Composes all agent dependencies—data loading, LLM initialization, prompt construction, interpreter creation, and tool registration—into a single factory function, providing a clean entry point for callers that need an agent without understanding internal wiring.

**Design decisions:**
- `dspy.configure(lm=lm)` sets the LLM globally as a side effect. This is intentional given that a single-agent use case does not require per-instance LLM configuration.
- The three registered tools (`read_source_file`, `get_files_using`, `graph_search`) are sourced from `qa_tools`, keeping tool implementations decoupled from agent construction.
- `max_iterations=10` caps the ReAct loop to prevent runaway execution on queries that produce ambiguous intermediate results.

**Edge cases:** Propagates any errors from `_load_project` (missing or malformed JSON) and from `dspy.LM` construction (invalid model name or missing API key) without wrapping them.

---

## `ask(rlm: dspy.RLM, question: str) -> str`

**Arguments:**
- `rlm` (`dspy.RLM`): A configured agent instance as returned by `create_qa_agent`.
- `question` (`str`): The natural language question to answer.

**Returns:** The answer string extracted from the `answer` field of the RLM result.

**Responsibility:** Provides a minimal, typed call site that hides the dspy invocation convention (keyword arguments matching the signature fields) and extracts the single relevant output field, so callers interact with plain strings rather than dspy result objects.

**Edge cases:** If the RLM produces no `answer` field, attribute access will raise `AttributeError`. Empty or whitespace-only questions are passed through without validation; filtering is the caller's responsibility.

---

## `main() -> None`

**Arguments:** None.

**Returns:** `None`.

**Responsibility:** Implements the interactive REPL entry point: validates that the target JSON exists, initializes the agent, and runs a question-answer loop until the user exits.

**Design decisions:**
- The agent's `_interpreter` is explicitly shut down in a `finally` block to ensure the Deno subprocess is terminated even if the loop exits via exception or `KeyboardInterrupt`.
- `KeyboardInterrupt` at the input prompt breaks the loop cleanly rather than propagating as an unhandled exception, but `KeyboardInterrupt` raised during `ask` will bypass the input handler and be caught by the outer `try/finally`, still triggering interpreter shutdown.
- Exit keywords (`exit`, `quit`, `q`) are checked case-insensitively; blank input is silently skipped.

**Edge cases:** If `json_path` does not exist, the function prints an error and calls `sys.exit(1)` before any agent initialization occurs. If `rlm._interpreter` is `None` at shutdown time, the shutdown step is skipped without error.

## Dependency Description

# Dependency Description

## Dependencies (what this file uses)

- **`qa_tools`** — A project-internal module that serves as the central data store and tool provider for the agent. This file relies on `qa_tools` for three distinct purposes:
  - **Shared state (`qa_tools.project_data`, `qa_tools.base_dir`)** — After loading `project_knowledge.json`, the parsed data and its base directory path are written into `qa_tools` module-level variables so they are accessible both within this file and inside the sandboxed Python interpreter at runtime.
  - **Tool functions (`qa_tools.read_source_file`, `qa_tools.get_files_using`, `qa_tools.graph_search`)** — These functions are registered as callable tools for the `dspy.RLM` agent, providing file reading and dependency graph traversal capabilities that the LLM can invoke during its reasoning loop.

## Dependents (what uses this file)

No dependent information available.

---

## Direction of Dependency

The dependency between this file and `qa_tools` is **unidirectional**: `rlm_qa_agent.py` depends on `qa_tools` (reads its tool functions and writes into its shared state), while `qa_tools` has no reference back to `rlm_qa_agent.py`.

## Data Flow

# Data Flow

## Overview

```
project_knowledge.json
        │
        ▼
  _load_project()
  ┌─────────────────────────────────────────┐
  │ qa_tools.project_data = parsed JSON     │
  │ qa_tools.base_dir = parent directory    │
  └─────────────────────────────────────────┘
        │
        ▼
  create_qa_agent()
  ┌─────────────────────────────────────────┐
  │ _build_doc_schema()                     │
  │   project_data → section table text     │
  │                                         │
  │ INSTRUCTIONS_TEMPLATE                   │
  │   .replace(<<<DOC_SCHEMA>>>)            │
  │   .replace(<<<RLM_OUTPUT_LANGUAGE>>>)   │
  │   → instructions (str)                  │
  │                                         │
  │ dspy.Signature(fields, instructions)    │
  │ _create_interpreter() → PythonInterpreter│
  │                                         │
  │ → dspy.RLM instance                     │
  └─────────────────────────────────────────┘
        │
        ▼
     ask(rlm, question)
  ┌─────────────────────────────────────────┐
  │ rlm(project_data=..., question=...)     │
  │   LLM generates Python code             │
  │   PythonInterpreter (Deno/Pyodide)      │
  │   executes code against project_data    │
  │   code output fed back to LLM           │
  │   (up to max_iterations=10)             │
  │ → result.answer (str)                   │
  └─────────────────────────────────────────┘
        │
        ▼
  printed to stdout
```

## Input Data

| Source | Format | Description |
|---|---|---|
| `project_knowledge.json` | JSON file | Project structure data; loaded once at startup |
| stdin (`input()`) | string | User's natural language question in interactive loop |
| `INSTRUCTIONS_TEMPLATE` | string constant | Prompt template with `<<<DOC_SCHEMA>>>` and `<<<RLM_OUTPUT_LANGUAGE>>>` placeholders |

## Main Data Structures

### `qa_tools.project_data` (module-level global)

Holds the parsed JSON. Acts as a shared state passed into every `rlm()` call as the `project_data` argument.

```
project_data
├── project_name          (str)
├── project_dependencies  (array)
│   └── { file, summary, callers[], callees[] }
└── files                 (array)
    └── { file,
          file_dependencies: { definitions[], callee_usages[], caller_usages[] },
          doc: { summary, sections[{ id, title, content }] } }
```

### Instructions string (built at agent creation)

```
INSTRUCTIONS_TEMPLATE
  + doc section table  ← extracted from first file entry with sections
  + OUTPUT_LANGUAGE
  → instructions (str) embedded into dspy.Signature
```

### Deno command list (built in `_create_interpreter`)

| Step | Input | Output |
|---|---|---|
| Locate `runner.js` | `PythonInterpreter` module path | absolute path string |
| Locate Deno cache | `DENO_DIR` env / `deno info --json` stdout / fallback path | `deno_dir` string |
| Set env | `deno_dir` | `os.environ["DENO_DIR"]` mutated |
| Build command | runner path + deno_dir | `list[str]` passed to `PythonInterpreter` |

## Output Data

| Destination | Format | Description |
|---|---|---|
| `ask()` return value | `str` (`result.answer`) | Final natural language answer |
| stdout | printed string | Answer displayed to the user in the interactive loop |

## Data Flow Through the RLM Iteration Loop

```
question (str)
project_data (dict)
      │
      ▼
  dspy.RLM  ──→  LLM generates Python code snippet
                        │
                        ▼
              PythonInterpreter (Deno)
              executes snippet with project_data in scope
              can call: read_source_file, get_files_using, graph_search
                        │
                        ▼
              stdout of executed code (str)
                        │
                        └──→ fed back to LLM as observation
                              (repeated up to 10 iterations)
                                    │
                                    ▼
                              result.answer (str)
```

The LLM never receives the raw `project_data` JSON directly; instead it writes Python code that selectively extracts and `print()`s relevant portions, which are returned as text observations to guide the next iteration.

## Error Handling

# Error Handling

## Overall Strategy

This file adopts a **fail-fast** strategy for critical initialization failures (missing JSON file, missing API key environment variable) combined with **graceful degradation** for runtime and subprocess-level issues. The agent exits immediately when required preconditions cannot be met, but allows the interactive loop to continue after recoverable mid-session interruptions.

## Main Error Patterns and Handling Policies

| Error Type | Handling | Impact |
|---|---|---|
| Missing `project_knowledge.json` | Detected by explicit existence check before initialization; prints an instructive message and calls `sys.exit(1)` | Process terminates immediately; no partial state is created |
| Missing `LLM_API_KEY` environment variable | Silently falls back to an empty string via `os.environ.get` default | LLM initialization proceeds with an empty key; downstream API call failure is delegated to dspy/litellm |
| `deno info` subprocess failure | Return code checked; failure is silently ignored and a hardcoded default path is used as fallback | Deno directory resolution continues with a best-effort path |
| `deno` binary not found (`FileNotFoundError`) | Caught and suppressed during `deno info` discovery; the hardcoded default path is used | Deno directory falls back to `~/.cache/deno`; actual sandbox execution failure would surface later at runtime |
| `KeyboardInterrupt` during interactive loop | Caught at the outer loop boundary; breaks the loop cleanly | Interactive session ends without a traceback; `finally` block still executes |
| `KeyboardInterrupt` during a single question | Caught by the inner `try/except` block | Current question is abandoned; the loop itself exits via the outer handler |
| PythonInterpreter (Deno process) not shut down | `finally` block calls `shutdown()` unconditionally if the interpreter exists | Ensures the subprocess is released regardless of how the session ends |

## Design Considerations

- **Precondition checking is front-loaded**: The file path validation is performed once at startup in `main()`, so the agent never enters the interactive loop in a partially initialized state.
- **Environment variable fallback is intentionally silent**: The empty-string default for `LLM_API_KEY` shifts the responsibility for authentication errors to the LLM client layer, keeping the configuration logic simple.
- **Subprocess resilience is layered**: The Deno directory resolution attempts multiple strategies in sequence (environment variable → `deno info` → hardcoded default), ensuring that a single discovery method failing does not block interpreter creation.
- **Resource cleanup is unconditional**: Placing `interpreter.shutdown()` in a `finally` block decouples cleanup from the specific exit path, covering both normal termination and interrupt scenarios.

## Summary

`rlm_qa_agent.py` orchestrates an interactive Q&A agent over `project_knowledge.json`. It loads project data into `qa_tools` shared state, dynamically builds a DSPy `Signature` by injecting the actual doc section schema into `INSTRUCTIONS_TEMPLATE`, and assembles a `dspy.RLM` agent with a Deno-sandboxed `PythonInterpreter` and tools from `qa_tools`. Public interface: `create_qa_agent(json_path)` returns a configured `dspy.RLM`; `ask(rlm, question)` returns an answer string; `main()` runs the interactive REPL with clean interpreter shutdown. Core data: parsed JSON in `qa_tools.project_data`, prompt built from `INSTRUCTIONS_TEMPLATE` with schema and language substitutions.
