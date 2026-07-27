# Design Document: examples/rlm_qa/rlm_qa_agent.py

# Overview & Purpose

`rlm_qa_agent.py` is the entry-point script for an interactive Q&A tool that lets a user ask natural-language questions about a codebase whose structural/documentation data has been pre-extracted into `project_knowledge.json`. It exists as a standalone example file (under `examples/rlm_qa/`) that wires together `dspy.RLM`, a sandboxed `PythonInterpreter` (Deno/Pyodide), and the helper functions in `qa_tools.py` into a runnable CLI application.

Its responsibilities are:
- **Configuration**: defines LLM model names, API key/base, output language, and the target JSON path as module-level constants.
- **Instruction/prompt construction**: builds the `dspy.Signature` instructions by combining a static template (`INSTRUCTIONS_TEMPLATE`) with a dynamically generated JSON-doc-schema summary (`build_doc_schema`), so the agent's system prompt always reflects the actual structure of the loaded data.
- **Environment setup**: loads `project_knowledge.json` into the shared `qa_tools` module state (`load_project`), and configures a Deno-based `PythonInterpreter` with the correct sandbox flags/cache directory (`create_interpreter`).
- **Agent assembly**: constructs and configures the `dspy.RLM` agent with the main LLM, a sub-LLM, the interpreter, and the `qa_tools` functions as callable tools (`create_qa_agent`).
- **Query execution & CLI loop**: provides a simple `ask()` wrapper to invoke the RLM on a question, and a `main()` function implementing an interactive REPL loop with graceful shutdown of the Deno interpreter process.

This logic is kept in its own file (separate from `qa_tools.py`) to isolate "agent orchestration and CLI" concerns from the "reusable data-query tool functions," which are designed to be invoked by the LLM inside the sandboxed interpreter.

### Main Public Interfaces

| Name | Arguments | Return | Responsibility |
|---|---|---|---|
| `build_doc_schema(project_data)` | `project_data: dict` | `str` | Extracts the first available `doc.sections` list from the loaded data and renders it as a markdown table for embedding into agent instructions |
| `load_project(json_path)` | `json_path: str` | `None` | Loads `project_knowledge.json` from disk and populates `qa_tools.project_data` / `qa_tools.base_dir` |
| `create_interpreter()` | — | `PythonInterpreter` | Builds a Deno-backed `PythonInterpreter` with correct `--allow-read`/`--node-modules-dir` flags and resolved Deno cache dir |
| `create_qa_agent(json_path)` | `json_path: str` | `dspy.RLM` | Loads project data, builds instructions/signature, creates the interpreter, and assembles a fully configured `dspy.RLM` agent with `qa_tools` functions as tools |
| `ask(rlm, question)` | `rlm: dspy.RLM`, `question: str` | `str` | Invokes the RLM agent with `project_data` and `question`, returning `result.answer` |
| `main()` | — | `None` | CLI entry point: validates the JSON file exists, initializes the agent, runs an interactive question/answer loop, and shuts down the interpreter on exit |

### Design Decisions

- **Template-based prompt construction**: Instructions are defined as a string template with placeholder tokens (`<<<DOC_SCHEMA>>>`, `<<<RLM_OUTPUT_LANGUAGE>>>`) substituted via `.replace()`, keeping the prompt text readable while allowing runtime customization based on actual loaded data and configured output language.
- **Shared mutable module state via `qa_tools`**: Rather than passing `project_data`/`base_dir` explicitly to every tool function, this file sets them as globals on the imported `qa_tools` module (`load_project`), so that tool functions executed inside the sandboxed interpreter (which only receives `project_data` as a signature input) can still access `base_dir` and reuse the same data reference.
- **Sandboxed code execution**: Uses `dspy.primitives.python_interpreter.PythonInterpreter` backed by a Deno subprocess (Pyodide sandbox) rather than executing LLM-generated code directly in-process, isolating arbitrary generated Python code from the host environment.
- **Separation of main vs. sub LLM**: A primary `LLM_MODEL` drives the RLM agent's reasoning/orchestration, while a distinct `SUB_LLM_MODEL` is passed as `sub_lm` for use by `llm_query`/`llm_query_batched` calls made from within the sandbox, allowing a cheaper/faster model for sub-queries.
- **Resource cleanup**: `main()` uses a `try/finally` block to guarantee `rlm._interpreter.shutdown()` is called, ensuring the Deno subprocess is terminated even on `KeyboardInterrupt` or early exit.

# Definition Design Specifications

### Module-level constants

- **`LLM_MODEL`** (str): Model identifier (litellm format) used for the main RLM reasoning LM. Exists to centralize the primary model choice as a single editable constant rather than scattering it across the script.
- **`SUB_LLM_MODEL`** (str): Model identifier used for `llm_query`/`llm_query_batched` calls made from within the sandboxed Python code executed by RLM. Kept separate from `LLM_MODEL` so a cheaper/faster model can be used for sub-queries issued inside the sandbox, independent of the top-level reasoning model.
- **`LLM_API_KEY`** (str): API key read from the `LLM_API_KEY` environment variable, shared by both the main and sub LLMs. Defaults to an empty string if unset, deferring failure to the LM client rather than failing at import time.
- **`LLM_API_BASE`** (str | None): Optional override for a non-standard LLM endpoint (e.g., Ollama, Azure). `None` means "use provider default."
- **`OUTPUT_LANGUAGE`** (str): Natural language name embedded into the instructions template to control the language of generated answers.
- **`TARGET_JSON_PATH`** (str): Filesystem path to the target `project_knowledge.json`, computed relative to this file's directory so the script is runnable from any working directory.
- **`INSTRUCTIONS_TEMPLATE`** (str): Template string for the `dspy.Signature` instructions, containing JSON schema documentation and code examples for the RLM sandbox. Uses `<<<...>>>` placeholder tokens replaced via `.replace()` (not `str.format`) to avoid conflicts with the literal `{`/`}` characters present in the embedded JSON/code examples.

### `build_doc_schema(project_data: dict) -> str`

- **Arguments**: `project_data` (dict) — the loaded `project_knowledge.json` content.
- **Returns**: `str` — a Markdown table (as text) enumerating `doc.sections` `id`/`title` pairs, prefixed with an explanatory sentence.
- **Responsibility**: Dynamically derives the project-specific documentation section schema from actual data, rather than hardcoding section IDs/titles in `INSTRUCTIONS_TEMPLATE`, so the agent's instructions stay accurate as the underlying document schema evolves across projects.
- **Design decision**: Only the sections from the *first* `files[]` entry that actually has a non-empty `doc.sections` are used, on the assumption that all files share the same section schema (uniform document structure across the project). Iteration stops as soon as such an entry is found.
- **Edge case**: If no file has `doc.sections`, `sections` remains an empty list and the function still returns a valid (header-only) table rather than raising.

### `load_project(json_path: str) -> None`

- **Arguments**: `json_path` (str) — path to `project_knowledge.json`.
- **Returns**: `None`.
- **Responsibility**: Loads the JSON file into memory and initializes the shared module-level state (`qa_tools.project_data`, `qa_tools.base_dir`) that the tool functions (`read_source_file`, `get_files_using`, `graph_search`) depend on, since those tools are called from within the sandboxed interpreter and need pre-populated globals rather than parameters.
- **Design decision**: State is injected by directly assigning to `qa_tools` module attributes rather than passing objects through function calls, because the sandbox-exposed tool functions have fixed signatures dictated by their use as RLM tools/LLM-callable functions.
- **Constraint**: Must be called before any `qa_tools` tool function is invoked; `base_dir` is derived from `json_path`'s directory and is used later to resolve relative source file paths.

### `create_interpreter() -> PythonInterpreter`

- **Arguments**: None.
- **Returns**: `PythonInterpreter` — configured with an explicit Deno command line.
- **Responsibility**: Builds a `PythonInterpreter` (Pyodide-in-Deno sandbox) with flags compatible with Deno 2.x, since the default `PythonInterpreter` construction does not account for the `--node-modules-dir=false` requirement and read-permission scoping needed here.
- **Design decisions**:
  - `runner_path` is located via `inspect.getfile(PythonInterpreter)` to find the `runner.js` shipped alongside the installed `dspy` package, avoiding a hardcoded path that would break across environments/versions.
  - The Deno cache directory (`DENO_DIR`) is resolved with a fallback chain: existing `DENO_DIR` env var → `deno info --json` output → default `~/.cache/deno`. This ensures `--allow-read` can be scoped precisely to the runner script and Deno's own cache, rather than granting broader filesystem read access.
  - `DENO_DIR` is written back into `os.environ` so that the actual `deno` subprocess invoked by `PythonInterpreter` uses the same resolved directory that was granted read permission.
- **Edge case**: If `deno info --json` fails or `deno` is not on `PATH` (`FileNotFoundError`), the failure is silently absorbed and the hardcoded default cache path is used instead.

### `create_qa_agent(json_path: str) -> dspy.RLM`

- **Arguments**: `json_path` (str) — path to `project_knowledge.json`.
- **Returns**: `dspy.RLM` — a fully configured, ready-to-query agent instance.
- **Responsibility**: Single entry point that wires together all pieces required for the Q&A agent: loading data, constructing the main/sub LMs, generating instructions, building the signature, creating the sandbox interpreter, and registering tool functions — so callers only need one function call to obtain a usable agent.
- **Design decisions**:
  - The `dspy.Signature` is built dynamically as `"project_data, question -> answer"` with instructions text assembled by string substitution, keeping the schema/instructions data-driven (via `build_doc_schema`) instead of static.
  - Only three tool functions (`read_source_file`, `get_files_using`, `graph_search`) are exposed to the RLM tool interface, complementing direct JSON manipulation of `project_data` that the sandboxed code performs itself.
  - The main LM is set via `rlm.set_lm(lm)` after construction rather than passed at construction time, reflecting `dspy.RLM`'s API for configuring its primary LM separately from the `sub_lm` used inside the sandbox.
- **Constraint**: Depends on `qa_tools.project_data` being loaded as a global before RLM tool calls occur; this is guaranteed by calling `load_project` first within this function.

### `ask(rlm: dspy.RLM, question: str) -> str`

- **Arguments**: `rlm` (`dspy.RLM`) — the configured agent; `question` (str) — the user's natural-language question.
- **Returns**: `str` — the answer text extracted from the RLM prediction's `answer` field.
- **Responsibility**: Provides a minimal, uniform calling convention for invoking the agent, always passing the currently loaded `qa_tools.project_data` alongside the question, so callers don't need to know about the signature's field names.
- **Constraint**: Assumes `qa_tools.project_data` has already been populated (via `load_project`/`create_qa_agent`); otherwise the call would pass `None` as `project_data`.

### `main() -> None`

- **Arguments**: None (reads module-level `TARGET_JSON_PATH`).
- **Returns**: `None`.
- **Responsibility**: Provides an interactive CLI loop for exploratory Q&A sessions against the loaded project knowledge base, serving as the script's runnable entry point.
- **Design decisions**:
  - Validates that the target JSON file exists up front and exits with a non-zero status and an actionable message if not, avoiding a confusing failure deeper in `create_qa_agent`.
  - Wraps the interactive loop in `try`/`finally` so that `rlm._interpreter.shutdown()` is always called on exit (including on `KeyboardInterrupt` or the `exit`/`quit`/`q` commands), ensuring the underlying Deno subprocess is terminated rather than left running.
  - Accepts case-insensitive exit commands (`exit`, `quit`, `q`) and silently skips empty input, prioritizing a forgiving interactive UX.
- **Edge case**: Directly accesses the private attribute `rlm._interpreter` to perform shutdown, since `dspy.RLM` does not expose a public shutdown method for the interpreter it owns.

# Dependency Description

### Dependencies (what this file uses)

This file depends on `examples/rlm_qa/qa_tools.py` for shared state and tool functions used by the RLM Q&A agent:

- **`qa_tools.project_data`**: A module-level global that holds the loaded JSON project knowledge data. This file sets it in `load_project()` after reading `project_knowledge.json`, and later reads it in `create_qa_agent()` (to build the doc schema) and in `ask()` (to pass as input to the RLM call).
- **`qa_tools.base_dir`**: A module-level global holding the directory containing the JSON file. It is set in `load_project()` so that `qa_tools.read_source_file()` can resolve relative source file paths.
- **`qa_tools.read_source_file`**: Passed as a callable tool to `dspy.RLM` so the agent can read the full content of a source file when it needs more context than the JSON snippets provide.
- **`qa_tools.get_files_using`**: Passed as a callable tool to `dspy.RLM` so the agent can find which files depend on a given target file.
- **`qa_tools.graph_search`**: Passed as a callable tool to `dspy.RLM` so the agent can perform a BFS over function/class definitions and their usage relationships to explore dependency graphs.

The dependency direction is unidirectional: `rlm_qa_agent.py` depends on `qa_tools.py`, both by importing its tool functions and by writing into its module-level globals (`project_data`, `base_dir`) to share state with those tool functions at runtime.

### Dependents (what uses this file)

No dependent information available.

# Data Flow

## Input Data Format and Source

| Source | Format | Description |
|---|---|---|
| `TARGET_JSON_PATH` (constant) | file path (str) | Points to `project_knowledge.json` generated by `main.py` |
| `project_knowledge.json` (file) | JSON | Loaded via `json.load()` into a Python `dict` (`qa_tools.project_data`) |
| `LLM_API_KEY` (env var) | str | API key for both main LM and sub-LM |
| Interactive `input()` | str | User's natural language question |
| `qa_tools` module (external file) | Python module | Provides tool functions and shared globals (`project_data`, `base_dir`) |

## Main Transformation Flow

```
TARGET_JSON_PATH
      │
      ▼
load_project(json_path)
  - json.load(file) → qa_tools.project_data (dict)
  - os.path.dirname(json_path) → qa_tools.base_dir
      │
      ▼
build_doc_schema(project_data)
  - scans project_data["files"][*]["doc"]["sections"]
  - builds a Markdown table string (id/title) → doc_schema
      │
      ▼
instructions = INSTRUCTIONS_TEMPLATE
  - "<<<DOC_SCHEMA>>>"           → replaced with doc_schema
  - "<<<RLM_OUTPUT_LANGUAGE>>>"  → replaced with OUTPUT_LANGUAGE
      │
      ▼
dspy.Signature("project_data, question -> answer", instructions)
      │
      ▼
create_interpreter()
  - locates runner.js, resolves Deno cache dir (DENO_DIR)
  - builds deno_command list
  - returns PythonInterpreter(deno_command=...)
      │
      ▼
dspy.RLM(signature, tools=[qa_tools.read_source_file,
                           qa_tools.get_files_using,
                           qa_tools.graph_search],
         sub_lm=sub_lm, interpreter=interpreter)
  - rlm.set_lm(lm)
      │
      ▼
ask(rlm, question)
  - rlm(project_data=qa_tools.project_data, question=question)
      - RLM sandbox executes Python code manipulating project_data
      - can call qa_tools.* tool functions for source lookup / graph traversal
      - LLM (lm) drives code generation & reasoning; sub_lm used internally for sub-queries
      │
      ▼
result.answer (str) → returned from ask() → printed to console
```

## Output Data Format and Destination

| Output | Format | Destination |
|---|---|---|
| `result.answer` | str (natural language, in `OUTPUT_LANGUAGE`) | Returned by `ask()`, printed via `print()` in the interactive loop |
| Console logs (`[OK] Loaded ...`) | str | stdout, informational only |
| `rlm._interpreter.shutdown()` | side effect | Terminates the Deno sandbox process on exit |

## Key Data Structures

### `qa_tools.project_data` (dict, loaded from JSON)
| Field | Type | Purpose |
|---|---|---|
| `project_name` | str | Project identifier |
| `project_dependencies` | array | File-level dependency graph (`file`, `summary`, `callers`, `callees`) |
| `files` | array | Per-file `file_dependencies` (definitions, callee/caller usages) and `doc` (summary + sections) |

### `instructions` (str)
Built by string substitution into `INSTRUCTIONS_TEMPLATE`; embeds the dynamically generated `doc_schema` table and `OUTPUT_LANGUAGE`, then passed to `dspy.Signature` to steer the RLM's code-generation and answering behavior.

### `deno_command` (list of str)
Constructed in `create_interpreter()`; configures the Deno sandbox invocation (`runner_path`, `--allow-read` scope including `deno_dir`) used by `PythonInterpreter` to execute RLM-generated Python code safely.

### `rlm` (dspy.RLM instance)
Holds `signature`, `tools`, `sub_lm`, `interpreter`, and the main `lm` (set via `set_lm`). Acts as the callable agent: consumes `project_data` + `question`, produces `answer`.

# Error Handling

**Overall strategy:** This file adopts a mixed approach — fail-fast for setup/prerequisite checks (missing JSON file, missing interpreter) combined with graceful degradation delegated to `qa_tools` for data-access errors during the interactive Q&A loop. Top-level orchestration code favors explicit, early termination on unrecoverable conditions, while the RLM sandbox execution itself relies on caller-facing tool functions that return error strings/dicts rather than raising, so the LLM agent can react to failures within its own reasoning loop.

| Error type | Handling | Impact |
|---|---|---|
| `project_knowledge.json` not found (`main()`) | Checked explicitly before initialization; prints a guidance message instructing the user to run `main.py` first | Process exits immediately via `sys.exit(1)`; agent is never created |
| Deno cache directory (`DENO_DIR`) not resolvable via env var | Falls back to querying `deno info --json`; if that also fails (`FileNotFoundError` caught), falls back further to a hardcoded default path (`~/.cache/deno`) | No crash; interpreter creation proceeds with a best-effort cache directory |
| `subprocess.run(["deno", ...])` failure or non-zero return code | Return code and stdout are checked; on failure the exception is swallowed only for `FileNotFoundError`, other subprocess outcomes simply skip the JSON parse and move to fallback | No fail-fast; silently degrades to fallback deno dir logic |
| Errors during RLM/tool execution inside the sandbox (e.g. file read errors in `read_source_file`, missing definitions in `graph_search`) | Not handled in this file; delegated entirely to `qa_tools`, which returns descriptive error strings/dicts instead of raising | The RLM agent receives the error as part of its data/tool output and can decide how to proceed or report it to the user; the outer loop is unaffected |
| `KeyboardInterrupt` during interactive `input()` loop | Caught explicitly inside the `while True` loop | Loop breaks cleanly, proceeding to the `finally` block instead of crashing with a traceback |
| Any exception during the interactive session (implicit, via `finally`) | `PythonInterpreter.shutdown()` is called in a `finally` block guarded by a `None` check on `rlm._interpreter` | Ensures the Deno subprocess is terminated even if the loop exits abnormally, preventing orphaned sandbox processes |

**Design considerations:**
- Responsibility for error handling is deliberately split by layer: this file handles *process-level* concerns (missing prerequisites, resource cleanup, user interrupt), while `qa_tools` handles *data-level* concerns (missing files, malformed lookups) using non-throwing return values suitable for LLM tool consumption.
- Resource cleanup (shutting down the Deno-based `PythonInterpreter`) is treated as a hard requirement, enforced through `finally` regardless of how the interactive loop terminates.
- Setup-phase failures (missing JSON, unavailable Deno info) are treated as unrecoverable and handled by either aborting the program or falling back to reasonable defaults, rather than raising further up the stack.

# Summary

`rlm_qa_agent.py` is the CLI entry point wiring `dspy.RLM`, a sandboxed Deno/Pyodide `PythonInterpreter`, and `qa_tools.py` into an interactive Q&A tool over `project_knowledge.json`. Key functions: `build_doc_schema` (derives doc-section schema), `load_project` (populates `qa_tools.project_data`/`base_dir` globals), `create_interpreter` (configures sandbox), `create_qa_agent` (assembles RLM with tools/LMs), `ask` (runs a query), `main` (REPL with fail-fast setup checks and guaranteed interpreter shutdown). Depends unidirectionally on `qa_tools` for shared state and tool functions.
