# Design Document: codetwine/llm/client.py

## Overview & Purpose

## 1. Module Summary

Provides an async LLM API wrapper that sends a prompt to a configured language model via litellm and returns the generated text, with built-in retry logic for rate-limit errors.

## 2. When to Use This Module

- **Instantiate `LLMClient`** when LLM-based documentation generation is enabled (`ENABLE_LLM_DOC`); pass the instance to pipeline and document-creation functions (`process_all_files`, `generate_file_doc`).
- **Call `LLMClient.generate(prompt)`** from `doc_creator.py` to obtain generated text for a documentation section, supplying a completed prompt string and an optional token limit.
- **Pass `None` instead of `LLMClient`** when LLM generation is disabled; the pipeline accepts `LLMClient | None` and skips generation accordingly.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `LLMClient.__init__` | `model (str)`, `api_key (str)`, `api_base (str)` | `None` | Initializes the client with model name, API key, and endpoint URL; raises `ValueError` if model is not set. |
| `async LLMClient.generate` | `prompt (str)`, `max_tokens (int)` | `str \| None` | Sends the prompt to the LLM and returns generated text, or `None` if generation failed or prompt is empty. |

## 4. Design Decisions

- **Retry only on rate-limit errors**: `litellm.RateLimitError` triggers up to `MAX_RETRIES` attempts with `RETRY_WAIT`-second delays, while `openai.APIError` fails immediately without retry, distinguishing transient throttling from unrecoverable API failures.
- **Re-raise `ContextWindowExceededError`**: This exception is intentionally propagated to the caller rather than swallowed, allowing upstream code (e.g., `doc_creator.py`) to implement its own progressive fallback strategy.
- **Optional kwargs construction**: `api_key` and `api_base` are only added to the litellm call when non-empty, supporting both hosted providers (key only) and custom endpoints (key + base URL) without passing empty strings.
- **`generate` as the sole public entry point**: The retry mechanism is encapsulated in the private `_call_with_retry`, keeping the public surface minimal and the retry logic hidden from callers.

## Definition Design Specifications

---

## `LLMClient`

**Signature:** `class LLMClient`

**Responsibility:** Wraps `litellm.acompletion` to provide a unified, async interface for invoking an LLM with configurable retry logic. Centralizes API credential handling, model selection, and error recovery so callers only need to supply a prompt.

**When to use:** Instantiate once per application run when LLM-based documentation generation is enabled; pass the instance to pipeline and document-creation functions.

---

### `__init__`

**Signature:**
```
__init__(
    self,
    model: str = LLM_MODEL,
    api_key: str = LLM_API_KEY,
    api_base: str = LLM_API_BASE,
) -> None
```

| Parameter | Type | Purpose |
|-----------|------|---------|
| `model` | `str` | litellm-format model identifier (e.g. `"openai/gpt-4o"`). Must not be empty. |
| `api_key` | `str` | Provider API key. Empty string disables key injection. |
| `api_base` | `str` | Custom base URL. Empty string disables base-URL override. |

**Responsibility:** Validates that a model name is present and stores credentials as instance state.

**Constraints & edge cases:**
- Raises `ValueError` if `model` is falsy (empty string or `None`). All defaults come from `settings.py`; if no environment variable is configured, `LLM_MODEL` defaults to `""`, which will always trigger this error.
- `api_key` and `api_base` are optional; they are only forwarded to `litellm` when non-empty.

---

### `_call_with_retry` (async)

**Signature:**
```
async _call_with_retry(self, prompt: str, max_tokens: int) -> str | None
```
- Return type `str | None`: the generated text string, or `None` if all retry attempts fail or a non-retryable error occurs.

**Responsibility:** Executes the `litellm.acompletion` call and handles transient rate-limit errors by waiting and retrying, while propagating or suppressing other error types according to their recoverability.

**Design decisions:**

| Error type | Behavior |
|---|---|
| `litellm.RateLimitError` | Retried up to `MAX_RETRIES` times with `RETRY_WAIT`-second async sleeps between attempts. Returns `None` after exhausting retries. |
| `ContextWindowExceededError` | Re-raised immediately without retrying, so callers can apply fallback strategies (e.g., truncation). |
| `openai.APIError` | Logged and returns `None` immediately; no retry is attempted. |

- `api_key` and `api_base` are conditionally added to the `litellm.acompletion` kwargs only when non-empty, to avoid sending empty-string overrides to the provider.
- Retry loop runs sequentially; each attempt awaits the previous one before deciding whether to retry.
- The `asyncio.sleep` call is awaited, meaning the coroutine yields control during the wait rather than blocking the event loop.

**Constraints & edge cases:**
- Total attempts are bounded by `MAX_RETRIES` (default `3`).
- On the final retry attempt, a `RateLimitError` results in `None` return (no further sleep).
- `ContextWindowExceededError` bypasses all retry logic entirely; callers must handle it.

---

### `generate` (async)

**Signature:**
```
async generate(self, prompt: str, max_tokens: int = DOC_MAX_TOKENS) -> str | None
```

| Parameter | Type | Purpose |
|-----------|------|---------|
| `prompt` | `str` | The complete prompt text to send to the model. |
| `max_tokens` | `int` | Maximum tokens in the model response. Defaults to `DOC_MAX_TOKENS` (default `8192`). |

- Return type `str | None`: generated text on success, `None` on failure or empty input.

**Responsibility:** Acts as the public entry point for text generation; guards against empty prompts and delegates all API interaction to `_call_with_retry`.

**When to use:** Called by document-creation and pipeline functions whenever a prompt is ready to be submitted to the LLM.

**Constraints & edge cases:**
- Returns `None` immediately—without making any API call—when `prompt` is falsy (empty string, `None`).
- All error handling and retry logic resides in `_call_with_retry`; `generate` itself adds no additional error handling.

## Dependency Description

### Dependencies (modules this file imports)

**`codetwine/llm/client_py/client.py` → `codetwine/config/settings.py`** : Retrieves all runtime configuration constants required to construct and operate the LLM client.

Specific symbols consumed and their purposes:
- `LLM_MODEL` — used as the default value for the `model` parameter in `__init__`, identifying which LLM to target via litellm.
- `LLM_API_KEY` — used as the default value for the `api_key` parameter in `__init__`, authenticating requests to the provider.
- `LLM_API_BASE` — used as the default value for the `api_base` parameter in `__init__`, pointing to a custom API endpoint when required.
- `MAX_RETRIES` — controls the upper bound of the retry loop in `_call_with_retry`, determining how many times a rate-limited call is reattempted.
- `RETRY_WAIT` — defines the number of seconds to sleep between retry attempts when a rate limit error is encountered.
- `DOC_MAX_TOKENS` — serves as the default value for the `max_tokens` parameter in `generate`, capping the length of the LLM's output.

---

### Dependents (modules that import this file)

**`main.py` → `codetwine/llm/client_py/client.py`** : Instantiates `LLMClient` (conditionally, when `ENABLE_LLM_DOC` is truthy) and passes the resulting instance to the pipeline entry point `process_all_files`.

**`codetwine/pipeline.py` → `codetwine/llm/client_py/client.py`** : Accepts `LLMClient | None` as a typed parameter in `process_all_files`, propagating the client through the file-processing pipeline to enable optional LLM-based document generation.

**`codetwine/doc_creator.py` → `codetwine/llm/client_py/client.py`** : Receives `LLMClient` as a required typed parameter in document-generation functions, using it to invoke the LLM for producing per-section design content with progressive fallback logic and for generating full per-file design documents.

---

### Dependency Direction

| Relationship | Direction |
|---|---|
| `client.py` → `codetwine/config/settings.py` | **Unidirectional** — `client.py` consumes configuration constants from `settings.py`; `settings.py` has no knowledge of `client.py`. |
| `main.py` → `client.py` | **Unidirectional** — `main.py` imports and instantiates `LLMClient`; `client.py` has no knowledge of `main.py`. |
| `codetwine/pipeline.py` → `client.py` | **Unidirectional** — `pipeline.py` references `LLMClient` as a type and consumer; `client.py` has no knowledge of `pipeline.py`. |
| `codetwine/doc_creator.py` → `client.py` | **Unidirectional** — `doc_creator.py` calls `LLMClient` methods to drive generation; `client.py` has no knowledge of `doc_creator.py`. |

## Data Flow

## 1. Inputs

| Source | Data | Format |
|--------|------|--------|
| Constructor arguments | `model`, `api_key`, `api_base` | `str`, defaulting to `LLM_MODEL`, `LLM_API_KEY`, `LLM_API_BASE` from settings |
| `generate()` argument | `prompt` | `str` |
| `generate()` argument | `max_tokens` | `int`, defaulting to `DOC_MAX_TOKENS` (default 8192) |
| Config constants | `MAX_RETRIES`, `RETRY_WAIT` | `int` (defaults: 3 and 2 respectively), read at module load time from `codetwine/config/settings.py` |

## 2. Transformation Overview

```
prompt (str) + max_tokens (int)
        │
        ▼
[generate()]
  Early-exit if prompt is empty → return None
        │
        ▼
[_call_with_retry()]
  Build kwargs dict:
    Always:  model, max_tokens, messages
    Optional: api_key (if truthy), api_base (if truthy)
        │
        ▼
  litellm.acompletion(**kwargs)   ← async HTTP call
        │
   ┌────┴──────────────────────────┐
   │ Success                       │ Exception
   ▼                               ▼
response.choices[0]          RateLimitError → sleep(RETRY_WAIT),
  .message.content.strip()         retry up to MAX_RETRIES times;
  → return str                     on exhaustion → return None
                               ContextWindowExceededError → re-raise
                               openai.APIError → log error, return None
```

**Async / retry fan-out:** `_call_with_retry` runs a `for` loop up to `MAX_RETRIES` iterations. Each iteration issues one `await litellm.acompletion(...)` call. On `RateLimitError` (and while attempts remain), the coroutine suspends for `RETRY_WAIT` seconds via `await asyncio.sleep()` before re-entering the loop. On `ContextWindowExceededError`, the exception propagates immediately to the caller without retry. On `openai.APIError`, the method returns `None` immediately without retry.

## 3. Outputs

| Output | Type | Condition |
|--------|------|-----------|
| Generated text | `str` | Successful API call; stripped of leading/trailing whitespace |
| `None` | `None` | Empty prompt passed to `generate()`; all retries exhausted on rate limit; `openai.APIError` encountered |
| `ContextWindowExceededError` (re-raised) | exception | Input prompt exceeds the model's context window |
| Log warnings / errors | side effect | Emitted via `logger` on rate limit retries and final failures |

## 4. Key Data Structures

### `kwargs` — API call parameter dict

Built dynamically inside `_call_with_retry` before each `litellm.acompletion` call.

| Field / Key | Type | Purpose |
|-------------|------|---------|
| `model` | `str` | Model name in litellm format; used by litellm to auto-detect the provider |
| `max_tokens` | `int` | Maximum number of tokens the LLM may produce in its response |
| `messages` | `list[dict]` | Conversation turns sent to the API; always contains a single user-role entry |
| `api_key` | `str` (optional) | Provider authentication key; included only when `self.api_key` is truthy |
| `api_base` | `str` (optional) | Custom endpoint base URL; included only when `self.api_base` is truthy |

### `messages` — list element schema

| Field / Key | Type | Purpose |
|-------------|------|---------|
| `role` | `str` | Always `"user"` in this module |
| `content` | `str` | The prompt string passed to `generate()` |

## Error Handling

## 1. Overall Strategy

`LLMClient` applies a **retry-with-graceful-degradation** policy. The client attempts the LLM API call up to `MAX_RETRIES` times for recoverable transient errors (rate limiting), while failing immediately and returning `None` for non-transient API errors. The sole exception to the return-`None` fallback is `ContextWindowExceededError`, which is re-raised to the caller without any retry, allowing upstream code to react to it explicitly. Throughout all paths, errors are recorded via the standard `logging` module rather than surfaced as unhandled exceptions (except where explicitly re-raised).

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `litellm.RateLimitError` | The LLM provider returns a 429 rate-limit response | Waits `RETRY_WAIT` seconds and retries; logs a warning on each intermediate attempt and an error when `MAX_RETRIES` is exhausted | Yes (up to `MAX_RETRIES` attempts) | Returns `None` after all retries are consumed |
| `ContextWindowExceededError` | The prompt or token count exceeds the model's context window | Immediately re-raised to the caller with no retry and no logging | No (propagates upward) | Exception propagates to the calling layer |
| `openai.APIError` | Any non-rate-limit API-level error from the provider | Logs an error message and returns immediately without retry | No (single attempt only) | Returns `None` on first occurrence |
| Missing `LLM_MODEL` | `model` is an empty string at construction time | Raises `ValueError` immediately in `__init__` | No (initialization fails) | Object cannot be constructed |
| Empty `prompt` | `generate()` is called with a falsy prompt string | Returns `None` immediately without calling the API | N/A (guard clause) | No API call is made |

---

## 3. Design Notes

- **Selective retry scope**: Retry logic is intentionally limited to `RateLimitError` only. This reflects the judgment that rate limiting is a transient, time-dependent condition, whereas other API errors (e.g., authentication failure, malformed request) are deterministic and would not benefit from retrying.

- **Re-raise for context overflow**: `ContextWindowExceededError` is explicitly caught and re-raised rather than swallowed. This signals that the error carries semantic information (the input is too large) that the caller—`doc_creator.py`—needs to act on (e.g., progressive fallback with a shorter context), making it a contract boundary rather than an internal fault.

- **Fail-fast on construction**: The `ValueError` guard on an empty `model` name surfaces misconfiguration at object creation time rather than at the first API call, avoiding deferred failures deep in an async pipeline.

- **`None` as the failure sentinel**: Rather than propagating exceptions for non-critical failures, the `generate()` interface uses `None` as its canonical "no result" return value. This allows callers to treat LLM generation as an optional enrichment step and continue processing without it, consistent with how `LLMClient` is used optionally (`LLMClient | None`) in `pipeline.py` and `main.py`.

## Summary

**codetwine/llm/client.py** — Async LLM API wrapper using litellm with retry logic.

**Class:** `LLMClient(model: str, api_key: str, api_base: str)`
**Methods:**
- `generate(prompt: str, max_tokens: int) → str | None`
- `_call_with_retry(prompt: str, max_tokens: int) → str | None`

**Key structures:**
- `kwargs` dict: `model`, `max_tokens`, `messages`, optional `api_key`/`api_base`
- `messages`: `list[dict]` with `role="user"` and `content=prompt`

Defaults sourced from settings: `LLM_MODEL`, `LLM_API_KEY`, `LLM_API_BASE`, `MAX_RETRIES`, `RETRY_WAIT`, `DOC_MAX_TOKENS`.
