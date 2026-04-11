# Design Document: examples/rlm_qa/rlm_qa_agent.py

# Overview & Purpose

## 1. Module Summary

Provides an interactive command-line Q&A agent that answers natural language questions about a loaded `project_knowledge.json` by orchestrating a `dspy.RLM` instance equipped with Python code execution and project-aware tool functions.

## 2. When to Use This Module

- **Run as a script** (`uv run python examples/rlm_qa/rlm_qa_agent.py`): Launches an interactive REPL where a developer types natural language questions and receives answers grounded in the project's source code and design documents.
- **Call `create_qa_agent(json_path)`**: When embedding the Q&A agent into another program, call this function to obtain a configured `dspy.RLM` instance ready to answer questions about the project at `json_path`.
- **Call `ask(rlm, question)`**: When programmatically submitting a single question to an already-created agent, call this function to receive the answer string without managing `dspy.RLM` internals directly.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `build_doc_schema` | `project_data` (dict) | `str` | Extracts the doc section list from loaded project data and returns a formatted Markdown table for embedding in agent instructions. |
| `load_project` | `json_path` (str) | `None` | Loads `project_knowledge.json` from disk and sets `qa_tools.project_data` and `qa_tools.base_dir` for use by tool functions. |
| `create_interpreter` | — | `PythonInterpreter` | Constructs a `PythonInterpreter` configured with the correct Deno 2.x flags (`--node-modules-dir=false`, `--allow-read`). |
| `create_qa_agent` | `json_path` (str) | `dspy.RLM` | Loads the project, initialises primary and sub LLMs, builds the agent `Signature` with embedded schema and language instructions, and returns a fully configured `dspy.RLM` agent. |
| `ask` | `rlm` (dspy.RLM), `question` (str) | `str` | Submits a question to the RLM agent with the loaded project data and returns the answer string. |
| `LLM_MODEL` | — | `str` (constant) | LiteLLM-format model name used for the primary LLM. |
| `SUB_LLM_MODEL` | — | `str` (constant) | LiteLLM-format model name used for the sub-LLM inside the RLM sandbox. |
| `LLM_API_KEY` | — | `str` (constant) | API key read from the `LLM_API_KEY` environment variable. |
| `LLM_API_BASE` | — | `str \| None` (constant) | Optional API base URL for non-standard LLM endpoints. |
| `OUTPUT_LANGUAGE` | — | `str` (constant) | Natural language in which the agent writes its answers. |
| `TARGET_JSON_PATH` | — | `str` (constant) | Default file path to `project_knowledge.json` used when running as a script. |

## 4. Design Decisions

- **Instruction template with runtime substitution**: `INSTRUCTIONS_TEMPLATE` uses `<<<DOC_SCHEMA>>>` and `<<<RLM_OUTPUT_LANGUAGE>>>` placeholder tokens replaced via `.replace()` at agent creation time rather than using Python f-strings. This keeps the large multi-line template readable as a module-level constant while still allowing it to incorporate project-specific schema and language configuration derived from loaded data.
- **Deferred Deno configuration via `create_interpreter`**: Rather than accepting the default `PythonInterpreter` construction, the module locates the `runner.js` path and the Deno cache directory at runtime and injects precise `--allow-read` allowances. This avoids granting broad filesystem access to the sandbox while remaining portable across machines with different Deno installations.
- **Stateful `qa_tools` module as shared context**: Project data and base directory are stored as module-level variables in `qa_tools` rather than passed through function arguments. This allows the tool functions registered with the RLM agent to access project context without requiring the agent framework to manage additional state.
- **Explicit interpreter shutdown in `finally`**: The `main` loop calls `rlm._interpreter.shutdown()` in a `finally` block to ensure the long-lived Deno subprocess is terminated even when the user exits via `KeyboardInterrupt`.

# Definition Design Specifications

---

## Module-level Constants

| Name | Type | Value / Description |
|---|---|---|
| `LLM_MODEL` | `str` | Primary LLM model identifier in litellm format, used to drive the top-level RLM agent. |
| `SUB_LLM_MODEL` | `str` | Secondary LLM model identifier used inside the RLM sandbox for `llm_query`/`llm_query_batched` calls. |
| `LLM_API_KEY` | `str` | API key read from the `LLM_API_KEY` environment variable; defaults to empty string if not set. |
| `LLM_API_BASE` | `None` | Base URL override for non-standard LLM endpoints (e.g., Ollama, Azure). `None` means use the provider's default endpoint. |
| `OUTPUT_LANGUAGE` | `str` | Natural language in which the agent must write all answers. |
| `TARGET_JSON_PATH` | `str` | Absolute path to the target `project_knowledge.json`, resolved relative to this file's directory. |
| `INSTRUCTIONS_TEMPLATE` | `str` | Prompt template string for the DSPy `Signature`. Contains two substitution tokens: `<<<DOC_SCHEMA>>>` and `<<<RLM_OUTPUT_LANGUAGE>>>`, which are replaced at agent creation time. |

---

## `build_doc_schema(project_data: dict) -> str`

**Responsibility:** Extracts the doc section list from the first file entry that contains sections in the loaded project data and formats it as a Markdown table, providing the LLM with an accurate schema description for the specific project being queried.

**When to use:** Called once during agent creation to generate the `<<<DOC_SCHEMA>>>` portion of the system prompt.

**Design decisions:**
- Only the sections from the *first* file entry that has a non-empty `sections` list are used. The assumption is that all files share the same section schema.
- Output is a static Markdown table (columns: `id`, `title`) embedded directly into the prompt string rather than being passed as structured data.

**Constraints & edge cases:**
- If no file in `project_data["files"]` contains a non-empty `sections` list, `sections` remains an empty list and the returned table will have a header row but no data rows.
- Does not validate that section `id` or `title` fields are present; missing keys would raise a `KeyError`.

---

## `load_project(json_path: str) -> None`

**Responsibility:** Reads `project_knowledge.json` from disk and populates the shared module-level state in `qa_tools` (`project_data` and `base_dir`) so that all tool functions have access to the project graph without requiring explicit argument passing.

**When to use:** Called once at the start of agent creation before any tool functions or prompt building occurs.

**Design decisions:**
- Side-effect–only function; it mutates `qa_tools.project_data` and `qa_tools.base_dir` directly. This mirrors the stateful design of `qa_tools.py` where these are module-level variables.
- `base_dir` is set to the directory containing the JSON file so that `read_source_file` can resolve relative paths from the JSON against the same directory.

**Constraints & edge cases:**
- Raises `FileNotFoundError` or `json.JSONDecodeError` if the path does not exist or the file is not valid JSON.
- Must be called before `build_doc_schema`, `create_qa_agent`, or any `qa_tools` function.

---

## `create_interpreter() -> PythonInterpreter`

**Responsibility:** Constructs a `PythonInterpreter` instance with a Deno command that is compatible with Deno 2.x, restricting file-system read access to only the runner script and the Deno cache directory.

**When to use:** Called once inside `create_qa_agent` to produce the sandboxed execution environment for RLM-generated Python code.

**Design decisions:**
- The Deno cache directory is resolved in priority order: `DENO_DIR` environment variable → `deno info --json` output → hardcoded fallback `~/.cache/deno`. This ensures compatibility across environments without requiring manual configuration.
- `--node-modules-dir=false` is passed explicitly to suppress Deno 2.x node-modules behavior.
- The `--allow-read` permission is scoped to exactly two paths (`runner.js` and the Deno cache directory) rather than being granted globally, enforcing a minimal-permission sandbox.
- `DENO_DIR` is written back to `os.environ` so the Deno subprocess inherits the resolved value.
- The runner path is obtained via `inspect.getfile(PythonInterpreter)` to locate the file relative to the installed `dspy` package rather than relying on a hardcoded path.

**Constraints & edge cases:**
- If `deno` is not installed or not on `PATH`, the fallback cache path is used without raising an error at construction time; failure will surface only when the interpreter is actually invoked.
- `subprocess.run` failure (non-zero return code) is silently ignored when resolving `deno info`.

---

## `create_qa_agent(json_path: str) -> dspy.RLM`

**Responsibility:** Orchestrates the full initialization pipeline—loading project data, building the dynamic prompt, instantiating both LMs, and assembling the `dspy.RLM` agent with its tool set—returning a ready-to-use agent.

**When to use:** Called once at program startup with the path to a `project_knowledge.json` file before any questions are posed.

**Design decisions:**
- Two separate `dspy.LM` instances are created: `lm` (primary, set via `rlm.set_lm`) drives top-level reasoning; `sub_lm` is passed to `dspy.RLM` for internal sandbox LLM calls. This allows different model capabilities or cost tiers to be applied at each level.
- The `Signature` is constructed with the string form `"project_data, question -> answer"` rather than a class, keeping the schema concise.
- The three tools passed to `dspy.RLM` (`read_source_file`, `get_files_using`, `graph_search`) are the public interface of `qa_tools.py`; `project_data` and `base_dir` state in that module is set by `load_project` before this function creates the agent.
- `verbose=True` is hardcoded, making intermediate reasoning steps visible at runtime.

**Constraints & edge cases:**
- `load_project` must succeed before `build_doc_schema` is called inside this function; failure in `load_project` will propagate as an exception.
- Both `LLM_API_KEY` and `LLM_MODEL` must be valid; invalid values will cause `dspy.LM` construction or subsequent API calls to fail.

---

## `ask(rlm: dspy.RLM, question: str) -> str`

**Responsibility:** Provides a thin, typed wrapper around `dspy.RLM.__call__` that passes the shared `project_data` and the user question, returning only the answer string.

**When to use:** Called in the interactive loop (or externally) whenever a question needs to be answered using the initialized agent.

**Constraints & edge cases:**
- `qa_tools.project_data` must already be populated (by `load_project`) before this function is called; otherwise the agent receives `None` as `project_data`.
- The return value is `result.answer`; any other fields of the RLM result object are discarded.

---

## `main() -> None`

**Responsibility:** Entry point that validates the JSON file path, initializes the RLM agent, and runs an interactive question-answering loop in the terminal, ensuring the `PythonInterpreter` (Deno process) is shut down on exit.

**When to use:** Executed when the module is run directly (`__name__ == "__main__"`).

**Design decisions:**
- The interactive loop catches `KeyboardInterrupt` at the outer level to allow clean termination via Ctrl-C without a traceback.
- A `try/finally` block guarantees `rlm._interpreter.shutdown()` is called regardless of how the loop exits, preventing orphaned Deno processes.
- Empty input lines are silently skipped; the loop continues without invoking the agent.
- The exit keywords (`exit`, `quit`, `q`) are checked case-insensitively.

**Constraints & edge cases:**
- Calls `sys.exit(1)` if `TARGET_JSON_PATH` does not exist, requiring `project_knowledge.json` to be generated beforehand.
- `rlm._interpreter` is accessed directly (private attribute); if `dspy.RLM` changes its internal attribute name, the shutdown call will silently fail (no `AttributeError` guard is present beyond the `is not None` check).

# Dependency Description

## Dependencies (modules this file imports)

**`examples/rlm_qa/rlm_qa_agent.py` → `examples/rlm_qa/qa_tools.py`**

This file depends on `qa_tools` for all project knowledge data access and tool functions exposed to the RLM agent. The specific symbols used and their purposes are as follows:

- `qa_tools.project_data` : Shared module-level variable that holds the loaded `project_knowledge.json` data. `rlm_qa_agent` writes to this variable via `load_project()` (by assigning `qa_tools.project_data = json.load(f)`) and reads from it when constructing the agent (e.g., passing it to `build_doc_schema()` and as the `project_data` argument to `rlm()`).

- `qa_tools.base_dir` : Shared module-level variable that holds the base directory path of the loaded JSON file. `rlm_qa_agent` writes to this variable in `load_project()` so that `qa_tools.read_source_file` can resolve relative file paths correctly.

- `qa_tools.read_source_file` : Tool function registered in the RLM agent's `tools` list. Provides the ability to read full source file contents from the project output directory, enabling the agent to retrieve complete file source when needed during Q&A.

- `qa_tools.get_files_using` : Tool function registered in the RLM agent's `tools` list. Provides the ability to find all files that depend on a specified target file by traversing `callee_usages`, enabling the agent to answer questions about reverse dependencies.

- `qa_tools.graph_search` : Tool function registered in the RLM agent's `tools` list. Provides BFS-based dependency graph traversal from a named definition across a configurable number of hops and directions, enabling the agent to answer questions about broader dependency relationships.

---

## Dependents (modules that import this file)

No dependent information is provided. There are no project-internal modules documented as importing `rlm_qa_agent.py`.

---

## Dependency Direction

The relationship between `rlm_qa_agent` and `qa_tools` is **unidirectional**:

- `rlm_qa_agent` → `qa_tools` : `rlm_qa_agent` imports and mutates `qa_tools`'s module-level state (`project_data`, `base_dir`) and consumes its tool functions (`read_source_file`, `get_files_using`, `graph_search`). `qa_tools` has no reference back to `rlm_qa_agent`.

# Data Flow

## 1. Inputs

| Source | Format | Description |
|---|---|---|
| `TARGET_JSON_PATH` (constant) | File path string | Resolved path to `project_knowledge.json`, constructed from `__file__` at module load time |
| `project_knowledge.json` | JSON file on disk | Full project knowledge graph including files, definitions, dependencies, and doc sections |
| `LLM_MODEL`, `SUB_LLM_MODEL` | String constants | litellm-format model identifiers for the primary and sub LLMs |
| `LLM_API_KEY` | Environment variable `LLM_API_KEY` | API key for the LLM provider |
| `LLM_API_BASE` | Constant (or `None`) | Optional custom API base URL |
| `OUTPUT_LANGUAGE` | String constant | Natural language for generated answers (e.g., `"English"`) |
| `INSTRUCTIONS_TEMPLATE` | Module-level string constant | Prompt template containing `<<<DOC_SCHEMA>>>` and `<<<RLM_OUTPUT_LANGUAGE>>>` placeholders |
| User keyboard input | Plain text string | Question entered interactively at the `>` prompt |

---

## 2. Transformation Overview

### Stage 1: Project Data Loading (`load_project`)
`project_knowledge.json` is read from disk and deserialized into a Python `dict`. This dict is assigned to the module-level variable `qa_tools.project_data`, and the JSON file's parent directory is assigned to `qa_tools.base_dir`. Both become globally accessible state for the tool functions in `qa_tools`.

### Stage 2: Instruction Assembly (`build_doc_schema` + template substitution)
`build_doc_schema` inspects `qa_tools.project_data["files"][0]["doc"]["sections"]` to extract the actual section schema of the loaded project, producing a Markdown table string. This string and `OUTPUT_LANGUAGE` are substituted into `INSTRUCTIONS_TEMPLATE` via `.replace()`, producing the final prompt `instructions` string.

### Stage 3: Agent Construction (`create_qa_agent`)
The assembled `instructions` string is used to instantiate a `dspy.Signature` object with the field schema `"project_data, question -> answer"`. A `PythonInterpreter` is created (Stage 3a, below) and, together with the `Signature`, the tool functions from `qa_tools`, and both LM instances, is passed to `dspy.RLM` to produce the agent. The primary LM is then bound to the agent via `set_lm`.

### Stage 3a: Interpreter Construction (`create_interpreter`)
The filesystem path to `runner.js` (located by inspecting `PythonInterpreter`'s source file location) and the Deno cache directory (obtained from `deno info --json` or defaulting to `~/.cache/deno`) are composed into a `deno run` command list. This list is passed as `deno_command` to `PythonInterpreter`, yielding a sandboxed Python execution environment.

### Stage 4: Question–Answer Cycle (`ask`)
In the interactive loop, each user question string is passed to `rlm()` together with `qa_tools.project_data` as the `project_data` argument. `dspy.RLM` uses the primary LM to generate Python code, executes it inside the `PythonInterpreter` sandbox (which has access to the tool functions `read_source_file`, `get_files_using`, and `graph_search`), and iterates until it produces a final answer. The `.answer` attribute of the returned result is extracted and printed to stdout.

### Stage 5: Cleanup
On exit (keyboard interrupt or `exit`/`quit`/`q`), the Deno subprocess backing `PythonInterpreter` is terminated via `rlm._interpreter.shutdown()`.

---

## 3. Outputs

| Output | Format | Destination |
|---|---|---|
| `qa_tools.project_data` | `dict` (deserialized JSON) | Module-level side effect; consumed by `dspy.RLM` and tool functions |
| `qa_tools.base_dir` | `str` (directory path) | Module-level side effect; consumed by `read_source_file` |
| `instructions` (intermediate) | `str` | Consumed internally to build `dspy.Signature`; not returned to caller |
| `rlm` (`dspy.RLM` instance) | Object | Returned by `create_qa_agent`; held in `main` for the session lifetime |
| `result.answer` | `str` | Printed to stdout for each user question |
| Deno subprocess shutdown | Side effect | Subprocess termination via `_interpreter.shutdown()` on exit |

---

## 4. Key Data Structures

### `project_data` (loaded from `project_knowledge.json`)
| Field / Key | Type | Purpose |
|---|---|---|
| `project_name` | `str` | Name of the analyzed project |
| `project_dependencies` | `list[dict]` | Per-file dependency graph entries with `file`, `summary`, `callers`, `callees` |
| `files` | `list[dict]` | Per-file detailed records containing `file`, `file_dependencies`, and `doc` |

### `project_dependencies[]` entry
| Field / Key | Type | Purpose |
|---|---|---|
| `file` | `str` | File path |
| `summary` | `str \| None` | File-level summary |
| `callers` | `list[str]` | Files that depend on this file |
| `callees` | `list[str]` | Files this file depends on |

### `files[]` entry
| Field / Key | Type | Purpose |
|---|---|---|
| `file` | `str` | File path |
| `file_dependencies` | `dict` | Contains `definitions`, `callee_usages`, `caller_usages` |
| `doc` | `dict` | Contains `summary` (str) and `sections` (list of section dicts) |

### `file_dependencies` dict
| Field / Key | Type | Purpose |
|---|---|---|
| `definitions` | `list[dict]` | Function/class definitions with `name`, `type`, `start_line`, `end_line`, `context` |
| `callee_usages` | `list[dict]` | Dependencies used by this file: `lines`, `name`, `from`, `target_context` |
| `caller_usages` | `list[dict]` | Dependents using this file: `lines`, `name`, `file`, `usage_context` |

### `doc` dict
| Field / Key | Type | Purpose |
|---|---|---|
| `summary` | `str` | Human-readable summary of the file |
| `sections` | `list[dict]` | Design document sections, each with `id` (str), `title` (str), `content` (str) |

### `deno_command` list (passed to `PythonInterpreter`)
| Element | Type | Purpose |
|---|---|---|
| `"deno"` | `str` | Deno executable |
| `"run"` | `str` | Deno subcommand |
| `"--node-modules-dir=false"` | `str` | Disables local node_modules resolution |
| `f"--allow-read=..."` | `str` | Grants read access restricted to `runner.js` and the Deno cache directory |
| `runner_path` | `str` | Absolute path to `runner.js` entry point |

# Error Handling

## 1. Overall Strategy

The file applies a **fail-fast** strategy for initialization-time failures and a **graceful continuation** strategy during the interactive session. Critical prerequisites (JSON file existence, project loading) terminate the process immediately with a diagnostic message. Runtime errors during interactive Q&A are absorbed by a `KeyboardInterrupt` catch that allows clean exit, and resource cleanup (Deno process shutdown) is guaranteed via a `finally` block regardless of how the session ends.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Missing `project_knowledge.json` | `TARGET_JSON_PATH` does not exist at startup | Prints error message and calls `sys.exit(1)` | No | Process terminates before initialization |
| Missing `LLM_API_KEY` | `LLM_API_KEY` environment variable is not set | Defaults to empty string `""`; passed to `dspy.LM` as-is | No | LM initialization or first API call will fail downstream |
| Deno binary not found | `deno` command is unavailable during `create_interpreter()` | `subprocess.run` `FileNotFoundError` is silently caught; falls back to default `deno_dir` path (`~/.cache/deno`) | Partial | `deno_dir` may be incorrect, causing later Deno execution to fail |
| `deno info` non-zero exit | `deno info --json` returns a non-zero return code | Return code is checked; result is ignored and fallback `deno_dir` is used | Partial | Same as above; Deno execution may fail at runtime |
| Source file read failure | `read_source_file()` cannot open the requested file | Returns an error string (handled inside `qa_tools.read_source_file`) | Yes | Only the specific file read fails; agent continues |
| `KeyboardInterrupt` during Q&A | User presses Ctrl+C during the interactive loop | Caught at the outer `try/except`; breaks the loop | Yes | Session ends; cleanup proceeds normally |
| Deno process not started | `rlm._interpreter` is `None` at shutdown | `None` guard check skips `shutdown()` call | Yes | No shutdown action taken; no crash |

---

## 3. Design Notes

- **Startup validation is strict**: The JSON file check with `sys.exit(1)` reflects the design decision that the agent is entirely useless without its knowledge base, making silent failure inappropriate.
- **Missing API key is not validated locally**: The file deliberately defers API key validation to the underlying `dspy.LM` and the LLM provider, keeping the agent code free of credential-checking logic.
- **Deno environment resolution is best-effort**: The multi-step fallback for locating the Deno cache directory (environment variable → `deno info` → hardcoded default) prioritizes keeping the interpreter creation non-fatal, but the fallback path may still result in a runtime failure when the Deno command is actually executed.
- **Resource cleanup is unconditional**: Placing the `PythonInterpreter.shutdown()` call in a `finally` block ensures the Deno subprocess is always terminated, preventing orphaned processes regardless of whether the session ended normally or via interrupt.
- **Interactive errors are not individually caught**: Individual question/answer cycles do not have their own error recovery; any unhandled exception from `rlm()` would propagate and terminate the loop, consistent with the fail-fast philosophy applied outside the `KeyboardInterrupt` path.

# Summary

**rlm_qa_agent.py** — Provides an interactive CLI Q&A agent answering natural language questions about a `project_knowledge.json` via a `dspy.RLM` instance.

**Public functions:** `load_project(json_path:str)`, `build_doc_schema(project_data:dict)->str`, `create_interpreter()->PythonInterpreter`, `create_qa_agent(json_path:str)->dspy.RLM`, `ask(rlm:dspy.RLM, question:str)->str`.

**Key data:** consumes `project_data` (dict with `files`, `project_dependencies` lists), produces `dspy.RLM` agent and `answer` (str). Shares state via `qa_tools.project_data` and `qa_tools.base_dir`.
