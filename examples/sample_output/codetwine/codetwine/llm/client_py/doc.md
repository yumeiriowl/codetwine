# Design Document: codetwine/llm/client.py

# Design Specification

**Overview**

Wrap asynchronous LLM API calls via litellm with configurable retry logic and error handling to generate text from prompts.

Developers use this file to:
- Instantiate an `LLMClient` with a model name and API credentials from environment settings (LLM_MODEL, LLM_API_KEY, LLM_API_BASE) to prepare for LLM interactions
- Call the `generate()` method with a prompt and optional token limit to retrieve LLM-generated text, receiving None on failure rather than exceptions for non-critical errors
- Handle `ContextWindowExceededError` exceptions in calling code when the LLM's context window is exhausted, which this client propagates rather than suppresses

This file depends on `codetwine/config/settings.py` to read environment-driven configuration values (model name, API credentials, retry parameters, and token limits) at initialization time. Files in `codetwine/doc_creator.py`, `codetwine/pipeline.py`, and `main.py` instantiate and use `LLMClient` to generate documentation summaries and descriptions during code analysis workflows.

The client implements exponential-backoff retry logic specific to rate-limit errors (litellm.RateLimitError): it retries up to MAX_RETRIES times with RETRY_WAIT second delays between attempts, but fails immediately on other API errors without retry. Context window exceeded errors are re-raised to allow calling code to handle them distinctly. The `generate()` method returns None for all non-fatal failures rather than raising exceptions, allowing callers to degrade gracefully.

**Definitions**

## `LLMClient`

Encapsulates litellm API interaction with configuration, retry logic, and consistent error handling. Initialized once per application run with model name, API key, and base URL from environment settings, it abstracts away litellm's complexity and enforces project-specific retry and token policies.

## `__init__`

Validates and stores the model name, API key, and API base URL from parameters or environment defaults. Raises `ValueError` if LLM_MODEL is empty or MAX_RETRIES is negative, ensuring invalid configurations fail fast at startup rather than during API calls.

## `_call_with_retry`

Executes a single litellm.acompletion request with configurable max_tokens, retrying on litellm.RateLimitError up to MAX_RETRIES times with RETRY_WAIT second delays between attempts. Returns the stripped message content on success, None if all retry attempts exhaust or an openai.APIError occurs, and re-raises ContextWindowExceededError for calling code to handle. Logs warnings for rate limits and output truncation at the token limit.

## `generate`

Public entry point that accepts a prompt and optional max_tokens (defaulting to DOC_MAX_TOKENS), returning generated text or None if the prompt is empty or _call_with_retry fails. Callers use this to obtain LLM-generated documentation or summaries while handling None gracefully for degraded workflows.

# Summary

# Summary: codetwine/llm/client.py

**Single Responsibility:** Wrap asynchronous LLM API calls via litellm with configurable retry logic and error handling to generate text from prompts.

**Main Public Definitions:** `LLMClient` class with `__init__` constructor, `generate()` method for text generation, and `_call_with_retry()` internal method.

**Key Terms:** Exponential-backoff retry logic for rate-limit errors, context window exceeded error propagation, graceful None returns for non-fatal failures, environment-driven configuration (model name, API credentials, token limits), litellm integration, immediate failure on non-rate-limit API errors.
