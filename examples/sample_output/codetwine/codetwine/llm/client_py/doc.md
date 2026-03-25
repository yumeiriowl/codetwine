# Design Document: codetwine/llm/client.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Wraps the litellm async completion API to provide a single-responsibility interface for sending prompts to a configured LLM and returning generated text, with built-in retry logic for rate limit errors.

## 2. When to Use This Module

- **Generating LLM-based documentation**: Instantiate `LLMClient()` and call `await client.generate(prompt)` to send a prompt string and receive the generated text response. Used by `doc_creator.py` for per-section documentation generation.
- **Conditionally enabling LLM features**: Pass an `LLMClient` instance (or `None`) to pipeline functions such as `process_all_files()` in `pipeline.py` to control whether LLM-based document generation runs.
- **Using a custom model or endpoint**: Instantiate `LLMClient(model=..., api_key=..., api_base=...)` to override the defaults drawn from environment configuration, enabling use with non-default providers or local endpoints.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `LLMClient` | `model: str`, `api_key: str`, `api_base: str` | — | Holds LLM connection configuration; raises `ValueError` if `model` is empty |
| `async LLMClient.generate` | `prompt: str`, `max_tokens: int` | `str \| None` | Sends a prompt to the LLM and returns the generated text, or `None` if the prompt is empty or generation fails |

## 4. Design Decisions

- **Retry only on rate limits**: The retry loop (`MAX_RETRIES` attempts with `RETRY_WAIT` second delays) is applied exclusively to `litellm.RateLimitError`. `openai.APIError` causes an immediate return of `None` without retrying, and `ContextWindowExceededError` is re-raised to propagate to the caller rather than being silenced.
- **Optional credentials via kwargs**: `api_key` and `api_base` are added to the litellm call only when non-empty, allowing the client to function without explicit credentials when the provider does not require them.
- **Separation of retry logic from public API**: `_call_with_retry` isolates retry behavior so that `generate` remains a thin, prompt-validation entry point, keeping the two concerns independently testable.

## Definition Design Specifications

# Definition Design Specifications

---

## Class: `LLMClient`

**Signature:** `class LLMClient`

**Responsibility:** Provides an async wrapper around the litellm library to submit prompts to a configurable LLM endpoint and return generated text, with built-in rate-limit retry logic.

**When to use:** Instantiate when the application needs to generate LLM-based documentation or text output; a single instance is typically created at application startup and passed through the pipeline.

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

| Parameter | Type | Source Default | Purpose |
|-----------|------|----------------|---------|
| `model` | `str` | `LLM_MODEL` env var | litellm-format model identifier; the prefix determines provider routing |
| `api_key` | `str` | `LLM_API_KEY` env var | Provider authentication credential |
| `api_base` | `str` | `LLM_API_BASE` env var | Base URL for custom or self-hosted endpoints |

**Design decisions:**
- `model` is the only required field at runtime; raises `ValueError` immediately if it resolves to a falsy value, preventing silent misconfiguration.
- `api_key` and `api_base` are optional; they are stored but only forwarded to litellm when non-empty, allowing provider-default authentication flows.

**Constraints & edge cases:**
- An empty or unset `LLM_MODEL` causes construction to fail with `ValueError`; the other two fields may be empty strings without raising.

---

### `_call_with_retry` (async)

**Signature:**
```
async _call_with_retry(self, prompt: str, max_tokens: int) -> str | None
```
`str | None` — either the stripped text content from the LLM response, or `None` if all retry attempts are exhausted or a non-retryable error occurs.

**Responsibility:** Encapsulates the direct litellm API call and the retry loop so that `generate` remains a clean public interface.

**When to use:** Called internally by `generate`; not intended for direct external invocation.

**Design decisions:**

| Concern | Decision |
|---------|----------|
| Rate limit handling | Retries up to `MAX_RETRIES` times with `RETRY_WAIT`-second sleep between attempts; logs a warning on each intermediate retry and an error when exhausted |
| Context window exceeded | Re-raises `ContextWindowExceededError` immediately without retry, allowing callers to handle prompt truncation |
| Generic API errors | `openai.APIError` is caught, logged, and causes immediate `None` return without retry |
| Optional kwargs | `api_key` and `api_base` are conditionally added to the litellm call only when truthy, avoiding conflicts with provider defaults |

**Concurrency semantics:** This is an `async` method. It `await`s `litellm.acompletion` (a single non-parallel network call per attempt) and `await`s `asyncio.sleep` between retries. Each invocation runs sequentially within its retry loop.

**Constraints & edge cases:**
- Returns `None` after `MAX_RETRIES` failed rate-limit attempts.
- Returns `None` on `openai.APIError` without retrying.
- `ContextWindowExceededError` propagates to the caller unconditionally.
- The retry counter starts at attempt `0`; sleep is skipped on the final attempt before returning `None`.

---

### `generate` (async)

**Signature:**
```
async generate(self, prompt: str, max_tokens: int = DOC_MAX_TOKENS) -> str | None
```
`str | None` — the LLM-generated text, or `None` if generation failed or the prompt was empty.

**Responsibility:** Serves as the primary public entry point for LLM text generation, guarding against empty prompts before delegating to the retry-enabled API call.

**When to use:** Call this method from any pipeline component (e.g., `doc_creator.py`) that needs to generate text from a fully-assembled prompt string.

**Concurrency semantics:** This is an `async` method. It `await`s `_call_with_retry`, making calls sequential within a single invocation. Multiple concurrent calls to `generate` from different coroutines are independent.

**Constraints & edge cases:**

| Condition | Behavior |
|-----------|----------|
| `prompt` is falsy (empty string, `None`) | Returns `None` immediately without an API call |
| `max_tokens` not specified | Defaults to `DOC_MAX_TOKENS` (env-configured, default `8192`) |
| API failure after retries | Returns `None` (propagated from `_call_with_retry`) |
| Context window exceeded | `ContextWindowExceededError` propagates to the caller |

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

**`codetwine/llm/client_py/client.py` → `codetwine/config/settings.py` : configuration values for LLM client construction and API call behavior**

Specific symbols imported and their purposes:

- `LLM_MODEL` — used as the default value for the `model` parameter in `__init__`, specifying which LLM model to invoke via litellm.
- `LLM_API_KEY` — used as the default value for the `api_key` parameter in `__init__`, providing the provider authentication credential.
- `LLM_API_BASE` — used as the default value for the `api_base` parameter in `__init__`, providing the custom API endpoint URL.
- `MAX_RETRIES` — used in `_call_with_retry` to control the total number of retry attempts on rate-limit errors.
- `RETRY_WAIT` — used in `_call_with_retry` to determine the number of seconds to wait between retry attempts.
- `DOC_MAX_TOKENS` — used as the default value for the `max_tokens` parameter in `generate`, capping the LLM's output length.

---

## Dependents (modules that import this file)

**`main.py` → `codetwine/llm/client_py/client.py` : instantiates `LLMClient` to pass into the processing pipeline**

`main.py` constructs an `LLMClient()` instance (conditionally, based on `ENABLE_LLM_DOC`) and passes it to `process_all_files`. This module treats `LLMClient` as the top-level entry point for LLM-backed documentation generation.

**`codetwine/pipeline.py` → `codetwine/llm/client_py/client.py` : receives `LLMClient` as a typed parameter to orchestrate file-level processing**

`codetwine/pipeline.py` accepts `LLMClient | None` as a parameter in `process_all_files`, using the type for annotation and passing the client downstream through the pipeline. It does not construct `LLMClient` itself.

**`codetwine/doc_creator.py` → `codetwine/llm/client_py/client.py` : receives `LLMClient` as a typed parameter to invoke LLM text generation for documentation sections and design documents**

`codetwine/doc_creator.py` accepts `LLMClient` as a parameter in multiple functions responsible for generating documentation content. It calls the client's generation capabilities to produce per-file design documents and individual documentation sections.

---

## Dependency Direction

| Relationship | Direction |
|---|---|
| `codetwine/llm/client_py/client.py` → `codetwine/config/settings.py` | **Unidirectional** — `client.py` reads configuration values from `settings.py`; `settings.py` has no knowledge of `client.py`. |
| `main.py` → `codetwine/llm/client_py/client.py` | **Unidirectional** — `main.py` imports and instantiates `LLMClient`; `client.py` has no knowledge of `main.py`. |
| `codetwine/pipeline.py` → `codetwine/llm/client_py/client.py` | **Unidirectional** — `pipeline.py` imports `LLMClient` for type annotation and parameter passing; `client.py` has no knowledge of `pipeline.py`. |
| `codetwine/doc_creator.py` → `codetwine/llm/client_py/client.py` | **Unidirectional** — `doc_creator.py` imports `LLMClient` for type annotation and delegates generation calls to it; `client.py` has no knowledge of `doc_creator.py`. |

## Data Flow

# Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `model` | `LLM_MODEL` config value or constructor argument | `str` (litellm model name, e.g. `"openai/gpt-4"`) |
| `api_key` | `LLM_API_KEY` config value or constructor argument | `str` (provider API key, may be empty) |
| `api_base` | `LLM_API_BASE` config value or constructor argument | `str` (base URL, may be empty) |
| `prompt` | Caller (e.g. `doc_creator.py`) | `str` (fully composed prompt text) |
| `max_tokens` | Caller or default `DOC_MAX_TOKENS` (default `8192`) | `int` |
| `MAX_RETRIES` | `settings.py` config value (default `3`) | `int` |
| `RETRY_WAIT` | `settings.py` config value (default `2`) | `int` (seconds) |

---

## 2. Transformation Overview

```
[Caller] → generate(prompt, max_tokens)
              │
              ▼
         Guard: prompt is empty?
              │ Yes → return None
              │ No
              ▼
         _call_with_retry(prompt, max_tokens)
              │
              ├─ Build kwargs dict
              │     model, max_tokens, messages
              │     + api_key   (if non-empty)
              │     + api_base  (if non-empty)
              │
              ▼
         litellm.acompletion(**kwargs)   [async HTTP call]
              │
              ├── Success
              │     └─ extract response.choices[0].message.content.strip()
              │           └─ return str
              │
              ├── RateLimitError
              │     ├─ attempt < MAX_RETRIES-1 → asyncio.sleep(RETRY_WAIT) → retry
              │     └─ attempt == MAX_RETRIES-1 → log error → return None
              │
              ├── ContextWindowExceededError
              │     └─ re-raise immediately (no retry, no suppression)
              │
              └── openai.APIError
                    └─ log error → return None (no retry)
```

**Async behaviour:** Each `generate()` call is independently async. No fan-out or merging occurs inside this module; concurrency is controlled entirely by the callers in `pipeline.py` and `doc_creator.py`.

---

## 3. Outputs

| Output | Format | Condition |
|---|---|---|
| Generated text | `str` — stripped response content | Successful API call |
| `None` | `None` | Empty prompt, rate-limit retries exhausted, or `openai.APIError` |
| `ContextWindowExceededError` (raised) | Exception | Prompt exceeds the model's context window; propagated to caller |
| Log warnings/errors | Side effect via `logger` | On rate-limit retries and terminal failures |

---

## 4. Key Data Structures

### `kwargs` — API call parameter dict

Assembled inside `_call_with_retry` before each `litellm.acompletion` call.

| Field / Key | Type | Purpose |
|---|---|---|
| `model` | `str` | litellm model identifier; prefix determines provider routing |
| `max_tokens` | `int` | Maximum number of tokens the model may generate |
| `messages` | `list[dict]` | Conversation turns sent to the model (see below) |
| `api_key` | `str` | Provider authentication key — included only when non-empty |
| `api_base` | `str` | Custom endpoint URL — included only when non-empty |

### `messages` — single-element list inside `kwargs`

| Field / Key | Type | Purpose |
|---|---|---|
| `role` | `str` | Fixed value `"user"` — identifies the message author |
| `content` | `str` | The full prompt text forwarded to the model |

### `response` — litellm completion object (read fields only)

| Field / Key | Type | Purpose |
|---|---|---|
| `choices[0].message.content` | `str` | Raw generated text extracted and stripped before returning |

## Error Handling

# Error Handling

## 1. Overall Strategy

`LLMClient` applies a **retry-with-fallback** strategy for transient errors (rate limiting) combined with **fail-fast** behavior for non-recoverable errors. When all retry attempts are exhausted or a permanent error occurs, the method returns `None` rather than raising an exception, enabling callers to treat generation failure as a graceful degradation. Critical errors that indicate an invalid call context (context window exceeded) are re-raised immediately to propagate to the caller without consuming retries.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `ValueError` | `model` is falsy (empty string or `None`) at construction | Raised immediately in `__init__` | No | Client cannot be instantiated; process terminates at setup |
| `litellm.RateLimitError` | API returns HTTP 429 (rate limit exceeded) during a completion call | Waits `RETRY_WAIT` seconds and retries up to `MAX_RETRIES` attempts; logs a warning per retry and an error on final failure | Yes (up to `MAX_RETRIES`) | Returns `None` after all retries exhausted |
| `ContextWindowExceededError` | Prompt exceeds the model's context window | Re-raised immediately without retry or logging | No (propagates to caller) | Caller receives the exception directly |
| `openai.APIError` | Any OpenAI-layer API error other than the above | Logged as an error; no retry | No | Returns `None` immediately |
| Empty prompt | `prompt` is falsy (empty string or `None`) passed to `generate` | Returns `None` without making any API call | N/A | No API call is made; caller receives `None` |

---

## 3. Design Notes

- **`None` as the failure sentinel**: Returning `None` on failure rather than raising isolates the LLM layer from callers; consumers (`pipeline.py`, `doc_creator.py`) receive `None` and can decide how to proceed without catching exceptions from this module (except for `ContextWindowExceededError`).
- **Selective re-raise for `ContextWindowExceededError`**: This error signals a structural incompatibility between the prompt and the model, not a transient condition. Re-raising it allows callers (e.g., `doc_creator.py`) to implement their own progressive fallback logic (such as reducing context size) rather than silently receiving `None`.
- **Retry scope is narrow**: Only `RateLimitError` triggers the retry loop. `openai.APIError` failures are treated as non-transient and fail immediately, preventing indefinite delays on persistent API-level problems.
- **Retry parameters are externally configurable**: Both `MAX_RETRIES` and `RETRY_WAIT` are sourced from `settings.py` (with defaults of 3 and 2 respectively), keeping retry behavior tunable without code changes.

## Summary

`LLMClient(model:str, api_key:str, api_base:str)` wraps litellm's async completion API to send prompts to a configured LLM and return generated text. Public method: `async generate(prompt:str, max_tokens:int) -> str|None`. Internally uses `async _call_with_retry(prompt:str, max_tokens:int) -> str|None`. Consumes a `messages` list of `{role:str, content:str}` dicts and a `kwargs` dict containing `model`, `max_tokens`, `messages`, and optional `api_key`/`api_base`. Returns stripped `response.choices[0].message.content` on success.
