import pathlib
from typing import Any

import botocore.exceptions
import pytest
from conftest import issue

from triage.model import bedrock
from triage.model import prompt
from triage.model import provider
from triage.pipeline import transform


class FakeClient:
    def __init__(self, response: Any = None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.kwargs: dict[str, Any] = {}

    def converse(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.response


def _response(stop_reason: str, content: list[dict[str, Any]]) -> Any:
    return {
        "stopReason": stop_reason,
        "output": {"message": {"role": "assistant", "content": content}},
        "usage": {"inputTokens": 900, "outputTokens": 40},
    }


def _send(client: FakeClient) -> provider.Reply:
    model = bedrock.BedrockProvider(bedrock.MODELS["haiku"], client)
    return model.classify(prompt.request(transform.to_issue(issue(1))))


def _client_error(status: int, message: str) -> botocore.exceptions.ClientError:
    return botocore.exceptions.ClientError(
        {
            "Error": {"Code": "AccessDeniedException", "Message": message},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        "Converse",
    )


def test_offers_the_tool_and_returns_its_input() -> None:
    call = {
        "toolUse": {
            "toolUseId": "t1",
            "name": bedrock.TOOL_NAME,
            "input": {"tag": "bug", "rationale": "Crash."},
        }
    }
    client = FakeClient(_response("tool_use", [call]))

    reply = _send(client)

    assert reply.data == {"tag": "bug", "rationale": "Crash."}
    assert reply.usage == provider.Usage(900, 40)
    # Offered, not forced: Sonnet 5.5 refuses a forced tool call.
    assert client.kwargs["toolConfig"]["toolChoice"] == {"auto": {}}
    assert client.kwargs["modelId"] == bedrock.MODELS["haiku"].id


def test_a_cut_off_reply_is_unusable_but_still_billed() -> None:
    reply = _send(
        FakeClient(_response("max_tokens", [{"text": "Let me think"}]))
    )

    assert reply.data is None
    assert "max_tokens" in reply.problem
    assert reply.usage.input_tokens == 900


@pytest.mark.parametrize(
    ("status", "message", "expected"),
    [
        (401, "Unauthorized", "rejected the key pair"),
        (403, "Invalid client id or secret", "rejected the key pair"),
        (
            403,
            "User is not authorized to perform: bedrock:InvokeModel",
            "may not use",
        ),
    ],
)
def test_errors_say_what_to_fix(
    status: int, message: str, expected: str
) -> None:
    with pytest.raises(provider.ProviderError, match=expected):
        _send(FakeClient(error=_client_error(status, message)))


def test_picks_a_model_by_name(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "id")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("TRIAGE_MODEL", "sonnet")

    model = bedrock.from_settings()
    assert model.model == bedrock.MODELS["sonnet"].id
    # Enough connections for the most parallel requests --workers allows.
    pool = model._client.meta.config.max_pool_connections
    assert pool == provider.MAX_PARALLEL_REQUESTS
    assert bedrock.from_settings("opus").model == bedrock.MODELS["opus"].id
    with pytest.raises(provider.ProviderError, match="Unknown model"):
        bedrock.from_settings("gpt")


def test_missing_keys_are_named(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")

    with pytest.raises(provider.ProviderError, match="AWS_ACCESS_KEY_ID"):
        bedrock.from_settings()
