# Design Document: examples/rlm_qa/rlm_qa_agent.py

# Design Specification

**Overview**

Orchestrate interactive Q&A against a codebase by combining a DSPy RLM agent with a knowledge store and specialized query tools.

This file is used when:
- A developer calls `create_qa_agent()` to initialize an RLM-based agent configured with project knowledge, LLM settings, and sandboxed code execution capabilities.
- A developer calls `ask()` to pose a question and receive an answer synthesized by the agent from project analysis data.
- A developer runs `main()` to enter an interactive loop where questions are answered until the session terminates.

The file depends on `knowledge_store.py` to open and query either JSON or SQLite knowledge files, retrieving the project dependency graph and per-file summaries that populate the `project_data` dictionary passed to the RLM sandbox. It depends on `qa_tools.py` to provide five query functions (`get_file_detail`, `search_text`, `read_source_file`, `get_files_using`, `graph_search`) that the RLM agent invokes during reasoning to access definitions, source code, design documents, and dependency information. No dependent files are documented as using this module.

The instructions template embeds dynamic content—the doc section schema and output language—to guide the agent's reasoning. The `PythonInterpreter` is configured with Deno-specific flags (`--node-modules-dir=false`, `--allow-read`) and automatic `DENO_DIR` detection to support sandboxed Python execution. The design passes only the file graph and summaries to the sandbox (in `project_data`), keeping detailed definitions and source code behind the host-side tools to manage sandbox size and enforce access patterns.

**Definitions**

## `LLM_MODEL`

The litellm-format model identifier used by the main dspy.LM instance that orchestrates the agent's reasoning loop. Examples include "anthropic/claude-sonnet-5" or "openai/gpt-4".

## `SUB_LLM_MODEL`

The litellm-format model identifier used by the sub_lm instance within the RLM sandbox for any LLM calls made during tool invocation and code execution. Allows independent configuration from the main orchestrating LLM.

## `LLM_API_KEY`

The API key string sourced from the LLM_API_KEY environment variable, passed to dspy.LM initialization for authentication with the configured LLM provider.

## `LLM_API_BASE`

An optional API base URL for non-standard LLM endpoints such as Ollama or Azure, set to None by default to use the provider's standard endpoint.

## `OUTPUT_LANGUAGE`

The natural language string (e.g., "English") used to instruct the agent to format all answers in the specified language.

## `TARGET_KNOWLEDGE_PATH`

The file path to the knowledge file, either project_knowledge.json or project_knowledge.sqlite, which is loaded at startup to populate the agent's knowledge base.

## `project_data`

A module-level dictionary holding the loaded project information: project_name and project_dependencies (the file graph with summaries). This dictionary is passed as an input variable to the RLM sandbox and remains accessible throughout the agent's lifetime.

## `INSTRUCTIONS_TEMPLATE`

A template string that guides the RLM agent's behavior during reasoning, defining investigation rules, schema documentation, and code examples. The template includes placeholder markers (`<<<DOC_SCHEMA>>>`, `<<<RLM_OUTPUT_LANGUAGE>>>`) that are replaced with dynamic content before the Signature is instantiated.

## `build_doc_schema`

Extracts the design document section list from the first file in the knowledge store that contains sections and returns a formatted markdown table listing each section's id and title. This table is embedded into the instructions template to document the doc schema available to the agent.

## `load_project`

Opens a knowledge file (JSON or SQLite) via `knowledge_store.open_store()`, assigns the resulting store to `qa_tools.store` for use by query functions, and builds the project_data dictionary containing project_name and the project dependency graph. Returns the project_data dict after printing confirmation of the file count loaded.

## `create_interpreter`

Instantiates a dspy PythonInterpreter configured with Deno 2.x runtime parameters: automatically detects the DENO_DIR from deno info or environment, constructs a deno run command with --node-modules-dir=false and appropriate --allow-read permissions, and returns the configured interpreter for sandboxed code execution.

## `create_qa_agent`

Loads the knowledge file, initializes main and sub LLMs with the configured API credentials, builds dynamic instructions by embedding doc schema and output language into the template, constructs a dspy Signature for the project_data-and-question-to-answer task, creates the PythonInterpreter, and assembles a dspy.RLM agent with the five query tools. Sets the main LLM at module level and returns the fully initialized RLM instance.

## `ask`

Invokes the RLM agent with the current project_data and a user question, returning the answer text from the prediction result. This is the primary entry point for obtaining answers after the agent is initialized.

## `main`

Implements the interactive Q&A loop: validates that the knowledge file exists, initializes the RLM agent, and repeatedly prompts the user for questions (supporting exit commands: exit, quit, q). For each non-empty question, calls ask() and prints the answer. Ensures the interpreter's Deno process is shut down when the session terminates.

# Summary

# Summary

Orchestrates interactive Q&A against a codebase using a DSPy RLM agent augmented with project knowledge and specialized query tools. Loads knowledge files (JSON or SQLite) to populate a project dependency graph and file summaries, then initializes an agent configured with LLM credentials, dynamic instructions, and sandboxed code execution via Deno. Primary public functions—`create_qa_agent()`, `ask()`, and `main()`—enable agent initialization, single-question answering, and interactive Q&A loops. Delegates file access to five tools (`get_file_detail`, `search_text`, `read_source_file`, `get_files_using`, `graph_search`) that query project data without loading it into the sandbox, maintaining isolation and controlling information flow.
