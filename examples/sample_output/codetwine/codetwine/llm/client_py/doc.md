# Design Document: codetwine/llm/client.py

# Overview & Purpose

## 1. Module Summary
Provides an async wrapper around litellm's completion API that sends a prompt to a configured LLM and returns the generated text, applying retry logic for rate-limit errors.

## 2. When to Use This Module
- When a component needs to send a prompt to an LLM and obtain generated text (e.g., code summarization or documentation generation): instantiate `LLMClient` and call `await generate(prompt, max_tokens)`.
- When the caller wants LLM calls to be resilient to transient rate-limit (429) errors without implementing retry logic itself: use `LLMClient.generate`, which internally retries up to `MAX_RETRIES` times with `RETRY_WAIT` seconds between attempts.
- When the caller needs to be notified explicitly if the prompt exceeds the model's context window (e.g., to trigger chunking or fallback logic upstream): call `LLMClient.generate`, which propagates `ContextWindowExceededError` instead of swallowing it.
- When constructing an LLM-backed pipeline component (as done in `main.py` and `codetwine/pipeline.py`) that conditionally enables LLM-based documentation: instantiate `LLMClient()` using default settings from `codetwine/config/settings.py`.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `LLMClient` (class) | `model: str = LLM_MODEL`, `api_key: str = LLM_API_KEY`, `api_base: str = LLM_API_BASE` | — | Holds LLM connection configuration (model name, API key, API base URL) and validates that a model is set. |
| `async LLMClient.generate` | `prompt: str`, `max_tokens: int = DOC_MAX_TOKENS` | `str \| None` | Sends a prompt to the LLM with retry-aware handling and returns the generated text, or `None` if the prompt is empty or generation fails. |

## 4. Design Decisions
- Differentiated error handling strategy: `litellm.RateLimitError` triggers bounded retries with a fixed wait (`RETRY_WAIT`), `ContextWindowExceededError` is deliberately re-raised (not retried or swallowed) so callers can handle context-length issues explicitly, and other `openai.APIError` failures fail fast by returning `None` without retry.
- Optional parameters (`api_key`, `api_base`) are only included in the request payload when explicitly set, relying on litellm/provider defaults otherwise.

# Definition Design Specifications

## `class LLMClient`

**Responsibility**: Provides an async wrapper around `litellm.acompletion` to send prompts to an LLM provider (OpenAI-compatible) with configurable model/credentials and built-in retry logic for rate-limiting.

**When to use**: Instantiated once by callers (e.g., `main.py`, `codetwine/pipeline.py`, `codetwine/doc_creator.py`) that need to generate LLM-based text (such as code summaries or documentation) from a prompt string.

**Design decisions**:
- Delegates provider-specific behavior entirely to `litellm`, relying on the model name prefix to auto-detect the provider rather than implementing per-provider logic.
- Optional parameters (`api_key`, `api_base`) are only added to the request kwargs if truthy, allowing default provider behavior when unset.

**Constraints & edge cases**:
- Raises `ValueError` at construction time if `model` is falsy (empty string), preventing usage without a configured model.
- Relies on module-level defaults (`LLM_MODEL`, `LLM_API_KEY`, `LLM_API_BASE`) from settings if no explicit constructor arguments are passed.

### `__init__(self, model: str = LLM_MODEL, api_key: str = LLM_API_KEY, api_base: str = LLM_API_BASE) -> None`

**Responsibility**: Validates and stores the model name, API key, and API base URL used for all subsequent LLM calls made by this instance.

**When to use**: Called implicitly when creating an `LLMClient()` instance, typically once per application run.

**Constraints & edge cases**:
- If `model` is empty/`None`, raises `ValueError` with guidance to set `LLM_MODEL` via `.env` or shell environment.
- `api_key` and `api_base` are not validated; empty values are accepted and simply omitted from API call kwargs later.

| Attribute | Type | Purpose |
|---|---|---|
| `model` | `str` | Model identifier in litellm format, determines provider routing |
| `api_key` | `str` | Provider API key, used only if non-empty |
| `api_base` | `str` | Custom endpoint base URL, used only if non-empty |

---

### `async def _call_with_retry(self, prompt: str, max_tokens: int) -> str | None`

**Signature explanation**: Returns either the generated response text (`str`) or `None` if generation failed after retries or due to a non-retryable error.

**Responsibility**: Executes the actual LLM API call via `litellm.acompletion`, handling rate-limit retries and distinguishing recoverable vs. non-recoverable errors.

**When to use**: Called internally by `generate()`; not intended to be invoked directly by external callers.

**Design decisions**:
- Retries only on `litellm.RateLimitError`, waiting `RETRY_WAIT` seconds between attempts, up to `MAX_RETRIES` total attempts.
- Explicitly re-raises `ContextWindowExceededError` instead of catching it, allowing callers to handle context-length failures differently (e.g., by chunking input) rather than silently returning `None`.
- Catches `openai.APIError` and fails immediately without retry, treating it as a non-transient error.
- Builds the `kwargs` dict conditionally to omit `api_key`/`api_base` when not configured, rather than passing empty strings to litellm.

**Constraints & edge cases**:
- If all `MAX_RETRIES` attempts are exhausted due to rate limiting, logs an error and returns `None` (does not raise).
- On `openai.APIError`, returns `None` immediately without exhausting retries.
- Assumes `response.choices[0].message.content` is a non-`None` string (calls `.strip()` on it without null-checking); would raise an `AttributeError` if the API returns no content.
- If the loop completes without hitting any `return`/`raise` in the try/except branches, the function implicitly returns `None` (Python default), though this path is only reachable if an attempt neither raises nor returns, which is not expected given the code structure.

**Concurrency semantics**: Async function; awaits `litellm.acompletion` for the network call and `asyncio.sleep` during rate-limit backoff. Calls are sequential (attempts happen one at a time within the retry loop, not in parallel).

---

### `async def generate(self, prompt: str, max_tokens: int = DOC_MAX_TOKENS) -> str | None`

**Signature explanation**: Returns generated text as `str`, or `None` if the prompt is empty or generation failed.

**Responsibility**: Serves as the public entry point for text generation, guarding against empty prompts before delegating to the retry-enabled API call.

**When to use**: Called by external modules (`doc_creator.py`) whenever a prompt needs to be sent to the LLM to produce a summary or generated text, using either the default `DOC_MAX_TOKENS` limit or a caller-specified token budget.

**Design decisions**: Short-circuits with `None` for falsy `prompt` input rather than making an API call, avoiding unnecessary requests.

**Constraints & edge cases**:
- Does not catch `ContextWindowExceededError` raised by `_call_with_retry`; it propagates to the caller, meaning callers of `generate` must handle this exception themselves.
- `max_tokens` is passed through without validation (e.g., no check for negative or zero values).

**Concurrency semantics**: Async function; awaits `_call_with_retry`, which internally performs sequential (non-parallel) network calls/retries.

# Dependency Description

### Dependencies (modules this file imports)

- `codetwine/llm/client.py` → `codetwine/config/settings.py` (`LLM_MODEL`) : obtains the default model name used to initialize `LLMClient`, and validates that it is set before constructing the client.
- `codetwine/llm/client.py` → `codetwine/config/settings.py` (`LLM_API_KEY`) : obtains the default provider API key used to authenticate calls to the LLM API.
- `codetwine/llm/client.py` → `codetwine/config/settings.py` (`LLM_API_BASE`) : obtains the default base URL for custom/self-hosted LLM API endpoints.
- `codetwine/llm/client.py` → `codetwine/config/settings.py` (`MAX_RETRIES`) : determines how many times to retry an LLM API call after rate-limit errors before giving up.
- `codetwine/llm/client.py` → `codetwine/config/settings.py` (`RETRY_WAIT`) : determines how many seconds to wait between retry attempts on rate-limit errors.
- `codetwine/llm/client.py` → `codetwine/config/settings.py` (`DOC_MAX_TOKENS`) : provides the default maximum output token limit used when generating text via `generate()`.

### Dependents (modules that import this file)

- `main.py` → `codetwine/llm/client.py` (`LLMClient`) : instantiates `LLMClient` (conditioned on `ENABLE_LLM_DOC`) and passes it into `process_all_files` to enable LLM-based documentation generation for the project.
- `codetwine/doc_creator.py` → `codetwine/llm/client.py` (`LLMClient`) : receives an `LLMClient` instance as a parameter in `_summarize_code` and `_summarize_callee_usages` to send code/context to the LLM and obtain concise summaries.
- `codetwine/pipeline.py` → `codetwine/llm/client.py` (`LLMClient`) : accepts an `LLMClient | None` instance in `process_all_files` to drive LLM-based document generation across all analyzed files.

### Dependency Direction

All relationships are unidirectional. `codetwine/llm/client.py` depends on `codetwine/config/settings.py` for configuration values, while `main.py`, `codetwine/doc_creator.py`, and `codetwine/pipeline.py` depend on `codetwine/llm/client.py` for LLM access via `LLMClient`. There is no reverse dependency in either direction (settings.py does not depend on client.py, and client.py does not depend on its dependents).

# Data Flow

## 1. Inputs

`LLMClient` receives data from two sources:

- **Constructor arguments** (with config-driven defaults):
  - `model: str` — defaults to `LLM_MODEL` (string; raises `ValueError` if empty)
  - `api_key: str` — defaults to `LLM_API_KEY` (string, may be empty)
  - `api_base: str` — defaults to `LLM_API_BASE` (string, may be empty)
- **Method call arguments**:
  - `prompt: str` — the text to send to the LLM, passed by callers (e.g., `doc_creator.py`)
  - `max_tokens: int` — defaults to `DOC_MAX_TOKENS` from config
- **Config constants** consumed internally during retry logic: `MAX_RETRIES` (int), `RETRY_WAIT` (int, seconds)

## 2. Transformation Overview

1. **Initialization**: `__init__` validates `model` is non-empty, then stores `model`, `api_key`, and `api_base` as instance state.
2. **Entry validation (`generate`)**: An incoming `prompt` is checked; if falsy (`None`/empty string), the method short-circuits and returns `None` immediately without calling the API.
3. **Request assembly (`_call_with_retry`)**: For a valid prompt, a `kwargs` dict is built containing `model`, `max_tokens`, and a `messages` list with a single user-role message wrapping `prompt`. `api_key`/`api_base` are conditionally added only if truthy.
4. **Retry loop**: The assembled kwargs are passed to `litellm.acompletion` (async network call) inside a loop bounded by `MAX_RETRIES`:
   - On success: the response object's `choices[0].message.content` is extracted and `.strip()`-ed into the final output string, and the loop exits via return.
   - On `litellm.RateLimitError`: if attempts remain, the coroutine sleeps `RETRY_WAIT` seconds (`asyncio.sleep`) and loops again, re-sending the same request; if it's the last attempt, `None` is returned after logging an error.
   - On `ContextWindowExceededError`: the exception is re-raised immediately (not swallowed), propagating to the caller.
   - On `openai.APIError`: no retry occurs; the error is logged and `None` is returned immediately.
5. **Result propagation**: The extracted/stripped text (or `None`) from `_call_with_retry` is returned as-is by `generate` to the calling code.

There is no fan-out/fan-in within this file itself — each `generate` call is a single async request-response cycle with sequential (not parallel) retries. Concurrency across multiple prompts (if any) is managed by external callers (e.g., `pipeline.py`), not by this module.

## 3. Outputs

- **Return value of `generate`**: `str | None` — either the stripped text content of the LLM's response, or `None` if:
  - the input prompt was empty/falsy,
  - rate limiting exhausted all retries,
  - an `openai.APIError` occurred.
- **Raised exception**: `ContextWindowExceededError` may propagate out of `generate`/`_call_with_retry` to the caller (not caught/converted to `None`).
- **Side effects**: Log messages via `logger.warning` (rate limit retry notice) and `logger.error` (rate limit exhaustion, API error) — no file writes or other persistent side effects occur in this module.
- **Network calls**: An outbound async API request is made per attempt via `litellm.acompletion`.

## 4. Key Data Structures

### `kwargs` (request payload dict, built in `_call_with_retry`)

| Field / Key | Type | Purpose |
|---|---|---|
| `model` | `str` | Model identifier passed to litellm, determines provider routing |
| `max_tokens` | `int` | Caps the length of the generated output |
| `messages` | `list[dict]` | Conversation payload; single entry `{"role": "user", "content": prompt}` |
| `api_key` (optional) | `str` | Provider API key, included only if `self.api_key` is truthy |
| `api_base` (optional) | `str` | Custom endpoint URL, included only if `self.api_base` is truthy |

### `messages` entry (dict inside `kwargs["messages"]`)

| Field / Key | Type | Purpose |
|---|---|---|
| `role` | `str` | Fixed to `"user"`, denotes the message sender role for the chat API |
| `content` | `str` | The raw `prompt` text supplied by the caller |

### `LLMClient` instance state

| Field | Type | Purpose |
|---|---|---|
| `model` | `str` | LLM model identifier used for every API call |
| `api_key` | `str` | Credential used to authenticate with the LLM provider |
| `api_base` | `str` | Custom base URL for API requests (empty means default provider endpoint) |

# Error Handling

## 1. Overall Strategy

`LLMClient` applies a mixed strategy depending on the error's nature: **retry with fallback** for transient/rate-limit failures, **fail-fast (re-raise)** for context-length errors that the caller must handle, and **log-and-return-None (graceful degradation)** for non-recoverable API errors. Additionally, at construction time the class enforces **fail-fast validation** for missing configuration. Overall, the client is designed so that a single generation failure never crashes the calling pipeline (it returns `None` instead), except for the context-window overflow case, which is deliberately propagated upward for higher-level handling.

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `ValueError` (missing model) | `model` argument is empty/falsy at `LLMClient.__init__` | Immediately raises `ValueError` with a guidance message | No | Object construction fails; caller cannot use the client at all |
| `litellm.RateLimitError` | LLM API responds with HTTP 429 (rate limit exceeded) during `acompletion` call | Logs a warning, waits `RETRY_WAIT` seconds, and retries up to `MAX_RETRIES` attempts | Yes (retried); becomes No after exhausting retries | Temporary delay in generation; returns `None` if all retries fail, allowing caller to continue without a result |
| `ContextWindowExceededError` | Prompt/token count exceeds the model's context window | Immediately re-raised without logging or retry | No (within this method) | Propagates to caller, who is responsible for handling oversized-input scenarios (e.g., truncation/splitting) |
| `openai.APIError` | Any other OpenAI-compatible API error (e.g., invalid request, server error) not covered by the above | Logs the error message and returns `None` immediately (no retry) | No | Generation fails for this call; caller receives `None` and continues |
| Empty prompt | `prompt` argument is empty/falsy in `generate()` | Returns `None` immediately without calling the API | Yes (treated as a no-op, not a failure) | No API call made; caller receives `None` as if generation failed |

## 3. Design Notes

- Rate-limit errors are treated as transient and given a bounded retry budget (`MAX_RETRIES`, `RETRY_WAIT`), reflecting the assumption that such errors often resolve after a short wait.
- `ContextWindowExceededError` is intentionally not retried or converted to `None`; it is re-raised so that upstream logic (e.g., document creation or summarization pipelines) can decide on remediation such as reducing input size, rather than the client silently failing.
- General `openai.APIError` is treated as non-transient (e.g., malformed request, auth issue) and is not retried, favoring quick failure with logging over wasted retry attempts.
- Returning `None` (rather than raising) for recoverable failure paths allows calling code (e.g., `doc_creator.py`, `pipeline.py`) to continue processing other files/units even when a single LLM call fails, supporting a log-and-continue behavior at the pipeline level while the client itself remains a thin, predictable interface.
- Configuration validation (`model` presence) is fail-fast at construction time, ensuring misconfiguration is caught early rather than surfacing as a runtime API error deep inside the retry logic.

# Summary

LLMClient: async wrapper around litellm for sending prompts to an LLM and returning generated text with rate-limit retry logic.

- `LLMClient(model: str, api_key: str, api_base: str)`
- `async generate(prompt: str, max_tokens: int) -> str | None`

Consumes settings (LLM_MODEL, LLM_API_KEY, LLM_API_BASE, MAX_RETRIES, RETRY_WAIT, DOC_MAX_TOKENS). Builds `kwargs` dict (model, max_tokens, messages: list[dict]) for litellm.acompletion; returns stripped response text or None.
