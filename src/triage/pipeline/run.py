"""Runs the pipeline for one repository.

Extract, transform and load fetch and store what changed; then the model
tags every open issue without an up-to-date tag, within the budget.
"""

import dataclasses
import datetime
import logging
import sqlite3

from triage import progress
from triage import repo_ref
from triage.model import classifier
from triage.model import demand
from triage.model import prompt
from triage.model import provider
from triage.pipeline import extract
from triage.pipeline import load
from triage.pipeline import transform

logger = logging.getLogger(__name__)


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
        outcome: What the model step did this run.
        budget_usd: The run's budget.
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
    another model or prompt version, most recently updated first.

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
    pending = sorted(
        (issue for issue in stored if issue.id not in current),
        key=lambda issue: issue.updated_at,
        reverse=True,
    )

    def tagging(done: int, total: int) -> None:
        status.bar("Tagging", done, total)

    if pending:
        tagging(0, len(pending))

    def save(issue: transform.Issue, tagging: prompt.Tagging) -> None:
        load.save_tag(connection, issue, tagging, model.model, prompt.VERSION)

    outcome = classifier.classify(
        pending, model, budget_usd, save, workers, tagging
    )
    status.clear()
    load.finish_run(
        connection, run_id, model.model, outcome.tagged, outcome.spent_usd
    )
    tags, raised = _with_demand_floor(
        load.current_tags(connection, name, model.model, prompt.VERSION),
        stored,
    )
    return RunResult(
        name_with_owner=name,
        url=extracted.repository["url"],
        previous_run_at=transform.parse_time(since) if since else None,
        fetch=fetch,
        limit=limit,
        fetched=len(issues),
        open_on_github=extracted.total_open,
        flagged=transform.flag_issues(stored, now),
        tags=tags,
        raised=raised,
        model=model.model,
        outcome=outcome,
        budget_usd=budget_usd,
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
