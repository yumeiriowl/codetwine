# Design Document: examples/rlm_qa/rlm_qa_agent.py

# Overview & Purpose

### 1. Module Summary
Bootstraps and runs an interactive `dspy.RLM`-based Q&A agent that lets a user ask natural-language questions about a codetwine project knowledge file, answered by having the LLM manipulate a lightweight project graph and call `qa_tools` functions inside a sandboxed Python interpreter.

### 2. When to Use This Module
- **Running interactive Q&A on a generated knowledge file**: Run this file as a script (`uv run python examples/rlm_qa/rlm_qa_agent.py`); `main()` loads `TARGET_KNOWLEDGE_PATH` (a `project_knowledge.json`/`.sqlite` produced by `main.py`) and starts a REPL loop that prints answers.
- **Embedding the Q&A agent in another script/tool**: Call `create_qa_agent(knowledge_path)` to obtain a configured `dspy.RLM` instance, then call `ask(rlm, question)` to get an `answer` string for a given question, without going through the interactive `main()` loop.
- **Loading knowledge data for the RLM sandbox independently**: Call `load_project(knowledge_path)` to open the knowledge store (via `knowledge_store.open_store`), wire it into `qa_tools.store`, and get back the `project_data` dict (`project_name` + `project_dependencies`) that is later passed into the RLM signature.
- **Customizing the sandbox execution environment**: Call `create_interpreter()` to obtain a `PythonInterpreter` preconfigured with the correct Deno flags (`--node-modules-dir=false`, `--allow-read`) when the default interpreter setup needs to be reused elsewhere or replaced.
- **Building the schema portion of agent instructions dynamically**: Call `build_doc_schema(store)` to generate a Markdown table of `doc.sections` (id/title) discovered from the knowledge store, for embedding into the RLM instructions template.

### 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `LLM_MODEL` | — (str constant) | — | Primary LLM model identifier (litellm format) used by `dspy.LM` for the RLM agent. |
| `SUB_LLM_MODEL` | — (str constant) | — | Sub-LLM model identifier used for `llm_query`/`llm_query_batched` calls inside the RLM sandbox. |
| `LLM_API_KEY` | — (str constant) | — | API key read from the `LLM_API_KEY` environment variable. |
| `LLM_API_BASE` | — (str \| None constant) | — | Optional custom API base URL for the LLM provider. |
| `OUTPUT_LANGUAGE` | — (str constant) | — | Natural language in which the RLM must write its answers. |
| `TARGET_KNOWLEDGE_PATH` | — (str constant) | — | Default filesystem path to the `project_knowledge.json`/`.sqlite` file consumed by the agent. |
| `project_data` | — (dict, module-level global) | — | Holds the currently loaded project graph (`project_name`, `project_dependencies`) passed to the RLM signature at query time. |
| `INSTRUCTIONS_TEMPLATE` | — (str constant) | — | Template text for the RLM signature's instructions, with `<<<DOC_SCHEMA>>>` and `<<<RLM_OUTPUT_LANGUAGE>>>` placeholders. |
| `build_doc_schema` | `store` (`knowledge_store.Store`) | `str` | Generates a Markdown table of `doc.sections` (id/title) found in the store, for embedding into agent instructions. |
| `load_project` | `knowledge_path` (str) | `dict` | Opens the knowledge store, assigns it to `qa_tools.store`, and builds the `project_data` dict (`project_name`, `project_dependencies`). |
| `create_interpreter` | — | `PythonInterpreter` | Builds a `PythonInterpreter` configured with the Deno command (`--node-modules-dir=false`, `--allow-read=<runner_path>,<deno_dir>`) needed to run the sandbox under Deno 2.x. |
| `create_qa_agent` | `knowledge_path` (str) | `dspy.RLM` | Loads project data, builds instructions from the template and doc schema, constructs the `dspy.Signature`, `PythonInterpreter`, and assembles a fully configured `dspy.RLM` (with `qa_tools` functions registered as tools and both LMs set). |
| `ask` | `rlm` (`dspy.RLM`), `question` (str) | `str` | Invokes the RLM with the current `project_data` and the given question, returning the `answer` field of the result. |
| `main` | — | `None` | Entry point: validates the knowledge file exists, builds the agent via `create_qa_agent`, runs an interactive question/answer REPL, and shuts down the interpreter on exit. |

### 4. Design Decisions
- **Two-tier data exposure**: The module deliberately keeps `project_data` (handed to the RLM sandbox) limited to the file dependency graph and one summary per file, while detailed information (source code, definitions, full design docs) stays server-side and is only reachable through the `qa_tools` tool functions (`get_file_detail`, `search_text`, `read_source_file`, `get_files_using`, `graph_search`). This avoids flooding the LLM context with the full knowledge file and enforces on-demand, verifiable lookups instead of answering from a bulk dump.
- **Template-based instruction assembly**: `INSTRUCTIONS_TEMPLATE` uses simple string placeholders (`<<<DOC_SCHEMA>>>`, `<<<RLM_OUTPUT_LANGUAGE>>>`) replaced via `.replace()` rather than a templating engine, keeping instruction generation dependency-free and explicit.
- **Sandbox/host separation via a global `store`**: `load_project` assigns the opened store to the module-level `qa_tools.store`, allowing tool functions (executed inside the sandboxed interpreter's calls) to access host-side knowledge without serializing the entire store into the sandbox.
- **Explicit Deno interpreter configuration**: `create_interpreter` manually resolves `runner.js`'s location and the Deno cache directory (via `DENO_DIR` env var or `deno info --json`) to construct a restrictive `--allow-read` Deno command, rather than relying on `PythonInterpreter`'s default sandbox settings, to support Deno 2.x compatibility.

# Definition Design Specifications

## Module-level constants

| Name | Type | Purpose |
|---|---|---|
| `LLM_MODEL` | `str` | Model identifier (litellm format) used by the main `dspy.LM` instance driving `dspy.RLM`. |
| `SUB_LLM_MODEL` | `str` | Model identifier used for the `sub_lm` argument of `dspy.RLM`, employed by `llm_query`/`llm_query_batched` tools inside the sandbox. |
| `LLM_API_KEY` | `str` | API key read from the `LLM_API_KEY` environment variable; empty string if unset. |
| `LLM_API_BASE` | `str \| None` | Optional custom API base URL (e.g., for Ollama/Azure); `None` uses the provider default. |
| `OUTPUT_LANGUAGE` | `str` | Natural language name embedded into the instructions template, forcing answers to be written in this language. |
| `TARGET_KNOWLEDGE_PATH` | `str` | Default filesystem path (relative to this file's directory) to `project_knowledge.json`. |
| `project_data` | `dict` | Module-level, mutable global holding the data passed to the RLM sandbox; populated by `load_project()` and reused by `ask()`. Initialized to `{}`. |
| `INSTRUCTIONS_TEMPLATE` | `str` | Multi-line prompt template for the `dspy.Signature` given to `dspy.RLM`. Contains placeholder tokens `<<<DOC_SCHEMA>>>` and `<<<RLM_OUTPUT_LANGUAGE>>>` that are substituted via `str.replace()` before use. Documents the `project_data` schema, tool usage rules, and code examples for the agent's Python sandbox. |

**Design decisions:**
- Configuration is expressed as plain module-level variables rather than a config object/CLI args, keeping the example editable by hand ("modify to match your environment").
- `project_data` is a global rather than being threaded through function signatures, because `ask()` needs to supply it to `rlm(...)` without the caller passing it explicitly each time.
- The instructions template uses literal placeholder tokens (`<<<...>>>`) with `.replace()` rather than an f-string/format call, avoiding accidental interpretation of the many literal `{`/`}`-like Markdown/code content in the template (none present, but also avoids conflicts with any future braces in embedded code examples).

---

## `build_doc_schema(store: knowledge_store.Store) -> str`

**Responsibility:** Dynamically produces a Markdown table of `doc.sections` `id`/`title` pairs for the current project, so the instructions given to the RLM reflect the actual document schema rather than a hardcoded one.

**When to use:** Called once during `create_qa_agent()` after the knowledge store is opened, to build the `<<<DOC_SCHEMA>>>` fragment of the instructions.

**Design decisions:**
- Scans `store.iter_entries()` and stops at the **first** entry whose `doc.sections` is non-empty, assuming section structure is uniform across files in a project (rather than aggregating all distinct sections across the whole project).
- Falls back to an empty `sections` list (and thus a header-only table) if no entry has sections, rather than raising an error.

**Constraints & edge cases:**
- Assumes each section dict has `id` and `title` keys; a `KeyError` would propagate if the schema differs.
- Does not deduplicate or validate that different files share the same section schema.

---

## `load_project(knowledge_path: str) -> dict`

**Responsibility:** Opens the knowledge file via `knowledge_store.open_store`, wires the resulting store into the shared `qa_tools.store` global (so tool functions can access it), and builds the lightweight `project_data` dict that is safe to hand to the sandboxed interpreter.

**When to use:** Called once per agent creation, from `create_qa_agent()`, before instructions/signature/interpreter are built.

**Design decisions:**
- Sets `qa_tools.store` as a side effect (module-level global mutation) rather than returning the store, since `qa_tools`'s tool functions read that module attribute directly and cannot receive it as a parameter (they are exposed as RLM tools with fixed signatures).
- Deliberately keeps `project_data` minimal — only `project_name`, and per-file `{file, summary, callers, callees}` via `store.dependencies()` — excluding definitions/source/docs, which must instead be fetched on demand through `qa_tools` functions run on the host side. This is required because the sandbox output is truncated/expensive; large content must not enter `project_data`.

**Constraints & edge cases:**
- Prints a load-confirmation line as a side effect (`[OK] Loaded N files...`); not just a pure function.
- Assumes `knowledge_path` exists and is a valid store; error handling (nonexistent path) is done by the caller (`main()`), not here.

---

## `create_interpreter() -> PythonInterpreter`

**Responsibility:** Constructs a `PythonInterpreter` (Deno/Pyodide sandbox wrapper from `dspy.primitives.python_interpreter`) with an explicit Deno command line tuned for Deno 2.x compatibility (`--node-modules-dir=false`) and restricted filesystem read access (`--allow-read`).

**When to use:** Called once from `create_qa_agent()` when assembling the `dspy.RLM` instance; needed because the default interpreter configuration is not compatible with Deno 2.x without these flags.

**Design decisions:**
- Locates `runner.js` by introspecting the installed `dspy` package via `inspect.getfile(PythonInterpreter)` and taking its directory, rather than hardcoding a path — keeps the example working across different `dspy` install locations.
- Resolves the Deno cache directory (`DENO_DIR`) with a fallback chain: (1) existing `DENO_DIR` env var, (2) `deno info --json` subprocess output's `denoDir` field, (3) hardcoded `~/.cache/deno`. This avoids requiring the user to manually configure Deno's cache location while still allowing `--allow-read` to be scoped narrowly instead of granting broad filesystem access.
- Explicitly sets `os.environ["DENO_DIR"]` after resolution, ensuring consistency between the subprocess's `--allow-read` scope and the actual cache directory subsequently used by Deno.
- `subprocess.run(..., check=False)` combined with an explicit `FileNotFoundError` catch handles the case where the `deno` binary is not on `PATH`, falling through to the default cache path instead of crashing.

**Constraints & edge cases:**
- If `deno info --json` fails (non-zero exit) or its JSON lacks `denoDir`, silently falls back to the default path — no error is surfaced to the caller.
- Depends on `PythonInterpreter` internals (`runner.js` co-located with the installed module) — a private/internal assumption about the `dspy` package layout.

---

## `create_qa_agent(knowledge_path: str) -> dspy.RLM`

**Responsibility:** Top-level factory that wires together all pieces (knowledge store, LLMs, instructions, interpreter, tools) into a single configured `dspy.RLM` agent ready to answer questions.

**When to use:** Called once at startup (from `main()`) with the resolved knowledge file path, before entering the interactive loop.

**Design decisions:**
- Mutates the module-level global `project_data` via `global project_data` rather than returning it alongside the agent, keeping the `ask()`/`rlm(...)` call site simple (no need to pass `project_data` around manually) at the cost of implicit global state.
- Instantiates two separate `dspy.LM` objects (`lm` and `sub_lm`) from possibly-identical model names/config, matching `dspy.RLM`'s API which distinguishes the primary reasoning LM (set later via `set_lm`) from the sub-LM used by in-sandbox tool calls (`llm_query`/`llm_query_batched`).
- Builds `instructions` by chained `.replace()` calls on `INSTRUCTIONS_TEMPLATE`, injecting `doc_schema` and `OUTPUT_LANGUAGE` — a simple, dependency-free templating approach appropriate for a two-variable substitution.
- Registers exactly five tool functions from `qa_tools` (`get_file_detail`, `search_text`, `read_source_file`, `get_files_using`, `graph_search`) as the RLM's available tools — a deliberate, fixed capability surface exposed to the sandboxed LLM code.
- Calls `rlm.set_lm(lm)` **after** construction rather than passing `lm` to the `dspy.RLM(...)` constructor, following the `dspy.RLM` API convention of separating agent assembly from LM binding.

**Constraints & edge cases:**
- Depends on `qa_tools.store` having been set as a side effect of `load_project()` — if `create_qa_agent()`'s internals were reordered, tool calls could see `store is None`.
- No validation that `LLM_API_KEY` is non-empty; an empty key will fail at LLM call time rather than fail fast here.

---

## `ask(rlm: dspy.RLM, question: str) -> str`

**Responsibility:** Thin wrapper invoking the RLM agent with the current `project_data` and a user-supplied `question`, returning just the `answer` field of the result.

**When to use:** Called for every user question in the interactive loop in `main()`.

**Design decisions:**
- Reads `project_data` from the module-level global rather than accepting it as a parameter, coupling this function to `create_qa_agent()`/`load_project()` having already populated it.

**Constraints & edge cases:**
- Will raise/behave incorrectly if called before `create_qa_agent()` (i.e., while `project_data` is still `{}` and `rlm` is not yet configured) — no internal guard against this.
- Assumes `result.answer` always exists on the `dspy.RLM` call result (per the two-field signature `project_data, question -> answer`).

---

## `main() -> None`

**Responsibility:** Entry point for interactive command-line Q&A: validates the knowledge file exists, initializes the agent, runs a read-question/print-answer loop, and ensures the sandbox process is shut down on exit.

**When to use:** Invoked when the script is run directly (`if __name__ == "__main__":`).

**Design decisions:**
- Validates `knowledge_path` existence up front and calls `sys.exit(1)` with a user-facing hint ("Run uv run python main.py first.") rather than letting a downstream `open_store` error surface, giving a clearer failure mode for the most likely misconfiguration.
- Uses a `try/finally` around the entire interactive loop so that `rlm._interpreter.shutdown()` is always invoked, even on `KeyboardInterrupt` or other exceptions escaping the loop — accesses the "private" `_interpreter` attribute directly since `dspy.RLM` exposes no public shutdown method here.
- Treats `KeyboardInterrupt` inside the loop as a normal loop-exit signal (breaks) rather than propagating it, allowing Ctrl-C to cleanly reach the `finally` cleanup.
- Recognizes `"exit"`, `"quit"`, `"q"` (case-insensitive) as loop-terminating commands; blank/whitespace-only input is silently skipped (`continue`) without invoking the agent.

**Constraints & edge cases:**
- Assumes `rlm._interpreter` is not `None` after `create_qa_agent()` succeeds (always true given `create_interpreter()` is unconditionally called), but still explicitly checks `is not None` before calling `shutdown()` defensively.
- No exception handling around `ask(rlm, question)` itself — an LLM/tool error during a question would propagate out of the loop (and still trigger the `finally` cleanup, ending the session).

# Dependency Description

### Dependencies (modules this file imports)

- `examples/rlm_qa/rlm_qa_agent.py` → `examples/rlm_qa/knowledge_store.py` (`Store`, `open_store`) : Opens the target knowledge file (JSON or SQLite) via `open_store()` and uses the `Store` type as the return-type annotation for `build_doc_schema()`, giving access to the unified store interface (`project_name`, `dependencies()`, `iter_entries()`, `entry()`, `base_dir`, etc.) needed to build `project_data` and to generate the doc-schema text embedded in the agent's instructions.

- `examples/rlm_qa/rlm_qa_agent.py` → `examples/rlm_qa/qa_tools.py` (`store`, `store.project_name`, `store.dependencies`, `get_file_detail`, `search_text`, `read_source_file`, `get_files_using`, `graph_search`) : Assigns the opened `Store` instance to the module-level `qa_tools.store` so the tool functions can operate on it, reads `store.project_name` and `store.dependencies()` to construct `project_data`, and registers `get_file_detail`, `search_text`, `read_source_file`, `get_files_using`, and `graph_search` as the tool set passed to `dspy.RLM` so the sandboxed agent can fetch file details, search text, read source files, find dependents, and traverse the dependency graph on demand.

### Dependents (modules that import this file)

No dependent information available.

### Dependency Direction

- `rlm_qa_agent.py` → `knowledge_store.py`: Unidirectional. `rlm_qa_agent.py` calls `open_store()` and uses the `Store` type; `knowledge_store.py` has no dependency back on `rlm_qa_agent.py`.
- `rlm_qa_agent.py` → `qa_tools.py`: Unidirectional. `rlm_qa_agent.py` configures `qa_tools.store` and invokes/registers `qa_tools`' functions; `qa_tools.py` does not import or call back into `rlm_qa_agent.py`.

# Data Flow

## 1. Inputs

- **Configuration constants** (module-level, hardcoded): `LLM_MODEL`, `SUB_LLM_MODEL`, `LLM_API_BASE`, `OUTPUT_LANGUAGE`, `TARGET_KNOWLEDGE_PATH` — plain Python strings defined at the top of the file.
- **Environment variable**: `LLM_API_KEY` read via `os.environ.get("LLM_API_KEY", "")` — a string used to authenticate `dspy.LM` instances.
- **Knowledge file path** (`knowledge_path: str`): either a `.json` or `.sqlite` file path, checked for existence with `os.path.exists()` and passed into `knowledge_store.open_store()`.
- **Knowledge store data**: obtained from `qa_tools.store` (a `knowledge_store.Store`, i.e. `JsonStore | SqliteStore`) via `.project_name` (str) and `.dependencies()` (list of dicts with `file`, `summary`, `callers`, `callees`).
- **Doc sections sample**: obtained by iterating `store.iter_entries()` to find the first file entry with a non-empty `doc.sections` list, used only to build instruction text.
- **Deno environment info**: output of `subprocess.run(["deno", "info", "--json"])` (JSON string) or `DENO_DIR` env var, used to locate the Deno cache directory.
- **User question** (interactive loop): a string typed at the `input("> ")` prompt.
- **RLM/LLM outputs**: responses produced internally by `dspy.RLM.__call__()` (not directly inspected by this file except via `result.answer`).

## 2. Transformation Overview

**Stage A — Knowledge loading (`load_project`)**
1. `knowledge_path` string → `knowledge_store.open_store(knowledge_path)` → a `Store` instance, assigned to the module-level `qa_tools.store` (so tool functions can query it later).
2. From the opened store: `store.project_name` (str) and `store.dependencies()` (list of file-graph dicts) are combined into a `project_data` dict with keys `project_name` and `project_dependencies`.
3. A log line is printed summarizing the file count and project name.

**Stage B — Instruction assembly (`build_doc_schema` + `create_qa_agent`)**
1. `build_doc_schema(store)` iterates `store.iter_entries()` until it finds a `doc.sections` list, then converts each section (`id`, `title`) into a Markdown table row, producing a single `section_table` string.
2. `INSTRUCTIONS_TEMPLATE` (a large static string with placeholders `<<<DOC_SCHEMA>>>` and `<<<RLM_OUTPUT_LANGUAGE>>>`) is transformed via `.replace()` twice: the doc-schema table is embedded, and `OUTPUT_LANGUAGE` is embedded, producing the final `instructions` string.

**Stage C — Interpreter construction (`create_interpreter`)**
1. `inspect.getfile(PythonInterpreter)` → directory path → joined with `"runner.js"` to form `runner_path`.
2. Deno cache directory is resolved in priority order: `DENO_DIR` env var → JSON output of `deno info --json` (parsed with `json.loads`, key `denoDir`) → fallback `~/.cache/deno`. The resolved value is written back into `os.environ["DENO_DIR"]`.
3. `runner_path` and `deno_dir` are combined into a `deno_command` list of CLI arguments.
4. `deno_command` → `PythonInterpreter(deno_command=deno_command)` → returned interpreter object.

**Stage D — Agent assembly (`create_qa_agent`)**
1. `LLM_MODEL`/`SUB_LLM_MODEL` strings + `LLM_API_KEY` + `LLM_API_BASE` → two `dspy.LM` instances (`lm`, `sub_lm`).
2. `instructions` string (from Stage B) → `dspy.Signature("project_data, question -> answer", instructions)` → `signature` object.
3. `signature`, the list of `qa_tools` tool functions (`get_file_detail`, `search_text`, `read_source_file`, `get_files_using`, `graph_search`), `sub_lm`, and the `interpreter` (from Stage C) are combined into a `dspy.RLM` instance (`rlm`).
4. `rlm.set_lm(lm)` binds the primary LM to the agent.
5. `create_qa_agent` returns the fully configured `rlm`; as a side effect, the module-level `project_data` global is set (Stage A's output).

**Stage E — Question answering (`ask`)**
1. `question` string + module-level `project_data` dict → passed as keyword arguments into `rlm(project_data=..., question=...)`.
2. Internally, `dspy.RLM` sends `project_data` and `question` to the LM, which generates Python code executed inside the sandboxed `PythonInterpreter` (Deno/Pyodide). That code may call the exposed `qa_tools` functions, which query `qa_tools.store` (opened in Stage A) to fetch definitions, source code, search hits, or graph traversal results — these tool outputs flow back into the sandbox and ultimately into the LM's reasoning.
3. The RLM call returns a `result` object; `result.answer` (str) is extracted and returned by `ask()`.

**Stage F — Interactive loop (`main`)**
1. Existence check on `knowledge_path` → if missing, error message printed and `sys.exit(1)`.
2. `create_qa_agent(knowledge_path)` → `rlm` (Stages A–D executed).
3. Loop: `input()` → `question` string → exit keywords (`exit`/`quit`/`q`) checked, blank input skipped → `ask(rlm, question)` → `answer` string → printed to stdout.
4. On loop termination (break or `KeyboardInterrupt`), `rlm._interpreter.shutdown()` is called as a cleanup side effect (terminates the Deno subprocess).

No async/parallel fan-out occurs in this file itself; concurrency (if any) is internal to `dspy.RLM`/`PythonInterpreter` and not directly observed here.

## 3. Outputs

- **stdout (print statements)**: status messages (`"[OK] Loaded ..."`, `"[OK] Initialization complete"`, `"Loading: ..."`, `"Processing..."`) and the final `answer` string for each question.
- **Return value of `load_project`**: `project_data` dict (`{"project_name": str, "project_dependencies": list[dict]}`).
- **Return value of `build_doc_schema`**: a Markdown-formatted string (doc schema table) embedded into instructions text.
- **Return value of `create_interpreter`**: a configured `PythonInterpreter` instance.
- **Return value of `create_qa_agent`**: a configured `dspy.RLM` instance (`rlm`), with the side effect of setting the module-level `project_data` global and `qa_tools.store`.
- **Return value of `ask`**: the `answer` string (`result.answer`) extracted from the RLM call result.
- **Process exit**: `sys.exit(1)` when the knowledge file is not found.
- **Side effect**: `os.environ["DENO_DIR"]` is set/modified; the Deno subprocess is spawned and later shut down via `rlm._interpreter.shutdown()`.

## 4. Key Data Structures

### `project_data` (dict, module-level global; also passed into `rlm()`)

| Field / Key | Type | Purpose |
|---|---|---|
| `project_name` | `str` | Name of the analyzed project, from `store.project_name` |
| `project_dependencies` | `list[dict]` | File-level dependency graph with per-file summaries |

### `project_dependencies[]` entry (dict)

| Field / Key | Type | Purpose |
|---|---|---|
| `file` | `str` | File path, used as the key argument for tools |
| `summary` | `str \| None` | Summary of the file (null if not generated) |
| `callers` | `list[str]` | Files that depend on this file |
| `callees` | `list[str]` | Files this file depends on |

### `dspy.Signature` inputs/outputs (used to build `rlm`)

| Field / Key | Type | Purpose |
|---|---|---|
| `project_data` | `dict` | Input field carrying the file graph/summaries |
| `question` | `str` | Input field carrying the user's question |
| `answer` | `str` | Output field carrying the generated answer |

### `get_file_detail(file)` return dict (schema documented in instructions, consumed inside sandbox, not directly manipulated by this file's own code)

| Field / Key | Type | Purpose |
|---|---|---|
| `file` | `str` | File path |
| `file_dependencies` | `dict` | Contains `definitions`, `callee_usages`, `caller_usages` lists |
| `doc` | `dict` | Contains `summary` (str) and `sections` (list of `{id, title, content}`) |

### `deno_command` (list, built in `create_interpreter`)

| Field / Key | Type | Purpose |
|---|---|---|
| Element 0 | `str` | `"deno"` executable |
| Element 1 | `str` | `"run"` subcommand |
| Element 2 | `str` | `"--node-modules-dir=false"` flag |
| Element 3 | `str` | `f"--allow-read={runner_path},{deno_dir}"` flag |
| Element 4 | `str` | `runner_path`, path to `runner.js` |

### `result` (return value of `rlm(...)` call in `ask`)

| Field / Key | Type | Purpose |
|---|---|---|
| `answer` | `str` | The generated answer text, extracted and returned by `ask()` |

# Error Handling

### 1. Overall Strategy

This file follows a predominantly **fail-fast** strategy at the top level (`main()`), combined with **best-effort fallback** logic for environment/tooling detection (`create_interpreter()`), and **delegated, silent-failure handling** for the tool layer (errors from `qa_tools` functions are returned as data, not raised, and are left for the RLM agent/LLM to interpret rather than being caught here). There is no retry logic anywhere in this file, and no logging framework is used—diagnostics rely on `print()` statements and propagated exceptions. The interactive loop in `main()` is the only place with a resilience mechanism, and it only guards against user-driven interruption (`KeyboardInterrupt`), not against runtime/model errors.

### 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Missing knowledge file | `TARGET_KNOWLEDGE_PATH` does not exist when `main()` starts | Explicit check via `os.path.exists`; prints an error message instructing the user to run `main.py` first, then calls `sys.exit(1)` | No | Program terminates before any agent/interpreter is created |
| Deno environment detection failure | `subprocess.run(["deno", "info", "--json"])` raises `FileNotFoundError` (Deno not installed/on PATH) in `create_interpreter()` | Caught with a bare `except FileNotFoundError: pass`, falling through to a hardcoded default (`~/.cache/deno`) for `DENO_DIR` | Yes (falls back to default path) | Execution continues; if the fallback path is also wrong, failure is deferred to later Deno invocation |
| Deno info command non-zero exit / malformed JSON | `deno info --json` returns non-zero exit code, or `result.stdout` is not valid JSON | Not caught for JSON parsing errors (`json.loads` failure propagates); non-zero exit is checked (`if result.returncode == 0`) but simply skips using the output, falling to the default path | Partially (only the exit-code case is handled; JSON decode errors are not) | If JSON parsing fails, an unhandled exception propagates and stops agent creation |
| Store/knowledge loading errors | `knowledge_store.open_store()` fails while reading a malformed or incompatible JSON/SQLite knowledge file | Not caught anywhere in this file; exception propagates up through `load_project()` and `create_qa_agent()` | No | Program crashes with an unhandled traceback before the interactive loop starts |
| Tool-level errors during Q&A (missing file, uninitialized store, etc.) | Sandbox code or agent calls a `qa_tools` function (e.g., `get_file_detail`, `search_text`, `graph_search`) for a file or definition that does not exist, or before `store` is initialized | Not handled by this file at all; the called tool function itself returns an error dict/string (e.g., `{"error": ...}`) instead of raising, and this file passes that result straight back into the RLM conversation for the LLM to interpret | Yes (treated as informational data the agent can react to, not a fatal error) | No crash; the agent may retry with a different approach or report the limitation in its answer |
| User interrupts input loop | `KeyboardInterrupt` raised while blocked on `input("> ")` in `main()` | Caught explicitly inside the `while True` loop; breaks out of the loop cleanly | Yes | Interactive session ends gracefully instead of crashing |
| Any other exception during `ask()` / RLM execution | LLM API error, sandbox execution error, network failure, etc., raised while processing a question in the interactive loop | Not caught; no try/except wraps `ask(rlm, question)` | No | Exception propagates out of the loop; the `finally` block still runs to shut down the interpreter, but the interactive session terminates |
| Program exit / interpreter shutdown | Normal loop exit, `KeyboardInterrupt`, or any propagating exception in `main()` | Guarded via `try/finally`: `rlm._interpreter.shutdown()` is always called if the interpreter was created, regardless of exit reason | Yes (cleanup always executes) | Ensures the Deno sandbox process does not remain orphaned, even on abnormal termination |

### 3. Design Notes

- The file draws a clear boundary between **environment/setup errors**, which are fatal and left unhandled or minimally guarded (missing knowledge file, Deno/interpreter creation issues, store loading), and **runtime query errors**, which are intentionally routed as structured data (`{"error": ...}` dicts/strings) into the LLM-driven RLM loop rather than being raised as Python exceptions. This reflects a design where the LLM agent itself is expected to reason about and react to tool-reported errors during investigation, rather than the host code intercepting them.
- The only defensive fallback logic is around Deno environment discovery (`DENO_DIR`), reflecting an assumption that the Deno CLI or its info command may be unavailable or behave inconsistently across environments, while still requiring Deno itself to be installed for the sandbox to function at all.
- Resource cleanup (`interpreter.shutdown()`) is treated as a hard guarantee via `try/finally`, independent of how the interactive loop terminates (normal exit, keyboard interrupt, or unexpected exception), indicating that leaving the Deno subprocess running is considered a more important failure mode to avoid than surfacing detailed error diagnostics to the user.
- There is no use of custom exception types, error codes, or centralized logging; all user-facing failure communication happens through plain `print()` statements or default Python traceback output, consistent with this being a small interactive example script rather than a production service.

# Summary

Bootstraps a `dspy.RLM` Q&A agent answering questions about a project knowledge file via sandboxed tool calls. Key functions: `build_doc_schema(store)->str`, `load_project(knowledge_path:str)->dict`, `create_interpreter()->PythonInterpreter`, `create_qa_agent(knowledge_path:str)->dspy.RLM`, `ask(rlm, question:str)->str`, `main()->None`. Core data: `project_data` dict (`project_name:str`, `project_dependencies:list[dict]` with file/summary/callers/callees), used as RLM signature input alongside `question:str`→`answer:str`.
