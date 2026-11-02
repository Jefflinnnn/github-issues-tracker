"""Picks the model provider from settings and builds it.

``TRIAGE_PROVIDER`` chooses: ``anthropic``, ``openai`` or ``bedrock``.
When it is empty, triage uses the first provider whose key is set, in that
order, so a settings file with one kind of key needs nothing more.
"""

import pathlib
from typing import Protocol

from triage import config
from triage.model import anthropic_api
from triage.model import bedrock
from triage.model import openai_compat
from triage.model import provider

LABELS = {
    "anthropic": "Anthropic API",
    "openai": "OpenAI-compatible endpoint",
    "bedrock": "Amazon Bedrock",
}


class CheckableProvider(provider.Provider, Protocol):
    """A provider that can also send a tiny test request."""

    def check(self) -> None:
        """Sends one tiny request to confirm the settings work.

        Raises:
            ProviderError: The request failed.
        """
        ...


def configured(env_file: pathlib.Path = config.ENV_FILE) -> str:
    """Returns which provider settings choose.

    Args:
        env_file: The project dotenv file.

    Returns:
        ``anthropic``, ``openai`` or ``bedrock``.

    Raises:
        ProviderError: ``TRIAGE_PROVIDER`` names no provider, or nothing is
            set up.
    """
    chosen = config.get("TRIAGE_PROVIDER", env_file).lower()
    if chosen:
        if chosen not in LABELS:
            raise provider.ProviderError(
                f"TRIAGE_PROVIDER={chosen} isn't a provider; use "
                f"{', '.join(LABELS)}, or run triage setup."
            )
        return chosen
    if config.get("ANTHROPIC_API_KEY", env_file):
        return "anthropic"
    if config.get("OPENAI_API_KEY", env_file) or config.get(
        "OPENAI_BASE_URL", env_file
    ):
        return "openai"
    if config.get("AWS_ACCESS_KEY_ID", env_file):
        return "bedrock"
    raise provider.ProviderError(
        "No model provider is set up. Run triage setup, or set "
        "ANTHROPIC_API_KEY, OPENAI_API_KEY or AWS keys in .env (see "
        ".env.example)."
    )


def from_settings(
    model_name: str = "", env_file: pathlib.Path = config.ENV_FILE
) -> CheckableProvider:
    """Builds the provider settings choose.

    Args:
        model_name: The model to use; empty uses ``TRIAGE_MODEL``.
        env_file: The project dotenv file.

    Returns:
        A provider ready to send requests.

    Raises:
        ProviderError: A setting is missing or wrong.
    """
    chosen = configured(env_file)
    if chosen == "anthropic":
        return anthropic_api.from_settings(model_name, env_file)
    if chosen == "openai":
        return openai_compat.from_settings(model_name, env_file)
    return bedrock.from_settings(model_name, env_file)
