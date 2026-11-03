import json
import pathlib
import types
from typing import Any

import pytest
from conftest import issue

from triage import config
from triage.model import anthropic_api
from triage.model import bedrock
from triage.model import openai_compat
from triage.model import prompt
from triage.model import provider
from triage.model import providers
from triage.pipeline import transform

REQUEST = prompt.request(transform.to_issue(issue(1)))

PRICED_MODELS = [
    *(("anthropic", name, m) for name, m in anthropic_api.MODELS.items()),
    *(("bedrock", name, m) for name, m in bedrock.MODELS.items()),
    *(
        ("openai", name, provider.Model(name, price))
        for name, price in openai_compat.OPENAI_PRICES.items()
    ),
]


@pytest.mark.parametrize(
    ("source", "name", "model"),
    PRICED_MODELS,
    ids=[f"{source}-{name}" for source, name, _ in PRICED_MODELS],
)
def test_every_model_setup_offers_has_a_price(
    source: str, name: str, model: provider.Model
) -> None:
    """A model with no price won't run, so setup may not offer one."""
    assert model.price is not None
    assert model.price.input_per_million > 0
    assert model.price.output_per_million > 0


def test_the_anthropic_api_offers_haiku_5_5() -> None:
    assert anthropic_api.MODELS["haiku"].id == "claude-haiku-5-5"


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        ({"TRIAGE_PROVIDER": "bedrock", "ANTHROPIC_API_KEY": "k"}, "bedrock"),
        ({"ANTHROPIC_API_KEY": "k", "AWS_ACCESS_KEY_ID": "id"}, "anthropic"),
        ({"OPENAI_BASE_URL": "http://localhost:11434/v1"}, "openai"),
        ({"AWS_ACCESS_KEY_ID": "id"}, "bedrock"),
    ],
)
def test_picks_the_provider_from_settings(
    settings: dict[str, str],
    expected: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AWS_ACCESS_KEY_ID"):
        monkeypatch.delenv(key, raising=False)
    config.write_env_file(settings, config.user_env_file())

    assert providers.configured() == expected


def test_anthropic_offers_the_tool_and_reads_its_input() -> None:
    sent: dict[str, Any] = {}

    def create(**kwargs: Any) -> Any:
        sent.update(kwargs)
        return types.SimpleNamespace(
            stop_reason="tool_use",
            content=[
                types.SimpleNamespace(
                    type="tool_use", input={"tag": "bug", "rationale": "Crash."}
                )
            ],
            usage=types.SimpleNamespace(input_tokens=800, output_tokens=30),
        )

    model = anthropic_api.AnthropicProvider(
        anthropic_api.MODELS["sonnet"],
        types.SimpleNamespace(messages=types.SimpleNamespace(create=create)),
    )

    reply = model.classify(REQUEST)

    assert reply.data == {"tag": "bug", "rationale": "Crash."}
    assert reply.usage == provider.Usage(800, 30)
    # Offered, not forced: Claude models such as Sonnet 5.5 refuse forcing.
    assert sent["tool_choice"] == {"type": "auto"}


def _openai_client(arguments: str) -> Any:
    def create(**kwargs: Any) -> Any:
        call = types.SimpleNamespace(
            function=types.SimpleNamespace(
                name=openai_compat.TOOL_NAME, arguments=arguments
            )
        )
        return types.SimpleNamespace(
            choices=[
                types.SimpleNamespace(
                    finish_reason="tool_calls",
                    message=types.SimpleNamespace(tool_calls=[call]),
                )
            ],
            usage=types.SimpleNamespace(
                prompt_tokens=700, completion_tokens=25
            ),
        )

    return types.SimpleNamespace(
        chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=create)
        )
    )


def test_openai_reads_the_function_arguments() -> None:
    model = openai_compat.OpenAICompatibleProvider(
        provider.Model("llama3.1", openai_compat.parse_price("0,0")),
        _openai_client(json.dumps({"tag": "docs", "rationale": "Typo."})),
    )

    reply = model.classify(REQUEST)

    assert reply.data == {"tag": "docs", "rationale": "Typo."}
    assert reply.usage == provider.Usage(700, 25)


def test_openai_arguments_that_arent_json_are_unusable() -> None:
    model = openai_compat.OpenAICompatibleProvider(
        provider.Model("llama3.1", openai_compat.parse_price("0,0")),
        _openai_client("{tag: bug"),
    )

    assert model.classify(REQUEST).problem == "the arguments weren't JSON"


@pytest.mark.parametrize("text", ["", "1", "a,b", "-1,2"])
def test_a_missing_or_bad_price_is_refused(text: str) -> None:
    with pytest.raises(provider.ProviderError, match="TRIAGE_PRICE_PER_MTOK"):
        openai_compat.parse_price(text)


def test_openai_switches_to_max_completion_tokens_when_told() -> None:
    import httpx2
    import openai

    sent: list[str] = []
    reply = _openai_client(json.dumps({"tag": "bug", "rationale": "Crash."}))
    answer = reply.chat.completions.create

    def create(**kwargs: Any) -> Any:
        length = (
            "max_tokens" if "max_tokens" in kwargs else "max_completion_tokens"
        )
        sent.append(length)
        if length == "max_tokens":
            raise openai.BadRequestError(
                "Unsupported parameter: 'max_tokens'. Use "
                "'max_completion_tokens' instead.",
                response=httpx2.Response(
                    400, request=httpx2.Request("POST", "https://x.example")
                ),
                body=None,
            )
        return answer(**kwargs)

    reply.chat.completions.create = create
    model = openai_compat.OpenAICompatibleProvider(
        provider.Model("gpt-6-luna", openai_compat.OPENAI_PRICES["gpt-6-luna"]),
        reply,
    )

    assert model.classify(REQUEST).data == {"tag": "bug", "rationale": "Crash."}
    model.classify(REQUEST)
    assert sent == [
        "max_tokens",
        "max_completion_tokens",
        "max_completion_tokens",
    ]


def test_a_local_server_gets_longer_and_says_when_it_timed_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx2
    import openai

    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("TRIAGE_MODEL", "llama3.1:8b")
    monkeypatch.setenv("TRIAGE_PRICE_PER_MTOK", "0,0")
    monkeypatch.setenv("TRIAGE_PROVIDER", "openai")
    model = providers.from_settings()
    assert model._client.timeout == openai_compat.LOCAL_TIMEOUT_S  # type: ignore[attr-defined]

    timeout = openai.APITimeoutError(
        request=httpx2.Request("POST", "http://localhost:11434/v1")
    )
    message = str(openai_compat._error(timeout, "llama3.1:8b", 300))
    assert message.startswith("llama3.1:8b didn't answer within 300 seconds")
    assert openai_compat.is_local("http://127.0.0.1:1234/v1")
    assert not openai_compat.is_local("https://api.groq.com/openai/v1")
