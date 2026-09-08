# Design Document: codetwine/llm/client.py

# Design Specification

**Overview**

Wraps litellm's async completion API to send a single prompt and return the generated text with retry-on-rate-limit handling.

- Another module needs to turn a prompt into LLM-generated text: instantiate `LLMClient` and call `generate`, which returns the completion text or `None` on failure.
- The doc-generation pipeline needs to summarize code or callee usages via an LLM: it holds an `LLMClient` instance and calls `generate` (or indirectly `_call_with_retry`) for each prompt.
- The CLI entrypoint needs to conditionally enable LLM-based documentation: it constructs `LLMClient()` once (only when `ENABLE_LLM_DOC` is true) and passes it through the pipeline.
- A caller needs to bound LLM output length: pass a custom `max_tokens` to `generate` instead of relying on the `DOC_MAX_TOKENS` default.

This file relies on `codetwine/config/settings.py` for its default configuration values (`LLM_MODEL`, `LLM_API_KEY`, `LLM_API_BASE`, `MAX_RETRIES`, `RETRY_WAIT`, `DOC_MAX_TOKENS`), which govern which model/endpoint to call and how retries and output size are bounded. It is used by `main.py` (constructs the single `LLMClient` instance for the run), `codetwine/doc_creator.py` (uses it to summarize code blocks and callee usage contexts), and `codetwine/pipeline.py` (threads the client through `process_all_files` as an optional dependency).

Design decisions: rate-limit errors (`litellm.RateLimitError`) trigger a fixed-wait retry loop up to `MAX_RETRIES` attempts, returning `None` if retries are exhausted; `ContextWindowExceededError` is deliberately re-raised (not swallowed) so callers can handle prompt-too-large cases distinctly; other `openai.APIError` failures are logged and immediately return `None` without retrying; truncated responses (`finish_reason == "length"`) are logged as warnings but still returned rather than treated as failures.

**Definitions**

## `LLMClient`

Async wrapper class around litellm's chat completion API for a single configured model/endpoint. It validates at construction time that a model name is present (raising `ValueError` if `LLM_MODEL`/the passed `model` is empty), and stores the model, API key, and API base used for every subsequent call. Developers instantiate this once (e.g., in `main.py`) and reuse it across multiple `generate` calls, such as during per-file documentation generation in the pipeline.

## `LLMClient.__init__`

Sets up the client's `model`, `api_key`, and `api_base` attributes, defaulting to `LLM_MODEL`, `LLM_API_KEY`, and `LLM_API_BASE` from settings. Raises `ValueError` immediately if no model is configured, preventing later calls from silently failing due to missing configuration.

## `LLMClient._call_with_retry`

Performs the actual `litellm.acompletion` call with the configured model, optional `api_key`/`api_base`, and a single user message containing the prompt, looping up to `MAX_RETRIES` times. On `litellm.RateLimitError` it sleeps `RETRY_WAIT` seconds and retries, logging a warning each time, and returns `None` after exhausting retries (logged as an error); on `ContextWindowExceededError` it re-raises without retrying so the caller can react to context-length overflow; on `openai.APIError` it logs the error and returns `None` without retrying. On success it logs a warning if `finish_reason == "length"` (indicating truncation at `max_tokens`) and returns the stripped completion text from `response.choices[0].message.content`.

## `LLMClient.generate`

Public entry point for turning a prompt into LLM output; returns `None` immediately if the prompt is empty, otherwise delegates to `_call_with_retry` with the given or default (`DOC_MAX_TOKENS`) token limit. This is the method external callers (`doc_creator.py`, `pipeline.py`) use to obtain generated summaries or documentation text.

# Summary

Provides an async `LLMClient` wrapper around litellm's chat completion API for generating text from a single prompt. Public members: `LLMClient`, `__init__`, `_call_with_retry`, `generate`. Handles model/API-key/API-base configuration (from settings), retry-on-rate-limit logic with fixed wait and max attempts, re-raising of context-window-exceeded errors, graceful failure (returns None) on other API errors, truncation warnings, and token-limit control via `max_tokens`/`DOC_MAX_TOKENS`. Used by doc_creator.py, pipeline.py, and main.py for LLM-based documentation generation.
