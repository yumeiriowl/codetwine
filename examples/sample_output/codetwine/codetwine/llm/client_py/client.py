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
        """
        if not model:
            raise ValueError(
                "LLM_MODEL is not set. "
                "Please set LLM_MODEL in the .env file or your shell."
            )
        self.model = model
        self.api_key = api_key
        self.api_base = api_base

    async def _call_with_retry(self, prompt: str, max_tokens: int) -> str | None:
        """Call the LLM API with retry logic and return the generated text.

        On 429 (rate limit exceeded) errors, waits RETRY_WAIT seconds before retrying.
        Returns None if all MAX_RETRIES attempts fail.

        Args:
            prompt: The prompt string to send to the LLM.
            max_tokens: Maximum output token limit for the LLM.

        Returns:
            The generated text, or None on failure.
        """
        for attempt in range(MAX_RETRIES):
            try:
                # litellm.acompletion: OpenAI-compatible async API
                # The model name prefix is used to auto-detect the provider
                kwargs = {
                    "model": self.model,
                    "max_tokens": max_tokens,
                    "messages": [{"role": "user", "content": prompt}],
                }
                # Add optional parameters
                if self.api_key:
                    kwargs["api_key"] = self.api_key
                if self.api_base:
                    kwargs["api_base"] = self.api_base

                response = await litellm.acompletion(**kwargs)
                # Extract and return the generated text from the response
                return response.choices[0].message.content.strip()

            except litellm.RateLimitError:
                # Wait and retry on rate limit exceeded
                if attempt < MAX_RETRIES - 1:
                    logger.warning(f"Rate limit exceeded. Retrying in {RETRY_WAIT} seconds")
                    await asyncio.sleep(RETRY_WAIT)
                else:
                    # Log error and return None when max retries reached
                    logger.error("Rate limit exceeded: max retries reached")
                    return None

            except ContextWindowExceededError:
                raise

            except openai.APIError as e:
                # Do not retry on API errors; fail immediately
                logger.error(f"LLM call failed: {e}")
                return None

    async def generate(
        self, prompt: str, max_tokens: int = DOC_MAX_TOKENS
    ) -> str | None:
        """Send a completed prompt to the LLM and return the generated text.

        Args:
            prompt: The prompt string to send to the LLM.
            max_tokens: Maximum output token limit for the LLM.

        Returns:
            The generated text, or None if generation failed.
        """
        if not prompt:
            return None

        # Delegate to the API call with retry logic
        return await self._call_with_retry(prompt, max_tokens)
