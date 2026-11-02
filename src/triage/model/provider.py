"""The contract between the classifier and a model provider.

A provider only talks to its API: it sends one request and returns the
parsed JSON reply with the tokens it used. The classifier owns the prompt,
checks on replies, retries, prices and the budget, so adding a provider
means one new class and no classifier changes.
"""

import dataclasses
from collections.abc import Mapping
from typing import TYPE_CHECKING
from typing import Any
from typing import Protocol

if TYPE_CHECKING:
    from triage.model import pricing


@dataclasses.dataclass(frozen=True)
class Request:
    """One classification request.

    Attributes:
        system: Instructions, including the tag definitions.
        user: One issue, fenced as untrusted data.
        schema: JSON schema the reply must follow.
        max_output_tokens: Upper bound on the reply's length.
    """

    system: str
    user: str
    schema: Mapping[str, Any]
    max_output_tokens: int


@dataclasses.dataclass(frozen=True)
class Usage:
    """Tokens one request was billed for.

    Attributes:
        input_tokens: Prompt tokens.
        output_tokens: Reply tokens.
    """

    input_tokens: int
    output_tokens: int


@dataclasses.dataclass(frozen=True)
class Reply:
    """A provider's answer to one request.

    Attributes:
        data: The parsed JSON reply, not yet checked. None when the reply
            wasn't usable JSON.
        usage: Tokens billed, which count even when ``data`` is None.
        problem: Why ``data`` is None; empty when it isn't.
    """

    data: Mapping[str, Any] | None
    usage: Usage
    problem: str = ""


class ProviderError(RuntimeError):
    """A failure that stops the run, such as a rejected key."""


class Provider(Protocol):
    """A model API the classifier can send issues to.

    Attributes:
        name: Provider name, e.g. ``bedrock``.
        model: The model ID requests use.
        price: The model's price, or None when unknown; the classifier
            refuses to run without one.
    """

    name: str
    model: str
    price: "pricing.Price | None"

    def classify(self, request: Request) -> Reply:
        """Sends one request and returns the parsed reply.

        Args:
            request: The request to send.

        Returns:
            The reply and the tokens it used.

        Raises:
            ProviderError: The request failed before the model answered.
        """
        ...
