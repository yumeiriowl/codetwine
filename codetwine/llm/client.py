import asyncio
import logging
import litellm
from litellm import ContextWindowExceededError
from litellm.litellm_core_utils.get_llm_provider_logic import declared_authenticating_provider
from litellm.llms.anthropic.common_utils import AnthropicModelInfo
from codetwine.config.settings import (
    LLM_MODEL,
    LLM_API_KEY,
    LLM_API_BASE,
    MAX_RETRIES,
    RETRY_WAIT,
    DOC_MAX_TOKENS,
    DOC_EFFORT,
    DOC_EFFORT_TUPLE,
)

logger = logging.getLogger(__name__)

# Texts of an error by which a provider refuses a request for its size (lower case)
_REQUEST_TOO_LARGE_TEXT_TUPLE = (
    "request_too_large",
    "request too large",
    "request entity too large",
    "payload too large",
)

# Words in a refusal that say it was the parameters of the effort it refused (lower case)
_EFFORT_REFUSAL_WORD_TUPLE = ("thinking", "effort", "output_config")

# Exceptions litellm raises for a failed call
_LLM_ERROR_TUPLE = tuple(litellm.LITELLM_EXCEPTION_TYPES)

# The least output token limit a call refused for the context window is sent again with:
# a call whose halved limit would fall below it is not sent again
_MIN_LOWER_MAX_TOKENS = 4096


def _table_output_limit(model: str, api_base: str, api_key: str) -> int:
    """Return the max_output_tokens litellm's model table holds for a model.

    A model of a provider litellm signs in to (declared_authenticating_provider) is not
    looked up. A provider litellm asks for the model's details is asked at api_base with
    api_key, as the calls are.

    Args:
        model: Model name in litellm format.
        api_base: The calls' API base URL; empty for the provider's standard endpoint.
        api_key: The calls' API key; empty for none.

    Returns:
        The table's max_output_tokens, or 0 when the table holds no whole number above 0
        for the model, does not know it, or the model is not looked up.
    """
    if declared_authenticating_provider(model) is not None:
        return 0
    try:
        output_limit = litellm.get_model_info(
            model, api_base=api_base or None, api_key=api_key or None
        ).get("max_output_tokens")
    except Exception:  # noqa: BLE001 - an unknown model raises
        return 0
    return output_limit if type(output_limit) is int and output_limit > 0 else 0


def _half_max_tokens(max_tokens: int) -> int:
    """Return the output token limit a call refused for the context window is sent again with.

    Args:
        max_tokens: The output token limit the refused call asked for.

    Returns:
        Half of max_tokens, or 0 when that is below _MIN_LOWER_MAX_TOKENS.
    """
    half = max_tokens // 2
    return half if half >= _MIN_LOWER_MAX_TOKENS else 0


def _is_always_think_model(model: str) -> bool:
    """Tell whether a model thinks whatever it is asked.

    Args:
        model: Model name in litellm format.

    Returns:
        What AnthropicModelInfo._is_always_on_thinking_model says of the model under
        its litellm provider; False for a model of a provider litellm does not know.
    """
    try:
        bare_model, provider, _, _ = litellm.get_llm_provider(model)
        return AnthropicModelInfo._is_always_on_thinking_model(bare_model, provider)
    except litellm.BadRequestError:
        return False


def _adaptive_param_dict(effort: str) -> dict:
    """Return the litellm kwargs that ask for adaptive thinking at an effort.

    Examples:
        "low" -> {"thinking": {"type": "adaptive"}, "output_config": {"effort": "low"}}

    Args:
        effort: "low", "medium" or "high".

    Returns:
        The kwargs to add to the call.
    """
    return {"thinking": {"type": "adaptive"}, "output_config": {"effort": effort}}


def _effort_param_list(effort: str, model: str) -> list[dict]:
    """Return the litellm kwargs that ask a model for an effort, in the order tried.

    Examples:
        ("off", a model that can stop thinking) -> [{"thinking": {"type": "disabled"}},
                                                    _adaptive_param_dict("low")]
        ("off", a model that always thinks)     -> [_adaptive_param_dict("low")]
        ("high", any model)                     -> [_adaptive_param_dict("high")]

    Args:
        effort: A value of DOC_EFFORT_TUPLE.
        model: Model name in litellm format.

    Returns:
        The forms to try, each the kwargs to add to the call. Past the last one the call
        is made without any.
    """
    if effort != "off":
        return [_adaptive_param_dict(effort)]
    if _is_always_think_model(model):
        return [_adaptive_param_dict("low")]
    return [{"thinking": {"type": "disabled"}}, _adaptive_param_dict("low")]


def _is_effort_refusal(error: Exception) -> bool:
    """Tell whether a refused request was refused for the parameters of the effort.

    Args:
        error: The BadRequestError of a failed call.

    Returns:
        True when the error's text holds one of _EFFORT_REFUSAL_WORD_TUPLE.
    """
    error_text = str(error).lower()
    return any(word in error_text for word in _EFFORT_REFUSAL_WORD_TUPLE)


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

    A call asks for no more output tokens than litellm's model table holds for the
    model. For a model whose output limit the table does not hold, a call refused for
    the context window is sent again with half the output token limit, down to
    _MIN_LOWER_MAX_TOKENS, unless an earlier call of this client went through asking
    for as many. A later call whose prompt is at least as long as that of a halved call
    that went through asks for no more than the limit that call took.
    """

    def __init__(
        self,
        model: str = LLM_MODEL,
        api_key: str = LLM_API_KEY,
        api_base: str = LLM_API_BASE,
        effort: str = DOC_EFFORT,
    ) -> None:
        """Initialize the LLM client with a model name, API key, endpoint URL, and effort.

        Args:
            model: Model name in litellm format.
            api_key: Provider's API key.
            api_base: Base URL of the API (for custom endpoints).
            effort: A value of DOC_EFFORT_TUPLE, the effort every call asks for.

        Raises:
            ValueError: When model is empty, MAX_RETRIES is negative, or effort is not
                one of DOC_EFFORT_TUPLE.
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
        if effort not in DOC_EFFORT_TUPLE:
            raise ValueError(
                f"DOC_EFFORT must be one of {' / '.join(DOC_EFFORT_TUPLE)}, but got "
                f"'{effort}'. Set it in the .env file or your shell."
            )
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        # The first form is sent; a form the model refuses is taken off
        self.effort_param_list = _effort_param_list(effort, model)
        # The output limit of litellm's model table, 0 when the table holds none: only a
        # model without one has its limit halved on a refusal for the context window
        self.table_max_tokens = _table_output_limit(model, api_base, api_key)
        # The output token limit each halved call that went through took -> the length
        # of the shortest prompt that took it (characters), and the largest output token
        # limit a call went through with
        self.fit_chars_dict: dict[int, int] = {}
        self.pass_max_tokens = 0

    async def _complete(self, request_dict: dict) -> litellm.ModelResponse:
        """Call litellm with the first form of self.effort_param_list, the next once refused.

        A refusal of the form sent takes it off self.effort_param_list, with a warning
        logged by the first call refused, and makes the same call once more with the
        next form, or without any when none is left.

        Args:
            request_dict: The kwargs of litellm.acompletion() but the effort's.

        Returns:
            The litellm response.
        """
        effort_param_list = self.effort_param_list
        effort_param_dict = effort_param_list[0] if effort_param_list else {}
        try:
            return await litellm.acompletion(**request_dict, **effort_param_dict)
        except litellm.BadRequestError as e:
            if (not effort_param_dict or isinstance(e, ContextWindowExceededError)
                    or not _is_effort_refusal(e)):
                raise
            if self.effort_param_list is effort_param_list:
                self.effort_param_list = effort_param_list[1:]
                next_text = (f"with {self.effort_param_list[0]}" if self.effort_param_list
                             else "without them")
                logger.warning(f"{self.model} refused the effort parameters "
                               f"{effort_param_dict}; it is called {next_text}: {e}")
            return await self._complete(request_dict)

    async def _complete_within_context(self, request_dict: dict) -> litellm.ModelResponse:
        """Call _complete, halving the output token limit on a refusal for the context window.

        A refusal for the context window is sent again with half of
        request_dict["max_tokens"] while the table holds no output limit for the model
        and no call has gone through asking for as many (_half_max_tokens). A halved call
        that goes through is recorded in self.fit_chars_dict (_keep_fit).

        Args:
            request_dict: The kwargs of litellm.acompletion() but the effort's. Its
                "max_tokens" is changed to the limit the call went through with.

        Returns:
            The litellm response.

        Raises:
            ContextWindowExceededError: When the refusal is not sent again, or the
                halving does not get past it.
        """
        first_max_tokens = request_dict["max_tokens"]
        while True:
            try:
                response = await self._complete(request_dict)
                break
            except ContextWindowExceededError as e:
                max_tokens = request_dict["max_tokens"]
                half = (_half_max_tokens(max_tokens)
                        if not self.table_max_tokens and self.pass_max_tokens < max_tokens
                        else 0)
                if not half:
                    raise
                logger.info(f"{self.model} refused a call for the context window; it is "
                            f"sent again asking for {half} output tokens in place of "
                            f"{max_tokens}: {e}")
                request_dict["max_tokens"] = half
        max_tokens = request_dict["max_tokens"]
        self.pass_max_tokens = max(self.pass_max_tokens, max_tokens)
        if max_tokens < first_max_tokens:
            self._keep_fit(max_tokens, len(request_dict["messages"][0]["content"]),
                           first_max_tokens)
        return response

    def _keep_fit(self, max_tokens: int, prompt_chars: int, first_max_tokens: int) -> None:
        """Record the output token limit a halved call went through with.

        A warning is logged for a limit not recorded before, an INFO line when a shorter
        prompt takes a recorded one.

        Args:
            max_tokens: The output token limit the call went through with.
            prompt_chars: The length of the call's prompt (characters).
            first_max_tokens: The output token limit the call first asked for.
        """
        before_chars = self.fit_chars_dict.get(max_tokens)
        if before_chars is not None and before_chars <= prompt_chars:
            return
        self.fit_chars_dict[max_tokens] = prompt_chars
        message = (f"{self.model} refused, for the context window, a call with a prompt of "
                   f"{prompt_chars} characters asking for {first_max_tokens} output "
                   f"tokens; calls with a prompt at least as long ask for no more than "
                   f"{max_tokens}")
        if before_chars is not None:
            logger.info(message)
            return
        logger.warning(f"{message}. When the model's context window is short, set "
                       f"DOC_MAX_TOKENS to what it leaves for the reply")

    def _fit_max_tokens(self, prompt_chars: int) -> int:
        """Return the output token limit a call with a prompt of this length asks for at most.

        Args:
            prompt_chars: The length of the prompt (characters).

        Returns:
            The lowest limit self.fit_chars_dict holds for a prompt no longer than this
            one, or 0 when it holds none.
        """
        return min((max_tokens for max_tokens, chars in self.fit_chars_dict.items()
                    if chars <= prompt_chars), default=0)

    async def _call_with_retry(self, prompt: str, max_tokens: int) -> str | None:
        """Call the LLM API with retry logic and return the generated text.

        Makes one call, and on 429 (rate limit exceeded) errors waits RETRY_WAIT
        seconds and retries up to MAX_RETRIES times. A call whose effort parameters
        are refused is made again with the next form, or without them (_complete), and
        a call refused for the context window may be made again with a lower output
        token limit (_complete_within_context). Returns None when every attempt fails,
        and when the reply holds no text.

        Args:
            prompt: The prompt string to send to the LLM.
            max_tokens: Maximum output token limit for the LLM, lowered to the table's
                (self.table_max_tokens) and to what earlier halved calls took
                (_fit_max_tokens).

        Returns:
            The generated text, or None on failure.

        Raises:
            ContextWindowExceededError: When the prompt exceeds the context window of
                the model, and when the provider refuses the request for its size
                (_is_request_too_large), a rate limit error included.
        """
        for attempt in range(MAX_RETRIES + 1):
            try:
                # The kwargs of litellm.acompletion() but the effort's
                request_dict = {
                    "model": self.model,
                    "max_tokens": min([max_tokens] + [limit for limit in (
                        self.table_max_tokens, self._fit_max_tokens(len(prompt))) if limit]),
                    "messages": [{"role": "user", "content": prompt}],
                }
                if self.api_key:
                    request_dict["api_key"] = self.api_key
                if self.api_base:
                    request_dict["api_base"] = self.api_base

                response = await self._complete_within_context(request_dict)
                if not response.choices:
                    logger.error("LLM returned no text")
                    return None
                # The text is returned as it is when the output was cut at max_tokens
                if response.choices[0].finish_reason == "length":
                    request_max_tokens = request_dict["max_tokens"]
                    lower_text = (f", lowered from {max_tokens}"
                                  if request_max_tokens < max_tokens else "")
                    logger.warning(f"LLM output was cut at {request_max_tokens} tokens "
                                   f"(DOC_MAX_TOKENS{lower_text})")
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

            except _LLM_ERROR_TUPLE as e:
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
