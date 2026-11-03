"""Classifies issues with Claude through the Anthropic API.

The reply comes back as a tool call, so the JSON matches the schema's
shape. The tool is offered, not forced: Claude models such as Sonnet 5.5
refuse a forced call, and the prompt tells the model to use it.

You pick one of three models by name: haiku, sonnet or opus.
"""

import logging
import pathlib
from typing import TYPE_CHECKING
from typing import Any

from triage import config
from triage.model import pricing
from triage.model import provider

if TYPE_CHECKING:
    import anthropic

# The SDK is imported inside the functions that use it, so Tab completion,
# which runs triage on every press, doesn't pay for loading it.

logger = logging.getLogger(__name__)

NAME = "anthropic"
TOOL_NAME = provider.TOOL_NAME
MAX_RETRIES = 3
TIMEOUT_S = 60.0
# Prices from https://platform.claude.com/docs/en/about-claude/pricing,
# checked 2026-10-31.
MODELS = {
    "haiku": provider.Model("claude-haiku-4-5", pricing.Price(1.00, 5.00)),
    "sonnet": provider.Model("claude-sonnet-5-5", pricing.Price(2.00, 10.00)),
    "opus": provider.Model("claude-opus-5-5", pricing.Price(4.00, 20.00)),
}
DEFAULT_MODEL = "haiku"


class AnthropicProvider:
    """Sends classification requests to Claude through the Anthropic API.

    Attributes:
        name: ``anthropic``.
        model: The model ID.
        price: The model's price, for the budget.
    """

    def __init__(self, model: provider.Model, client: Any) -> None:
        """Wraps an Anthropic client.

        Args:
            model: The model to use.
            client: An ``anthropic.Anthropic`` client, or a stand-in with the
                same ``messages.create`` method.
        """
        self.name = NAME
        self.model = model.id
        self.price: pricing.Price | None = model.price
        self._client = client

    def check(self) -> None:
        """Sends one tiny request to confirm the key and model work.

        Raises:
            ProviderError: The request failed; the message says why.
        """
        import anthropic

        try:
            self._client.messages.create(
                model=self.model,
                max_tokens=8,
                messages=[{"role": "user", "content": "Say OK."}],
            )
        except anthropic.APIError as error:
            raise _error(error, self.model) from error

    def classify(self, request: provider.Request) -> provider.Reply:
        """Sends one request and returns the tool call's input.

        Args:
            request: The request to send.

        Returns:
            The parsed reply and the tokens it used.

        Raises:
            ProviderError: The request failed before the model answered.
        """
        import anthropic

        try:
            message = self._client.messages.create(
                model=self.model,
                max_tokens=request.max_output_tokens,
                system=request.system,
                messages=[{"role": "user", "content": request.user}],
                tools=[
                    {
                        "name": request.tool_name,
                        "description": request.tool_description,
                        "input_schema": dict(request.schema),
                    }
                ],
                tool_choice={"type": "auto"},
            )
        except anthropic.APIError as error:
            raise _error(error, self.model) from error
        usage = provider.Usage(
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
        )
        calls = [block for block in message.content if block.type == "tool_use"]
        if not calls:
            return provider.Reply(
                data=None,
                usage=usage,
                problem=(
                    "the model didn't call the tool (stop reason: "
                    f"{message.stop_reason})"
                ),
            )
        data = calls[0].input
        if not isinstance(data, dict):
            return provider.Reply(
                data=None, usage=usage, problem="the reply wasn't an object"
            )
        return provider.Reply(data=data, usage=usage)


def from_settings(
    model_name: str = "", env_file: pathlib.Path = config.ENV_FILE
) -> AnthropicProvider:
    """Builds the provider from settings.

    Needs ``ANTHROPIC_API_KEY``. ``TRIAGE_MODEL`` (haiku, sonnet or opus) is
    optional.

    Args:
        model_name: haiku, sonnet or opus; empty uses ``TRIAGE_MODEL``,
            then haiku.
        env_file: The project dotenv file.

    Returns:
        A provider ready to send requests.

    Raises:
        ProviderError: The key is missing or the model name is unknown.
    """
    model = provider.pick_model(
        MODELS,
        model_name or config.get("TRIAGE_MODEL", env_file),
        DEFAULT_MODEL,
    )
    api_key = config.get("ANTHROPIC_API_KEY", env_file)
    if not api_key:
        raise provider.ProviderError(
            "The Anthropic API needs ANTHROPIC_API_KEY. Run triage setup, or "
            "set it in .env; create a key at https://platform.claude.com."
        )
    import anthropic

    client = anthropic.Anthropic(
        api_key=api_key,
        # Explicit, so a base URL exported for another tool can't redirect
        # requests unnoticed.
        base_url="https://api.anthropic.com",
        max_retries=MAX_RETRIES,
        timeout=TIMEOUT_S,
    )
    return AnthropicProvider(model, client)


def _error(error: "anthropic.APIError", model: str) -> provider.ProviderError:
    """Turns an SDK error into a message that says what to do."""
    import anthropic

    if isinstance(error, anthropic.AuthenticationError):
        return provider.ProviderError(
            "Anthropic rejected the API key (HTTP 401). Check "
            "ANTHROPIC_API_KEY, or run triage setup."
        )
    if isinstance(
        error, (anthropic.PermissionDeniedError, anthropic.NotFoundError)
    ):
        return provider.ProviderError(
            f"Your key may not use {model} (HTTP {error.status_code}). Try "
            "another model with --model (haiku, sonnet or opus)."
        )
    if isinstance(error, anthropic.RateLimitError):
        return provider.ProviderError(
            "Anthropic's rate limit held after retries (HTTP 429). Wait a "
            "minute and run again; tagged issues aren't sent twice."
        )
    if isinstance(error, anthropic.APIConnectionError):
        return provider.ProviderError(
            "Couldn't reach the Anthropic API. Check your connection and "
            "run again."
        )
    if isinstance(error, anthropic.APIStatusError):
        return provider.ProviderError(
            f"The Anthropic API returned HTTP {error.status_code}: "
            f"{error.message}"
        )
    return provider.ProviderError(f"The Anthropic API failed: {error}")
