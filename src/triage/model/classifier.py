"""Tags issues with a model, one request each, within a dollar budget.

Requests run in parallel threads, up to ``workers`` at a time; most of a
run is spent waiting on the API. A reply that can't be used is retried
once, telling the model what was wrong; if the retry fails too, the issue
is skipped and stays untagged.

Spend is added up from the tokens each reply reports. The first request
goes out alone, to learn what a request costs. After that, a request is
sent only if the spend so far, plus every request in flight and this one
each counted as costing as much as the priciest so far, stays within the
budget. So a run can end over budget by at most about one request.

Workers only talk to the model. Tags are saved on the calling thread, as
replies arrive, because a SQLite connection can't be shared across threads.
"""

import collections
import concurrent.futures
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
DEFAULT_WORKERS = 4
MAX_WORKERS = 64

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


@dataclasses.dataclass(frozen=True)
class _Job:
    """One request to send: an issue, which attempt, and why it's retried."""

    issue: transform.Issue
    attempt: int
    problem: str = ""


def classify(
    issues: Sequence[transform.Issue],
    model: provider.Provider,
    budget_usd: float,
    save: SaveTag,
    workers: int = DEFAULT_WORKERS,
) -> Outcome:
    """Tags the issues, several at a time, until done or out of budget.

    Args:
        issues: The issues to tag, most important first: whatever the
            budget leaves out comes from the end.
        model: The provider to send requests to. It must be safe to call
            from several threads; the SDK clients are.
        budget_usd: Most this pass may spend.
        save: Called with each issue, its tag and the rationale as soon as
            the tag is known, always on the calling thread, so progress
            survives a later failure.
        workers: Most requests in flight at once, 1 to ``MAX_WORKERS``.

    Returns:
        What the pass did.

    Raises:
        ValueError: ``workers`` is out of range.
        ProviderError: The model has no known price, or a request failed.
            Requests not yet sent are cancelled.
    """
    if not 1 <= workers <= MAX_WORKERS:
        raise ValueError(f"workers must be 1 to {MAX_WORKERS}, got {workers}")
    price = model.price
    if price is None:
        raise provider.ProviderError(
            f"triage has no price for {model.model} on {model.name}, so it "
            "can't keep within the budget."
        )
    queue = collections.deque(_Job(issue, 1) for issue in issues)
    in_flight: dict[concurrent.futures.Future[provider.Reply], _Job] = {}
    spent = 0.0
    priciest = 0.0
    tagged = 0
    skipped: list[int] = []

    def affordable() -> bool:
        if spent >= budget_usd:
            return False
        if priciest == 0.0:
            # Nothing is known about cost until the first reply.
            return not in_flight
        return spent + (len(in_flight) + 1) * priciest <= budget_usd

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
    try:
        while queue or in_flight:
            while queue and len(in_flight) < workers and affordable():
                job = queue.popleft()
                request = prompt.request(job.issue, job.problem)
                in_flight[executor.submit(model.classify, request)] = job
            if not in_flight:
                break
            done, _ = concurrent.futures.wait(
                in_flight, return_when=concurrent.futures.FIRST_COMPLETED
            )
            for future in done:
                job = in_flight.pop(future)
                reply = future.result()
                request_cost = pricing.cost(price, reply.usage)
                spent += request_cost
                priciest = max(priciest, request_cost)
                try:
                    tag, rationale = validate(reply)
                except InvalidReplyError as error:
                    logger.warning(
                        "#%d: unusable reply (attempt %d of %d): %s",
                        job.issue.number,
                        job.attempt,
                        ATTEMPTS,
                        error,
                    )
                    if job.attempt < ATTEMPTS:
                        # First in line, so a retry isn't starved.
                        queue.appendleft(
                            _Job(job.issue, job.attempt + 1, str(error))
                        )
                    else:
                        skipped.append(job.issue.number)
                    continue
                save(job.issue, tag, rationale)
                tagged += 1
    finally:
        # On a failure, drop requests that haven't started.
        executor.shutdown(wait=True, cancel_futures=True)

    left = len({job.issue.id for job in queue})
    if left:
        logger.warning(
            "Budget reached ($%.4f of $%.2f); %d %s left untagged. Run again "
            "or raise --budget-usd.",
            spent,
            budget_usd,
            left,
            "issue" if left == 1 else "issues",
        )
    return Outcome(tagged, sorted(skipped), left, spent)


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
