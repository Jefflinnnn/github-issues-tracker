"""Tags issues with a model, one request each, within a dollar budget.

A reply that can't be used is retried once, telling the model what was
wrong; if the retry fails too, the issue is skipped and stays untagged.
Spend is added up from the tokens each reply reports. Before each request
the classifier stops if the budget is spent, or if a request as expensive
as the priciest one so far would cross it. The first request always goes
out, so a run can end over budget by at most one request.
"""

import dataclasses
import logging
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any

from triage.model import pricing
from triage.model import prompt
from triage.model import provider
from triage.pipeline import transform

logger = logging.getLogger(__name__)

ATTEMPTS = 2

SaveTag = Callable[[transform.Issue, prompt.Tag, str], None]


class InvalidReplyError(ValueError):
    """Raised when a reply doesn't fit the schema or the tags."""


@dataclasses.dataclass(frozen=True)
class Outcome:
    """What one classification pass did.

    Attributes:
        tagged: Issues tagged.
        skipped: Numbers of issues whose replies were unusable twice.
        left_for_budget: Issues not sent because the budget ran out.
        spent_usd: What the requests cost.
    """

    tagged: int
    skipped: list[int]
    left_for_budget: int
    spent_usd: float


def classify(
    issues: Sequence[transform.Issue],
    model: provider.Provider,
    budget_usd: float,
    save: SaveTag,
) -> Outcome:
    """Tags each issue, in order, until done or out of budget.

    Args:
        issues: The issues to tag, most important first: whatever the
            budget leaves out comes from the end.
        model: The provider to send requests to.
        budget_usd: Most this pass may spend.
        save: Called with each issue, its tag and the rationale as soon as
            the tag is known, so progress survives a later failure.

    Returns:
        What the pass did.

    Raises:
        ProviderError: The model has no known price, or a request failed.
    """
    price = model.price
    if price is None:
        raise provider.ProviderError(
            f"triage has no price for {model.model} on {model.name}, so it "
            "can't keep within the budget."
        )
    spent = 0.0
    priciest = 0.0
    tagged = 0
    skipped: list[int] = []
    for position, issue in enumerate(issues):
        problem = ""
        for attempt in range(1, ATTEMPTS + 1):
            if spent >= budget_usd or spent + priciest > budget_usd:
                left = len(issues) - position
                logger.warning(
                    "Budget reached ($%.4f of $%.2f); %d %s left untagged. "
                    "Run again or raise --budget-usd.",
                    spent,
                    budget_usd,
                    left,
                    "issue" if left == 1 else "issues",
                )
                return Outcome(tagged, skipped, left, spent)
            reply = model.classify(prompt.request(issue, problem))
            request_cost = pricing.cost(price, reply.usage)
            spent += request_cost
            priciest = max(priciest, request_cost)
            try:
                tag, rationale = validate(reply)
            except InvalidReplyError as error:
                problem = str(error)
                logger.warning(
                    "#%d: unusable reply (attempt %d of %d): %s",
                    issue.number,
                    attempt,
                    ATTEMPTS,
                    problem,
                )
                continue
            save(issue, tag, rationale)
            tagged += 1
            break
        else:
            skipped.append(issue.number)
    return Outcome(tagged, skipped, 0, spent)


def validate(reply: provider.Reply) -> tuple[prompt.Tag, str]:
    """Checks a reply and returns its tag and rationale.

    A rationale longer than allowed is cut rather than retried: the tag is
    what matters, and a retry costs a request.

    Args:
        reply: The provider's reply.

    Returns:
        The tag and the rationale.

    Raises:
        InvalidReplyError: The reply is missing, has an unknown tag or has
            no rationale.
    """
    if reply.data is None:
        raise InvalidReplyError(reply.problem or "no reply")
    data: Mapping[str, Any] = reply.data
    try:
        tag = prompt.Tag(data.get("tag"))
    except ValueError:
        allowed = ", ".join(tag.value for tag in prompt.Tag)
        raise InvalidReplyError(
            f"tag {data.get('tag')!r} isn't one of {allowed}"
        ) from None
    rationale = data.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise InvalidReplyError("the rationale is missing")
    return tag, rationale.strip()[: prompt.RATIONALE_MAX_CHARS]
