# Design Document: codetwine/llm/client.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Wraps the litellm async completion API to provide a single-responsibility interface for sending prompts to a configured LLM and returning generated text, with built-in rate-limit retry logic.

## 2. When to Use This Module

- **Instantiate `LLMClient`** when LLM-based documentation generation is enabled (e.g., in `main.py` guarded by `ENABLE_LLM_DOC`); the constructor reads model, API key, and base URL from application settings by default.
- **Call `LLMClient.generate(prompt)`** from `doc_creator.py` to produce a documentation section or design document for a source file; it returns the generated text string, or `None` if generation failed.
- **Pass an `LLMClient` instance** into `process_all_files` in `pipeline.py` to enable per-file LLM-generated documentation during a full project analysis run; passing `None` disables LLM generation.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `LLMClient` | `model: str`, `api_key: str`, `api_base: str` | — | Holds connection configuration for a specific LLM endpoint; raises `ValueError` if `model` is not set |
| `async LLMClient.generate` | `prompt: str`, `max_tokens: int` | `str \| None` | Sends a prompt to the LLM and returns the generated text, or `None` if the prompt is empty or all attempts fail |

## 4. Design Decisions

- **Retry scope is limited to rate-limit errors (`litellm.RateLimitError`)**: other API errors (`openai.APIError`) cause an immediate `None` return without retry, while `ContextWindowExceededError` is re-raised to the caller, allowing upstream code to handle context overflow as a distinct condition rather than a generic failure.
- **`_call_with_retry` is separated from `generate`**: `generate` handles the empty-prompt guard and serves as the public entry point, while retry logic is isolated in a private method, keeping each method's responsibility narrow.
- **Optional kwargs pattern for `api_key` and `api_base`**: these parameters are only added to the litellm call when non-empty, allowing the client to work with providers that infer credentials from environment variables without passing empty strings.

## Definition Design Specifications

# Definition Design Specifications

---

## Class: `LLMClient`

**Signature:** `class LLMClient`

**Responsibility:** Wraps the `litellm` async completion API to provide a uniform, retry-aware interface for generating text from a configured LLM. Isolates all provider-specific and retry logic from callers.

**When to use:** Instantiate when the application needs to send prompts to an LLM and receive generated text, typically once per run when `ENABLE_LLM_DOC` is enabled.

---

### `__init__`

| Parameter | Type | Default | Purpose |
|-----------|------|---------|---------|
| `model` | `str` | `LLM_MODEL` | Model name in litellm routing format (used to auto-detect provider) |
| `api_key` | `str` | `LLM_API_KEY` | Authentication credential for the provider |
| `api_base` | `str` | `LLM_API_BASE` | Base URL override for custom or self-hosted endpoints |

**Responsibility:** Validates that a model name is present and stores the three provider-connection parameters as instance attributes.

**Constraints & edge cases:**
- Raises `ValueError` if `model` is falsy (empty string or `None`). Since `LLM_MODEL` defaults to `""`, omitting the setting without passing an explicit argument will always raise.
- `api_key` and `api_base` are optional in the sense that empty strings are accepted and simply omitted from API calls at call time.

---

### `_call_with_retry` (async)

**Signature:** `async def _call_with_retry(self, prompt: str, max_tokens: int) -> str | None`
- Return type: either the generated text string, or `None` if all attempts failed.

**Responsibility:** Executes the `litellm.acompletion` call and handles transient rate-limit errors through a bounded retry loop.

**Concurrency semantics:** Async; `litellm.acompletion` is awaited for each attempt sequentially. A `asyncio.sleep` await introduces a non-blocking delay between retries.

**Design decisions:**
- Only `litellm.RateLimitError` triggers a retry; all other error types cause immediate termination of attempts.
- `ContextWindowExceededError` is re-raised directly to the caller rather than suppressed, allowing upper layers to handle prompt truncation or fallback strategies.
- `openai.APIError` (excluding rate-limit sub-cases caught above) is caught and logged, returning `None` without retrying.
- `api_key` and `api_base` are conditionally included in the kwargs dict only when non-empty, avoiding passing empty strings to the provider.

**Constraints & edge cases:**

| Scenario | Behavior |
|----------|----------|
| `litellm.RateLimitError` on attempt < `MAX_RETRIES - 1` | Logs a warning, sleeps `RETRY_WAIT` seconds, retries |
| `litellm.RateLimitError` on final attempt | Logs an error, returns `None` |
| `ContextWindowExceededError` | Re-raised immediately, no retry |
| `openai.APIError` | Logged, returns `None` immediately |
| Successful response | Returns stripped content string from `choices[0].message.content` |

---

### `generate` (async)

**Signature:** `async def generate(self, prompt: str, max_tokens: int = DOC_MAX_TOKENS) -> str | None`
- `max_tokens`: integer token budget for the model's output; defaults to the `DOC_MAX_TOKENS` configuration value (default `8192`).
- Return type: generated text string, or `None` if generation failed or prompt was empty.

**Responsibility:** Public entry point for text generation; guards against empty prompts and delegates to `_call_with_retry`.

**When to use:** Call whenever a caller (e.g., `doc_creator.py`) needs to produce LLM-generated content for a given prompt string.

**Concurrency semantics:** Async; awaits `_call_with_retry`, so it participates in the event loop without blocking.

**Constraints & edge cases:**
- Returns `None` immediately if `prompt` is falsy, without making any API call.
- All retry and error-handling semantics are governed entirely by `_call_with_retry`; `generate` adds no additional error handling of its own.

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

**`codetwine/llm/client_py/client.py` → `codetwine/config/settings.py`** : Imports six configuration constants to parameterize LLM client behaviour.

- `LLM_MODEL` — used as the default value for the `model` constructor parameter, identifying which LLM to call via litellm.
- `LLM_API_KEY` — used as the default value for the `api_key` constructor parameter, supplying the provider authentication credential.
- `LLM_API_BASE` — used as the default value for the `api_base` constructor parameter, supplying the custom endpoint URL.
- `MAX_RETRIES` — controls the upper bound of the retry loop inside `_call_with_retry`, determining how many times a rate-limited request is attempted before giving up.
- `RETRY_WAIT` — controls the sleep duration (in seconds) between retry attempts when a `RateLimitError` is received.
- `DOC_MAX_TOKENS` — serves as the default value for the `max_tokens` parameter of `generate`, capping the LLM's output length.

All six symbols originate from `codetwine/config/settings.py`, which reads them from environment variables via `get_config_value`.

---

## Dependents (modules that import this file)

**`main.py` → `codetwine/llm/client_py/client.py`** : Instantiates `LLMClient()` (with no arguments, relying entirely on configuration defaults) when the `ENABLE_LLM_DOC` flag is set, and passes the resulting instance (or `None`) to `process_all_files` for document generation across the project.

**`codetwine/pipeline.py` → `codetwine/llm/client_py/client.py`** : Declares `LLMClient | None` as the type of the `llm_client` parameter of `process_all_files`, using `LLMClient` purely as a type annotation to describe the optional client handed in from the entry point.

**`codetwine/doc_creator.py` → `codetwine/llm/client_py/client.py`** : Uses `LLMClient` as the declared type of the `llm_client` parameter in its document-generation functions. The actual `generate` method on the client is invoked within those functions to produce per-section and per-file design document content via the LLM.

---

## Dependency Direction

| Relationship | Direction |
|---|---|
| `client.py` → `codetwine/config/settings.py` | **Unidirectional** — `client.py` consumes configuration constants; `settings.py` has no knowledge of `client.py`. |
| `main.py` → `client.py` | **Unidirectional** — `main.py` constructs and passes `LLMClient`; `client.py` has no knowledge of `main.py`. |
| `codetwine/pipeline.py` → `client.py` | **Unidirectional** — `pipeline.py` references `LLMClient` as a type; `client.py` has no knowledge of `pipeline.py`. |
| `codetwine/doc_creator.py` → `client.py` | **Unidirectional** — `doc_creator.py` receives and calls `LLMClient`; `client.py` has no knowledge of `doc_creator.py`. |

## Data Flow

# Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `model` | Constructor argument / `LLM_MODEL` config | `str` (litellm model name, e.g. `"openai/gpt-4o"`) |
| `api_key` | Constructor argument / `LLM_API_KEY` config | `str` (provider API key, may be empty) |
| `api_base` | Constructor argument / `LLM_API_BASE` config | `str` (base URL for custom endpoints, may be empty) |
| `prompt` | `generate()` argument | `str` (fully constructed prompt text) |
| `max_tokens` | `generate()` argument / `DOC_MAX_TOKENS` config | `int` (default: 8192) |
| `MAX_RETRIES` | `settings.py` config | `int` (default: 3) |
| `RETRY_WAIT` | `settings.py` config | `int` (default: 2, seconds) |

---

## 2. Transformation Overview

```
[prompt: str, max_tokens: int]
        │
        ▼
[generate()]
  ─ Guard: empty prompt → return None immediately
        │
        ▼
[_call_with_retry()]
  ─ Build kwargs dict:
      always:   model, max_tokens, messages
      optional: api_key (if truthy), api_base (if truthy)
        │
        ▼
  ─ litellm.acompletion(**kwargs)   ← async HTTP call
        │
        ├─ Success
        │     └─ extract response.choices[0].message.content.strip()
        │              → return str
        │
        ├─ RateLimitError (attempt < MAX_RETRIES - 1)
        │     └─ asyncio.sleep(RETRY_WAIT) → retry loop
        │
        ├─ RateLimitError (all retries exhausted)
        │     └─ return None
        │
        ├─ ContextWindowExceededError
        │     └─ re-raise to caller (no retry, no suppression)
        │
        └─ openai.APIError
              └─ return None (no retry)
```

The retry loop iterates up to `MAX_RETRIES` times. Each iteration is a full async round-trip to the LLM API. Only `RateLimitError` triggers a wait-and-retry cycle; all other errors either propagate or terminate immediately.

---

## 3. Outputs

| Output | From | Format | Condition |
|---|---|---|---|
| Generated text | `generate()` / `_call_with_retry()` | `str` (stripped) | Successful API response |
| `None` | `generate()` | `None` | Empty prompt, rate limit exhausted, or `openai.APIError` |
| `ContextWindowExceededError` | `_call_with_retry()` | exception | Prompt exceeds model context window; re-raised to caller |
| Log warnings/errors | `logger` | side effect | Rate limit retries and terminal failures |

No file writes are performed by this module.

---

## 4. Key Data Structures

### `kwargs` — API call parameters dict

Assembled inside `_call_with_retry` before each call to `litellm.acompletion`.

| Field / Key | Type | Purpose |
|---|---|---|
| `model` | `str` | litellm model identifier (used to auto-detect provider) |
| `max_tokens` | `int` | Maximum number of tokens the LLM may generate |
| `messages` | `list[dict]` | Conversation turns sent to the API |
| `api_key` | `str` | Provider API key (included only if `self.api_key` is truthy) |
| `api_base` | `str` | Custom endpoint base URL (included only if `self.api_base` is truthy) |

### `messages` — single-element list of message dicts

| Field / Key | Type | Purpose |
|---|---|---|
| `role` | `str` | Always `"user"` in this module |
| `content` | `str` | The full prompt string passed to `generate()` |

### `LLMClient` instance attributes

| Field | Type | Purpose |
|---|---|---|
| `model` | `str` | Stored litellm model name used for every API call |
| `api_key` | `str` | Stored API key forwarded to litellm when non-empty |
| `api_base` | `str` | Stored base URL forwarded to litellm when non-empty |

## Error Handling

# Error Handling

## 1. Overall Strategy

`LLMClient` adopts a **retry-with-fallback** strategy combined with **selective propagation**. Transient network-level errors (rate limiting) are retried up to `MAX_RETRIES` times with a fixed `RETRY_WAIT` delay between attempts. Context window violations are propagated immediately to the caller. Permanent API errors are logged and cause the method to return `None`, allowing callers to treat a missing result as a graceful degradation rather than a hard failure. Empty prompt input is rejected silently at the `generate` boundary before any API call is made.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `ValueError` | `model` is an empty string at construction time | Raised immediately; initialization aborts | No | Client object cannot be created |
| Empty prompt | `prompt` is falsy when `generate` is called | Returns `None` immediately; no API call is made | Yes (caller receives `None`) | No LLM call is issued |
| `litellm.RateLimitError` | API responds with HTTP 429 (rate limit exceeded) | Waits `RETRY_WAIT` seconds, retries up to `MAX_RETRIES` times; logs warning on each wait, logs error on final failure, returns `None` | Yes (up to `MAX_RETRIES` attempts) | Returns `None` after exhausting retries |
| `ContextWindowExceededError` | Prompt or token count exceeds the model's context window | Re-raised immediately without retrying | No (propagated to caller) | Caller must handle or the call stack unwinds |
| `openai.APIError` | Any other API-level error from the provider | Logged as an error; returns `None` immediately without retry | No (no retry, single attempt) | Returns `None`; caller receives no generated text |

---

## 3. Design Notes

- **Rate limiting is the only retried condition.** The design distinguishes between transient quota exhaustion (`RateLimitError`), which is inherently temporary and worth retrying, and structural errors (`APIError`, `ContextWindowExceededError`), which retrying would not resolve.
- **`ContextWindowExceededError` is propagated rather than absorbed.** This is a deliberate exception to the general `None`-return fallback pattern. By re-raising, the client signals to callers (e.g., `doc_creator.py`) that a content-reduction strategy—such as progressive fallback with shorter context—should be attempted at a higher level.
- **`None` as a neutral failure signal.** Returning `None` on unrecoverable errors (rather than raising) allows the pipeline to continue processing other files without halting on a single LLM failure, consistent with the graceful degradation needs of the dependent pipeline (`pipeline.py`, `doc_creator.py`).
- **Retry parameters are externally configurable.** `MAX_RETRIES` and `RETRY_WAIT` are drawn from `settings.py`, meaning the retry policy can be tuned via environment variables without code changes.

## Summary

**`codetwine/llm/client.py`**: Wraps `litellm` async completion to send prompts to a configured LLM and return generated text.

**Public interface:**
- `LLMClient(model: str, api_key: str, api_base: str)` — stores provider connection config
- `async LLMClient.generate(prompt: str, max_tokens: int) → str | None` — sends prompt, returns generated text or `None`

**Key data structures:**
- `messages: list[dict]` with `role: str` and `content: str` fields sent to the API
- `kwargs: dict` with `model`, `max_tokens`, `messages`, optional `api_key` and `api_base`
