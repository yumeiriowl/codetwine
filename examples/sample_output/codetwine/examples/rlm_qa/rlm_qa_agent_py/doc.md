# Design Document: examples/rlm_qa/rlm_qa_agent.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Constructs and operates an interactive Q&A agent that answers natural language questions about a loaded `project_knowledge.json` by directing a `dspy.RLM` instance to generate and execute Python code against the project data structure.

## 2. When to Use This Module

- **Run interactively from the command line**: Execute `uv run python examples/rlm_qa/rlm_qa_agent.py` to start a REPL-style session where the user types questions and receives answers about a target project's codebase.
- **Programmatically create a Q&A agent**: Call `create_qa_agent(json_path)` with a path to `project_knowledge.json` to obtain a configured `dspy.RLM` instance backed by the project data and tool functions from `qa_tools`.
- **Ask a single question programmatically**: Call `ask(rlm, question)` with the `dspy.RLM` instance returned by `create_qa_agent` to receive a plain string answer for a given question without managing the agent internals.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `create_qa_agent` | `json_path: str` | `dspy.RLM` | Loads `project_knowledge.json`, initializes the primary and sub LLMs, builds the instruction prompt with the doc schema, creates a `PythonInterpreter`, and assembles the `dspy.RLM` agent with `qa_tools` tool functions. |
| `ask` | `rlm: dspy.RLM`, `question: str` | `str` | Invokes the RLM agent with the loaded `project_data` and the given question, returning the answer string from the result. |
| `main` | _(none)_ | `None` | Validates the target JSON path, initializes the agent via `create_qa_agent`, runs an interactive question-answer loop, and shuts down the `PythonInterpreter` on exit. |
| `LLM_MODEL` | — | `str` | Configuration constant specifying the primary LLM model name in litellm format. |
| `SUB_LLM_MODEL` | — | `str` | Configuration constant specifying the sub-LLM model name used inside the RLM sandbox for `llm_query`/`llm_query_batched`. |
| `LLM_API_KEY` | — | `str` | Configuration constant holding the API key read from the `LLM_API_KEY` environment variable. |
| `OUTPUT_LANGUAGE` | — | `str` | Configuration constant specifying the natural language in which answers are written. |
| `TARGET_JSON_PATH` | — | `str` | Configuration constant specifying the default file path to `project_knowledge.json`. |

## 4. Design Decisions

- **Template-based instruction injection**: Rather than hardcoding instructions, `INSTRUCTIONS_TEMPLATE` uses `<<<DOC_SCHEMA>>>` and `<<<RLM_OUTPUT_LANGUAGE>>>` placeholder tokens replaced at agent construction time via `str.replace()`. This allows the prompt to reflect the actual section structure of the loaded project data without requiring a templating library.
- **Deferred `PythonInterpreter` configuration**: The Deno command is assembled at runtime by locating the `runner.js` path from `dspy`'s installed package directory and resolving the Deno cache directory via `deno info --json`, falling back to `~/.cache/deno`. This avoids hardcoded paths while satisfying Deno 2.x's `--allow-read` restriction to only the paths the interpreter actually needs.
- **Module-level state in `qa_tools`**: `project_data` and `base_dir` are set as module-level variables on the `qa_tools` module directly after loading. This makes the same data available both to the agent's instruction context (passed as the `project_data` input field) and to the tool functions (`read_source_file`, `get_files_using`, `graph_search`) without passing arguments through the RLM boundary.

## Definition Design Specifications

# Definition Design Specifications

---

## Module-Level Constants

| Name | Type | Value / Purpose |
|---|---|---|
| `LLM_MODEL` | `str` | Primary LLM model name in litellm format used by `dspy.LM` |
| `SUB_LLM_MODEL` | `str` | Secondary LLM model name used inside the RLM sandbox for `llm_query`/`llm_query_batched` calls |
| `LLM_API_KEY` | `str` | API key read from the `LLM_API_KEY` environment variable; defaults to empty string if unset |
| `OUTPUT_LANGUAGE` | `str` | Natural language in which the agent writes answers |
| `TARGET_JSON_PATH` | `str` | Absolute path to the target `project_knowledge.json`, resolved relative to the script's own directory |
| `INSTRUCTIONS_TEMPLATE` | `str` | Multi-section prompt template containing JSON schema documentation and code examples; uses `<<<DOC_SCHEMA>>>` and `<<<RLM_OUTPUT_LANGUAGE>>>` as substitution placeholders |

---

## Functions

---

### `_build_doc_schema`

**Signature:**
```
_build_doc_schema(project_data: dict) -> str
```

**Responsibility:**  
Extracts the `doc.sections` list from the first file entry in loaded project data and renders it as a Markdown table, producing a string that is injected into `INSTRUCTIONS_TEMPLATE` to give the LLM a concrete map of available document sections.

**When to use:**  
Called once during agent creation, after `project_knowledge.json` has been loaded, to dynamically tailor the prompt to the specific project being queried.

**Design decisions:**
- Uses the first file entry that contains a non-empty `sections` list rather than aggregating across all files, on the assumption that all files share the same document schema.
- Returns a Markdown table with `id` and `title` columns only; `content` fields are intentionally omitted to avoid bloating the prompt.

**Constraints & edge cases:**
- If no file in `project_data["files"]` has a non-empty `sections` list, `sections` remains an empty list and the returned table body will be empty.
- Does not validate that `project_data` contains the `"files"` key; missing key will raise `KeyError`.

---

### `_load_project`

**Signature:**
```
_load_project(json_path: str) -> None
```

**Responsibility:**  
Loads `project_knowledge.json` from disk and writes the parsed data and its containing directory into the `qa_tools` module's shared state (`qa_tools.project_data` and `qa_tools.base_dir`), making the data available to all tool functions.

**When to use:**  
Called once at the start of `create_qa_agent` before any other operation that depends on project data.

**Design decisions:**  
- Mutates module-level variables in `qa_tools` directly rather than passing data through function arguments, because `qa_tools` tool functions read those variables at call time from inside the RLM sandbox.
- Derives `base_dir` from the directory containing the JSON file so that `qa_tools.read_source_file` can locate copied source files relative to it.

**Constraints & edge cases:**
- Raises `FileNotFoundError` or `json.JSONDecodeError` if the file is missing or malformed.
- Prints a confirmation message including file count and project name as a side effect.

---

### `_create_interpreter`

**Signature:**
```
_create_interpreter() -> PythonInterpreter
```

**Responsibility:**  
Constructs a `PythonInterpreter` instance with a Deno command line that disables the node modules directory and restricts file-read permissions to only the runner script and the Deno cache directory.

**When to use:**  
Called once inside `create_qa_agent` to produce the sandboxed Python execution environment for the RLM agent.

**Design decisions:**
- The path to `runner.js` is resolved via `inspect.getfile(PythonInterpreter)` rather than being hardcoded, making it robust to package installation location changes.
- Deno cache directory is determined by attempting `deno info --json` at runtime; if that subprocess call fails (e.g., Deno is not on `PATH`), it falls back to `~/.cache/deno`.
- `DENO_DIR` is set as an environment variable before constructing the command so that Deno uses the discovered cache path.
- `--node-modules-dir=false` prevents npm package resolution, keeping the sandbox isolated.
- `--allow-read` is scoped to only two paths, minimising the sandbox's filesystem access surface.

**Constraints & edge cases:**
- Requires Deno to be installed and accessible on `PATH` for the interpreter to function; construction itself may succeed even if Deno is absent, but execution will fail later.
- If `deno info --json` returns non-zero or malformed JSON, the exception is silently caught and the fallback path is used.

---

### `create_qa_agent`

**Signature:**
```
create_qa_agent(json_path: str) -> dspy.RLM
```

**Responsibility:**  
Orchestrates the full initialisation sequence—loading project data, configuring dspy LMs, building the prompt, and assembling the RLM agent—returning a ready-to-call `dspy.RLM` instance.

**When to use:**  
Called once per session before any questions are posed; the returned `dspy.RLM` object is reused for all subsequent calls to `ask`.

**Design decisions:**
- Two separate `dspy.LM` instances are created: the primary `lm` is set as the dspy global default, while `sub_lm` is passed explicitly to `dspy.RLM` for use within sandbox-level LLM calls.
- Prompt instructions are finalised by string substitution into `INSTRUCTIONS_TEMPLATE` at agent-creation time rather than at call time, so the template rendering cost is paid once.
- The `dspy.Signature` is created with the string shorthand `"project_data, question -> answer"` plus a full instruction block.
- Three tools from `qa_tools` are registered: `read_source_file`, `get_files_using`, and `graph_search`.
- `max_iterations=12` caps the agent's reasoning loop.

**Constraints & edge cases:**
- `LLM_API_KEY` must be non-empty; an empty string is passed to `dspy.LM` without error at construction time but will fail on first use.
- `json_path` must point to a valid, fully-populated `project_knowledge.json` produced by the project's main pipeline.
- Mutates `qa_tools.project_data` and `qa_tools.base_dir` as a side effect.

---

### `ask`

**Signature:**
```
ask(rlm: dspy.RLM, question: str) -> str
```

**Responsibility:**  
Invokes the RLM agent with the loaded project data and user question, returning the plain-text answer string.

**When to use:**  
Called each time a user submits a question after the agent has been initialised with `create_qa_agent`.

**Design decisions:**
- `qa_tools.project_data` is passed as the `project_data` argument at call time, reflecting whatever state the module variable holds at that moment.
- Returns only `result.answer`, discarding any other fields in the RLM result object.

**Constraints & edge cases:**
- `rlm` must be a fully initialised `dspy.RLM` instance returned by `create_qa_agent`.
- An empty or whitespace-only `question` is not filtered here; the caller (`main`) handles that guard.

---

### `main`

**Signature:**
```
main() -> None
```

**Responsibility:**  
Provides an interactive REPL that loads the agent once and then repeatedly prompts the user for questions, printing answers until the user exits.

**When to use:**  
Invoked automatically when the script is run as `__main__`.

**Design decisions:**
- Validates the existence of `TARGET_JSON_PATH` before initialisation and exits with a non-zero status if absent, giving an actionable error message.
- The `finally` block unconditionally calls `rlm._interpreter.shutdown()` to terminate the background Deno process, preventing resource leaks regardless of how the loop exits.
- Both `KeyboardInterrupt` (outer `try`) and normal exit keywords (`"exit"`, `"quit"`, `"q"`) break the loop cleanly.
- Blank input lines are silently skipped without invoking the agent.

**Constraints & edge cases:**
- `rlm._interpreter` accesses a private attribute of `dspy.RLM`; this may break if the dspy internal API changes.
- The `finally` guard checks `rlm._interpreter is not None` before calling `shutdown`, but `rlm` itself is only defined if `create_qa_agent` succeeded; a failure there would raise `NameError` in the `finally` block.

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

**`rlm_qa_agent` → `examples/rlm_qa/qa_tools.py` : shared project data state and tool functions for the RLM agent**

Specifically, the following symbols are imported and used:

- `qa_tools.project_data` — A module-level variable initialized to `None` in `qa_tools.py`. `rlm_qa_agent` writes the loaded JSON content into this variable during `_load_project()`, and reads it back when invoking the RLM agent via `ask()`. It also passes this variable directly as the `project_data` input field to the `dspy.RLM` call.

- `qa_tools.base_dir` — A module-level variable initialized to `None` in `qa_tools.py`. `rlm_qa_agent` writes the directory path of the loaded JSON file into this variable during `_load_project()`, enabling `qa_tools.read_source_file` to resolve file paths at runtime.

- `qa_tools.read_source_file` — Registered as a tool in the `dspy.RLM` instance. Allows the LLM agent to read the content of a source file from the output directory at query time.

- `qa_tools.get_files_using` — Registered as a tool in the `dspy.RLM` instance. Allows the LLM agent to look up which files depend on a given file by traversing `callee_usages` across the project graph.

- `qa_tools.graph_search` — Registered as a tool in the `dspy.RLM` instance. Allows the LLM agent to perform BFS traversal over the dependency graph from a named definition, in either or both directions.

## Dependents (modules that import this file)

No dependent information is provided.

## Dependency Direction

The relationship between `rlm_qa_agent` and `qa_tools` is **unidirectional**:

- `rlm_qa_agent` → `qa_tools` : `rlm_qa_agent` both configures the shared state of `qa_tools` (by writing `project_data` and `base_dir`) and consumes its tool functions by registering them into the `dspy.RLM` instance. `qa_tools` has no reference back to `rlm_qa_agent`.

## Data Flow

# Data Flow

## 1. Inputs

| Source | Format | Description |
|---|---|---|
| `TARGET_JSON_PATH` | File path string | Path to `project_knowledge.json`, resolved relative to the script's directory |
| `project_knowledge.json` | JSON file | Serialized project knowledge graph containing file metadata, dependency graphs, and design documents |
| `LLM_API_KEY` | Environment variable (`LLM_API_KEY`) | API key for the LLM provider |
| `LLM_MODEL` / `SUB_LLM_MODEL` | Module-level string constants | Model identifiers in litellm format |
| `OUTPUT_LANGUAGE` | Module-level string constant | Natural language for generated answers |
| `INSTRUCTIONS_TEMPLATE` | Module-level string constant | Instruction template with `<<<DOC_SCHEMA>>>` and `<<<RLM_OUTPUT_LANGUAGE>>>` placeholders |
| User question | Plain string from `stdin` | Entered interactively at the `>` prompt |

---

## 2. Transformation Overview

### Stage 1: Project Data Loading (`_load_project`)
`project_knowledge.json` is read from disk and deserialized into a Python dict. The result is stored in `qa_tools.project_data` and the JSON's parent directory is stored in `qa_tools.base_dir`. These module-level variables make the knowledge graph available to all tool functions (`read_source_file`, `get_files_using`, `graph_search`) without passing it explicitly.

### Stage 2: Instruction Construction
`_build_doc_schema` scans `qa_tools.project_data["files"]` to extract the `doc.sections` list from the first file entry that has sections, then renders it as a Markdown table (`| id | title |`). The resulting string and `OUTPUT_LANGUAGE` are substituted into `INSTRUCTIONS_TEMPLATE` via `.replace()`, producing the final instruction string passed to `dspy.Signature`.

### Stage 3: Agent Assembly (`create_qa_agent`)
A `dspy.Signature` object is created from the string `"project_data, question -> answer"` combined with the constructed instruction string. A `PythonInterpreter` (Deno sandbox) is instantiated via `_create_interpreter`. A `dspy.RLM` agent is assembled from the signature, interpreter, and the three tool functions, with `max_iterations=12`.

### Stage 4: Interpreter Configuration (`_create_interpreter`)
The path to `runner.js` (located inside the `dspy.primitives` package directory) is resolved at runtime. The Deno cache directory is discovered via `deno info --json` or falls back to `~/.cache/deno`. The Deno command is constructed with `--node-modules-dir=false` and `--allow-read` restricted to `runner_path` and `deno_dir`, then passed to `PythonInterpreter`.

### Stage 5: Interactive Q&A Loop (`main` → `ask`)
For each user question, `ask` calls `rlm(project_data=qa_tools.project_data, question=question)`. Internally, `dspy.RLM` iteratively generates Python code, executes it inside the Deno/Pyodide sandbox via the `PythonInterpreter`, and may invoke the registered tools (`read_source_file`, `get_files_using`, `graph_search`) up to `max_iterations=12` times. The final result object's `.answer` field is extracted and printed to `stdout`.

### Stage 6: Shutdown
On exit (user types `exit`/`quit`/`q` or sends `KeyboardInterrupt`), `rlm._interpreter.shutdown()` terminates the Deno subprocess.

---

## 3. Outputs

| Output | Format | Description |
|---|---|---|
| `qa_tools.project_data` | Python dict (side effect) | Loaded knowledge graph, written as a module-level variable for tool functions |
| `qa_tools.base_dir` | String (side effect) | Directory path of the JSON file, used by `read_source_file` to resolve source file paths |
| `dspy.RLM` instance | Object (return value of `create_qa_agent`) | Configured agent ready to accept questions |
| Answer string | Plain string printed to `stdout` | The `.answer` field extracted from the `dspy.RLM` result, written in `OUTPUT_LANGUAGE` |

---

## 4. Key Data Structures

### `qa_tools.project_data` (top-level dict)

| Field / Key | Type | Purpose |
|---|---|---|
| `project_name` | `str` | Name of the analyzed project |
| `project_dependencies` | `list[dict]` | Per-file dependency graph nodes (callers/callees) |
| `files` | `list[dict]` | Per-file detailed records including dependencies and design docs |

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
| `file_dependencies` | `dict` | Definitions, callee usages, and caller usages |
| `doc` | `dict` | Design document with summary and sections |

### `file_dependencies` dict

| Field / Key | Type | Purpose |
|---|---|---|
| `definitions` | `list[dict]` | Functions/classes defined in this file, each with `name`, `type`, `start_line`, `end_line`, `context` |
| `callee_usages` | `list[dict]` | External symbols used by this file, each with `lines`, `name`, `from`, `target_context` |
| `caller_usages` | `list[dict]` | Symbols from this file used by other files, each with `lines`, `name`, `file`, `usage_context` |

### `doc` dict

| Field / Key | Type | Purpose |
|---|---|---|
| `summary` | `str` | Human-readable summary of the file |
| `sections` | `list[dict]` | Design document sections, each with `id` (str), `title` (str), `content` (str) |

### `dspy.RLM` construction arguments (passed as a plain dict equivalent)

| Field / Key | Type | Purpose |
|---|---|---|
| `signature` | `dspy.Signature` | Defines input fields (`project_data`, `question`) and output field (`answer`) with instructions |
| `max_iterations` | `int` (12) | Maximum sandbox execution iterations per question |
| `tools` | `list[callable]` | `[read_source_file, get_files_using, graph_search]` available inside the sandbox |
| `sub_lm` | `dspy.LM` | Secondary LLM used for `llm_query`/`llm_query_batched` within the sandbox |
| `interpreter` | `PythonInterpreter` | Deno-backed sandbox for executing generated Python code |

## Error Handling

# Error Handling

## 1. Overall Strategy

The file adopts a **fail-fast** strategy for initialization-phase errors and a **graceful degradation** approach for runtime tool errors. Critical preconditions (missing JSON file, missing environment variable) are validated at startup and terminate the process immediately with an informative message. Within the interactive loop, errors are handled by surfacing an error message string rather than raising exceptions, allowing the session to continue. The Deno subprocess is always shut down cleanly via a `finally` block regardless of how the loop exits.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Missing `project_knowledge.json` | `TARGET_JSON_PATH` does not exist at startup | Prints error message and calls `sys.exit(1)` | No | Process terminates before initialization |
| Missing `LLM_API_KEY` | `LLM_API_KEY` environment variable is not set | Silently defaults to empty string `""`; failure deferred to LLM call | No | LLM authentication fails at first query |
| Missing `DENO_DIR` / Deno not found | `deno info --json` subprocess fails or `FileNotFoundError` | Falls back to `~/.cache/deno` as the default Deno cache directory | Yes | Interpreter may fail at execution if Deno is not installed |
| Source file read failure | File path invalid, file missing, or permission error in `read_source_file` | Returns an error message string to the LLM | Yes (session continues) | Single tool call returns error; other queries unaffected |
| `project_data` not loaded | `graph_search` or `read_source_file` called before `base_dir`/`project_data` is set | Returns an error message string or dict | Yes (session continues) | Single tool call returns error |
| `KeyboardInterrupt` in interactive loop | User presses Ctrl+C during input or processing | `break` exits the loop; `finally` block runs shutdown | Yes (graceful exit) | Session terminates cleanly |
| Deno subprocess not cleaned up | Any exit path from the interactive loop (normal, exception, interrupt) | `finally` block calls `rlm._interpreter.shutdown()` if interpreter is not `None` | N/A | Prevents orphaned Deno processes |

---

## 3. Design Notes

- **Deferred API key validation**: The `LLM_API_KEY` is accepted as an empty string without raising an error at configuration time. This keeps initialization lightweight but means authentication failures only surface when the first LLM call is made, not at startup.
- **Tool errors as strings, not exceptions**: `read_source_file` and `graph_search` return error message strings or dicts rather than raising exceptions. This is consistent with the RLM tool-use pattern, where the LLM receives the error message as a tool result and can adapt its next action accordingly.
- **Subprocess cleanup as an invariant**: The `finally` block for Deno shutdown is unconditional (guarded only by a `None` check), treating resource cleanup as a mandatory postcondition rather than an optional courtesy. This prevents resource leaks across all exit paths including `KeyboardInterrupt`.
- **Best-effort Deno directory detection**: The interpreter setup attempts subprocess introspection (`deno info --json`) to locate the Deno cache, but silently ignores failures and applies a hardcoded fallback. This tolerates non-standard environments without aborting initialization.

## Summary

**rlm_qa_agent**: Builds and operates an interactive Q&A agent answering natural language questions about a `project_knowledge.json` using a `dspy.RLM` instance that generates and executes Python code against project data.

**Public functions:**
- `create_qa_agent(json_path: str) → dspy.RLM`
- `ask(rlm: dspy.RLM, question: str) → str`
- `main() → None`

**Key data structures:**
- `project_knowledge.json` (dict) with `files[]`, `project_dependencies[]`, each file having `doc.sections[]` and `file_dependencies`
- `dspy.RLM` configured with signature `"project_data, question -> answer"`, tools `[read_source_file, get_files_using, graph_search]`, and `max_iterations=12`
