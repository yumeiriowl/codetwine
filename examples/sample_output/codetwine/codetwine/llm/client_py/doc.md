# Design Document: codetwine/llm/client.py

# Overview & Purpose

## 1. Module Summary

Provides an async LLM API client that sends prompts to a configured language model via litellm and returns generated text, with built-in retry logic for rate limit errors.

## 2. When to Use This Module

- **Instantiate `LLMClient`** when LLM-based document generation is enabled (e.g., in `main.py` guarded by `ENABLE_LLM_DOC`), to obtain a client configured from environment settings.
- **Pass an `LLMClient` instance to `process_all_files`** (in `codetwine/pipeline.py`) or to document generation functions (in `codetwine/doc_creator.py`) when per-file design documents need to be generated.
- **Call `LLMClient.generate(prompt)`** from any async context that needs to submit a prompt string and receive the model's text response, with rate-limit retries handled transparently.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `LLMClient` | `model: str`, `api_key: str`, `api_base: str` | — | Wraps litellm async completion with retry logic; raises `ValueError` if `model` is not set |
| `async LLMClient.generate` | `prompt: str`, `max_tokens: int` | `str \| None` | Sends a prompt to the LLM and returns generated text, or `None` if the prompt is empty or generation fails |

## 4. Design Decisions

- **`ContextWindowExceededError` is re-raised** rather than caught, allowing callers to handle context overflow (e.g., by truncating the prompt) without masking it as a generic failure.
- **`openai.APIError` fails immediately** without retry, while only `litellm.RateLimitError` triggers the wait-and-retry loop, keeping non-recoverable errors fast-fail.
- **`None` return on failure** instead of raising exceptions gives callers a uniform signal that generation was unsuccessful, decoupling error-handling policy from this module.
- **Optional `api_key` and `api_base`** are passed to litellm only when non-empty, allowing both standard provider keys and custom endpoint configurations (e.g., locally hosted models) without changing call logic.

# Definition Design Specifications

---

## Class: `LLMClient`

**Responsibility:** Provides an async interface to LLM APIs via the litellm library, encapsulating retry logic, provider configuration, and error handling so that callers interact with a single `generate` method.

**When to use:** Instantiate when the application needs to send prompts to an LLM and receive generated text, such as during documentation generation in the codetwine pipeline.

---

### `__init__`

| Parameter | Type | Default | Purpose |
|-----------|------|---------|---------|
| `model` | `str` | `LLM_MODEL` | litellm-format model identifier; used to auto-detect provider |
| `api_key` | `str` | `LLM_API_KEY` | Provider authentication credential; optional if empty string |
| `api_base` | `str` | `LLM_API_BASE` | Base URL for custom or self-hosted API endpoints; optional if empty string |

**Behavior:** Validates that `model` is non-empty; raises `ValueError` with a descriptive message if it is not set. Stores the three parameters as instance attributes.

**Constraints:**
- `model` must be a non-empty string; all other parameters may be empty strings (falsy values are treated as "not provided" when building API call kwargs).

---

### `_call_with_retry` (async)

**Signature:** `async def _call_with_retry(self, prompt: str, max_tokens: int) -> str | None`

- Return type `str | None`: either the stripped text content from the LLM response, or `None` if all retries are exhausted or a non-retryable error occurs.

**Responsibility:** Executes the litellm async completion call and applies retry logic specifically for rate-limit errors, isolating the concurrency and error-handling mechanics from the public interface.

**When to use:** Called internally by `generate`; not intended for direct external invocation.

**Design decisions:**
- Only `litellm.RateLimitError` triggers retry behaviour; all other errors either propagate or cause immediate `None` return.
- `ContextWindowExceededError` is explicitly re-raised rather than suppressed, allowing callers to handle token-budget overflows distinctly.
- `openai.APIError` causes immediate failure without retrying (fail-fast on infrastructure or malformed-request errors).
- `api_key` and `api_base` are conditionally added to the kwargs dict only when truthy, avoiding passing empty strings to litellm.
- Retry attempts are bounded by `MAX_RETRIES`; the sleep between attempts uses `await asyncio.sleep(RETRY_WAIT)`, yielding the event loop rather than blocking the thread.

**Concurrency semantics:** `await litellm.acompletion(...)` suspends this coroutine while the network call is in progress. `await asyncio.sleep(RETRY_WAIT)` suspends this coroutine during the retry wait. Retries are sequential, not parallel.

**Constraints & edge cases:**

| Condition | Behaviour |
|-----------|-----------|
| Rate limit on the final attempt | Logs an error and returns `None` |
| Rate limit on attempts before the last | Logs a warning, waits `RETRY_WAIT` seconds, retries |
| `ContextWindowExceededError` | Re-raised immediately; not caught here |
| `openai.APIError` | Logged and returns `None` immediately |
| Successful response | Returns stripped string content from `choices[0].message.content` |

---

### `generate` (async)

**Signature:** `async def generate(self, prompt: str, max_tokens: int = DOC_MAX_TOKENS) -> str | None`

- Return type `str | None`: the generated text on success, or `None` if the prompt is empty or generation failed.

**Responsibility:** Serves as the public entry point for LLM text generation, providing a guard against empty prompts before delegating to the retry-aware internal method.

**When to use:** Call whenever a prompt has been fully constructed and the caller wants to obtain generated text from the configured LLM, for example when producing a documentation section in `doc_creator.py`.

**Concurrency semantics:** Async; suspends the calling coroutine for the duration of the underlying API call chain. Does not introduce additional parallelism itself.

**Constraints & edge cases:**

| Condition | Behaviour |
|-----------|-----------|
| `prompt` is falsy (empty string, `None`) | Returns `None` immediately without an API call |
| Non-empty `prompt` | Delegates to `_call_with_retry`; returns its result |
| `max_tokens` not supplied | Defaults to `DOC_MAX_TOKENS` (8192 unless overridden by environment) |

# Dependency Description

### Dependencies (modules this file imports)

**`codetwine/llm/client.py` → `codetwine/config/settings.py`** : retrieves all runtime configuration constants required to construct and operate the LLM client.

Specific symbols consumed and their purposes:

- `LLM_MODEL` — default model name passed to `LLMClient.__init__` and subsequently forwarded to `litellm.acompletion`
- `LLM_API_KEY` — default API key passed to `LLMClient.__init__` and conditionally included in API call kwargs
- `LLM_API_BASE` — default base URL passed to `LLMClient.__init__` and conditionally included in API call kwargs
- `MAX_RETRIES` — controls the upper bound of the retry loop in `_call_with_retry`
- `RETRY_WAIT` — specifies the sleep duration (in seconds) between rate-limit retry attempts in `_call_with_retry`
- `DOC_MAX_TOKENS` — serves as the default value for the `max_tokens` parameter in `generate`

---

### Dependents (modules that import this file)

**`main.py` → `codetwine/llm/client.py`** : instantiates `LLMClient` (conditionally, guarded by `ENABLE_LLM_DOC`) and passes the resulting instance into the top-level pipeline entry point `process_all_files`.

**`codetwine/pipeline.py` → `codetwine/llm/client.py`** : uses `LLMClient` as a type annotation for the `llm_client` parameter of `process_all_files`, allowing the pipeline to accept and propagate an optional client instance through file-processing logic.

**`codetwine/doc_creator.py` → `codetwine/llm/client.py`** : uses `LLMClient` as the type annotation for the `llm_client` parameter in document-generation functions, invoking the client to produce per-section and per-file design document content with progressive fallback handling.

---

### Dependency Direction

| Relationship | Direction |
|---|---|
| `codetwine/llm/client.py` → `codetwine/config/settings.py` | Unidirectional — `client.py` reads configuration from `settings.py`; `settings.py` has no knowledge of `client.py` |
| `main.py` → `codetwine/llm/client.py` | Unidirectional — `main.py` depends on `client.py`; `client.py` has no knowledge of `main.py` |
| `codetwine/pipeline.py` → `codetwine/llm/client.py` | Unidirectional — `pipeline.py` depends on `client.py`; `client.py` has no knowledge of `pipeline.py` |
| `codetwine/doc_creator.py` → `codetwine/llm/client.py` | Unidirectional — `doc_creator.py` depends on `client.py`; `client.py` has no knowledge of `doc_creator.py` |

# Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `model` | `LLM_MODEL` config constant or constructor argument | `str` (litellm model name, e.g. `"openai/gpt-4o"`) |
| `api_key` | `LLM_API_KEY` config constant or constructor argument | `str` (provider API key, may be empty) |
| `api_base` | `LLM_API_BASE` config constant or constructor argument | `str` (custom endpoint URL, may be empty) |
| `prompt` | Caller (e.g. `doc_creator.py`) via `generate()` | `str` (fully assembled prompt text) |
| `max_tokens` | Caller via `generate()`, defaults to `DOC_MAX_TOKENS` (8192) | `int` |
| `MAX_RETRIES` | `settings.py` config constant | `int` (default 3) |
| `RETRY_WAIT` | `settings.py` config constant | `int` (default 2, seconds) |

---

## 2. Transformation Overview

```
[Caller] --prompt, max_tokens--> generate()
              |
              v
         guard: prompt is empty? --> return None
              |
              v
        _call_with_retry(prompt, max_tokens)
              |
              v
     [Retry loop: 0..MAX_RETRIES-1]
              |
              +-- assemble kwargs dict:
              |     model, max_tokens, messages
              |     + api_key (if set)
              |     + api_base (if set)
              |
              v
     litellm.acompletion(**kwargs)   [async, awaited]
              |
        ______+______________________________
       |             |                       |
   success      RateLimitError         ContextWindowExceededError
       |             |                       |
  extract text   attempt < MAX_RETRIES?    raise (propagates to caller)
  .strip()           |
       |          yes: asyncio.sleep(RETRY_WAIT)
       |               --> retry loop
       |          no:  log error, return None
       |
       +-- openai.APIError --> log error, return None
       |
       v
  return str (generated text)
```

**Key flow characteristics:**
- `generate()` acts as a thin guard and delegates immediately to `_call_with_retry()`.
- `_call_with_retry()` is the core retry loop; each iteration constructs the `kwargs` dict fresh and issues one async API call.
- Rate limit errors trigger a timed wait and a re-attempt; all other API errors short-circuit immediately.
- `ContextWindowExceededError` is re-raised without retry, allowing the caller to handle prompt reduction.
- On exhaustion of retries or an unretriable error, the method returns `None`.

---

## 3. Outputs

| Output | Destination | Format |
|---|---|---|
| Generated text | Return value of `generate()` / `_call_with_retry()` | `str` (whitespace-stripped) or `None` on failure |
| Warning log | Logger | `str` message when rate limit is hit and retry will occur |
| Error log | Logger | `str` message when max retries are exhausted or an `APIError` occurs |
| `ContextWindowExceededError` | Re-raised to caller | Exception (no transformation applied) |

No file writes or other side effects are produced by this module.

---

## 4. Key Data Structures

### `kwargs` — API call parameter dict

Assembled inside `_call_with_retry()` and passed to `litellm.acompletion()`.

| Field / Key | Type | Purpose |
|---|---|---|
| `model` | `str` | litellm model identifier (e.g. `"openai/gpt-4o"`) |
| `max_tokens` | `int` | Maximum number of tokens in the LLM response |
| `messages` | `list[dict]` | Chat message list; always a single user-role entry |
| `api_key` | `str` | Provider API key; included only when `self.api_key` is non-empty |
| `api_base` | `str` | Custom endpoint URL; included only when `self.api_base` is non-empty |

### `messages` entry (element of `kwargs["messages"]`)

| Field / Key | Type | Purpose |
|---|---|---|
| `role` | `str` | Always `"user"` |
| `content` | `str` | The full prompt string passed by the caller |

### `response` — litellm completion response object

Consumed but not stored; only one field path is accessed:

| Access Path | Type | Purpose |
|---|---|---|
| `response.choices[0].message.content` | `str` | Raw generated text returned by the LLM; `.strip()` is applied before returning |

# Error Handling

## 1. Overall Strategy

The `LLMClient` adopts a **retry-with-graceful-degradation** strategy. Transient failures caused by rate limiting are retried up to `MAX_RETRIES` times with a fixed wait interval (`RETRY_WAIT` seconds) between attempts. All other API failures are handled as immediate, non-retryable terminations of the current request. In all failure cases, the method returns `None` rather than raising an exception to the caller, allowing upstream components to treat LLM generation as an optional, skippable step. The single exception to this pattern is `ContextWindowExceededError`, which is re-raised unconditionally to propagate the error to the caller for handling at a higher level.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `litellm.RateLimitError` | The LLM provider returns a 429 rate-limit response | Waits `RETRY_WAIT` seconds and retries up to `MAX_RETRIES` attempts; logs a warning on each retry and an error when retries are exhausted | Yes (up to `MAX_RETRIES` attempts) | Returns `None` after all retries are exhausted |
| `ContextWindowExceededError` | The prompt exceeds the model's context window | Re-raised immediately without retrying or logging | No (propagated to caller) | Exception propagates up the call stack |
| `openai.APIError` | A general API-level error is returned by the provider | Logged as an error and the method returns immediately | No | Returns `None`; no retry is attempted |
| `ValueError` (missing model) | `LLM_MODEL` is an empty string at instantiation | Raised immediately during `__init__` | No | Object construction fails; process cannot proceed without a valid model name |
| Empty prompt | `generate()` is called with a falsy `prompt` value | Returns `None` immediately without calling the API | Yes (operation skipped) | No API call is made; caller receives `None` |

---

## 3. Design Notes

- **Selective re-raise for `ContextWindowExceededError`:** This error is distinguished from other failures because it signals a structural problem with the input (the prompt is too large) rather than a transient infrastructure issue. Re-raising it allows callers such as `doc_creator.py` to implement their own progressive fallback logic (e.g., reducing prompt size), which would be impossible if the error were silently swallowed and `None` returned.

- **No retry for `openai.APIError`:** General API errors (authentication failures, malformed requests, server errors, etc.) are treated as non-transient by design. Retrying them would be unlikely to succeed and could introduce unnecessary latency or cost.

- **`None` as the universal failure signal:** Returning `None` on failure rather than raising exceptions keeps the LLM generation step loosely coupled from the rest of the pipeline. Callers (`pipeline.py`, `doc_creator.py`) can treat a `None` result as an absent but non-fatal output, consistent with LLM generation being an optional feature controlled by `ENABLE_LLM_DOC`.

- **Configuration-driven retry parameters:** Both the retry count (`MAX_RETRIES`) and the wait duration (`RETRY_WAIT`) are externalized to `settings.py` and ultimately sourced from environment variables, allowing retry behavior to be tuned per deployment without code changes.

# Summary

**`codetwine/llm/client.py`**: Async LLM API client wrapping litellm with retry logic for rate-limit errors.

- `LLMClient(model: str, api_key: str, api_base: str)` — main class; raises `ValueError` if `model` is empty
- `async LLMClient.generate(prompt: str, max_tokens: int) -> str | None` — public entry point for text generation
- `async LLMClient._call_with_retry(prompt: str, max_tokens: int) -> str | None` — internal retry loop

Consumes: `prompt: str`, `kwargs dict` (`model`, `max_tokens`, `messages: list[dict]`, optional `api_key`, `api_base`). Produces: stripped `str` from `response.choices[0].message.content` or `None`.
