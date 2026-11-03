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

# The tool a tagging request is answered with.
TOOL_NAME = "tag_issue"


@dataclasses.dataclass(frozen=True)
class Request:
    """One request: an issue to tag, or a run to sum up.

    Attributes:
        system: Instructions, including the tag definitions.
        user: The material, fenced as untrusted data.
        schema: JSON schema the reply must follow.
        max_output_tokens: Upper bound on the reply's length.
        tool_name: The tool the model answers with.
        tool_description: What the tool records, as the model sees it.
    """

    system: str
    user: str
    schema: Mapping[str, Any]
    max_output_tokens: int
    tool_name: str = TOOL_NAME
    tool_description: str = "Record the issue's tag."


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


# Most requests the classifier sends at once. Providers size their
# connection pools for it, so parallel requests don't open throwaway
# connections.
MAX_PARALLEL_REQUESTS = 64


class ProviderError(RuntimeError):
    """A failure that stops the run, such as a rejected key."""


@dataclasses.dataclass(frozen=True)
class Model:
    """A model a provider offers by a short name.

    Attributes:
        id: The model ID the API expects.
        price: Its list price, for the budget.
    """

    id: str
    price: "pricing.Price"


def pick_model(models: Mapping[str, Model], name: str, default: str) -> Model:
    """Looks up a model by its short name.

    Args:
        models: The provider's models by short name, e.g. ``haiku``.
        name: The name asked for; empty picks ``default``.
        default: The name to use when none is given.

    Returns:
        The model.

    Raises:
        ProviderError: The name isn't one of ``models``.
    """
    key = (name or default).lower()
    if key not in models:
        raise ProviderError(
            f"Unknown model {key!r}. Pick {', '.join(models)} with --model "
            "or TRIAGE_MODEL."
        )
    return models[key]


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
