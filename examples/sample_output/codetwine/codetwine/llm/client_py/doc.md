# Design Document: codetwine/llm/client.py

## Overview & Purpose

# Overview & Purpose

## Role and Responsibilities

`client.py` encapsulates all direct interaction with the LLM API behind a single, reusable async class. It exists as a separate module to isolate the concerns of API communication, retry logic, and error handling from the rest of the pipeline. The rest of the codebase (`pipeline.py`, `doc_creator.py`, `main.py`) depends only on the `LLMClient` type and its `generate` method, with no knowledge of the underlying HTTP transport or retry mechanism. All LLM-specific configuration values (`LLM_MODEL`, `LLM_API_KEY`, `LLM_API_BASE`, `MAX_RETRIES`, `RETRY_WAIT`, `DOC_MAX_TOKENS`) are sourced from `codetwine/config/settings.py`, keeping this module free of hardcoded values.

The module delegates the actual API call to [litellm](https://github.com/BerriAI/litellm)'s `acompletion`, which provides an OpenAI-compatible async interface and uses the model name prefix to auto-detect the provider.

---

## Public Interfaces

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `LLMClient` | — | — | Class that wraps the LLM API; holds model, API key, and endpoint configuration |
| `LLMClient.__init__` | `model: str`, `api_key: str`, `api_base: str` | `None` | Validates that `model` is non-empty and stores connection parameters |
| `LLMClient.generate` | `prompt: str`, `max_tokens: int = DOC_MAX_TOKENS` | `str \| None` | Public entry point; sends a prompt to the LLM and returns the generated text, or `None` on failure or empty prompt |
| `LLMClient._call_with_retry` | `prompt: str`, `max_tokens: int` | `str \| None` | Internal method that executes the API call with up to `MAX_RETRIES` attempts, sleeping `RETRY_WAIT` seconds on rate-limit errors |

---

## Design Decisions

- **Retry only on rate limits (`litellm.RateLimitError`)**: The retry loop exclusively handles HTTP 429-style rate limit errors with a configurable sleep interval. Any other API error (`litellm.APIError`) causes an immediate return of `None` without retrying, avoiding unnecessary delays for unrecoverable errors.
- **`None` as the failure sentinel**: Both `generate` and `_call_with_retry` return `None` on all failure paths (exhausted retries, API error, empty prompt), giving callers a uniform way to detect failure without exception handling.
- **Optional API parameters**: `api_key` and `api_base` are only added to the `litellm.acompletion` kwargs when they are non-empty strings, supporting both hosted providers (where the key may come from the environment) and custom endpoints interchangeably.
- **Separation of public and internal interfaces**: `generate` acts as the stable public facade (input validation + delegation), while `_call_with_retry` is an internal implementation detail, keeping the retry logic testable in isolation.

## Definition Design Specifications

# Definition Design Specifications

---

## Class: `LLMClient`

**Responsibility:** Provides an async wrapper around `litellm.acompletion` for sending prompts to an LLM provider. Encapsulates connection configuration, retry logic, and response extraction behind a single `generate` interface.

**Constructor: `__init__`**

| Parameter | Type | Meaning |
|-----------|------|---------|
| `model` | `str` | litellm-format model identifier; defaults to `LLM_MODEL` from settings |
| `api_key` | `str` | Provider API key; defaults to `LLM_API_KEY` from settings |
| `api_base` | `str` | Base URL for custom or self-hosted endpoints; defaults to `LLM_API_BASE` from settings |

**Returns:** `None` (constructor)

Raises `ValueError` if `model` resolves to a falsy value, because a missing model name makes every subsequent API call meaningless and failure should surface at construction time rather than at call time. `api_key` and `api_base` are optional at the type level; empty strings are treated as "not provided" and excluded from API call kwargs.

---

## Method: `_call_with_retry`

**Signature:** `async def _call_with_retry(self, prompt: str, max_tokens: int) -> str | None`

| Parameter | Type | Meaning |
|-----------|------|---------|
| `prompt` | `str` | Fully formed prompt string forwarded directly to the LLM |
| `max_tokens` | `int` | Upper bound on generated output tokens |

**Returns:** The stripped text content from the first choice of the API response, or `None` if all attempts are exhausted or a non-retryable error occurs.

**Design decisions:**

- Only `litellm.RateLimitError` (HTTP 429) triggers a retry with a `RETRY_WAIT`-second delay, because rate limits are transient and retrying is the standard recovery strategy. All other `litellm.APIError` subclasses fail immediately, as they typically indicate configuration or request-level problems that retrying will not resolve.
- The retry loop runs at most `MAX_RETRIES` times. On the final attempt, a rate-limit error logs at `ERROR` level and returns `None` rather than raising, keeping failure handling in the caller's domain.
- `api_key` and `api_base` are injected into kwargs only when truthy, allowing litellm's provider auto-detection to function correctly when these values are not configured.

**Constraints:** Callers should treat `None` as a signal that generation failed after exhausting retry budget. The method does not raise exceptions to the caller.

---

## Method: `generate`

**Signature:** `async def generate(self, prompt: str, max_tokens: int = DOC_MAX_TOKENS) -> str | None`

| Parameter | Type | Meaning |
|-----------|------|---------|
| `prompt` | `str` | The prompt string to send to the LLM |
| `max_tokens` | `int` | Maximum output token limit; defaults to `DOC_MAX_TOKENS` from settings |

**Returns:** The LLM-generated text string on success, or `None` if the prompt is empty or generation failed.

**Design decisions:**

- A falsy `prompt` is short-circuited to `None` immediately, avoiding a wasted API call for an empty input.
- `DOC_MAX_TOKENS` (default 8192) is chosen as the default because this client is primarily used for documentation generation, where longer outputs are expected.
- This method is the sole public entry point for generation; it delegates all retry and error-handling logic to `_call_with_retry`, keeping the public interface simple for callers in `doc_creator.py` and `pipeline.py`.

**Constraints:** The caller receives `None` for any failure mode (empty prompt, rate limit exhaustion, or API error) and is responsible for handling that case.

## Dependency Description

## Dependency Description

### Dependencies (what this file uses)

**`codetwine/config/settings.py`**

This file depends solely on `settings.py` for all externally configurable values. The specific constants consumed and their roles are as follows:

- **`LLM_MODEL`**: Used as the default value for the `model` parameter in `__init__`. Identifies which LLM provider/model litellm should route requests to.
- **`LLM_API_KEY`**: Used as the default value for the `api_key` parameter in `__init__`. Provides authentication credentials when calling the LLM API.
- **`LLM_API_BASE`**: Used as the default value for the `api_base` parameter in `__init__`. Supplies the endpoint URL for custom or self-hosted LLM deployments.
- **`MAX_RETRIES`**: Controls how many times `_call_with_retry` will attempt the API call before giving up on rate-limit errors.
- **`RETRY_WAIT`**: Determines the number of seconds to wait between retry attempts when a rate-limit error (`RateLimitError`) is encountered.
- **`DOC_MAX_TOKENS`**: Serves as the default value for the `max_tokens` parameter in `generate`, capping the length of the LLM's output.

The dependency is strictly unidirectional: this file reads configuration values from `settings.py` and `settings.py` has no knowledge of `LLMClient`.

---

### Dependents (what uses this file)

**`main.py`**

Instantiates `LLMClient` conditionally (only when `ENABLE_LLM_DOC` is enabled) and passes the resulting instance into the top-level pipeline entry point `process_all_files`. This file treats `LLMClient` as an optional service object.

**`codetwine/pipeline.py`**

Receives an `LLMClient | None` instance as a parameter to `process_all_files`. Uses the type annotation to declare the expected interface for the LLM client within the pipeline orchestration layer.

**`codetwine/doc_creator.py`**

Accepts `LLMClient` as a parameter in its document generation functions. Uses the client to invoke LLM-based text generation when producing design document sections and per-file documentation.

**Direction of dependency**: All dependencies are unidirectional — `main.py`, `pipeline.py`, and `doc_creator.py` each depend on `LLMClient`, while `LLMClient` has no knowledge of any of these files.

## Data Flow

# Data Flow

## Overview

```
Caller (main.py / pipeline.py / doc_creator.py)
        │
        │  prompt: str, max_tokens: int
        ▼
  LLMClient.generate()
        │
        │  validates prompt (returns None if empty)
        ▼
  LLMClient._call_with_retry()
        │
        │  builds kwargs dict → litellm.acompletion(**kwargs)
        │
        ├─ success ──→ response.choices[0].message.content.strip()
        │                        │
        │                        ▼
        │               generated text: str
        │
        ├─ RateLimitError (attempt < MAX_RETRIES-1) ──→ sleep(RETRY_WAIT) → retry
        ├─ RateLimitError (all retries exhausted)   ──→ None
        └─ APIError                                 ──→ None (no retry)
```

## Input Data

| Source | Parameter | Type | Description |
|---|---|---|---|
| Caller | `prompt` | `str` | Complete prompt text to send to the LLM |
| Caller | `max_tokens` | `int` | Max output token limit; defaults to `DOC_MAX_TOKENS` (8192) |
| Config (`settings.py`) | `model` | `str` | litellm model name (required, raises `ValueError` if empty) |
| Config (`settings.py`) | `api_key` | `str` | Provider API key (optional, omitted from kwargs if empty) |
| Config (`settings.py`) | `api_base` | `str` | Custom endpoint URL (optional, omitted from kwargs if empty) |

## Request Payload (`kwargs` dict)

```python
{
    "model":     str,           # always present
    "max_tokens": int,          # always present
    "messages": [
        {"role": "user", "content": prompt}   # single user turn
    ],
    "api_key":  str,            # included only if api_key is non-empty
    "api_base": str,            # included only if api_base is non-empty
}
```

## Output Data

| Scenario | Return value | Type |
|---|---|---|
| Successful generation | Extracted and stripped response text | `str` |
| Empty prompt | Early return | `None` |
| Rate limit – all retries exhausted | After `MAX_RETRIES` attempts with `RETRY_WAIT`-second waits | `None` |
| API error | Immediate failure, no retry | `None` |

The extracted text comes from `response.choices[0].message.content.strip()` — a single string delivered to the caller (`doc_creator.py`, `pipeline.py`, or `main.py`).

## Retry State

| Variable | Source | Role |
|---|---|---|
| `attempt` | loop counter `range(MAX_RETRIES)` | Tracks current attempt number |
| `MAX_RETRIES` | `settings.py` (default 3) | Upper bound on total attempts |
| `RETRY_WAIT` | `settings.py` (default 2 s) | Sleep duration between rate-limit retries |

## Error Handling

# Error Handling

## Overall Strategy

`LLMClient` applies a **mixed strategy**: graceful degradation with bounded retries for transient failures, and fail-fast for permanent failures. Rather than propagating exceptions to callers, the client absorbs errors internally and signals failure by returning `None`. This allows upstream code (e.g., `doc_creator.py`, `pipeline.py`) to treat LLM generation as optional without requiring try/except at the call site.

---

## Error Patterns and Handling Policies

| Error Type | Handling | Impact |
|---|---|---|
| `litellm.RateLimitError` | Retried up to `MAX_RETRIES` times with `RETRY_WAIT`-second delays between attempts; on final attempt, logs an error and returns `None` | Transient rate limiting is tolerated; persistent rate limiting causes silent `None` return to caller |
| `litellm.APIError` | Logged immediately at error level; no retry; returns `None` | Any non-rate-limit API failure (e.g., invalid request, server error) causes immediate termination of the attempt |
| Empty/falsy `prompt` | Checked at `generate()` entry point; returns `None` without making any API call | Prevents unnecessary network calls for degenerate inputs |
| Missing `LLM_MODEL` at construction | Raises `ValueError` eagerly in `__init__` | Prevents instantiation of a client that can never function; the only exception that propagates to the caller |

---

## Design Considerations

- **`None` as a uniform failure signal**: All error paths within `_call_with_retry` converge on returning `None`, giving callers a single, type-safe condition to check rather than a mix of exceptions and return values.
- **Retry scope is intentionally narrow**: Only `RateLimitError` triggers retries. `APIError` (covering a broad range of server-side and request errors) is treated as non-recoverable to avoid redundant calls that are unlikely to succeed.
- **Configuration-driven retry behavior**: Retry count and wait duration are externalized to `MAX_RETRIES` and `RETRY_WAIT` (from `settings.py`), so the retry policy can be adjusted without code changes.
- **Fail-fast at construction vs. graceful degradation at runtime**: The hard `ValueError` on a missing model name reflects a configuration error that cannot be recovered from at runtime, while API-level failures are treated as operational conditions that should not crash the pipeline.

## Summary

`client.py` defines `LLMClient`, an async wrapper around `litellm.acompletion` that isolates all LLM API communication from the rest of the codebase. Configuration (model, API key, endpoint, retry settings, token limits) is sourced entirely from `settings.py`. The public interface is a single `generate(prompt, max_tokens)` method returning generated text or `None` on failure. Internally, `_call_with_retry` executes up to `MAX_RETRIES` attempts, sleeping `RETRY_WAIT` seconds on rate-limit errors and failing immediately on other API errors. Empty prompts and missing model names are rejected eagerly. All failure paths return `None`; no exceptions propagate to callers.
