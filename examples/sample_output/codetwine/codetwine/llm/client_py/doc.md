# Design Document: codetwine/llm/client.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Wraps the litellm async completion API to send prompts to a configured LLM and return generated text, with built-in rate-limit retry logic.

## 2. When to Use This Module

- **Generating documentation text**: Instantiate `LLMClient()` and call `await client.generate(prompt)` to receive a generated text string from the configured LLM. Used by `codetwine/doc_creator.py` to produce per-file design document sections.
- **Conditional LLM integration**: Instantiate `LLMClient()` only when LLM-based documentation is enabled (e.g., `LLMClient() if ENABLE_LLM_DOC else None` in `main.py`), then pass the instance through the pipeline via `codetwine/pipeline.py`.
- **Custom endpoint or model**: Pass explicit `model`, `api_key`, and `api_base` arguments to `LLMClient(model=..., api_key=..., api_base=...)` to override the defaults sourced from environment configuration.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `LLMClient` | `model: str`, `api_key: str`, `api_base: str` | — | Holds the model name, API key, and base URL used for all completion calls on this instance. Raises `ValueError` if `model` is empty. |
| `async LLMClient.generate` | `prompt: str`, `max_tokens: int` | `str \| None` | Sends the prompt to the LLM and returns the generated text, or `None` if the prompt is empty or all retry attempts fail. |

## 4. Design Decisions

- **Selective re-raise for context window errors**: `ContextWindowExceededError` is re-raised immediately rather than returning `None`, allowing callers such as `doc_creator.py` to implement their own progressive fallback strategy based on that specific failure mode.
- **Fail-fast on API errors**: `openai.APIError` is caught and logged without retrying, reserving retry budget exclusively for transient rate-limit (`429`) failures.
- **Optional kwargs construction**: `api_key` and `api_base` are added to the litellm call only when non-empty, allowing the client to work with provider defaults when those values are not configured.

## Definition Design Specifications

# Definition Design Specifications

---

## `LLMClient`

**Signature:** `class LLMClient`

**Responsibility:** Wraps the `litellm` async completion API with configuration management and retry logic, providing a single entry point for all LLM text generation calls in the pipeline.

**When to use:** Instantiated once at application startup (or per-pipeline run) and passed as a dependency to any component that needs to generate text via the configured LLM.

---

### `LLMClient.__init__`

| Parameter | Type | Source / Default | Purpose |
|-----------|------|-----------------|---------|
| `model` | `str` | `LLM_MODEL` | Model name in litellm format (e.g., `"openai/gpt-4o"`) |
| `api_key` | `str` | `LLM_API_KEY` | Provider authentication key; may be empty for local endpoints |
| `api_base` | `str` | `LLM_API_BASE` | Base URL override for custom or self-hosted endpoints; may be empty |

**Responsibility:** Validates that a model name is present and stores the three connection parameters as instance attributes.

**Constraints & edge cases:**
- Raises `ValueError` if `model` is falsy (empty string or `None`). All other parameters are optional and may be empty strings; emptiness is checked later at call time before being forwarded to `litellm`.
- Does not establish a network connection; no I/O occurs at construction.

---

### `LLMClient._call_with_retry` *(async)*

**Signature:** `async def _call_with_retry(self, prompt: str, max_tokens: int) -> str | None`

- **`str | None`**: returns the generated text string on success, or `None` if all retry attempts fail.

**Responsibility:** Executes the `litellm.acompletion` call, handling rate-limit retries and surface-level API errors, so that callers receive either a result or `None` without needing error-handling boilerplate.

**When to use:** Called internally by `generate`; not intended for direct external invocation.

**Design decisions:**

- **Selective retry:** Only `litellm.RateLimitError` triggers a wait-and-retry cycle (up to `MAX_RETRIES` attempts with `RETRY_WAIT` seconds between attempts). All other `openai.APIError` subtypes fail immediately and return `None` without retry.
- **Re-raise on context overflow:** `ContextWindowExceededError` is explicitly re-raised rather than caught, delegating the decision of how to handle oversized inputs to the caller.
- **Optional kwargs forwarding:** `api_key` and `api_base` are only added to the `litellm.acompletion` call when they are truthy, preventing litellm from receiving empty-string overrides that might conflict with its own provider detection logic.
- **Concurrency semantics:** Each `litellm.acompletion` call is individually awaited (sequential within a single `_call_with_retry` invocation). The inter-retry sleep is also awaited, yielding control to the event loop during the wait.

**Constraints & edge cases:**

- Returns `None` after the final retry attempt on `RateLimitError`.
- Returns `None` on the first `openai.APIError` (no retry).
- `ContextWindowExceededError` propagates to the caller unconditionally.
- `MAX_RETRIES` and `RETRY_WAIT` are read from module-level constants sourced from `settings.py` and are not overridable per-call.

---

### `LLMClient.generate` *(async)*

**Signature:** `async def generate(self, prompt: str, max_tokens: int = DOC_MAX_TOKENS) -> str | None`

- **`str | None`**: returns the generated text string, or `None` if the prompt is empty or generation fails.
- **`max_tokens`** defaults to `DOC_MAX_TOKENS` (default value: `8192`).

**Responsibility:** Provides the public async interface for text generation, adding a prompt-emptiness guard before delegating to `_call_with_retry`.

**When to use:** Called by `doc_creator.py` and any other pipeline component that needs LLM-generated text for a given prompt string.

**Constraints & edge cases:**

- Returns `None` immediately if `prompt` is falsy, without making any network call.
- `max_tokens` can be overridden per-call to accommodate prompts with different output-size requirements.
- Inherits all failure modes from `_call_with_retry` (returns `None` on rate-limit exhaustion or API error; propagates `ContextWindowExceededError`).

## Dependency Description

## Dependency Description

### Dependencies (modules this file imports)

`codetwine/llm/client_py/client.py` → `codetwine/config/settings.py` : imports six configuration constants that govern runtime behaviour of the LLM client.

| Symbol | Purpose |
|---|---|
| `LLM_MODEL` | Default model name used when no model is supplied to `__init__` |
| `LLM_API_KEY` | Default API key passed to litellm calls |
| `LLM_API_BASE` | Default base URL for custom API endpoints |
| `MAX_RETRIES` | Upper bound on retry attempts inside `_call_with_retry` |
| `RETRY_WAIT` | Number of seconds to sleep between retries on rate-limit errors |
| `DOC_MAX_TOKENS` | Default maximum output token count used in `generate` |

All six values are read-only scalars produced by `get_config_value` in the settings module and consumed here purely as configuration inputs; this module does not write back to settings.

---

### Dependents (modules that import this file)

**`main.py`** → `codetwine/llm/client_py/client.py` : instantiates `LLMClient` (with no arguments, relying entirely on settings defaults) and passes the resulting object into the async processing pipeline. When the `ENABLE_LLM_DOC` flag is falsy, `None` is passed instead, so `LLMClient` is used as an optional, top-level entry point for LLM-backed documentation generation.

**`codetwine/pipeline.py`** → `codetwine/llm/client_py/client.py` : accepts an `LLMClient | None` parameter in `process_all_files` and forwards it through the pipeline. The type annotation is the only direct coupling; the module treats the client as an opaque object whose concrete behaviour is defined here.

**`codetwine/doc_creator.py`** → `codetwine/llm/client_py/client.py` : receives `LLMClient` as a typed parameter in multiple functions responsible for generating design-document sections and per-file design documents. It calls the client's `generate` method (exposed via the public interface of this module) to produce LLM-generated text, with progressive fallback logic driven by the return value (`str | None`).

---

### Dependency Direction

| Relationship | Direction |
|---|---|
| `client.py` → `codetwine/config/settings.py` | **Unidirectional** — `client.py` reads from settings; settings has no knowledge of `client.py`. |
| `main.py` → `client.py` | **Unidirectional** — `main.py` constructs and passes the client; `client.py` has no knowledge of `main.py`. |
| `codetwine/pipeline.py` → `client.py` | **Unidirectional** — the pipeline receives and forwards the client object; `client.py` has no knowledge of the pipeline. |
| `codetwine/doc_creator.py` → `client.py` | **Unidirectional** — `doc_creator.py` calls `client.py`'s public `generate` method; `client.py` has no knowledge of `doc_creator.py`. |

## Data Flow

# Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `model` | `LLM_MODEL` config constant (default `""`) | `str` |
| `api_key` | `LLM_API_KEY` config constant (default `""`) | `str` |
| `api_base` | `LLM_API_BASE` config constant (default `""`) | `str` |
| `prompt` | Caller (`doc_creator.py`, etc.) | `str` |
| `max_tokens` | Caller, or `DOC_MAX_TOKENS` default (default `8192`) | `int` |
| `MAX_RETRIES` | Config constant (default `3`) | `int` |
| `RETRY_WAIT` | Config constant (default `2`) | `int` |

All configuration values are read at import time from `codetwine/config/settings.py` via `get_config_value()` and bound as module-level constants. The `prompt` and `max_tokens` values arrive at call time through `generate()`.

---

## 2. Transformation Overview

```
Caller
  │
  ▼
generate(prompt, max_tokens)
  │  Guard: empty prompt → return None immediately
  │
  ▼
_call_with_retry(prompt, max_tokens)
  │
  ├─ Build kwargs dict ──────────────────────────────────────┐
  │    model, max_tokens, messages=[{role,content}]          │
  │    + api_key (if set)                                    │
  │    + api_base (if set)                                   │
  │                                                          │
  ▼                                                          │
litellm.acompletion(**kwargs)  ◄──────────────────────────── ┘
  │
  ├─ Success → extract response.choices[0].message.content.strip()
  │            → return str
  │
  ├─ RateLimitError (attempt < MAX_RETRIES-1)
  │    → asyncio.sleep(RETRY_WAIT) → retry loop
  │
  ├─ RateLimitError (attempt == MAX_RETRIES-1)
  │    → log error → return None
  │
  ├─ ContextWindowExceededError
  │    → re-raise (propagates to caller)
  │
  └─ openai.APIError
       → log error → return None
```

The retry loop iterates up to `MAX_RETRIES` times. On each iteration a fresh `kwargs` dict is assembled and dispatched to `litellm.acompletion`. Only `RateLimitError` triggers a wait-and-retry cycle; all other errors either propagate or cause an immediate `None` return.

---

## 3. Outputs

| Output | Type | Condition |
|---|---|---|
| Generated text string | `str` | Successful API response; `.strip()` applied |
| `None` | `None` | Empty prompt, all retries exhausted on rate limit, or `openai.APIError` |
| `ContextWindowExceededError` (re-raised) | exception | Input prompt exceeds model context window |

No file writes or global state mutations occur. The sole side effects are log messages emitted via `logger` (`WARNING` on rate-limit retry, `ERROR` on exhaustion or API error).

---

## 4. Key Data Structures

### `kwargs` — API call parameters dict

This dict is constructed inside `_call_with_retry` on every attempt and passed directly to `litellm.acompletion`.

| Field / Key | Type | Purpose |
|---|---|---|
| `model` | `str` | litellm model identifier (provider prefix + model name) |
| `max_tokens` | `int` | Maximum number of tokens in the generated response |
| `messages` | `list[dict]` | Conversation turns sent to the model |
| `api_key` | `str` *(optional)* | Provider authentication key; included only when non-empty |
| `api_base` | `str` *(optional)* | Custom endpoint URL; included only when non-empty |

### `messages` — single-element list of message dicts

| Field / Key | Type | Purpose |
|---|---|---|
| `role` | `str` | Always `"user"` for all calls made by this module |
| `content` | `str` | The full prompt string passed by the caller |

### `LLMClient` — instance attributes

| Field / Key | Type | Purpose |
|---|---|---|
| `model` | `str` | Stored model identifier used in every API call |
| `api_key` | `str` | Stored API key forwarded to litellm when non-empty |
| `api_base` | `str` | Stored base URL forwarded to litellm when non-empty |

## Error Handling

# Error Handling

## 1. Overall Strategy

The `LLMClient` adopts a **retry-with-graceful-degradation** strategy. Transient failures caused by rate limiting are retried up to a configurable maximum (`MAX_RETRIES`) with a fixed delay (`RETRY_WAIT`) between attempts. All other API-level failures are treated as non-retryable and fail immediately. In both cases, exhausted retries and immediate failures resolve to a `None` return value rather than raising an exception to the caller, allowing upstream code to continue without crashing. The sole exception to this pattern is context window overflow, which is re-raised unconditionally so that callers can apply their own fallback logic (e.g., prompt truncation).

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `litellm.RateLimitError` | The LLM provider returns a rate-limit response (HTTP 429) | Waits `RETRY_WAIT` seconds and retries; after `MAX_RETRIES` attempts, logs an error and returns `None` | Yes (up to `MAX_RETRIES` attempts) | Generation result is `None` after exhausting retries |
| `ContextWindowExceededError` | The prompt exceeds the model's maximum context window | Re-raised immediately without retry or logging | No (propagated to caller) | Exception propagates; caller is responsible for handling |
| `openai.APIError` | Any other API-level error returned by the provider | Logs the error and returns `None` immediately without retry | No | Generation result is `None` |
| Empty prompt (`not prompt`) | `generate()` is called with a falsy prompt string | Returns `None` immediately before any API call is made | No (no operation attempted) | Generation result is `None`; no error logged |

---

## 3. Design Notes

- **`ContextWindowExceededError` is intentionally surfaced**, not absorbed. This separates the concern of prompt size management from the LLM client itself. The client treats it as a structural problem that the caller (e.g., `doc_creator.py`) must resolve, consistent with the progressive-fallback pattern described in dependent files.
- **Only rate limiting is retried**, reflecting a deliberate distinction between transient infrastructure conditions (rate limits) and deterministic failures (`APIError`). Retrying on general API errors could mask persistent misconfigurations and is avoided.
- **`None` as a sentinel return value** allows callers to handle missing LLM output as optional behavior, supporting the broader design where LLM documentation generation is optional (`ENABLE_LLM_DOC` in `main.py`).
- **Retry count and wait time are externally configurable** via `MAX_RETRIES` and `RETRY_WAIT` from `settings.py`, keeping the retry policy tunable without modifying client code.

## Summary

`LLMClient(model:str, api_key:str, api_base:str)` wraps the `litellm` async completion API to send prompts to a configured LLM and return generated text. Public interface: `async generate(prompt:str, max_tokens:int) -> str|None`. Consumes a `messages` list of `{role:str, content:str}` dicts and a `kwargs` dict containing `model`, `max_tokens`, `api_key`, `api_base`. Configuration sourced from `LLM_MODEL`, `LLM_API_KEY`, `LLM_API_BASE`, `MAX_RETRIES`, `RETRY_WAIT`, `DOC_MAX_TOKENS`.
