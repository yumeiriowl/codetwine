import asyncio
from types import SimpleNamespace

import litellm
import pytest

from codetwine.llm import ContextWindowExceededError, client as client_module
from codetwine.llm.client import LLMClient


def _rate_limit_error() -> litellm.RateLimitError:
    return litellm.RateLimitError(message="429", llm_provider="anthropic", model="m")


def _reply(text: str) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(
        finish_reason="stop", message=SimpleNamespace(content=text))])


def _run(monkeypatch, max_retries: int, outcome_list: list) -> tuple[str | None, int]:
    """Call the client once with acompletion returning the outcomes in order."""
    call_count = 0

    async def fake_acompletion(**kwargs):
        nonlocal call_count
        outcome = outcome_list[call_count]
        call_count += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(client_module, "MAX_RETRIES", max_retries)
    monkeypatch.setattr(client_module, "RETRY_WAIT", 0)
    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    text = asyncio.run(LLMClient(model="anthropic/m", effort="low")._call_with_retry("p", 10))
    return text, call_count


def test_zero_retries_still_calls_once(monkeypatch):
    assert _run(monkeypatch, 0, [_reply("ok")]) == ("ok", 1)


def test_zero_retries_gives_up_after_one_rate_limit(monkeypatch):
    assert _run(monkeypatch, 0, [_rate_limit_error()]) == (None, 1)


def test_retries_count_after_the_first_call(monkeypatch):
    outcome_list = [_rate_limit_error()] * 3 + [_reply("ok")]
    assert _run(monkeypatch, 3, outcome_list) == ("ok", 4)


def test_gives_up_when_retries_run_out(monkeypatch):
    assert _run(monkeypatch, 2, [_rate_limit_error()] * 3) == (None, 3)


def test_a_reply_without_text_is_a_failure(monkeypatch):
    assert _run(monkeypatch, 0, [_reply("  ")]) == (None, 1)
    assert _run(monkeypatch, 0, [_reply(None)]) == (None, 1)


def test_a_negative_value_stops_the_client(monkeypatch):
    monkeypatch.setattr(client_module, "MAX_RETRIES", -1)
    with pytest.raises(ValueError, match="MAX_RETRIES"):
        LLMClient(model="anthropic/m", effort="low")


@pytest.mark.parametrize("error", [
    litellm.BadRequestError(
        message='AnthropicException - {"error": {"type": "request_too_large", '
                '"message": "Request exceeds the maximum size"}}',
        llm_provider="anthropic", model="m"),
    litellm.RateLimitError(
        message="Request too large for m on tokens per min (TPM): Limit 30000, Requested 45123. "
                "The input or output tokens must be reduced in order to run successfully.",
        llm_provider="openai", model="m"),
    litellm.APIError(status_code=413, message="413 Request Entity Too Large",
                     llm_provider="openai", model="m"),
])
def test_a_request_refused_for_its_size_is_an_overflow(monkeypatch, error):
    """It is raised at the first attempt, the retries of a rate limit left out."""
    call_count = 0

    async def fake_acompletion(**kwargs):
        nonlocal call_count
        call_count += 1
        raise error

    monkeypatch.setattr(client_module, "MAX_RETRIES", 3)
    monkeypatch.setattr(client_module, "RETRY_WAIT", 0)
    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    with pytest.raises(ContextWindowExceededError):
        asyncio.run(LLMClient(model="anthropic/m", effort="low")._call_with_retry("p", 10))
    assert call_count == 1


def test_another_bad_request_is_a_failure(monkeypatch):
    error = litellm.BadRequestError(message="invalid model", llm_provider="anthropic", model="m")
    assert _run(monkeypatch, 3, [error]) == (None, 1)


def _record_run(monkeypatch, effort: str, outcome_list: list,
                call_count: int = 1) -> tuple[list[str | None], list[dict]]:
    """Call one client call_count times with acompletion returning the outcomes in order."""
    kwarg_dict_list = []

    async def fake_acompletion(**kwargs):
        kwarg_dict_list.append(kwargs)
        outcome = outcome_list[len(kwarg_dict_list) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(client_module, "MAX_RETRIES", 0)
    monkeypatch.setattr(client_module, "_is_always_think_model", lambda model: False)
    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    llm_client = LLMClient(model="anthropic/m", effort=effort)
    text_list = [asyncio.run(llm_client._call_with_retry("p", 10)) for _ in range(call_count)]
    return text_list, kwarg_dict_list


def _effort_kwarg_dict(kwarg_dict: dict) -> dict:
    return {key: kwarg_dict[key] for key in ("thinking", "output_config") if key in kwarg_dict}


@pytest.mark.parametrize("effort, expect", [
    ("off", {"thinking": {"type": "disabled"}}),
    ("low", {"thinking": {"type": "adaptive"}, "output_config": {"effort": "low"}}),
    ("medium", {"thinking": {"type": "adaptive"}, "output_config": {"effort": "medium"}}),
    ("high", {"thinking": {"type": "adaptive"}, "output_config": {"effort": "high"}}),
])
def test_the_effort_is_sent(monkeypatch, effort, expect):
    text_list, kwarg_dict_list = _record_run(monkeypatch, effort, [_reply("ok")])
    assert text_list == ["ok"]
    assert _effort_kwarg_dict(kwarg_dict_list[0]) == expect


_LOW_DICT = {"thinking": {"type": "adaptive"}, "output_config": {"effort": "low"}}


def test_off_asks_a_model_that_always_thinks_for_low(monkeypatch):
    monkeypatch.setattr(client_module, "MAX_RETRIES", 0)
    monkeypatch.setattr(client_module, "_is_always_think_model",
                        lambda model: model == "anthropic/a")
    assert LLMClient(model="anthropic/a", effort="off").effort_param_list == [_LOW_DICT]
    assert LLMClient(model="anthropic/b", effort="off").effort_param_list == [
        {"thinking": {"type": "disabled"}}, _LOW_DICT]


def test_a_model_that_always_thinks_is_the_one_litellm_drops_disabled_for(monkeypatch):
    monkeypatch.delenv("USE_LITELLM_PROXY", raising=False)
    ask_list = []

    def is_always_on(model, provider):
        ask_list.append((model, provider))
        return model == "claude-x-20261001"

    monkeypatch.setattr(client_module.AnthropicModelInfo, "_is_always_on_thinking_model",
                        staticmethod(is_always_on))
    assert client_module._is_always_think_model("anthropic/claude-x-20261001") is True
    assert client_module._is_always_think_model("anthropic/claude-y") is False
    assert ask_list == [("claude-x-20261001", "anthropic"), ("claude-y", "anthropic")]
    param_dict = {"thinking": {"type": "disabled"}}
    client_module.AnthropicModelInfo.maybe_drop_disabled_thinking(
        "claude-x-20261001", param_dict, "anthropic")
    assert param_dict == {}


def test_a_model_of_no_provider_is_no_model_that_always_thinks(monkeypatch):
    monkeypatch.delenv("USE_LITELLM_PROXY", raising=False)
    assert client_module._is_always_think_model("no-provider-model") is False


def test_another_error_of_the_lookup_is_raised(monkeypatch):
    def broken(model):
        raise RuntimeError("broken")

    monkeypatch.setattr(litellm, "get_llm_provider", broken)
    with pytest.raises(RuntimeError):
        client_module._is_always_think_model("anthropic/m")


def test_an_unknown_effort_stops_the_client(monkeypatch):
    monkeypatch.setattr(client_module, "MAX_RETRIES", 0)
    with pytest.raises(ValueError, match="DOC_EFFORT"):
        LLMClient(model="anthropic/m", effort="max")


@pytest.mark.parametrize("error", [
    litellm.UnsupportedParamsError(
        message="ollama does not support parameters: ['thinking'], for model=llama3.",
        llm_provider="ollama", model="llama3"),
    litellm.BadRequestError(
        message='{"type": "invalid_request_error", "message": "This model does not support '
                'the effort parameter."}', llm_provider="anthropic", model="m"),
    litellm.BadRequestError(
        message='Invalid JSON payload received. Unknown name "output_config"',
        llm_provider="gemini", model="m"),
    litellm.BadRequestError(
        message="Thinking is not supported for this model", llm_provider="anthropic", model="m"),
])
def test_a_refused_effort_is_left_out_from_then_on(monkeypatch, caplog, error):
    text_list, kwarg_dict_list = _record_run(
        monkeypatch, "low", [error, _reply("a"), _reply("b")], call_count=2)
    assert text_list == ["a", "b"]
    assert [_effort_kwarg_dict(kwargs) for kwargs in kwarg_dict_list] == [
        {"thinking": {"type": "adaptive"}, "output_config": {"effort": "low"}}, {}, {}]
    assert sum("refused the effort parameters" in record.message
               for record in caplog.records) == 1


def test_a_refused_off_moves_to_low_then_to_nothing(monkeypatch, caplog):
    error = litellm.BadRequestError(message="thinking.type: disabled is not supported",
                                    llm_provider="anthropic", model="m")
    text_list, kwarg_dict_list = _record_run(
        monkeypatch, "off", [error, _reply("a"), _reply("b")], call_count=2)
    assert text_list == ["a", "b"]
    assert [_effort_kwarg_dict(kwargs) for kwargs in kwarg_dict_list] == [
        {"thinking": {"type": "disabled"}}, _LOW_DICT, _LOW_DICT]
    text_list, kwarg_dict_list = _record_run(
        monkeypatch, "off", [error, error, _reply("ok")])
    assert text_list == ["ok"]
    assert [_effort_kwarg_dict(kwargs) for kwargs in kwarg_dict_list] == [
        {"thinking": {"type": "disabled"}}, _LOW_DICT, {}]
    assert sum("refused the effort parameters" in record.message
               for record in caplog.records) == 3


def test_a_rate_limit_after_a_refusal_is_retried_without_the_effort(monkeypatch):
    refusal = litellm.BadRequestError(message="effort is not supported",
                                      llm_provider="anthropic", model="m")
    kwarg_dict_list = []

    async def fake_acompletion(**kwargs):
        kwarg_dict_list.append(kwargs)
        outcome = [refusal, _rate_limit_error(), _reply("ok")][len(kwarg_dict_list) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(client_module, "MAX_RETRIES", 1)
    monkeypatch.setattr(client_module, "RETRY_WAIT", 0)
    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    text = asyncio.run(LLMClient(model="anthropic/m", effort="low")._call_with_retry("p", 10))
    assert text == "ok"
    assert [bool(_effort_kwarg_dict(kwargs)) for kwargs in kwarg_dict_list] == [True, False, False]


def test_another_bad_request_after_a_refusal_is_a_failure(monkeypatch):
    refusal = litellm.BadRequestError(message="effort is not supported",
                                      llm_provider="anthropic", model="m")
    error = litellm.BadRequestError(message="invalid model", llm_provider="anthropic", model="m")
    text_list, kwarg_dict_list = _record_run(monkeypatch, "low", [refusal, error])
    assert text_list == [None]
    assert len(kwarg_dict_list) == 2


def test_a_refusal_after_the_effort_is_left_out_is_a_failure(monkeypatch):
    refusal = litellm.BadRequestError(message="effort is not supported",
                                      llm_provider="anthropic", model="m")
    error = litellm.BadRequestError(message="thinking blocks are malformed",
                                    llm_provider="anthropic", model="m")
    text_list, kwarg_dict_list = _record_run(
        monkeypatch, "low", [refusal, _reply("ok"), error], call_count=2)
    assert text_list == ["ok", None]
    assert len(kwarg_dict_list) == 3


def test_another_bad_request_keeps_the_effort(monkeypatch):
    error = litellm.BadRequestError(message="invalid model", llm_provider="anthropic", model="m")
    text_list, kwarg_dict_list = _record_run(
        monkeypatch, "low", [error, _reply("ok")], call_count=2)
    assert text_list == [None, "ok"]
    assert all(_effort_kwarg_dict(kwargs) for kwargs in kwarg_dict_list)


def test_an_overflow_with_the_effort_is_raised(monkeypatch):
    error = ContextWindowExceededError(
        message="prompt is too long; thinking budget", model="m", llm_provider="anthropic")
    with pytest.raises(ContextWindowExceededError):
        _record_run(monkeypatch, "low", [error])


def test_refusals_of_calls_running_together_warn_once(monkeypatch, caplog):
    error = litellm.UnsupportedParamsError(
        message="ollama does not support parameters: ['thinking']",
        llm_provider="ollama", model="llama3")
    send_list = []

    async def fake_acompletion(**kwargs):
        send_list.append("thinking" in kwargs)
        await asyncio.sleep(0)
        if "thinking" in kwargs:
            raise error
        return _reply("ok")

    async def call_together(llm_client):
        return await asyncio.gather(*(llm_client._call_with_retry("p", 10) for _ in range(3)))

    monkeypatch.setattr(client_module, "MAX_RETRIES", 0)
    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    llm_client = LLMClient(model="ollama/llama3", effort="low")
    assert asyncio.run(call_together(llm_client)) == ["ok"] * 3
    assert send_list == [True] * 3 + [False] * 3
    assert sum("refused the effort parameters" in record.message
               for record in caplog.records) == 1


def test_refusals_of_off_running_together_keep_the_low_form(monkeypatch, caplog):
    error = litellm.BadRequestError(message="thinking.type: disabled is not supported",
                                    llm_provider="anthropic", model="m")
    send_list = []

    async def fake_acompletion(**kwargs):
        send_list.append(_effort_kwarg_dict(kwargs))
        await asyncio.sleep(0)
        if kwargs.get("thinking") == {"type": "disabled"}:
            raise error
        return _reply("ok")

    async def call_together(llm_client):
        return await asyncio.gather(*(llm_client._call_with_retry("p", 10) for _ in range(3)))

    monkeypatch.setattr(client_module, "MAX_RETRIES", 0)
    monkeypatch.setattr(client_module, "_is_always_think_model", lambda model: False)
    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    llm_client = LLMClient(model="anthropic/m", effort="off")
    assert asyncio.run(call_together(llm_client)) == ["ok"] * 3
    assert send_list == [{"thinking": {"type": "disabled"}}] * 3 + [_LOW_DICT] * 3
    assert llm_client.effort_param_list == [_LOW_DICT]
    assert sum("refused the effort parameters" in record.message
               for record in caplog.records) == 1


def test_the_setting_is_read_trimmed_and_in_lower_case_and_is_the_default(monkeypatch):
    import importlib
    from codetwine.config import settings
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.delenv("DOC_EFFORT", raising=False)
    monkeypatch.delenv("MAX_RETRIES", raising=False)
    try:
        assert importlib.reload(settings).DOC_EFFORT == "low"
        monkeypatch.setenv("DOC_EFFORT", " High ")
        assert importlib.reload(settings).DOC_EFFORT == "high"
        reload_client = importlib.reload(client_module)
        assert reload_client.LLMClient(model="anthropic/m").effort_param_list == [
            {"thinking": {"type": "adaptive"}, "output_config": {"effort": "high"}}]
    finally:
        monkeypatch.undo()
        importlib.reload(settings)
        importlib.reload(client_module)


def test_a_rate_limit_naming_thinking_is_no_refusal(monkeypatch):
    error = litellm.RateLimitError(message="rate limit for thinking tokens",
                                   llm_provider="anthropic", model="m")
    kwarg_dict_list = []

    async def fake_acompletion(**kwargs):
        kwarg_dict_list.append(kwargs)
        if len(kwarg_dict_list) == 1:
            raise error
        return _reply("ok")

    monkeypatch.setattr(client_module, "MAX_RETRIES", 1)
    monkeypatch.setattr(client_module, "RETRY_WAIT", 0)
    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    llm_client = LLMClient(model="anthropic/m", effort="low")
    assert asyncio.run(llm_client._call_with_retry("p", 10)) == "ok"
    assert [_effort_kwarg_dict(kwargs) for kwargs in kwarg_dict_list] == [_LOW_DICT, _LOW_DICT]
    assert llm_client.effort_param_list == [_LOW_DICT]


def test_a_reply_without_choices_is_a_failure(monkeypatch):
    assert _run(monkeypatch, 0, [SimpleNamespace(choices=[])]) == (None, 1)


@pytest.mark.parametrize("error", [
    litellm.Timeout(message="timeout", model="m", llm_provider="anthropic"),
    litellm.APIConnectionError(message="no connection", llm_provider="anthropic", model="m"),
    litellm.InternalServerError(message="500", llm_provider="anthropic", model="m"),
    litellm.AuthenticationError(message="401", llm_provider="anthropic", model="m"),
])
def test_an_error_of_the_provider_is_a_failure_without_a_retry(monkeypatch, error):
    assert _run(monkeypatch, 3, [error]) == (None, 1)


def test_every_package_the_code_imports_is_a_dependency():
    """A package imported by codetwine is in the standard library or in pyproject.toml."""
    import ast
    import importlib.metadata
    import pathlib
    import re
    import sys
    import tomllib

    root = pathlib.Path(__file__).resolve().parent.parent
    with open(root / "pyproject.toml", "rb") as f:
        requirement_list = tomllib.load(f)["project"]["dependencies"]
    dependency_set = {
        re.split(r"[<>=!~\[; ]", requirement, maxsplit=1)[0].lower().replace("_", "-")
        for requirement in requirement_list
    }
    distribution_dict = importlib.metadata.packages_distributions()

    module_set: set[str] = set()
    for path in [root / "main.py", *sorted((root / "codetwine").rglob("*.py"))]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                module_set.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                module_set.add(node.module.split(".")[0])

    missing_list = sorted(
        module for module in module_set - set(sys.stdlib_module_names) - {"codetwine"}
        if not {
            name.lower().replace("_", "-") for name in distribution_dict.get(module, [])
        } & dependency_set
    )
    assert missing_list == []
