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
import datetime
import enum
import logging
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any
from typing import TypeVar

from triage.model import pricing
from triage.model import prompt
from triage.model import provider
from triage.pipeline import transform

logger = logging.getLogger(__name__)

ATTEMPTS = 2
DEFAULT_WORKERS = 4
MAX_WORKERS = provider.MAX_PARALLEL_REQUESTS

SaveTag = Callable[[transform.Issue, prompt.Tagging], None]
# Called with (issues finished, issues in all).
Progress = Callable[[int, int], None]


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
    progress: Progress | None = None,
    since: datetime.datetime | None = None,
) -> Outcome:
    """Tags the issues, several at a time, until done or out of budget.

    Args:
        issues: The issues to tag, most important first: whatever the
            budget leaves out comes from the end.
        model: The provider to send requests to. It must be safe to call
            from several threads; the SDK clients are.
        budget_usd: Most this pass may spend.
        save: Called with each issue and its tagging as soon as it is
            known, always on the calling thread, so progress
            survives a later failure.
        workers: Most requests in flight at once, 1 to ``MAX_WORKERS``.
        progress: Called on the calling thread each time an issue is
            tagged or given up on, with how many are finished and the
            total.
        since: When the last run started, so the prompt can mark what
            changed; None on a first run.

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

    def report() -> None:
        if progress is not None:
            progress(tagged + len(skipped), len(issues))

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
                request = prompt.request(job.issue, job.problem, since)
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
                    tagging = validate(reply)
                except InvalidReplyError as error:
                    logger.debug(
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
                        report()
                    continue
                save(job.issue, tagging)
                tagged += 1
                report()
    finally:
        # On a failure, drop requests that haven't started.
        executor.shutdown(wait=True, cancel_futures=True)

    left = len({job.issue.id for job in queue})
    if left:
        # The summary's Run line says how many are left; no need to say it
        # twice.
        logger.debug(
            "Budget reached ($%.4f of $%.2f); %d %s left untagged",
            spent,
            budget_usd,
            left,
            "issue" if left == 1 else "issues",
        )
    return Outcome(tagged, sorted(skipped), left, spent)


def validate(reply: provider.Reply) -> prompt.Tagging:
    """Checks a reply and returns what the model decided.

    A bug must come with a severity and a priority, and a feature with a
    MoSCoW priority; those fields, and a bug's reasons for its severity
    and priority, are dropped for other tags. A subtag is optional for any
    tag, and so is a change note. A summary, brief and rationale are
    required. Text longer than allowed is cut rather than retried: the
    tags are what matter, and a retry costs a request.

    Args:
        reply: The provider's reply.

    Returns:
        The tagging.

    Raises:
        InvalidReplyError: The reply is missing, has an unknown value, lacks
            a field its tag needs, or has no summary, brief or rationale.
    """
    if reply.data is None:
        raise InvalidReplyError(reply.problem or "no reply")
    data: Mapping[str, Any] = reply.data
    tag = _enum(prompt.Tag, data, "tag")
    rationale = data.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise InvalidReplyError("the rationale is missing")
    rationale = rationale.strip()[: prompt.RATIONALE_MAX_CHARS]
    summary = data.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise InvalidReplyError("the summary is missing")
    summary = _cut_at_word(summary.strip(), prompt.SUMMARY_HARD_MAX_CHARS)
    brief = data.get("brief")
    if not isinstance(brief, str) or not brief.strip():
        raise InvalidReplyError("the brief is missing")
    brief_text = _cut_at_word(brief.strip(), prompt.BRIEF_HARD_MAX_CHARS)
    change_note = _optional_text(data, "change", prompt.CHANGE_HARD_MAX_CHARS)
    subtag = (
        _enum(prompt.Subtag, data, "subtag")
        if data.get("subtag") not in (None, "", "none")
        else None
    )
    tagging = prompt.Tagging(
        tag,
        rationale,
        summary,
        subtag=subtag,
        change_note=change_note,
        brief=brief_text,
    )
    if tag is prompt.Tag.BUG:
        return dataclasses.replace(
            tagging,
            severity=_enum(prompt.Level, data, "severity"),
            priority=_enum(prompt.Level, data, "priority"),
            severity_reason=_optional_text(
                data, "severity_reason", prompt.REASON_HARD_MAX_CHARS
            ),
            priority_reason=_optional_text(
                data, "priority_reason", prompt.REASON_HARD_MAX_CHARS
            ),
        )
    if tag is prompt.Tag.FEATURE:
        return dataclasses.replace(
            tagging, moscow=_enum(prompt.Moscow, data, "moscow")
        )
    return tagging


def _optional_text(
    data: Mapping[str, Any], field: str, limit: int
) -> str | None:
    """Reads an optional text field, cut at a word; None when absent."""
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        return None
    return _cut_at_word(value.strip(), limit)


def _cut_at_word(text: str, limit: int) -> str:
    """Cuts text to at most ``limit`` characters, at a word, marking the cut."""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


_EnumT = TypeVar("_EnumT", bound=enum.Enum)


def _enum(kind: type[_EnumT], data: Mapping[str, Any], field: str) -> _EnumT:
    """Reads one field that must hold one of an enum's values."""
    allowed = ", ".join(str(member.value) for member in kind)
    if field not in data or data[field] is None:
        raise InvalidReplyError(f"{field} is missing; give one of {allowed}")
    try:
        return kind(data[field])
    except ValueError:
        raise InvalidReplyError(
            f"{field} {data[field]!r} isn't one of {allowed}"
        ) from None
