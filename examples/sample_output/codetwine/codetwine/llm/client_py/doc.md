# Design Document: codetwine/llm/client.py

# Overview & Purpose

## Role and Responsibilities

`codetwine/llm/client.py` provides a single, isolated wrapper around LLM API access for the entire project. It encapsulates all details of communicating with an LLM provider (via `litellm`'s OpenAI-compatible async interface), including configuration binding, retry/backoff logic for rate limiting, and error handling. By centralizing this logic in its own module, the rest of the codebase (`main.py`, `codetwine/doc_creator.py`, `codetwine/pipeline.py`) can depend on a simple, stable async interface (`LLMClient.generate`) without needing to know provider-specific details, retry strategies, or configuration sourcing. This separation keeps LLM integration concerns decoupled from documentation generation and pipeline orchestration logic, and allows the underlying LLM provider/model to be swapped or reconfigured via settings without touching calling code.

## Main Public Interfaces

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `LLMClient.__init__` | `model: str = LLM_MODEL`, `api_key: str = LLM_API_KEY`, `api_base: str = LLM_API_BASE` | `None` | Validates that a model is configured (raises `ValueError` if missing) and stores model/credentials/endpoint for later calls. |
| `LLMClient.generate` | `prompt: str`, `max_tokens: int = DOC_MAX_TOKENS` | `str \| None` | Public entry point: returns `None` immediately for an empty prompt, otherwise delegates to `_call_with_retry` to obtain generated text. |
| `LLMClient._call_with_retry` (internal) | `prompt: str`, `max_tokens: int` | `str \| None` | Calls `litellm.acompletion` with retry-on-rate-limit logic (up to `MAX_RETRIES`, waiting `RETRY_WAIT` seconds between attempts), re-raises `ContextWindowExceededError`, and returns `None` on non-retryable `openai.APIError` or exhausted retries. |

## Design Decisions

- **Thin wrapper / adapter pattern**: `LLMClient` acts as an adapter over `litellm.acompletion`, exposing only what callers need (`generate`) while hiding provider-specific kwargs construction (model, api_key, api_base, messages).
- **Fail-fast configuration validation**: The constructor raises a `ValueError` immediately if `model` is not set, preventing misconfigured clients from being used downstream.
- **Selective error handling strategy**: Different exception types are handled differently by design — `litellm.RateLimitError` triggers a bounded retry loop with delay, `ContextWindowExceededError` is deliberately re-raised (not swallowed) so callers can handle it distinctly, and `openai.APIError` fails immediately without retrying, avoiding wasted calls on non-transient errors.
- **Async-first design**: The client is built entirely around `async`/`await` (using `asyncio.sleep` for retry waits) to integrate with the project's async pipeline (`asyncio.run(process_all_files(...))` in `main.py`).
- **Configuration externalized**: All tunable parameters (model, API key/base, retry count, retry wait, max tokens) are sourced from `codetwine/config/settings.py` rather than hardcoded, keeping the client reusable across environments.

# Definition Design Specifications

## `LLMClient`

Async wrapper around `litellm.acompletion` providing a single, provider-agnostic entry point for generating text from a prompt, with built-in retry handling for transient failures.

**Design intent**: Centralizes all LLM invocation logic (model selection, credentials, retry/backoff behavior) so callers (`doc_creator.py`, `pipeline.py`, `main.py`) only need to call `generate()` without knowing provider-specific details. Using `litellm` as the underlying transport allows the model string to determine the provider automatically, decoupling this class from any single LLM vendor's SDK.

### `__init__(self, model: str = LLM_MODEL, api_key: str = LLM_API_KEY, api_base: str = LLM_API_BASE) -> None`

- **Arguments**:
  - `model`: Model identifier in litellm's provider-prefixed format (e.g., `"openai/gpt-4"`); defaults to the configured `LLM_MODEL`.
  - `api_key`: API key for the target provider; defaults to `LLM_API_KEY`. May be empty for providers/endpoints that don't require a key.
  - `api_base`: Custom base URL for the API endpoint; defaults to `LLM_API_BASE`. May be empty to use the provider's default endpoint.
- **Returns**: `None`.
- **Responsibility**: Validates and stores the LLM connection configuration at construction time so that misconfiguration is caught early (fail-fast) rather than during an async call.
- **Design decision**: Raises `ValueError` immediately if `model` is falsy, since a missing model makes the client unusable; this surfaces configuration errors at startup instead of at the first `generate()` call.
- **Edge cases**: `api_key` and `api_base` are optional and stored as-is even if empty; only `model` is mandatory.

### `async _call_with_retry(self, prompt: str, max_tokens: int) -> str | None`

- **Arguments**:
  - `prompt`: The text prompt to send to the LLM as a single user-role message.
  - `max_tokens`: Maximum number of output tokens the LLM may generate.
- **Returns**: The trimmed generated text (`str`) on success, or `None` if all retry attempts are exhausted or a non-retryable API error occurs.
- **Responsibility**: Performs the actual API call to the LLM via `litellm.acompletion` and implements differentiated error handling for the distinct failure modes that can occur (rate limiting vs. context overflow vs. other API errors).
- **Design decisions**:
  - Rate-limit errors (`litellm.RateLimitError`) are retried up to `MAX_RETRIES` times with a fixed `RETRY_WAIT` second delay between attempts, since these are typically transient and self-resolving.
  - `ContextWindowExceededError` is deliberately **not** retried and is re-raised, since retrying with the same oversized prompt would fail identically; the caller is expected to handle this (e.g., by shortening the prompt).
  - Generic `openai.APIError` is treated as non-retryable and immediately returns `None`, since such errors (e.g., malformed requests) are unlikely to succeed on retry.
  - `api_key` and `api_base` are only added to the request kwargs when non-empty, allowing `litellm` to fall back to its own defaults/environment configuration otherwise.
  - Returning `None` on failure (rather than raising) lets calling code treat LLM failures as a soft/optional outcome (e.g., skipping summarization) rather than crashing the pipeline.
- **Edge cases/constraints**: Assumes `MAX_RETRIES >= 1` for the loop to execute at least once; on the final attempt, a rate-limit error is logged and `None` is returned rather than retried further. `ContextWindowExceededError` bypasses the retry loop entirely and propagates to the caller.

### `async generate(self, prompt: str, max_tokens: int = DOC_MAX_TOKENS) -> str | None`

- **Arguments**:
  - `prompt`: The prompt string to send to the LLM; may be empty.
  - `max_tokens`: Maximum output token limit, defaulting to `DOC_MAX_TOKENS`.
- **Returns**: Generated text (`str`) from the LLM, or `None` if `prompt` is empty or generation failed.
- **Responsibility**: Serves as the public-facing API of `LLMClient`, providing a simple guard against empty prompts before delegating to the retry-handling internals.
- **Design decision**: Short-circuits with `None` for falsy `prompt` values to avoid unnecessary API calls for empty input, keeping the retry logic in `_call_with_retry` focused solely on API-level concerns.
- **Edge cases**: Does not validate `max_tokens` (e.g., negative or zero values are passed through unchecked to `litellm`).

# Dependency Description

## Dependencies (what this file uses)

`codetwine/llm/client.py` depends on `codetwine/config/settings.py` for its runtime configuration:

- **LLM_MODEL**: Used as the default model identifier passed to `LLMClient.__init__`, specifying which litellm-supported model to call. The constructor raises a `ValueError` if this value is empty, making it a required configuration.
- **LLM_API_KEY**: Used as the default API key for authenticating requests to the LLM provider, passed to `litellm.acompletion` when set.
- **LLM_API_BASE**: Used as the default base URL for custom or self-hosted LLM endpoints, passed to `litellm.acompletion` when set.
- **MAX_RETRIES**: Defines how many attempts `_call_with_retry` makes before giving up on rate-limited requests.
- **RETRY_WAIT**: Defines the number of seconds to wait between retry attempts after a rate-limit error.
- **DOC_MAX_TOKENS**: Used as the default value for the `max_tokens` parameter in `generate`, controlling the maximum length of the generated output when the caller doesn't specify one.

## Dependents (what uses this file)

- **main.py**: Instantiates `LLMClient()` (conditionally, based on `ENABLE_LLM_DOC`) and passes the instance into `process_all_files` to enable LLM-based documentation generation for the project.
- **codetwine/doc_creator.py**: Uses an `LLMClient` instance (received as a parameter) in `_summarize_code` and `_summarize_callee_usages` to generate concise natural-language summaries of code blocks and large callee usage contexts via the LLM.
- **codetwine/pipeline.py**: Accepts an `LLMClient | None` instance as a parameter in `process_all_files`, forwarding it through the file-processing pipeline so downstream components (like `doc_creator.py`) can perform LLM-based summarization when available.

The dependency direction is **unidirectional**: `main.py`, `codetwine/pipeline.py`, and `codetwine/doc_creator.py` depend on `codetwine/llm/client.py` for LLM access, while `codetwine/llm/client.py` has no dependency on any of these files.

# Data Flow

## Input
| Source | Data | Format |
|---|---|---|
| Caller (`main.py`, `doc_creator.py`, `pipeline.py`) | `prompt: str` | Plain text prompt to send to the LLM |
| Caller | `max_tokens: int` | Optional override, defaults to `DOC_MAX_TOKENS` |
| Constructor args / `settings.py` | `model`, `api_key`, `api_base` | Strings from config, defaulting to `LLM_MODEL`, `LLM_API_KEY`, `LLM_API_BASE` |

## Main Transformation Flow

```
generate(prompt, max_tokens)
   │
   ├─ prompt empty? ──yes──> return None
   │
   ▼
_call_with_retry(prompt, max_tokens)
   │
   ▼
build kwargs dict:
   { model, max_tokens, messages: [{role:"user", content: prompt}],
     api_key (optional), api_base (optional) }
   │
   ▼
litellm.acompletion(**kwargs)  ── async API call
   │
   ├─ success ──> response.choices[0].message.content.strip() ──> return str
   │
   ├─ RateLimitError ──> sleep(RETRY_WAIT) ──> retry (up to MAX_RETRIES) ──> else log error, return None
   │
   ├─ ContextWindowExceededError ──> re-raise (propagated to caller, no retry)
   │
   └─ openai.APIError ──> log error, return None (no retry)
```

## Output
| Destination | Data | Format |
|---|---|---|
| Caller | Generated text | `str` (stripped) on success |
| Caller | Failure signal | `None` (empty prompt, rate-limit exhaustion, API error) |
| Caller | Exception propagation | `ContextWindowExceededError` raised, not swallowed |
| Logger | Warning/error messages | Log lines on rate limiting or API failure |

## Key Data Structures

| Structure | Fields | Purpose |
|---|---|---|
| `self` (instance state) | `model`, `api_key`, `api_base` | Holds LLM connection config for reuse across calls |
| `kwargs` (per-call dict) | `model`, `max_tokens`, `messages` (list with single `{"role": "user", "content": prompt}` dict), optional `api_key`, `api_base` | Assembled request payload passed directly to `litellm.acompletion` |
| `response` (litellm result object) | `choices[0].message.content` | Raw API response; only the first choice's message content is extracted |

No intermediate collections/maps beyond the single-request `kwargs` and `messages` list are constructed; data flows linearly from prompt string → API request → response string (or `None`/exception).

# Error Handling

**Overall strategy:** `LLMClient` follows a graceful-degradation approach for transient/recoverable failures and a fail-fast approach for configuration errors and non-recoverable failures. The client is designed so that callers receive either a valid generated string or `None`, never an unhandled exception propagating from routine API failures — with one deliberate exception (context window overflow), which is re-raised for the caller to handle explicitly.

**Error patterns and handling policy:**

| Error type | Handling | Impact |
|---|---|---|
| Missing `model` (empty `LLM_MODEL`) at construction | Raises `ValueError` immediately in `__init__` | Fail-fast; client cannot be instantiated without a valid model, preventing later runtime failures |
| Empty/falsy `prompt` in `generate()` | Short-circuits and returns `None` without calling the LLM | No API call is made; caller must handle `None` as "no result" |
| `litellm.RateLimitError` (HTTP 429) | Retries up to `MAX_RETRIES` attempts, waiting `RETRY_WAIT` seconds between attempts; logs a warning on each retry | Recoverable via retry; degrades to `None` with an error log once retries are exhausted |
| `litellm.ContextWindowExceededError` | Re-raised immediately, bypassing retry logic | Not treated as recoverable at this layer; propagated to caller for explicit handling (e.g., chunking/summarization logic upstream) |
| `openai.APIError` (and subclasses) | Logged as an error and returns `None` immediately, no retry | Treated as non-transient failure; fails fast without consuming retry budget |
| Exhaustion of all retry attempts (rate limit only) | Logs an error and returns `None` | Caller receives `None`, must treat it as generation failure |

**Design considerations:**
- Retry logic is narrowly scoped to rate-limit errors only, reflecting the assumption that this is the primary transient failure mode worth retrying; other API errors are assumed to indicate persistent/non-retryable problems.
- `ContextWindowExceededError` is intentionally excluded from the retry/suppress pattern and re-raised, since retrying or silently returning `None` would not resolve an oversized-prompt issue — this signals that upstream code (e.g., `doc_creator.py`) is expected to catch it and reduce input size before retrying.
- The uniform `None` return convention (rather than raising) for most failure paths pushes failure interpretation to callers, simplifying the client's contract at the cost of losing detailed error context beyond what is logged.
- Configuration validation (`model` presence) is enforced synchronously at construction time rather than deferred to the first API call, ensuring invalid setups fail immediately rather than during async execution.

# Summary

`codetwine/llm/client.py` defines `LLMClient`, an async adapter over `litellm.acompletion` giving callers a single stable method: `generate(prompt, max_tokens) -> str | None`. It validates config (model required) at init, sourcing model/api_key/api_base/retry settings/max_tokens from `settings.py`. Internally, `_call_with_retry` retries `RateLimitError` up to `MAX_RETRIES`, re-raises `ContextWindowExceededError`, and returns `None` on other `APIError`s or empty prompts. Used by `main.py`, `pipeline.py`, `doc_creator.py` for LLM-based summarization; has no dependencies on them.
