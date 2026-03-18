# Design Document: codetwine/llm/client.py

## Overview & Purpose

# Overview & Purpose

## Role and Responsibilities

`client.py` provides a single, self-contained async wrapper around the [litellm](https://github.com/BerriAI/litellm) library for making LLM API calls within the CodeTwine project. It exists as a separate module to isolate all LLM communication concerns—model selection, authentication, endpoint configuration, retry logic, and error handling—from the rest of the pipeline. Consumers such as `doc_creator.py` and `pipeline.py` depend only on the `LLMClient` type and its `generate` method, with no knowledge of the underlying HTTP client or retry mechanics.

## Public Interface

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `LLMClient` (class) | — | — | Encapsulates LLM API configuration and async call logic |
| `LLMClient.__init__` | `model: str`, `api_key: str`, `api_base: str` | `None` | Validates and stores model name, API key, and base URL; raises `ValueError` if `model` is empty |
| `LLMClient.generate` | `prompt: str`, `max_tokens: int = DOC_MAX_TOKENS` | `str \| None` | Public entry point: sends a prompt to the LLM and returns the generated text, or `None` on failure or empty prompt |
| `LLMClient._call_with_retry` | `prompt: str`, `max_tokens: int` | `str \| None` | Internal: executes the litellm API call with up to `MAX_RETRIES` attempts, handling rate-limit back-off and error cases |

## Design Decisions

- **litellm as the transport layer**: All API calls go through `litellm.acompletion`, which provides a unified OpenAI-compatible interface across providers. The model name prefix is used by litellm to auto-detect the target provider, keeping provider-specific logic out of this file.

- **Differentiated error handling**: Three error types are handled distinctly:
  - `litellm.RateLimitError` — retried up to `MAX_RETRIES` times with an `asyncio.sleep(RETRY_WAIT)` delay between attempts.
  - `ContextWindowExceededError` — re-raised immediately without retry, allowing callers (e.g., `doc_creator.py`) to implement their own fallback strategy.
  - `openai.APIError` — logged and returns `None` immediately; no retry is attempted.

- **Optional kwargs pattern**: `api_key` and `api_base` are only added to the `litellm.acompletion` call if they are non-empty strings, supporting both hosted providers (where credentials come from environment variables managed by litellm itself) and custom endpoints.

- **Configuration via `settings.py`**: All tunable values (`LLM_MODEL`, `LLM_API_KEY`, `LLM_API_BASE`, `MAX_RETRIES`, `RETRY_WAIT`, `DOC_MAX_TOKENS`) are imported from the central `settings.py` module and used as constructor defaults, making the class usable with zero arguments in normal operation while remaining fully injectable for testing.

## Definition Design Specifications

# Definition Design Specifications

---

## Class: `LLMClient`

**Responsibility:** Provides an async wrapper around the `litellm` library to send prompts to a configured LLM provider and return generated text. Encapsulates connection parameters and retry behavior, decoupling callers from provider-specific API details.

**Constructor: `__init__`**

| Parameter | Type | Meaning |
|---|---|---|
| `model` | `str` | litellm-format model identifier (e.g., `"openai/gpt-4o"`); defaults to `LLM_MODEL` from settings |
| `api_key` | `str` | Provider authentication key; defaults to `LLM_API_KEY` from settings |
| `api_base` | `str` | Base URL for custom or self-hosted endpoints; defaults to `LLM_API_BASE` from settings |

**Returns:** `None` (constructor)

**Design decisions:**
- Raises `ValueError` immediately if `model` is an empty string, since a missing model name makes the client entirely non-functional and there is no sensible fallback. The error message includes actionable guidance for the user.
- `api_key` and `api_base` are stored but treated as optional at construction time; their emptiness is checked lazily at call time to allow configurations where one or both are unnecessary (e.g., models that infer credentials from the environment).

**Constraints:** `model` must be a non-empty string.

---

## Method: `_call_with_retry`

**Signature:** `async def _call_with_retry(self, prompt: str, max_tokens: int) -> str | None`

| Parameter | Type | Meaning |
|---|---|---|
| `prompt` | `str` | The fully constructed prompt string to send to the LLM |
| `max_tokens` | `int` | Upper bound on the number of tokens the model may produce in its response |

**Returns:** The generated text as a stripped `str`, or `None` if all retry attempts are exhausted or a non-retryable error occurs.

**Responsibility:** Implements the retry policy for transient rate-limit errors and distinguishes between retryable and non-retryable failure modes, insulating `generate` from provider-level reliability concerns.

**Design decisions:**
- Only `litellm.RateLimitError` triggers a retry with a fixed delay of `RETRY_WAIT` seconds, repeated up to `MAX_RETRIES` times. All other errors are treated as non-retryable by design: `ContextWindowExceededError` is re-raised so callers can react to it explicitly (e.g., by truncating input), and `openai.APIError` causes an immediate `None` return since these errors are generally not transient.
- `api_key` and `api_base` are added to the `litellm.acompletion` call only when non-empty, allowing litellm's own environment-variable resolution to operate when these values are absent.
- Returns `None` rather than raising on exhausted retries or API errors, giving callers a uniform optional result type to handle gracefully without exception handling at every call site.

**Edge cases:**
- If `MAX_RETRIES` is 1, the rate-limit path logs an error and returns `None` immediately without sleeping.
- `ContextWindowExceededError` propagates unconditionally regardless of the attempt count.

---

## Method: `generate`

**Signature:** `async def generate(self, prompt: str, max_tokens: int = DOC_MAX_TOKENS) -> str | None`

| Parameter | Type | Meaning |
|---|---|---|
| `prompt` | `str` | The prompt string to submit to the LLM |
| `max_tokens` | `int` | Maximum tokens for the response; defaults to `DOC_MAX_TOKENS` from settings (default value: 8192) |

**Returns:** The LLM-generated text as a `str`, or `None` if the prompt is empty or generation failed.

**Responsibility:** Serves as the public entry point for LLM text generation, performing a prompt validity guard before delegating to the retry-aware internal method.

**Design decisions:**
- An empty prompt short-circuits to `None` immediately rather than making a wasted API call. This is the only input validation applied at this layer; content correctness is the caller's responsibility.
- The default `max_tokens` is sourced from `DOC_MAX_TOKENS` in settings, aligning the default with the documentation-generation use case that is the primary consumer of this client.

**Constraints:** Callers should not pass an empty `prompt` if a meaningful result is expected; `None` is returned silently in that case without logging.

## Dependency Description

## Dependency Description

### Dependencies (what this file uses)

**`codetwine/config/settings.py`**
This file depends on `settings.py` as its sole project-internal dependency, importing the following configuration constants:

- **`LLM_MODEL`**: Used as the default value for the `model` parameter in `__init__`, identifying which LLM provider/model litellm should target.
- **`LLM_API_KEY`**: Used as the default value for the `api_key` parameter in `__init__`, supplying the provider authentication credential passed to litellm calls.
- **`LLM_API_BASE`**: Used as the default value for the `api_base` parameter in `__init__`, specifying the custom endpoint URL forwarded to litellm for non-standard deployments.
- **`MAX_RETRIES`**: Used in `_call_with_retry` to bound the number of retry attempts made on rate limit errors.
- **`RETRY_WAIT`**: Used in `_call_with_retry` to determine the sleep duration (in seconds) between retry attempts when a rate limit error is encountered.
- **`DOC_MAX_TOKENS`**: Used as the default value for the `max_tokens` parameter in `generate`, capping the LLM's output token count.

All six constants are read-only configuration values sourced from environment variables; this file consumes them purely as inputs and does not modify them.

---

### Dependents (what uses this file)

**`main.py`**
Instantiates `LLMClient` (with no arguments, relying entirely on defaults from `settings.py`) when the `ENABLE_LLM_DOC` feature flag is active, and passes the resulting instance to `process_all_files`. The dependency is unidirectional: `main.py` depends on `LLMClient`.

**`codetwine/pipeline.py`**
Accepts an `LLMClient | None` instance as a parameter to `process_all_files`, propagating it through the file-processing pipeline. `pipeline.py` uses `LLMClient` as a typed interface; it does not construct instances itself. The dependency is unidirectional: `pipeline.py` depends on `LLMClient`.

**`codetwine/doc_creator.py`**
Receives `LLMClient` as a required parameter in its document-generation functions, invoking the client to call the LLM for generating per-file design document sections. `doc_creator.py` is the primary consumer of the `generate` method. The dependency is unidirectional: `doc_creator.py` depends on `LLMClient`.

## Data Flow

# Data Flow

## Input Data Format and Source

| Input | Type | Source |
|-------|------|--------|
| `prompt` | `str` | Caller (`doc_creator.py`, or any consumer of `generate()`) |
| `max_tokens` | `int` | Caller, defaults to `DOC_MAX_TOKENS` (from `settings.py`) |
| Constructor config | `str` | `settings.py` (`LLM_MODEL`, `LLM_API_KEY`, `LLM_API_BASE`) |
| Retry config | `int` | `settings.py` (`MAX_RETRIES`, `RETRY_WAIT`) |

---

## Main Transformation Flow

```
Caller
  │
  ▼
generate(prompt, max_tokens)
  │  guards: empty prompt → None immediately
  │
  ▼
_call_with_retry(prompt, max_tokens)
  │
  ├─ builds kwargs dict ──────────────────────────────────────────┐
  │    { model, max_tokens,                                        │
  │      messages: [{"role":"user","content": prompt}],           │
  │      api_key (if set), api_base (if set) }                    │
  │                                                               │
  ▼                                                               │
litellm.acompletion(**kwargs)  ◄────────────────────────────────-┘
  │
  │  success → response.choices[0].message.content.strip()
  │                                       │
  │  RateLimitError (not last attempt) ──►│ asyncio.sleep(RETRY_WAIT) → retry
  │  RateLimitError (last attempt)    ──►│ return None
  │  ContextWindowExceededError       ──►│ re-raise (propagates to caller)
  │  openai.APIError                  ──►│ return None (no retry)
  │
  ▼
str | None
  │
  ▼
Caller (doc_creator.py / pipeline.py)
```

---

## Output Data Format and Destination

| Output | Type | Condition | Destination |
|--------|------|-----------|-------------|
| Generated text | `str` | Successful API call | Returned to caller |
| `None` | `None` | Empty prompt, rate-limit exhaustion, or `APIError` | Returned to caller |
| `ContextWindowExceededError` | exception | Context too large | Re-raised to caller |

---

## Key Data Structures

### `kwargs` dict (constructed per attempt)

| Field | Type | Purpose | Presence |
|-------|------|---------|----------|
| `model` | `str` | litellm model identifier (provider prefix + model name) | Always |
| `max_tokens` | `int` | Upper bound on output tokens | Always |
| `messages` | `list[dict]` | Single-element list with `role="user"` and the prompt as `content` | Always |
| `api_key` | `str` | Provider authentication credential | Only if non-empty |
| `api_base` | `str` | Custom endpoint URL override | Only if non-empty |

### `LLMClient` instance state

| Attribute | Type | Purpose |
|-----------|------|---------|
| `self.model` | `str` | Model name forwarded to every `litellm.acompletion` call |
| `self.api_key` | `str` | Conditionally injected into `kwargs` |
| `self.api_base` | `str` | Conditionally injected into `kwargs` |

## Error Handling

# Error Handling

## Overall Strategy

`LLMClient` follows a **mixed strategy**: graceful degradation for recoverable or expected failure conditions, and fail-fast (re-raise) for errors that must be surfaced to the caller. The public API (`generate` / `_call_with_retry`) returns `None` as a sentinel value to signal failure rather than propagating most exceptions, allowing callers to decide how to proceed without crashing the pipeline.

---

## Error Patterns and Handling Policies

| Error Type | Handling | Impact |
|---|---|---|
| `litellm.RateLimitError` | Retried up to `MAX_RETRIES` times with `RETRY_WAIT`-second delays between attempts; returns `None` after all retries are exhausted | Temporary slowdown; `None` returned to caller on final failure |
| `ContextWindowExceededError` | Re-raised immediately without retry | Exception propagates to the caller; no `None` sentinel is used |
| `openai.APIError` | Logged at error level and returns `None` immediately; no retry | Single failure ends the call; caller receives `None` |
| Empty prompt (`not prompt`) | Returns `None` immediately before any API call | No API call is made; caller receives `None` silently |

---

## Design Considerations

- **Retry scope is narrow**: Only rate-limit errors trigger the retry loop. All other API-level errors are treated as non-retriable, reflecting the assumption that general API errors (e.g., invalid request, authentication failure) will not resolve on their own.
- **`ContextWindowExceededError` breaks the pattern**: Unlike every other error, this exception is re-raised rather than absorbed. This is a deliberate fail-fast choice, as the caller (e.g., `doc_creator.py`) is expected to handle context overflow with its own fallback logic (progressive fallback), making silent `None` semantics inappropriate here.
- **`None` as a uniform failure signal**: By returning `None` from `generate`, the module decouples error semantics from the rest of the pipeline. Callers treat `None` as "generation unavailable" without needing to handle multiple exception types, keeping the integration surface simple.
- **No partial-result recovery**: There is no mechanism to recover or retry on a partial or malformed response; the retry mechanism addresses only availability (rate limits), not response quality.

## Summary

`client.py` provides an async LLM wrapper via litellm. `LLMClient` stores model, api_key, and api_base (from settings.py defaults). Its public `generate(prompt, max_tokens)` method returns generated text as `str` or `None` (empty prompt short-circuits immediately). Internal `_call_with_retry` handles retry logic: `RateLimitError` retries up to `MAX_RETRIES` times with `RETRY_WAIT` delays; `ContextWindowExceededError` re-raises unconditionally; `openai.APIError` returns `None` immediately. The litellm kwargs dict always includes model, max_tokens, and a user-role messages list; api_key and api_base are added only when non-empty. Primary consumers are `doc_creator.py`, `pipeline.py`, and `main.py`.
