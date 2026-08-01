# Design Document: codetwine/llm/client.py

# Overview & Purpose

`codetwine/llm/client.py` provides a single, centralized async wrapper around `litellm.acompletion` for all LLM invocations in the project. It exists as a dedicated module to isolate LLM API concerns (provider configuration, retry/backoff logic, error handling) from the callers that need generated text (`main.py`, `codetwine/doc_creator.py`, `codetwine/pipeline.py`), so those modules can simply request text generation without dealing with rate limits, transient failures, or provider-specific setup.

The module reads its default configuration (model name, API key, API base, retry count/wait, and default max tokens) from `codetwine/config/settings.py`, keeping provider/runtime settings externally configurable while the client logic itself stays provider-agnostic (relying on litellm's model-name-based provider detection).

### Main Public Interfaces

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `LLMClient.__init__` | `model: str = LLM_MODEL`, `api_key: str = LLM_API_KEY`, `api_base: str = LLM_API_BASE` | `None` | Validates that a model is configured (raises `ValueError` if empty) and stores model/api_key/api_base for later calls. |
| `LLMClient.generate` | `prompt: str`, `max_tokens: int = DOC_MAX_TOKENS` | `str \| None` | Public entry point: returns `None` immediately for empty prompts, otherwise delegates to the retry-enabled internal call and returns the generated text or `None` on failure. |

Internally, `_call_with_retry` (a "private" method, prefixed with `_`) implements the retry logic against `litellm.acompletion`, but it is not part of the public interface.

### Design Decisions

- **Encapsulation of retry policy**: Retry behavior (`MAX_RETRIES`, `RETRY_WAIT`) is fully encapsulated inside `_call_with_retry`, so callers only interact with the simple `generate` interface and never handle retries themselves.
- **Selective exception handling**:
  - `litellm.RateLimitError` (HTTP 429) triggers a bounded retry loop with a fixed wait (`RETRY_WAIT`), returning `None` and logging an error once `MAX_RETRIES` is exhausted.
  - `ContextWindowExceededError` is deliberately re-raised (not swallowed), allowing calling code (e.g., `doc_creator.py`) to detect and handle context-length issues (such as chunking/summarization) rather than silently failing.
  - `openai.APIError` is treated as non-retryable: it is logged and immediately returns `None`, avoiding wasted retries on non-transient API errors.
- **Provider-agnostic design**: The client builds a `kwargs` dict and conditionally injects `api_key`/`api_base` only when set, relying on litellm's model-name-based provider auto-detection rather than hardcoding provider-specific logic — keeping the client reusable across different LLM backends.
- **Fail-fast configuration validation**: The constructor raises a `ValueError` immediately if no model is configured, preventing silent misconfiguration from propagating into async call sites.
- **Async-first design**: All API interaction is asynchronous (`async def`, `litellm.acompletion`, `asyncio.sleep`), matching the async orchestration used elsewhere in the pipeline (`main.py`'s `asyncio.run`, `process_all_files`).

# Definition Design Specifications

## `LLMClient`

Async wrapper around litellm's OpenAI-compatible completion API. Centralizes model/API-key/API-base configuration and retry logic so callers only need to supply a prompt and token budget. Raises `ValueError` at construction time if `model` is falsy, enforcing fail-fast behavior instead of deferring to a runtime API error when no model is configured.

## `LLMClient.__init__`

Takes `model: str` (litellm-format model name, defaults to `LLM_MODEL`), `api_key: str` (provider API key, defaults to `LLM_API_KEY`), and `api_base: str` (custom endpoint base URL, defaults to `LLM_API_BASE`). No return value; stores these as instance attributes for reuse across calls.

Exists to validate and centralize connection configuration once per client instance rather than per call. Requires `model` to be a non-empty string; empty/None triggers a `ValueError` with guidance to set `LLM_MODEL` via environment/.env, since a client without a model cannot function.

## `LLMClient._call_with_retry`

Takes `prompt: str` (the text sent as the single user message) and `max_tokens: int` (output token cap). Returns `str | None`: the stripped generated text on success, or `None` if all retry attempts are exhausted or a non-retryable API error occurs.

Responsible for the low-level, retryable interaction with `litellm.acompletion`, isolating error-handling policy from the higher-level `generate` interface. Builds the request kwargs conditionally, only including `api_key`/`api_base` when they are set, so litellm's own defaults/env-based resolution can apply when they are not.

Design decisions:
- Retries only on `litellm.RateLimitError` (HTTP 429), waiting `RETRY_WAIT` seconds between attempts, up to `MAX_RETRIES` total attempts; this treats rate limiting as a transient condition worth waiting out, unlike other errors.
- `ContextWindowExceededError` is deliberately re-raised rather than swallowed, since exceeding the context window is a caller-side input problem that should propagate for the caller to handle (e.g., by truncating/summarizing), not something retrying can fix.
- Other `openai.APIError` instances are treated as fatal: logged and immediately returned as `None` without retrying, since retrying identical malformed/invalid requests is assumed unlikely to succeed.
- Returns `None` (rather than raising) for rate-limit exhaustion and generic API errors, giving callers a uniform "no result" signal that doesn't require catching exceptions in normal failure paths.

Edge cases/constraints: assumes `response.choices[0].message.content` is present and string-like (calls `.strip()` on it directly, so a malformed provider response could raise an unhandled exception). Loop runs exactly `MAX_RETRIES` times; if `MAX_RETRIES <= 0` no call is attempted and the method implicitly returns `None`.

## `LLMClient.generate`

Public entry point for text generation. Takes `prompt: str` and `max_tokens: int` (defaults to `DOC_MAX_TOKENS`). Returns `str | None`, the generated text or `None` on failure/empty input.

Exists as the stable, simple interface for callers (documentation generation, summarization) to obtain LLM output without needing to know about retry/error internals. Short-circuits to `None` immediately when `prompt` is empty/falsy, avoiding an unnecessary API call for a request that cannot produce meaningful output. Delegates all actual work, including exception propagation of `ContextWindowExceededError`, to `_call_with_retry`.

# Dependency Description

### Dependencies (what this file uses)

`codetwine/llm/client.py` depends on `codetwine/config/settings.py` to obtain configuration values needed to initialize and operate the LLM client:

- **LLM_MODEL**: Provides the default model name used to identify which LLM provider/model to call via litellm. The client raises a `ValueError` if this is not set, since a model name is mandatory for operation.
- **LLM_API_KEY**: Supplies the default API key used for authenticating requests to the LLM provider.
- **LLM_API_BASE**: Supplies the default base URL for custom or self-hosted API endpoints.
- **MAX_RETRIES**: Controls how many times the client retries an LLM call when a rate limit (429) error occurs before giving up.
- **RETRY_WAIT**: Specifies the number of seconds to wait between retry attempts after a rate limit error.
- **DOC_MAX_TOKENS**: Provides the default maximum output token limit used when generating text if the caller does not specify one explicitly.

These configuration values allow `LLMClient` to be instantiated with sensible defaults while still permitting override at construction time.

### Dependents (what uses this file)

- **main.py**: Instantiates `LLMClient()` (conditionally, based on `ENABLE_LLM_DOC`) and passes it into `process_all_files` to enable LLM-based documentation generation during the pipeline run.
- **codetwine/doc_creator.py**: Uses `LLMClient` instances passed as parameters in functions like `_summarize_code` and `_summarize_callee_usages` to generate concise natural-language summaries of code blocks and callee usage contexts via LLM calls.
- **codetwine/pipeline.py**: Accepts an `LLMClient | None` parameter in `process_all_files`, passing it through the file-processing workflow so that documentation generation steps can optionally leverage LLM-based summarization.

The dependency direction is **unidirectional**: `main.py`, `codetwine/doc_creator.py`, and `codetwine/pipeline.py` all depend on `codetwine/llm/client.py` for LLM text-generation capability, while `client.py` itself has no dependency on any of these files.

# Data Flow

## Input
| Source | Data | Format |
|---|---|---|
| Caller (`main.py`, `doc_creator.py`, `pipeline.py`) | `prompt: str` | Raw text prompt to send to the LLM |
| Caller | `max_tokens: int` (optional) | Defaults to `DOC_MAX_TOKENS` from settings |
| `codetwine/config/settings.py` | `LLM_MODEL`, `LLM_API_KEY`, `LLM_API_BASE`, `MAX_RETRIES`, `RETRY_WAIT`, `DOC_MAX_TOKENS` | Config values loaded at import time, used to initialize/configure the client instance |

## Main Transformation Flow

```
generate(prompt, max_tokens)
   │
   ├─ guard: empty prompt → return None
   │
   ▼
_call_with_retry(prompt, max_tokens)
   │
   ├─ build request kwargs:
   │     { model, max_tokens, messages: [{role: "user", content: prompt}] }
   │     + optional api_key / api_base if set
   │
   ├─ loop up to MAX_RETRIES attempts:
   │     ├─ litellm.acompletion(**kwargs)  → response object
   │     │     └─ success: extract response.choices[0].message.content.strip() → return str
   │     ├─ RateLimitError → sleep(RETRY_WAIT), retry; on last attempt → return None
   │     ├─ ContextWindowExceededError → re-raise immediately (not swallowed)
   │     └─ openai.APIError → log and return None (no retry)
   │
   ▼
return generated text (str) or None
```

The class itself holds no mutable state beyond configuration (`model`, `api_key`, `api_base`) set at construction; each call is stateless and independent.

## Output
| Destination | Data | Format |
|---|---|---|
| Caller | Generated text | `str` on success, `None` on failure (empty prompt, exhausted retries, or non-retryable API error) |
| Caller | Exception propagation | `ContextWindowExceededError` is re-raised for the caller to handle (e.g., prompt truncation/splitting logic upstream) |

## Key Data Structures

| Structure | Fields | Purpose |
|---|---|---|
| `LLMClient` instance | `model`, `api_key`, `api_base` | Holds connection/config parameters for all LLM calls made by this client |
| `kwargs` (request payload) | `model`, `max_tokens`, `messages` (list of `{role, content}`), optional `api_key`, `api_base` | Assembled per-call payload passed to `litellm.acompletion` |
| `response` (litellm return object) | `choices[0].message.content` | Only the generated text content is extracted; rest of response object is discarded |

# Error Handling

## Overall Strategy

`LLMClient` follows a **graceful degradation** strategy for transient/recoverable failures and a **fail-fast (propagate)** strategy for unrecoverable or caller-relevant failures. Configuration validation at construction time is fail-fast (raises immediately), while runtime API call failures are absorbed and converted to a `None` return value so that calling code (e.g. `doc_creator.py`, `pipeline.py`) can continue processing other files/units instead of crashing the whole run. One exception—context window overflow—is deliberately re-raised rather than swallowed, since it signals a structural problem (input too large) that the caller must handle differently (e.g., by chunking or skipping).

## Error Patterns and Handling Policy

| Error Type | Handling | Impact |
|---|---|---|
| Missing/empty `model` at initialization (`ValueError`) | Fail-fast: raised immediately in `__init__`, not caught | Client cannot be constructed; caller must fix configuration before startup |
| Empty/falsy `prompt` in `generate` | Guarded early return of `None` (no API call attempted) | Caller receives `None`, treated as "no result", processing continues |
| `litellm.RateLimitError` (HTTP 429) | Retried up to `MAX_RETRIES` times with `RETRY_WAIT` second delay between attempts; logs a warning on each retry | Transparent recovery if rate limit clears; if retries exhausted, logs an error and returns `None` instead of raising |
| `litellm.ContextWindowExceededError` | Not caught for recovery; explicitly re-raised | Propagates to the caller, signaling that the prompt/content is too large for the model and requires different handling upstream |
| `openai.APIError` (and subclasses) | Caught, logged as an error, no retry performed | Immediate `None` return; failure is not retried since it's assumed non-transient |
| All retry attempts exhausted (rate limit case) | Logged via `logger.error`, returns `None` | Signals failure without raising, allowing batch/pipeline processing to continue for other items |

## Design Considerations

- Returning `None` (rather than raising) on most failures is intentional, enabling upstream batch/multi-file processing (e.g., `process_all_files`, `_summarize_code`) to skip a failed item without aborting the entire pipeline.
- `ContextWindowExceededError` is the sole exception explicitly re-raised, distinguishing "prompt too large" (a caller-side/structural issue) from transient or non-retryable service errors that are better represented as `None`.
- Retry logic is limited strictly to `RateLimitError`, reflecting the assumption that only rate-limiting is a transient condition worth waiting out; all other `openai.APIError` cases are treated as immediately fatal for that call.
- Logging (`logger.warning` for retries, `logger.error` for final failures) is used to preserve observability despite errors being converted into `None` return values rather than exceptions.

# Summary

`codetwine/llm/client.py` centralizes async LLM access via `LLMClient`, wrapping `litellm.acompletion`. `__init__(model, api_key, api_base)` validates config (raises `ValueError` if no model), storing defaults from `settings.py`. `generate(prompt, max_tokens)` returns generated text or `None`, short-circuiting on empty prompts and delegating to private `_call_with_retry`, which retries on `RateLimitError`, re-raises `ContextWindowExceededError`, and returns `None` on other `APIError`s. Used by `main.py`, `doc_creator.py`, `pipeline.py` for stateless, provider-agnostic LLM text generation with built-in retry/error handling.
