"""Classifies issues with any OpenAI-compatible chat completions endpoint.

That covers OpenAI itself and servers that copy its API, such as Ollama,
vLLM, LM Studio and Groq. The reply comes back as a function call. The
function is offered, not forced, the same as for Claude, and the prompt
tells the model to use it.

triage knows the prices of a few current OpenAI models. For any other
model or server, the price comes from ``TRIAGE_PRICE_PER_MTOK``; ``0,0``
suits a local model.
"""

import json
import logging
import pathlib
from typing import TYPE_CHECKING
from typing import Any

from triage import config
from triage.model import pricing
from triage.model import provider

if TYPE_CHECKING:
    import openai

# The SDK is imported inside the functions that use it, so Tab completion,
# which runs triage on every press, doesn't pay for loading it.

logger = logging.getLogger(__name__)

NAME = "openai"
TOOL_NAME = "tag_issue"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
MAX_RETRIES = 3
TIMEOUT_S = 60.0
# OpenAI's recommended models for this kind of work, cheapest first, from
# https://developers.openai.com/api/docs/pricing (standard tier), checked
# 2026-11-01. Setup suggests them; their prices need no setting.
OPENAI_PRICES = {
    "gpt-6-luna": pricing.Price(0.10, 0.50),
    "gpt-6.1-terra": pricing.Price(1.00, 5.00),
    "gpt-6.1-sol": pricing.Price(2.00, 10.00),
}
OPENAI_NOTES = ("cheapest", "balanced", "strongest of the three")


class OpenAICompatibleProvider:
    """Sends classification requests to a chat completions endpoint.

    Attributes:
        name: ``openai``.
        model: The model name the server expects.
        price: The price from settings, for the budget.
    """

    def __init__(self, model: provider.Model, client: Any) -> None:
        """Wraps an OpenAI client.

        Args:
            model: The model to use, with its price.
            client: An ``openai.OpenAI`` client, or a stand-in with the same
                ``chat.completions.create`` method.
        """
        self.name = NAME
        self.model = model.id
        self.price: pricing.Price | None = model.price
        self._client = client
        # OpenAI's newer models want max_completion_tokens and refuse
        # max_tokens; most other servers know only max_tokens. Start with
        # max_tokens and switch once if the server says so.
        self._length_setting = "max_tokens"

    def check(self) -> None:
        """Sends one tiny request to confirm the key, server and model work.

        Raises:
            ProviderError: The request failed; the message says why.
        """
        import openai

        try:
            self._create(8, messages=[{"role": "user", "content": "Say OK."}])
        except openai.APIError as error:
            raise _error(error, self.model) from error

    def classify(self, request: provider.Request) -> provider.Reply:
        """Sends one request and returns the function call's arguments.

        Args:
            request: The request to send.

        Returns:
            The parsed reply and the tokens it used.

        Raises:
            ProviderError: The request failed before the model answered.
        """
        import openai

        try:
            completion = self._create(
                request.max_output_tokens,
                messages=[
                    {"role": "system", "content": request.system},
                    {"role": "user", "content": request.user},
                ],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": TOOL_NAME,
                            "description": "Record the issue's tag.",
                            "parameters": dict(request.schema),
                        },
                    }
                ],
                tool_choice="auto",
            )
        except openai.APIError as error:
            raise _error(error, self.model) from error
        usage = _usage(completion)
        message = completion.choices[0].message
        calls = message.tool_calls or []
        if not calls:
            return provider.Reply(
                data=None,
                usage=usage,
                problem=(
                    "the model didn't call the function (finish reason: "
                    f"{completion.choices[0].finish_reason})"
                ),
            )
        try:
            data = json.loads(calls[0].function.arguments)
        except json.JSONDecodeError:
            return provider.Reply(
                data=None, usage=usage, problem="the arguments weren't JSON"
            )
        if not isinstance(data, dict):
            return provider.Reply(
                data=None, usage=usage, problem="the reply wasn't an object"
            )
        return provider.Reply(data=data, usage=usage)

    def _create(self, max_output_tokens: int, **arguments: Any) -> Any:
        """Sends one request, naming the reply-length limit the server's way."""
        import openai

        try:
            return self._client.chat.completions.create(
                model=self.model,
                **{self._length_setting: max_output_tokens},
                **arguments,
            )
        except openai.BadRequestError as error:
            if not _length_error(error):
                raise
            self._length_setting = (
                "max_completion_tokens"
                if self._length_setting == "max_tokens"
                else "max_tokens"
            )
            logger.info("Retrying with %s", self._length_setting)
            return self._client.chat.completions.create(
                model=self.model,
                **{self._length_setting: max_output_tokens},
                **arguments,
            )


def _length_error(error: Exception) -> bool:
    """Says whether a server refused the reply-length setting's name."""
    message = str(error)
    return "max_tokens" in message and "max_completion_tokens" in message


def from_settings(
    model_name: str = "", env_file: pathlib.Path = config.ENV_FILE
) -> OpenAICompatibleProvider:
    """Builds the provider from settings.

    Needs a model name (``--model`` or ``TRIAGE_MODEL``) and its price
    (``TRIAGE_PRICE_PER_MTOK``), unless it is one of ``OPENAI_PRICES`` on
    OpenAI itself. ``OPENAI_BASE_URL`` defaults to OpenAI;
    ``OPENAI_API_KEY`` may be empty for a local server.

    Args:
        model_name: The model name; empty uses ``TRIAGE_MODEL``.
        env_file: The project dotenv file.

    Returns:
        A provider ready to send requests.

    Raises:
        ProviderError: A setting is missing or malformed.
    """
    name = model_name or config.get("TRIAGE_MODEL", env_file)
    if not name:
        raise provider.ProviderError(
            "Name the model for your OpenAI-compatible endpoint with --model "
            "or TRIAGE_MODEL, e.g. gpt-6-luna or qwen3:8b."
        )
    base_url = config.get("OPENAI_BASE_URL", env_file) or DEFAULT_BASE_URL
    price_setting = config.get("TRIAGE_PRICE_PER_MTOK", env_file)
    known = OPENAI_PRICES.get(name) if base_url == DEFAULT_BASE_URL else None
    price = known if known and not price_setting else parse_price(price_setting)
    api_key = config.get("OPENAI_API_KEY", env_file)
    if not api_key and base_url == DEFAULT_BASE_URL:
        raise provider.ProviderError(
            "OpenAI needs OPENAI_API_KEY. Run triage setup, or set it in "
            ".env. For a local server, set OPENAI_BASE_URL instead."
        )
    import openai

    client = openai.OpenAI(
        # Local servers ignore the key, but the SDK requires one.
        api_key=api_key or "unused",
        base_url=base_url,
        max_retries=MAX_RETRIES,
        timeout=TIMEOUT_S,
    )
    return OpenAICompatibleProvider(provider.Model(name, price), client)


def parse_price(text: str) -> pricing.Price:
    """Parses ``TRIAGE_PRICE_PER_MTOK``: input and output USD per million.

    Args:
        text: For example ``0.40,1.60``, or ``0,0`` for a local model.

    Returns:
        The price.

    Raises:
        ProviderError: The text is empty or not two numbers of 0 or more.
    """
    parts = [part.strip() for part in text.split(",")]
    try:
        input_price, output_price = (float(part) for part in parts)
    except ValueError:
        raise provider.ProviderError(
            "Set TRIAGE_PRICE_PER_MTOK to the model's input and output price "
            "in USD per million tokens, e.g. 0.40,1.60 (0,0 for a local "
            "model), so triage can keep within the budget."
        ) from None
    if input_price < 0 or output_price < 0:
        raise provider.ProviderError(
            f"TRIAGE_PRICE_PER_MTOK can't be negative, got {text}."
        )
    return pricing.Price(input_price, output_price)


def _usage(completion: Any) -> provider.Usage:
    """Reads token counts; a server that reports none counts as zero."""
    if completion.usage is None:
        logger.warning(
            "The server reported no token usage; spend counts as $0."
        )
        return provider.Usage(0, 0)
    return provider.Usage(
        input_tokens=completion.usage.prompt_tokens or 0,
        output_tokens=completion.usage.completion_tokens or 0,
    )


def _error(error: "openai.APIError", model: str) -> provider.ProviderError:
    """Turns an SDK error into a message that says what to do."""
    import openai

    if isinstance(error, openai.AuthenticationError):
        return provider.ProviderError(
            "The endpoint rejected the API key (HTTP 401). Check "
            "OPENAI_API_KEY, or run triage setup."
        )
    if isinstance(error, (openai.PermissionDeniedError, openai.NotFoundError)):
        return provider.ProviderError(
            f"The endpoint doesn't serve {model} to this key (HTTP "
            f"{error.status_code}). Check the model name and OPENAI_BASE_URL."
        )
    if isinstance(error, openai.RateLimitError):
        return provider.ProviderError(
            "The endpoint's rate limit held after retries (HTTP 429). Wait "
            "a minute and run again; tagged issues aren't sent twice."
        )
    if isinstance(error, openai.APIConnectionError):
        return provider.ProviderError(
            "Couldn't reach the endpoint. Check OPENAI_BASE_URL and your "
            "connection, then run again."
        )
    if isinstance(error, openai.APIStatusError):
        return provider.ProviderError(
            f"The endpoint returned HTTP {error.status_code}: {error.message}"
        )
    return provider.ProviderError(f"The endpoint failed: {error}")
