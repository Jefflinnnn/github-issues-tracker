"""Runs the pipeline for one repository: extract, transform, load."""

import dataclasses
import datetime
import logging
import sqlite3

from triage import repo_ref
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
        fetched: Issues fetched this run, open or closed.
        open_on_github: Open issues on GitHub right now.
        flagged: The stored open issues with their computed flags.
    """

    name_with_owner: str
    url: str
    previous_run_at: datetime.datetime | None
    fetched: int
    open_on_github: int
    flagged: list[transform.FlaggedIssue]


def run(
    repo: repo_ref.RepoRef,
    limit: int,
    token: str,
    connection: sqlite3.Connection,
    now: datetime.datetime,
) -> RunResult:
    """Fetches what's new for a repository, stores it and flags its issues.

    The first run fetches open issues. Later runs fetch only issues that
    changed since the previous run started, closed ones included.

    Args:
        repo: The repository.
        limit: Most open issues to fetch on the first run; 0 for all.
            Later runs fetch every change and ignore it.
        token: A GitHub token that can read the repository.
        connection: An open database.
        now: The current time, timezone-aware.

    Returns:
        The run's result.
    """
    since = load.last_run_started(connection, repo.full_name)
    if since is None:
        extracted = extract.fetch_open_issues(repo, limit, token)
    else:
        if limit:
            logger.warning(
                "--limit applies only to the first run; fetching every "
                "issue changed since %s",
                since,
            )
        extracted = extract.fetch_changed_issues(repo, since, token)
    issues = [transform.to_issue(node) for node in extracted.nodes]
    load.save_run(
        connection, extracted.repository, issues, now, extracted.total_open
    )
    name = extracted.repository["nameWithOwner"]
    return RunResult(
        name_with_owner=name,
        url=extracted.repository["url"],
        previous_run_at=transform.parse_time(since) if since else None,
        fetched=len(issues),
        open_on_github=extracted.total_open,
        flagged=transform.flag_issues(load.open_issues(connection, name), now),
    )
