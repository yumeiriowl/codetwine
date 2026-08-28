# Design Document: codetwine/llm/client.py

# Overview & Purpose

## Role and Responsibilities

`codetwine/llm/client.py` provides a single, centralized abstraction (`LLMClient`) for making asynchronous LLM API calls via the `litellm` library. It exists as a separate file to isolate all LLM communication concerns—model configuration, request construction, retry/backoff logic, and error handling—from the rest of the application (e.g., `codetwine/doc_creator.py`, `codetwine/pipeline.py`, and `main.py`), which consume this client to generate documentation summaries without needing to know the details of provider selection, rate-limit handling, or the underlying API library.

The module reads its default configuration (model name, API key, API base, retry count, retry wait time, and max output tokens) from `codetwine/config/settings.py`, keeping provider/runtime configuration centralized there while this file focuses purely on the call mechanics.

## Public Interfaces

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `LLMClient.__init__` | `model: str = LLM_MODEL`, `api_key: str = LLM_API_KEY`, `api_base: str = LLM_API_BASE` | `None` | Initializes the client with model/API settings; raises `ValueError` if no model is configured. |
| `LLMClient._call_with_retry` | `prompt: str`, `max_tokens: int` | `str \| None` | Calls `litellm.acompletion` with retry on rate-limit errors, re-raises `ContextWindowExceededError`, and returns `None` on API errors or exhausted retries. |
| `LLMClient.generate` | `prompt: str`, `max_tokens: int = DOC_MAX_TOKENS` | `str \| None` | Public entry point: validates the prompt is non-empty and delegates to `_call_with_retry` to produce the generated text. |

## Design Decisions

- **Adapter/Wrapper pattern**: `LLMClient` wraps `litellm.acompletion`, presenting a simplified, project-specific interface (`generate`) so callers don't interact directly with `litellm` or `openai` exception types beyond what's necessary.
- **Retry-with-backoff strategy**: On `litellm.RateLimitError` (HTTP 429), the client retries up to `MAX_RETRIES` times, sleeping `RETRY_WAIT` seconds between attempts, and logs/returns `None` when retries are exhausted—centralizing resilience logic in one place rather than duplicating it across callers.
- **Selective error propagation**: `ContextWindowExceededError` is deliberately re-raised (not swallowed), allowing calling code to handle context-length-specific failures differently (e.g., splitting/summarizing input), while generic `openai.APIError` failures are logged and suppressed by returning `None`, treating them as non-retryable.
- **Fail-fast configuration validation**: The constructor raises a `ValueError` immediately if `model` is empty, preventing misconfigured clients from being used downstream.
- **Optional parameter inclusion**: `api_key` and `api_base` are only added to the request kwargs if truthy, deferring to `litellm`/provider defaults otherwise.

# Definition Design Specifications

## `LLMClient`

Async wrapper around litellm's OpenAI-compatible completion API, providing a unified interface for prompt-based text generation regardless of the underlying provider. Its responsibility is to centralize model/API configuration and retry logic so that callers (e.g., `main.py`, `codetwine/doc_creator.py`, `codetwine/pipeline.py`) do not need to handle provider-specific details or transient failures themselves.

Design intent: by relying on litellm, the client abstracts away differences between LLM providers, using the model name prefix for provider auto-detection. The class enforces at construction time that a model must be configured, failing fast rather than allowing silent misconfiguration to propagate into async call sites.

## `__init__`

Arguments: `model` (str, litellm-formatted model identifier, defaults to `LLM_MODEL`), `api_key` (str, provider API key, defaults to `LLM_API_KEY`), `api_base` (str, custom endpoint base URL, defaults to `LLM_API_BASE`). No return value.

Responsible for validating and storing the configuration needed for every subsequent API call. Raises `ValueError` immediately if `model` is empty/falsy, since a missing model makes the client unusable and this should surface at initialization rather than at first call, giving a clear actionable error message pointing to environment configuration.

## `_call_with_retry`

Arguments: `prompt` (str, the fully-formed text to send to the LLM), `max_tokens` (int, upper bound on generated tokens). Returns `str | None`: the stripped generated text on success, or `None` if generation could not be completed.

This method exists to isolate the retry/error-handling policy from the public `generate` entry point, keeping request construction and failure handling in one place. It builds the `litellm.acompletion` keyword arguments conditionally, only including `api_key`/`api_base` when they are set, so that litellm's own defaults or environment-based resolution can apply when they are not explicitly configured on the client.

Design decisions:
- Retries only on `litellm.RateLimitError` (HTTP 429), waiting `RETRY_WAIT` seconds between attempts, up to `MAX_RETRIES` total attempts; this reflects the assumption that rate-limit errors are transient and worth waiting out, whereas other errors are not.
- `ContextWindowExceededError` is deliberately re-raised rather than swallowed, since this is a caller-level input-sizing problem that the retry loop cannot fix and callers may need to handle specially (e.g., by truncating or chunking input).
- Generic `openai.APIError` is treated as non-retryable: it is logged and `None` is returned immediately, on the assumption that such errors (e.g., malformed requests) will not be resolved by retrying.
- If all retry attempts are exhausted on rate-limit errors, the method logs an error and returns `None` rather than raising, keeping failure signaling consistent (`None`) across all non-context-window failure modes.

Edge case: if the loop completes without hitting a `return` or `raise` in any branch (not expected under normal control flow given the exhaustive except handling), the function implicitly returns `None` due to the `for` loop finishing without a final return statement.

## `generate`

Arguments: `prompt` (str, the prompt text to send to the LLM), `max_tokens` (int, defaults to `DOC_MAX_TOKENS`, the generation length limit). Returns `str | None`: the generated text, or `None` if `prompt` is falsy/empty or generation failed.

This is the public entry point intended for external callers, providing an input guard so that empty prompts short-circuit without making a network call, and otherwise delegating to `_call_with_retry` for the actual API interaction and retry handling. Keeping this method thin ensures the retry/error-handling complexity remains isolated in a single internal method.

# Dependency Description

## Dependencies (what this file uses)

`codetwine/llm/client.py` relies on `codetwine/config/settings.py` for all runtime configuration needed to construct and operate the `LLMClient`:

- **LLM_MODEL**: Used as the default model identifier passed to `litellm.acompletion`. It also drives a validation check in `__init__` that raises a `ValueError` if no model is configured, ensuring the client cannot be instantiated without a valid target model.
- **LLM_API_KEY**: Supplies the default API key for authenticating requests to the LLM provider. It is conditionally added to the request kwargs when calling the API.
- **LLM_API_BASE**: Supplies the default base URL for custom or self-hosted LLM endpoints, conditionally included in the API call parameters.
- **MAX_RETRIES**: Controls how many times `_call_with_retry` attempts the LLM call before giving up on rate-limit errors.
- **RETRY_WAIT**: Defines the wait time (in seconds) between retry attempts when a rate-limit error occurs.
- **DOC_MAX_TOKENS**: Used as the default value for the `max_tokens` parameter in `generate`, capping the length of LLM-generated output.

These constants allow `LLMClient` to be configured centrally without hardcoding provider details, retry behavior, or token limits.

## Dependents (what uses this file)

- **main.py**: Instantiates `LLMClient` (conditionally, based on `ENABLE_LLM_DOC`) and passes it into `process_all_files` for the documentation generation pipeline.
- **codetwine/pipeline.py**: Accepts an `LLMClient` instance (or `None`) as a parameter in `process_all_files`, using it to drive LLM-based processing across the analyzed project files.
- **codetwine/doc_creator.py**: Uses `LLMClient` in `_summarize_code` and `_summarize_callee_usages` to generate concise summaries of code blocks and large callee usage contexts via LLM calls.

The dependency direction is unidirectional: `main.py`, `codetwine/pipeline.py`, and `codetwine/doc_creator.py` depend on `codetwine/llm/client.py` for LLM interaction capabilities, while `client.py` itself has no dependency on these files.

# Data Flow

## Input
- **Source**: Callers such as `codetwine/doc_creator.py` (`_summarize_code`, `_summarize_callee_usages`) and `codetwine/pipeline.py` (`process_all_files`) instantiate `LLMClient` and call `generate(prompt, max_tokens)`.
- **Format**:
  - `prompt: str` — a completed natural-language/code prompt string.
  - `max_tokens: int` — optional, defaults to `DOC_MAX_TOKENS` from settings.
  - Constructor inputs (`model`, `api_key`, `api_base`) default to config values (`LLM_MODEL`, `LLM_API_KEY`, `LLM_API_BASE`) loaded from `codetwine/config/settings.py`.

## Main Transformation Flow

```
generate(prompt, max_tokens)
    │
    ├─ guard: empty prompt → return None
    │
    └─ _call_with_retry(prompt, max_tokens)
            │
            ├─ build kwargs dict:
            │     { model, max_tokens,
            │       messages: [{role: "user", content: prompt}],
            │       api_key? , api_base? }
            │
            ├─ loop up to MAX_RETRIES:
            │     await litellm.acompletion(**kwargs)
            │        │
            │        ├─ success → extract response.choices[0].message.content
            │        │             → strip() → return str
            │        │
            │        ├─ RateLimitError → sleep(RETRY_WAIT), retry
            │        │                   (or return None if retries exhausted)
            │        │
            │        ├─ ContextWindowExceededError → re-raise (propagated to caller)
            │        │
            │        └─ openai.APIError → log error, return None immediately
            │
            └─ return generated text or None
```

- The prompt/config values are assembled into a single request payload (`kwargs`) sent to `litellm.acompletion`, an OpenAI-compatible async completion call.
- The raw API response object is narrowed down to just the text content field, trimmed of whitespace.
- Error conditions are converted into either a retry loop, an immediate `None`, or a re-raised exception, rather than propagating raw API response data.

## Output
- **Format**: `str | None`
  - `str`: stripped generated text from the LLM response.
  - `None`: returned when prompt is empty, rate limit retries are exhausted, or an `openai.APIError` occurs.
  - Exception: `ContextWindowExceededError` is not swallowed—it is re-raised to the caller.
- **Destination**: Returned to calling code (e.g., `doc_creator.py` summarization functions, `pipeline.py` orchestration), which uses the text or handles the `None`/exception case.

## Key Data Structures

| Structure | Fields | Purpose |
|---|---|---|
| `self` (instance state) | `model: str`, `api_key: str`, `api_base: str` | Holds connection/config parameters reused across all `generate` calls |
| `kwargs` (request payload) | `model`, `max_tokens`, `messages` (list with one `{role, content}` dict), optional `api_key`, `api_base` | Assembled per-call arguments passed directly to `litellm.acompletion` |
| `response` (litellm result) | `choices[0].message.content` (only field accessed) | External API response; only the message text is extracted |

No persistent or shared data structures are maintained beyond the client's own configuration attributes; each `generate` call is stateless aside from retry/backoff timing controlled by `MAX_RETRIES` and `RETRY_WAIT`.

# Error Handling

## Overall Strategy

`LLMClient` adopts a **graceful degradation** approach for transient or unrecoverable LLM call failures, returning `None` instead of propagating most exceptions, while making a single deliberate exception — context window overflow — **fail-fast** by re-raising it to the caller. This allows upstream orchestration code (e.g., `doc_creator.py`, `pipeline.py`) to treat `None` as a uniform "generation failed" signal without needing to catch multiple exception types, while still surfacing context-size problems explicitly so callers can react (e.g., by chunking or resizing input).

## Error Patterns and Handling Policy

| Error Type | Handling | Impact |
|---|---|---|
| Empty/falsy `prompt` (in `generate`) | Short-circuits before calling the API, returns `None` immediately | No API call is made; caller receives `None` as if generation failed |
| `litellm.RateLimitError` (HTTP 429) | Retried up to `MAX_RETRIES` attempts with a fixed `RETRY_WAIT` second delay between attempts, logged as a warning on each retry | Temporary rate limiting is absorbed transparently; if all retries fail, error is logged and `None` is returned |
| `litellm.ContextWindowExceededError` | Caught and immediately re-raised (no retry, no logging here) | Propagates to the caller, allowing distinct handling (e.g., input truncation) instead of silent failure |
| `openai.APIError` (and subclasses) | Caught, logged as an error, no retry attempted | Fails immediately; `None` is returned to indicate failure |
| All attempts exhausted for rate limiting | Logged as an error ("max retries reached") | `None` returned; caller must handle absence of result |

## Design Considerations

- Retry logic is scoped narrowly to rate-limit errors only; other API errors are treated as non-retryable to avoid masking persistent issues (e.g., invalid requests, auth failures) behind repeated retries.
- `ContextWindowExceededError` is deliberately excluded from the "return `None`" pattern used elsewhere, since it represents a structural/input-size problem that the caller is expected to handle differently (not a transient failure suitable for silent suppression).
- The consistent `str | None` return contract across `generate` and `_call_with_retry` centralizes failure signaling, simplifying error handling for dependents that only need to check for `None`.
- Retry wait time and retry count are externalized via configuration (`MAX_RETRIES`, `RETRY_WAIT`) rather than hardcoded, allowing tuning without code changes.

# Summary

`codetwine/llm/client.py` defines `LLMClient`, an async wrapper around `litellm.acompletion` centralizing LLM configuration and retry logic. `__init__(model, api_key, api_base)` validates config, raising `ValueError` if model is missing. `generate(prompt, max_tokens)` is the public entry point, guarding empty prompts and delegating to `_call_with_retry`, which builds request kwargs, retries on `RateLimitError` up to `MAX_RETRIES`, re-raises `ContextWindowExceededError`, and returns `None` on other API errors. Returns `str | None`. Used by `doc_creator.py`, `pipeline.py`, `main.py`.
