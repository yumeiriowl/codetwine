# Design Document: codetwine/llm/client.py

# Design Specification

**Overview**

Provide an async wrapper around litellm's OpenAI-compatible API that sends prompts to a language model and returns generated text with automatic retry handling for rate limit errors.

- Call `LLMClient()` constructor to initialize the client with a model name, API key, and optional endpoint URL; raises `ValueError` if the model is empty or `MAX_RETRIES` is negative.
- Call `generate(prompt, max_tokens)` to asynchronously send a prompt to the LLM and receive generated text, or `None` if generation fails; respects the `DOC_MAX_TOKENS` default and logs warnings when output is truncated.
- The client detects the LLM provider automatically from the model name prefix and supports custom API endpoints via `api_base`.

This file supports documentation generation in the codetwine project: `doc_creator.py` uses `LLMClient` to generate code summaries and section content, `pipeline.py` passes an `LLMClient` instance through the document processing workflow, and `main.py` instantiates the client when `ENABLE_LLM_DOC` is enabled. Configuration for the client comes entirely from `codetwine/config/settings.py`, which supplies the model name, API credentials, retry behavior, and token limits via environment variables.

Error handling distinguishes between recoverable and fatal failures: rate limit errors (`429`) trigger exponential backoff using `RETRY_WAIT` and `MAX_RETRIES`, while context window exceeded and other API errors are not retried and return `None` on failure. The `ContextWindowExceededError` is re-raised rather than caught to allow callers in `doc_creator.py` to handle it explicitly with fallback strategies.

**Definitions**

## `LLMClient`

A stateful async API client that encapsulates connection details (model name, API key, endpoint URL) and provides methods to call the LLM with automatic retry logic. Initialize once with model configuration and reuse across multiple `generate()` calls.

## `LLMClient.__init__`

Constructor that validates the model name and `MAX_RETRIES` configuration, storing the model identifier, API credentials, and endpoint URL for subsequent API calls. Raises `ValueError` if the model is empty or if `MAX_RETRIES` is negative.

## `LLMClient._call_with_retry`

Internal async method that executes a single LLM API call via litellm and implements retry logic: on rate limit errors, waits `RETRY_WAIT` seconds and retries up to `MAX_RETRIES` times; logs warnings when output is truncated due to `max_tokens`, and returns `None` on fatal errors or exhausted retries. Other API errors are not retried and fail immediately.

## `LLMClient.generate`

Public async method that validates the input prompt and delegates to `_call_with_retry()` with an optional `max_tokens` parameter defaulting to `DOC_MAX_TOKENS`. Returns the generated text stripped of whitespace, or `None` if the prompt is empty or generation fails.

# Summary

# Summary: codetwine/llm/client.py

**Single Responsibility:** Provide an async wrapper around litellm that sends prompts to language models and returns generated text with automatic retry handling for rate limits.

**Main Public Definitions:** `LLMClient` class with `__init__()` constructor and `generate()` async method.

**Key Capabilities:** Initializes with model name, API key, and optional endpoint URL; validates configuration on construction; automatically detects LLM provider from model prefix; sends prompts asynchronously via litellm's OpenAI-compatible API; implements exponential backoff retry logic for rate limit errors (429); logs warnings when output is truncated; returns `None` on fatal errors; re-raises context window exceeded errors for explicit caller handling; respects configurable token limits and retry parameters from settings.
