# Design Document: examples/rlm_qa/qa_tools.py

# Design Specification

**Overview**

Provides the query functions the QA agent calls to inspect a single project's pre-computed code analysis (definitions, dependencies, and generated documentation) file-by-file rather than loading the whole analysis into the agent's context.

- Call `get_file_detail` when the agent has narrowed its focus to a specific file and needs that file's definitions, callee/caller usages, and design-doc summary/sections.
- Call `read_source_file` when the agent needs the actual source text of a file (e.g. to slice out a function body using line numbers returned by `get_file_detail`).
- Call `search_text` when the agent has a keyword and needs to find which files' summaries, doc sections, definitions, or usages mention it.
- Call `get_files_using` when the agent needs to know which files depend on a given file (reverse dependency lookup).
- Call `graph_search` when the agent needs a bounded-hop dependency graph (callers and/or callees) starting from a named definition.

This file relies on the module-level `store` object (a `KnowledgeStore`), which every function queries via `store.entry`, `store.iter_entries`, `store.find_definitions`, `store.project_name`, and `store.base_dir`; it performs no other project-internal calls. It is used by `rlm_qa_agent.py`, which sets `qa_tools.store` via `load_project()`, reads `store.project_name` and `store.dependencies()` to build the initial project data payload, and registers `get_file_detail`, `search_text`, `read_source_file`, `get_files_using`, and `graph_search` as the tool set passed to `dspy.RLM`.

The `store` variable is a module-level global set externally rather than passed as an argument, so each tool function can be used directly as a standalone callable by the agent framework; every function checks for an uninitialized store and returns an error string/dict/list instead of raising, and `search_text` and `graph_search` cache per-file lookups (via a hit limit and an `entry_cache`, respectively) to bound how much of the store is scanned or re-read in one call.

**Definitions**

## `store`
Module-level variable holding the active `KnowledgeStore` instance, set externally by `load_project()` (in `rlm_qa_agent.py`) before any tool function is called; every function in this file reads it to access `project_name`, `base_dir`, `entry()`, `iter_entries()`, and `find_definitions()`. All functions treat a `None` value as "not initialized" and return an error instead of querying it.

## `SEARCH_HIT_LIMIT`
Constant defining the default cap (40) on how many hits `search_text` collects before it stops scanning the project, used to bound the cost of an unbounded keyword search across all files.

## `read_source_file`
Reads and returns the raw text of a source file that was copied into the analysis output directory, given the file path as it appears in the project's `file` field; it strips a leading `project_name/` prefix before joining with `store.base_dir`. Used after `get_file_detail` to fetch a definition's actual code by slicing lines between `start_line` and `end_line`; returns an error string (not an exception) on read failure.

## `get_file_detail`
Looks up one file's full analysis entry via `store.entry(file)` and returns its `file_dependencies` (`definitions`, `callee_usages`, `caller_usages`) and its `doc` (`summary`, `sections`). Intended for files the agent has already narrowed down to, since it exposes detail not present in the project-wide dependency list; returns `{"error": ...}` if the file is not found in the project.

## `search_text`
Performs a case-insensitive keyword search across every file's doc summary, doc sections, definition source (`context`), callee usage target context, and caller usage context, using `store.iter_entries()`. Returns a list of hit dicts tagged by `kind` ("summary", "section", "definition", "callee", "caller") plus the file and matched name, stopping early once `limit` hits are collected; used when the agent needs to locate where a term (e.g. a function name, error message, or concept like "retry") appears anywhere in the project.

## `add`
Internal helper closure inside `search_text` that appends one hit dict to the running `hits` list and reports whether the `limit` has been reached, letting `search_text` short-circuit its nested loops as soon as the cap is hit.

## `get_files_using`
Scans every file's `callee_usages` via `store.iter_entries()` and collects entries whose `from` field contains `target_file` as a substring, returning each match paired with the consuming file's path. Used to find dependents (files that use a given file) by partial path match, complementing `graph_search`'s definition-level, exact-name traversal.

## `graph_search`
Performs a breadth-first search over the project's definitions treated as a dependency graph, starting from a definition found by exact match (`store.find_definitions(name)`) or, if absent, partial match, and expanding up to `hops` steps in the `outgoing` (callee), `incoming` (caller), or `both` direction. For each visited definition it determines outgoing edges by checking whether a callee usage's line numbers fall inside the current definition's line range (falling back to a synthetic `"__module__"` node for usages outside any definition), and incoming edges by matching caller usages whose `name` equals the current definition and mapping their `lines` back to the enclosing definition in the source file; returns discovered `nodes` (each tagged with `hop` and `via`) and `edges`. Used to trace how a specific function or class is connected to the rest of the codebase, and caches per-file `file_dependencies` lookups (`entry_cache`) for the duration of one call to avoid re-reading the same file's entry multiple times.

## `deps_of`
Internal helper inside `graph_search` that returns a file's `file_dependencies`, populating and reusing `entry_cache` so each file touched during the BFS is read from `store.entry` at most once per `graph_search` call.

# Summary

This module exposes file-scoped query tools for a QA agent to explore a project's precomputed code analysis without loading it all into context. Main functions: read_source_file, get_file_detail, search_text (with helper add), get_files_using, graph_search (with helper deps_of); uses module-level `store` (KnowledgeStore). Key concepts: definitions, dependencies, callee/caller usages, documentation sections, keyword search, reverse dependency lookup, BFS graph traversal, caching (hit limits, entry_cache), graceful error handling instead of exceptions.
