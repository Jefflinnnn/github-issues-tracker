"""Turns token counts into dollars, so the classifier can keep a budget."""

import dataclasses

from triage.model import provider


@dataclasses.dataclass(frozen=True)
class Price:
    """Prices for one model, in USD per million tokens.

    Attributes:
        input_per_million: Price of prompt tokens.
        output_per_million: Price of reply tokens.
    """

    input_per_million: float
    output_per_million: float


def cost(price: Price, usage: provider.Usage) -> float:
    """Returns what a request cost.

    Args:
        price: The model's price.
        usage: The tokens the request was billed for.

    Returns:
        The cost in USD.
    """
    return (
        usage.input_tokens * price.input_per_million
        + usage.output_tokens * price.output_per_million
    ) / 1_000_000
