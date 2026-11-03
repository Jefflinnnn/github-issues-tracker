"""Classifies issues with Claude on Amazon Bedrock.

Requests use Bedrock's Converse API through boto3, signed with an AWS key
pair (SigV4), and go to AWS directly or to an organization's gateway. The
reply comes back as a tool call, so the JSON matches the schema's shape.
The tool is offered, not forced: Claude models such as Sonnet 5.5 refuse a
forced call, and the prompt tells the model to use it.

You pick one of three models by name: haiku, sonnet or opus.
"""

import logging
import os
import pathlib
from typing import TYPE_CHECKING
from typing import Any

from triage import config
from triage.model import pricing
from triage.model import provider

if TYPE_CHECKING:
    import botocore.exceptions

# boto3 is imported inside the functions that use it: it takes about a
# tenth of a second to load, and Tab completion runs triage on every press.

logger = logging.getLogger(__name__)

NAME = "bedrock"
TOOL_NAME = provider.TOOL_NAME
DEFAULT_REGION = "us-west-2"


# Prices from https://aws.amazon.com/bedrock/pricing/ (Anthropic tab,
# US West (Oregon)), checked 2026-10-31. Haiku 5.5 costs 5x more for a
# prompt over 100,000 tokens; triage's prompts are far shorter.
MODELS = {
    "haiku": provider.Model(
        "us.anthropic.claude-haiku-5-5", pricing.Price(0.11, 0.55)
    ),
    "sonnet": provider.Model(
        "us.anthropic.claude-sonnet-5-5", pricing.Price(2.20, 11.00)
    ),
    "opus": provider.Model(
        "us.anthropic.claude-opus-5-5", pricing.Price(4.40, 22.00)
    ),
}
DEFAULT_MODEL = "haiku"

_REQUIRED = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
# boto3 reads this by itself and, when set, sends it instead of signing
# with the key pair, which would hand that key to whatever endpoint is
# configured.
_BEARER_TOKEN = "AWS_BEARER_TOKEN_BEDROCK"
# What a gateway or AWS says when it rejects the key pair itself.
_REJECTED_KEY_PHRASES = (
    "invalid client id",
    "client id or secret",
    "invalid client secret",
    "signature we calculated does not match",
    "security token included in the request is invalid",
)
# What it says when the key pair works but may not use the model.
_REFUSED_MODEL_PHRASES = (
    "not authorized to perform: bedrock:invoke",
    "don't have access to the model",
)


class BedrockProvider:
    """Sends classification requests to Claude on Bedrock.

    Attributes:
        name: ``bedrock``.
        model: The Bedrock model ID.
        price: The model's price, for the budget.
    """

    def __init__(self, model: provider.Model, client: Any) -> None:
        """Wraps a ``bedrock-runtime`` client.

        Args:
            model: The model to use.
            client: A boto3 ``bedrock-runtime`` client, or a stand-in with
                the same ``converse`` method.
        """
        self.name = NAME
        self.model = model.id
        self.price: pricing.Price | None = model.price
        self._client = client

    def check(self) -> None:
        """Sends one tiny request to confirm the key pair and model work.

        It costs a few tokens: a one-line prompt and a reply of at most 8.

        Raises:
            ProviderError: The request failed; the message says why.
        """
        import botocore.exceptions

        try:
            self._client.converse(
                modelId=self.model,
                messages=[{"role": "user", "content": [{"text": "Say OK."}]}],
                inferenceConfig={"maxTokens": 8},
            )
        except botocore.exceptions.ClientError as error:
            raise _client_error(error, self.model) from error
        except botocore.exceptions.BotoCoreError as error:
            raise provider.ProviderError(
                f"Couldn't reach Bedrock ({error}). Check your connection "
                "and BEDROCK_ENDPOINT_URL."
            ) from error

    def classify(self, request: provider.Request) -> provider.Reply:
        """Sends one request and returns the tool call's input.

        Args:
            request: The request to send.

        Returns:
            The parsed reply and the tokens it used.

        Raises:
            ProviderError: The request failed before the model answered.
        """
        import botocore.exceptions

        try:
            response = self._converse(request)
        except botocore.exceptions.ClientError as error:
            raise _client_error(error, self.model) from error
        except botocore.exceptions.BotoCoreError as error:
            raise provider.ProviderError(
                f"Couldn't reach Bedrock ({error}). Check your connection "
                "and BEDROCK_ENDPOINT_URL, then run again."
            ) from error
        usage = provider.Usage(
            input_tokens=response["usage"]["inputTokens"],
            output_tokens=response["usage"]["outputTokens"],
        )
        calls = [
            block["toolUse"]
            for block in response["output"]["message"]["content"]
            if "toolUse" in block
        ]
        if not calls:
            return provider.Reply(
                data=None,
                usage=usage,
                problem=(
                    "the model didn't call the tool (stop reason: "
                    f"{response['stopReason']})"
                ),
            )
        data = calls[0].get("input")
        if not isinstance(data, dict):
            return provider.Reply(
                data=None, usage=usage, problem="the reply wasn't an object"
            )
        return provider.Reply(data=data, usage=usage)

    def _converse(self, request: provider.Request) -> Any:
        """Sends one Converse request offering the tagging tool."""
        return self._client.converse(
            modelId=self.model,
            system=[{"text": request.system}],
            messages=[{"role": "user", "content": [{"text": request.user}]}],
            inferenceConfig={"maxTokens": request.max_output_tokens},
            toolConfig={
                "tools": [
                    {
                        "toolSpec": {
                            "name": request.tool_name,
                            "description": request.tool_description,
                            "inputSchema": {"json": dict(request.schema)},
                        }
                    }
                ],
                "toolChoice": {"auto": {}},
            },
        )


def from_settings(
    model_name: str = "", env_file: pathlib.Path = config.ENV_FILE
) -> BedrockProvider:
    """Builds the provider from ``.env``, then the environment.

    Needs ``AWS_ACCESS_KEY_ID`` and ``AWS_SECRET_ACCESS_KEY``.
    ``TRIAGE_MODEL`` (haiku, sonnet or opus), ``AWS_REGION`` and
    ``BEDROCK_ENDPOINT_URL`` (a gateway) are optional.

    Args:
        model_name: haiku, sonnet or opus; empty uses ``TRIAGE_MODEL``,
            then haiku.
        env_file: The dotenv file to read first.

    Returns:
        A provider ready to send requests.

    Raises:
        ProviderError: A setting is missing or names no known model.
    """
    model = provider.pick_model(
        MODELS,
        model_name or config.get("TRIAGE_MODEL", env_file),
        DEFAULT_MODEL,
    )
    keys = {key: config.get(key, env_file) for key in _REQUIRED}
    missing = [key for key, value in keys.items() if not value]
    if missing:
        raise provider.ProviderError(
            f"Bedrock needs {' and '.join(missing)}. Run triage setup, or "
            "set them in .env (see .env.example)."
        )
    if os.environ.pop(_BEARER_TOKEN, None):
        logger.debug("Ignoring %s; signing with the key pair", _BEARER_TOKEN)
    region = config.get("AWS_REGION", env_file) or DEFAULT_REGION
    import boto3
    import botocore.config

    session = boto3.session.Session(
        aws_access_key_id=keys["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=keys["AWS_SECRET_ACCESS_KEY"],
        aws_session_token=config.get("AWS_SESSION_TOKEN", env_file) or None,
        region_name=region,
    )
    client = session.client(
        "bedrock-runtime",
        # None sends requests to AWS directly.
        endpoint_url=config.get("BEDROCK_ENDPOINT_URL", env_file) or None,
        config=botocore.config.Config(
            signature_version="v4",
            retries={"max_attempts": 3, "mode": "standard"},
            # boto3 keeps 10 connections by default; more parallel requests
            # than that each open and discard one, with a warning.
            max_pool_connections=provider.MAX_PARALLEL_REQUESTS,
            connect_timeout=10,
            read_timeout=60,
        ),
    )
    return BedrockProvider(model, client)


def _client_error(
    error: "botocore.exceptions.ClientError", model: str
) -> provider.ProviderError:
    """Turns a Bedrock or gateway error into a message that says what to do."""
    status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    code = error.response.get("Error", {}).get("Code", "")
    message = error.response.get("Error", {}).get("Message") or str(error)
    lowered = message.lower()
    if status == 401 or any(p in lowered for p in _REJECTED_KEY_PHRASES):
        return provider.ProviderError(
            f"Bedrock rejected the key pair ({message}). Check "
            "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY."
        )
    if any(phrase in lowered for phrase in _REFUSED_MODEL_PHRASES):
        others = ", ".join(
            name for name, option in MODELS.items() if option.id != model
        )
        return provider.ProviderError(
            f"Your key pair works, but it may not use {model}. Try another "
            f"model with --model ({others})."
        )
    if code == "ThrottlingException":
        return provider.ProviderError(
            "Bedrock's rate limit held after retries. Wait a minute and run "
            "again; tagged issues aren't sent twice."
        )
    return provider.ProviderError(f"Bedrock returned HTTP {status}: {message}")
