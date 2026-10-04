import asyncio
import logging
import openai
import litellm
from litellm import ContextWindowExceededError
from codetwine.config.settings import (
    LLM_MODEL,
    LLM_API_KEY,
    LLM_API_BASE,
    MAX_RETRIES,
    RETRY_WAIT,
    DOC_MAX_TOKENS,
)

logger = logging.getLogger(__name__)

# Texts of an error by which a provider refuses a request for its size (lower case)
_REQUEST_TOO_LARGE_TEXT_TUPLE = (
    "request_too_large",
    "request too large",
    "request entity too large",
    "payload too large",
)


def _is_request_too_large(error: Exception) -> bool:
    """Tell whether a provider refused a request for its size.

    Args:
        error: The exception of a failed call.

    Returns:
        True for HTTP status 413, and for an error whose text holds one of
        _REQUEST_TOO_LARGE_TEXT_TUPLE.
    """
    if getattr(error, "status_code", None) == 413:
        return True
    error_text = str(error).lower()
    return any(text in error_text for text in _REQUEST_TOO_LARGE_TEXT_TUPLE)


class LLMClient:
    """Async LLM API wrapper via litellm.

    Accepts a prompt, calls the API with retry logic, and returns the generated text.
    """

    def __init__(
        self,
        model: str = LLM_MODEL,
        api_key: str = LLM_API_KEY,
        api_base: str = LLM_API_BASE,
    ) -> None:
        """Initialize the LLM client with a model name, API key, and endpoint URL.

        Args:
            model: Model name in litellm format.
            api_key: Provider's API key.
            api_base: Base URL of the API (for custom endpoints).

        Raises:
            ValueError: When model is empty, or MAX_RETRIES is negative.
        """
        if not model:
            raise ValueError(
                "LLM_MODEL is not set. "
                "Please set LLM_MODEL in the .env file or your shell."
            )
        if MAX_RETRIES < 0:
            raise ValueError(
                f"MAX_RETRIES must be 0 or more, but got {MAX_RETRIES}. "
                "Set it in the .env file or your shell."
            )
        self.model = model
        self.api_key = api_key
        self.api_base = api_base

    async def _call_with_retry(self, prompt: str, max_tokens: int) -> str | None:
        """Call the LLM API with retry logic and return the generated text.

        Makes one call, and on 429 (rate limit exceeded) errors waits RETRY_WAIT
        seconds and retries up to MAX_RETRIES times. Returns None when every attempt
        fails, and when the reply holds no text.

        Args:
            prompt: The prompt string to send to the LLM.
            max_tokens: Maximum output token limit for the LLM.

        Returns:
            The generated text, or None on failure.

        Raises:
            ContextWindowExceededError: When the prompt exceeds the context window of
                the model, and when the provider refuses the request for its size
                (_is_request_too_large), a rate limit error included.
        """
        for attempt in range(MAX_RETRIES + 1):
            try:
                # litellm.acompletion: OpenAI-compatible async API
                # The model name prefix is used to auto-detect the provider
                request_dict = {
                    "model": self.model,
                    "max_tokens": max_tokens,
                    "messages": [{"role": "user", "content": prompt}],
                }
                if self.api_key:
                    request_dict["api_key"] = self.api_key
                if self.api_base:
                    request_dict["api_base"] = self.api_base

                response = await litellm.acompletion(**request_dict)
                # The text is returned as it is when the output was cut at max_tokens
                if response.choices[0].finish_reason == "length":
                    logger.warning(f"LLM output was cut at {max_tokens} tokens (DOC_MAX_TOKENS)")
                text = (response.choices[0].message.content or "").strip()
                if not text:
                    logger.error("LLM returned no text")
                    return None
                return text

            except litellm.RateLimitError as e:
                if _is_request_too_large(e):
                    raise self._too_large_error(e) from e
                # Wait and retry on rate limit exceeded
                if attempt < MAX_RETRIES:
                    logger.warning(f"Rate limit exceeded. Retrying in {RETRY_WAIT} seconds")
                    await asyncio.sleep(RETRY_WAIT)
                else:
                    logger.error("Rate limit exceeded: max retries reached")
                    return None

            except ContextWindowExceededError:
                raise

            except openai.APIError as e:
                if _is_request_too_large(e):
                    raise self._too_large_error(e) from e
                # Do not retry on API errors; fail immediately
                logger.error(f"LLM call failed: {e}")
                return None

    def _too_large_error(self, error: Exception) -> ContextWindowExceededError:
        """Return the ContextWindowExceededError standing for a request refused for its size.

        Args:
            error: The exception of the failed call.

        Returns:
            The exception to raise in its place.
        """
        return ContextWindowExceededError(
            message=str(error),
            model=self.model,
            llm_provider=getattr(error, "llm_provider", None) or "",
        )

    async def generate(
        self, prompt: str, max_tokens: int = DOC_MAX_TOKENS
    ) -> str | None:
        """Send a completed prompt to the LLM and return the generated text.

        Args:
            prompt: The prompt string to send to the LLM.
            max_tokens: Maximum output token limit for the LLM.

        Returns:
            The generated text, or None if generation failed.

        Raises:
            ContextWindowExceededError: When the prompt exceeds the context window of the
                model, or the provider refuses the request for its size.
        """
        if not prompt:
            return None
        return await self._call_with_retry(prompt, max_tokens)
