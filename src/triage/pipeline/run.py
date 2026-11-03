"""Runs the pipeline for one repository.

Extract, transform and load fetch and store what changed; then the model
tags every open issue without an up-to-date tag, within the budget, and
sums up what changed since the previous run.
"""

import dataclasses
import datetime
import logging
import sqlite3

from triage import progress
from triage import repo_ref
from triage.model import classifier
from triage.model import demand
from triage.model import digest
from triage.model import prompt
from triage.model import provider
from triage.pipeline import extract
from triage.pipeline import load
from triage.pipeline import transform

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class SinceLastRun:
    """What changed since the previous run.

    Attributes:
        days: Whole days since the previous run started.
        new: Open issues opened since then.
        changed: Open issues opened before then and updated since.
        closed: Issues closed since then.
        digest: What matters most among the changes, in one or two
            sentences, or None when it wasn't written.
        skipped: Why there is no digest; empty when there is one or
            nothing changed.
        counts: The previous run's tag counts, or None if it saved none.
    """

    days: int
    new: int
    changed: int
    closed: int
    digest: str | None
    skipped: str
    counts: dict[str, int] | None


@dataclasses.dataclass(frozen=True)
class RunResult:
    """What one run found.

    Attributes:
        name_with_owner: ``owner/name`` with GitHub's casing.
        url: Link to the repository.
        previous_run_at: When the previous run started, or None on the
            first run.
        fetch: How this run fetched: ``all`` (every open issue),
            ``changes`` (only what changed since the previous run) or
            ``limited`` (the most recently updated open issues, plus what
            changed since the previous run).
        limit: The ``--limit`` this run used; 0 for none.
        fetched: Issues fetched this run, open or closed.
        open_on_github: Open issues on GitHub right now.
        flagged: The stored open issues with their computed flags.
        tags: ``{issue ID: tagging}`` for issues with an up-to-date tag. A
            feature's MoSCoW priority already includes the demand floor.
        raised: IDs of features whose priority the demand floor raised
            above what the model chose.
        model: The model ID that tagged them.
        outcome: What the tagging step did this run.
        budget_usd: The run's budget.
        spent_usd: What the run's requests cost, the digest included.
        counts: This run's tag counts; see :func:`counts`.
        since_last_run: What changed since the previous run; None on the
            first run.
    """

    name_with_owner: str
    url: str
    previous_run_at: datetime.datetime | None
    fetch: str
    limit: int
    fetched: int
    open_on_github: int
    flagged: list[transform.FlaggedIssue]
    tags: dict[str, prompt.Tagging]
    raised: set[str]
    model: str
    outcome: classifier.Outcome
    budget_usd: float
    spent_usd: float
    counts: dict[str, int]
    since_last_run: SinceLastRun | None


def run(
    repo: repo_ref.RepoRef,
    limit: int,
    token: str,
    connection: sqlite3.Connection,
    model: provider.Provider,
    budget_usd: float,
    now: datetime.datetime,
    workers: int = classifier.DEFAULT_WORKERS,
    status: progress.Status | None = None,
) -> RunResult:
    """Fetches what's new for a repository, stores it and tags its issues.

    Without a limit, a run fetches every open issue the first time, and
    again whenever an earlier limited run left some out; otherwise it
    fetches only what changed since the previous run, closures included.
    With a limit, it fetches the ``limit`` most recently updated open
    issues, plus everything that changed since the previous run, so
    nothing stored goes stale. After a fetch that saw every open issue,
    stored issues GitHub no longer lists as open are marked closed.

    The model then tags open issues that are new, changed or tagged under
    another model or prompt version: hot issues first, busiest first, then
    the rest, most recently updated first. With a
    limit, it looks only at the issues this run fetched, so a small limit
    means a small run; older untagged issues wait for a run without one.

    Args:
        repo: The repository.
        limit: Most recently updated open issues to fetch; 0 for all.
        token: A GitHub token that can read the repository.
        connection: An open database.
        model: The provider that tags issues.
        budget_usd: Most the model step may spend this run.
        now: The current time, timezone-aware.
        workers: Most model requests in flight at once.
        status: Where to show fetch and tagging progress; None shows
            nothing.

    Returns:
        The run's result.
    """
    status = status or progress.Status(live=False)

    def fetching(label: str) -> extract.Progress:
        def show(done: int, total: int) -> None:
            if total:
                status.bar(label, done, total)
            else:
                status.show(f"{label} {done}…")

        status.show(f"{label}…")
        return show

    since = load.last_run_started(connection, repo.full_name)
    since_time = transform.parse_time(since) if since else None
    previous_counts = load.last_counts(connection, repo.full_name)
    if limit:
        fetch = "limited"
        extracted = extract.fetch_open_issues(
            repo, limit, token, fetching("Fetching")
        )
        saw_every_open = len(extracted.nodes) >= extracted.total_open
        nodes = {node["id"]: node for node in extracted.nodes}
        if since is not None:
            changes = extract.fetch_changed_issues(
                repo, since, token, fetching("Fetching changes")
            )
            # The newest copy of each issue wins.
            for node in changes.nodes:
                kept = nodes.get(node["id"])
                if kept is None or node["updatedAt"] >= kept["updatedAt"]:
                    nodes[node["id"]] = node
        fetched_nodes = list(nodes.values())
    elif since is not None and load.is_complete(connection, repo.full_name):
        fetch = "changes"
        extracted = extract.fetch_changed_issues(
            repo, since, token, fetching("Fetching changes")
        )
        saw_every_open = False
        fetched_nodes = extracted.nodes
    else:
        fetch = "all"
        extracted = extract.fetch_open_issues(
            repo, 0, token, fetching("Fetching")
        )
        saw_every_open = True
        fetched_nodes = extracted.nodes
    open_ids = {node["id"] for node in extracted.nodes}
    issues = [transform.to_issue(node) for node in fetched_nodes]
    run_id = load.save_run(
        connection, extracted.repository, issues, now, extracted.total_open
    )
    if saw_every_open:
        closed = load.close_missing(
            connection, extracted.repository["id"], open_ids, now
        )
        if closed:
            logger.debug(
                "Marked %d %s closed that GitHub no longer lists as open",
                closed,
                "issue" if closed == 1 else "issues",
            )
    name = extracted.repository["nameWithOwner"]
    stored = load.open_issues(connection, name)

    current = load.current_tags(connection, name, model.model, prompt.VERSION)
    # Read before tagging, to see afterwards what changed.
    previous_tags = load.latest_tags(connection, name)
    fetched_ids = {node["id"] for node in fetched_nodes}
    flagged = transform.flag_issues(stored, now)
    pending = [
        item.issue
        for item in sorted(flagged, key=_tagging_order)
        if item.issue.id not in current
        and (not limit or item.issue.id in fetched_ids)
    ]

    def tagging(done: int, total: int) -> None:
        status.bar("Tagging", done, total)

    if pending:
        tagging(0, len(pending))

    def save(issue: transform.Issue, tagging: prompt.Tagging) -> None:
        load.save_tag(connection, issue, tagging, model.model, prompt.VERSION)

    outcome = classifier.classify(
        pending, model, budget_usd, save, workers, tagging, since_time
    )
    tags, raised = _with_demand_floor(
        load.current_tags(connection, name, model.model, prompt.VERSION),
        stored,
    )
    spent = outcome.spent_usd
    since_last_run = None
    if since is not None and since_time is not None:
        status.show("Summing up what changed…")
        since_last_run, digest_cost = _since_last_run(
            connection,
            name,
            since,
            since_time,
            now,
            flagged,
            tags,
            previous_tags,
            model,
            budget_usd - spent,
            previous_counts,
        )
        spent += digest_cost
    status.clear()
    run_counts = counts(flagged, tags, extracted.total_open)
    load.finish_run(
        connection,
        run_id,
        model.model,
        outcome.tagged,
        spent,
        run_counts,
        since_last_run.digest if since_last_run else None,
    )
    return RunResult(
        name_with_owner=name,
        url=extracted.repository["url"],
        previous_run_at=transform.parse_time(since) if since else None,
        fetch=fetch,
        limit=limit,
        fetched=len(issues),
        open_on_github=extracted.total_open,
        flagged=flagged,
        tags=tags,
        raised=raised,
        model=model.model,
        outcome=outcome,
        budget_usd=budget_usd,
        spent_usd=spent,
        counts=run_counts,
        since_last_run=since_last_run,
    )


def counts(
    flagged: list[transform.FlaggedIssue],
    tags: dict[str, prompt.Tagging],
    open_on_github: int,
) -> dict[str, int]:
    """Counts what the summary's tag table shows, to compare across runs.

    Args:
        flagged: The stored open issues.
        tags: Their up-to-date taggings.
        open_on_github: Open issues on GitHub.

    Returns:
        ``open`` (on GitHub), ``untagged``, and one count per main tag.
    """
    result = {"open": open_on_github, "untagged": 0}
    result |= {tag.value: 0 for tag in prompt.Tag}
    for item in flagged:
        found = tags.get(item.issue.id)
        result[found.tag.value if found else "untagged"] += 1
    return result


def _since_last_run(
    connection: sqlite3.Connection,
    name: str,
    since: str,
    since_time: datetime.datetime,
    now: datetime.datetime,
    flagged: list[transform.FlaggedIssue],
    tags: dict[str, prompt.Tagging],
    before: dict[str, prompt.Tagging],
    model: provider.Provider,
    budget_left_usd: float,
    previous_counts: dict[str, int] | None,
) -> tuple[SinceLastRun, float]:
    """Finds what changed since the previous run and writes the digest.

    Returns:
        What changed, and what writing the digest cost.
    """
    new, changed = [], []
    for item in flagged:
        issue = item.issue
        tagging = tags.get(issue.id)
        if tagging is not None and tagging.tag is prompt.Tag.SPAM:
            continue
        entry = digest.Item(
            number=issue.number,
            label=prompt.describe(tagging) if tagging else "untagged",
            summary=tagging.summary if tagging else issue.title,
            activity=item.engagement,
            note=(tagging.change_note or "") if tagging else "",
            transition=_transition(before.get(issue.id), tagging),
        )
        if issue.created_at >= since_time:
            new.append((tagging, entry))
        elif issue.updated_at >= since_time:
            changed.append((tagging, entry))
    closed = [
        digest.Item(number=number, label="", summary=title)
        for number, title in load.closed_since(connection, name, since)
    ]
    changed.sort(key=lambda pair: (not pair[1].transition, -pair[1].activity))
    new.sort(key=lambda pair: (not _pressing(pair[0]), -pair[1].activity))
    days = (now - since_time).days
    if new or changed or closed:
        written = digest.write(
            model,
            digest.request(
                days,
                [entry for _, entry in changed],
                [entry for _, entry in new],
                closed,
            ),
            budget_left_usd,
        )
    else:
        written = digest.Result(None)
    result = SinceLastRun(
        days=days,
        new=len(new),
        changed=len(changed),
        closed=len(closed),
        digest=written.text,
        skipped=written.skipped,
        counts=previous_counts,
    )
    return result, written.cost_usd


def _transition(
    before: prompt.Tagging | None, after: prompt.Tagging | None
) -> str:
    """Says what changed in an issue's tags, e.g. ``fix-released → fix-failed``.

    Returns:
        The changes, comma-separated; empty when nothing changed.
    """
    if before is None or after is None:
        return ""
    parts = []
    if before.tag is not after.tag:
        parts.append(f"{before.tag.value} → {after.tag.value}")
    if before.subtag is not after.subtag:
        old = before.subtag.value if before.subtag else "no subtag"
        new = after.subtag.value if after.subtag else "no subtag"
        parts.append(f"{old} → {new}")
    if (
        after.priority is prompt.Level.HIGH
        and before.priority is not prompt.Level.HIGH
    ):
        parts.append("now urgent")
    return ", ".join(parts)


def _pressing(tagging: prompt.Tagging | None) -> bool:
    """Says whether a new issue is an urgent or severe bug or a must-have."""
    if tagging is None:
        return False
    return (
        tagging.priority is prompt.Level.HIGH
        or tagging.severity is prompt.Level.HIGH
        or tagging.moscow is prompt.Moscow.MUST
    )


def _tagging_order(item: transform.FlaggedIssue) -> tuple[bool, float, float]:
    """Sorts hot issues first, busiest first, then the rest, newest first.

    The budget may run out before every issue is tagged, so the issues a
    maintainer most needs to see go to the model first.
    """
    hot = transform.Flag.HOT in item.flags
    return (
        not hot,
        -item.engagement if hot else 0,
        -item.issue.updated_at.timestamp(),
    )


def _with_demand_floor(
    tags: dict[str, prompt.Tagging], issues: list[transform.Issue]
) -> tuple[dict[str, prompt.Tagging], set[str]]:
    """Raises each feature's priority to the floor its demand sets.

    Args:
        tags: The stored taggings, as the model gave them.
        issues: The repository's open issues.

    Returns:
        The taggings with floors applied, and the IDs of features the
        floor raised.
    """
    features = [
        issue
        for issue in issues
        if issue.id in tags and tags[issue.id].tag is prompt.Tag.FEATURE
    ]
    final = dict(tags)
    raised = set()
    for issue_id, floor in demand.floors(features).items():
        tagging = tags[issue_id]
        picked = tagging.moscow or prompt.Moscow.COULD
        lifted = demand.raise_to(picked, floor)
        if lifted is not picked:
            final[issue_id] = dataclasses.replace(tagging, moscow=lifted)
            raised.add(issue_id)
    return final, raised
