"""Load: stores issues and run history in one SQLite file per user.

The file is a cache of GitHub plus the tool's own history. There are no
migrations: when the schema changes, the tool asks you to delete the file,
and the next run fetches everything again.
"""

import dataclasses
import datetime
import enum
import json
import logging
import pathlib
import sqlite3
from collections.abc import Sequence
from typing import Any

from triage.model import prompt
from triage.pipeline import transform

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 3

_SCHEMA = """
CREATE TABLE repos (
    id               TEXT PRIMARY KEY,
    name_with_owner  TEXT NOT NULL UNIQUE COLLATE NOCASE,
    url              TEXT NOT NULL
);

CREATE TABLE issues (
    id                  TEXT PRIMARY KEY,
    repo_id             TEXT NOT NULL REFERENCES repos(id),
    number              INTEGER NOT NULL,
    title               TEXT NOT NULL,
    body                TEXT NOT NULL,
    url                 TEXT NOT NULL,
    state               TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    closed_at           TEXT,
    author              TEXT,
    author_type         TEXT,
    author_association  TEXT NOT NULL,
    labels_json         TEXT NOT NULL,
    comment_count       INTEGER NOT NULL,
    reaction_count      INTEGER NOT NULL,
    UNIQUE (repo_id, number)
);

CREATE TABLE comments (
    id                  TEXT PRIMARY KEY,
    issue_id            TEXT NOT NULL
                        REFERENCES issues(id) ON DELETE CASCADE,
    author              TEXT,
    author_type         TEXT,
    author_association  TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    body                TEXT NOT NULL
);

CREATE INDEX comments_by_issue ON comments(issue_id);

CREATE TABLE runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id         TEXT NOT NULL REFERENCES repos(id),
    -- Before the fetch began: the next run asks GitHub for changes since
    -- this time, so edits made during a fetch aren't missed.
    started_at      TEXT NOT NULL,
    finished_at     TEXT NOT NULL,
    issues_fetched  INTEGER NOT NULL,
    open_count      INTEGER NOT NULL,
    model           TEXT,
    issues_tagged   INTEGER NOT NULL DEFAULT 0,
    spent_usd       REAL NOT NULL DEFAULT 0
);

CREATE TABLE tags (
    -- One row per model answer, so earlier tags stay for comparison.
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id          TEXT NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    -- The issue's updated_at when it was tagged; a newer one means the
    -- issue changed and needs tagging again.
    issue_updated_at  TEXT NOT NULL,
    model             TEXT NOT NULL,
    prompt_version    TEXT NOT NULL,
    tagged_at         TEXT NOT NULL,
    tag               TEXT NOT NULL,
    rationale         TEXT NOT NULL,
    -- high or low; bugs only.
    severity          TEXT,
    priority          TEXT,
    -- must-have, should-have or could-have, as the model chose it (before
    -- the demand floor); features only.
    moscow            TEXT
);

CREATE INDEX tags_by_issue ON tags(issue_id);
"""


class LoadError(RuntimeError):
    """Raised when the database file can't be used."""


@dataclasses.dataclass(frozen=True)
class RepoHistory:
    """A repository the tool has run on, for ``triage list``.

    Attributes:
        name_with_owner: ``owner/name`` with GitHub's casing.
        url: Link to the repository.
        runs: How many runs are stored.
        last_run_at: When the latest run started.
        open_count: Open issues on GitHub at the latest run.
    """

    name_with_owner: str
    url: str
    runs: int
    last_run_at: datetime.datetime
    open_count: int


def connect(path: pathlib.Path) -> sqlite3.Connection:
    """Opens the database, creating it on first use.

    Args:
        path: The SQLite file.

    Returns:
        An open connection with foreign keys on.

    Raises:
        LoadError: The file was written by a different schema version.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version == 0:
        with connection:
            connection.executescript(_SCHEMA)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    elif version != SCHEMA_VERSION:
        connection.close()
        raise LoadError(
            f"{path} was written by a different version of triage. Delete "
            "it and run again; triage will fetch the issues again."
        )
    return connection


def last_run_started(
    connection: sqlite3.Connection, name_with_owner: str
) -> str | None:
    """Returns when the latest run on a repository started.

    Args:
        connection: An open database.
        name_with_owner: ``owner/name``, in any casing.

    Returns:
        The ISO 8601 time, or None if the tool hasn't run on it.
    """
    row = connection.execute(
        """
        SELECT MAX(runs.started_at) FROM runs
        JOIN repos ON repos.id = runs.repo_id
        WHERE repos.name_with_owner = ?
        """,
        (name_with_owner,),
    ).fetchone()
    started: str | None = row[0]
    return started


def is_complete(connection: sqlite3.Connection, name_with_owner: str) -> bool:
    """Says whether every open issue of a repository is stored.

    An earlier run with ``--limit`` may have fetched only some. The stored
    set counts as complete when it holds at least as many open issues as
    GitHub reported at the latest run; later runs then only need changes.

    Args:
        connection: An open database.
        name_with_owner: ``owner/name``, in any casing.

    Returns:
        True when nothing is missing; False after a limited run, or when
        the tool hasn't run on the repository.
    """
    row = connection.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM issues
             WHERE issues.repo_id = repos.id AND issues.state = 'OPEN'),
            (SELECT runs.open_count FROM runs
             WHERE runs.repo_id = repos.id ORDER BY runs.id DESC LIMIT 1)
        FROM repos WHERE repos.name_with_owner = ?
        """,
        (name_with_owner,),
    ).fetchone()
    if row is None or row[1] is None:
        return False
    stored, on_github = row
    return bool(stored >= on_github)


def close_missing(
    connection: sqlite3.Connection,
    repo_id: str,
    open_ids: set[str],
    now: datetime.datetime,
) -> int:
    """Marks stored open issues closed when GitHub no longer lists them.

    Call it only after fetching every open issue: then an issue missing
    from the fetch was closed, deleted or transferred.

    Args:
        connection: An open database.
        repo_id: GitHub's ID for the repository.
        open_ids: IDs of every issue GitHub lists as open.
        now: When the fetch ran; stored as the closing time.

    Returns:
        How many issues were marked closed.
    """
    stored = [
        issue_id
        for (issue_id,) in connection.execute(
            "SELECT id FROM issues WHERE repo_id = ? AND state = 'OPEN'",
            (repo_id,),
        )
    ]
    missing = [issue_id for issue_id in stored if issue_id not in open_ids]
    with connection:
        connection.executemany(
            "UPDATE issues SET state = 'CLOSED', closed_at = ? WHERE id = ?",
            [(_iso(now), issue_id) for issue_id in missing],
        )
    return len(missing)


def save_run(
    connection: sqlite3.Connection,
    repository: dict[str, Any],
    issues: Sequence[transform.Issue],
    started_at: datetime.datetime,
    open_count: int,
) -> int:
    """Stores fetched issues and records the run, all or nothing.

    An issue's stored comments are replaced by the fetched ones, since
    GitHub returns only the latest few.

    Args:
        connection: An open database.
        repository: GitHub's ``id``, ``nameWithOwner`` and ``url``.
        issues: The fetched issues, open or closed.
        started_at: When the fetch began.
        open_count: Open issues on GitHub.

    Returns:
        The run's ID, for :func:`finish_run`.
    """
    with connection:
        connection.execute(
            """
            INSERT INTO repos (id, name_with_owner, url) VALUES (?, ?, ?)
            ON CONFLICT (id) DO UPDATE SET
                name_with_owner = excluded.name_with_owner,
                url = excluded.url
            """,
            (repository["id"], repository["nameWithOwner"], repository["url"]),
        )
        for issue in issues:
            _upsert_issue(connection, repository["id"], issue)
        cursor = connection.execute(
            """
            INSERT INTO runs (repo_id, started_at, finished_at,
                              issues_fetched, open_count)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                repository["id"],
                _iso(started_at),
                _iso(datetime.datetime.now(datetime.timezone.utc)),
                len(issues),
                open_count,
            ),
        )
    logger.debug(
        "Stored %d %s", len(issues), "issue" if len(issues) == 1 else "issues"
    )
    if cursor.lastrowid is None:
        raise LoadError("SQLite didn't return the new run's ID.")
    return cursor.lastrowid


def finish_run(
    connection: sqlite3.Connection,
    run_id: int,
    model: str,
    issues_tagged: int,
    spent_usd: float,
) -> None:
    """Records what the model step of a run did.

    Args:
        connection: An open database.
        run_id: The ID :func:`save_run` returned.
        model: The model ID the run used.
        issues_tagged: Issues the model tagged.
        spent_usd: What the requests cost.
    """
    with connection:
        connection.execute(
            """
            UPDATE runs SET finished_at = ?, model = ?, issues_tagged = ?,
                            spent_usd = ?
            WHERE id = ?
            """,
            (
                _iso(datetime.datetime.now(datetime.timezone.utc)),
                model,
                issues_tagged,
                spent_usd,
                run_id,
            ),
        )


def save_tag(
    connection: sqlite3.Connection,
    issue: transform.Issue,
    tagging: prompt.Tagging,
    model: str,
    prompt_version: str,
) -> None:
    """Stores one model answer right away, so progress survives a crash.

    Args:
        connection: An open database.
        issue: The issue that was tagged.
        tagging: What the model decided.
        model: The model ID.
        prompt_version: The prompt's version.
    """
    with connection:
        connection.execute(
            """
            INSERT INTO tags (issue_id, issue_updated_at, model,
                              prompt_version, tagged_at, tag, rationale,
                              severity, priority, moscow)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                issue.id,
                _iso(issue.updated_at),
                model,
                prompt_version,
                _iso(datetime.datetime.now(datetime.timezone.utc)),
                tagging.tag.value,
                tagging.rationale,
                _value(tagging.severity),
                _value(tagging.priority),
                _value(tagging.moscow),
            ),
        )


def current_tags(
    connection: sqlite3.Connection,
    name_with_owner: str,
    model: str,
    prompt_version: str,
) -> dict[str, prompt.Tagging]:
    """Returns the tags that are up to date for a repository's issues.

    A tag is up to date when the issue hasn't changed since, and the same
    model and prompt version produced it.

    Args:
        connection: An open database.
        name_with_owner: ``owner/name``, in any casing.
        model: The model ID in use.
        prompt_version: The prompt version in use.

    Returns:
        ``{issue ID: tagging}``, the newest answer per issue.
    """
    rows = connection.execute(
        """
        SELECT tags.issue_id, tags.tag, tags.rationale, tags.severity,
               tags.priority, tags.moscow
        FROM tags
        JOIN issues ON issues.id = tags.issue_id
        JOIN repos ON repos.id = issues.repo_id
        WHERE repos.name_with_owner = ?
          AND tags.issue_updated_at = issues.updated_at
          AND tags.model = ? AND tags.prompt_version = ?
        ORDER BY tags.id
        """,
        (name_with_owner, model, prompt_version),
    ).fetchall()
    return {
        issue_id: prompt.Tagging(
            tag=prompt.Tag(tag),
            rationale=rationale,
            severity=prompt.Level(severity) if severity else None,
            priority=prompt.Level(priority) if priority else None,
            moscow=prompt.Moscow(moscow) if moscow else None,
        )
        for issue_id, tag, rationale, severity, priority, moscow in rows
    }


def open_issues(
    connection: sqlite3.Connection, name_with_owner: str
) -> list[transform.Issue]:
    """Returns a repository's stored open issues, newest first.

    Args:
        connection: An open database.
        name_with_owner: ``owner/name``, in any casing.

    Returns:
        The open issues with their stored comments, oldest comment first.
    """
    connection.row_factory = sqlite3.Row
    try:
        issue_rows = connection.execute(
            """
            SELECT issues.* FROM issues
            JOIN repos ON repos.id = issues.repo_id
            WHERE repos.name_with_owner = ? AND issues.state = 'OPEN'
            ORDER BY issues.number DESC
            """,
            (name_with_owner,),
        ).fetchall()
        comments: dict[str, list[transform.Comment]] = {}
        for row in connection.execute(
            """
            SELECT comments.* FROM comments
            JOIN issues ON issues.id = comments.issue_id
            JOIN repos ON repos.id = issues.repo_id
            WHERE repos.name_with_owner = ? AND issues.state = 'OPEN'
            ORDER BY comments.created_at
            """,
            (name_with_owner,),
        ):
            comments.setdefault(row["issue_id"], []).append(
                transform.Comment(
                    id=row["id"],
                    author=row["author"],
                    author_type=row["author_type"],
                    author_association=row["author_association"],
                    created_at=transform.parse_time(row["created_at"]),
                    body=row["body"],
                )
            )
    finally:
        connection.row_factory = None
    return [_issue(row, comments.get(row["id"], [])) for row in issue_rows]


def history(connection: sqlite3.Connection) -> list[RepoHistory]:
    """Returns every repository the tool has run on, latest run first.

    Args:
        connection: An open database.

    Returns:
        One entry per repository.
    """
    rows = connection.execute(
        """
        SELECT repos.name_with_owner, repos.url, COUNT(*),
               MAX(runs.started_at),
               (SELECT latest.open_count FROM runs AS latest
                WHERE latest.repo_id = repos.id
                ORDER BY latest.id DESC LIMIT 1)
        FROM repos JOIN runs ON runs.repo_id = repos.id
        GROUP BY repos.id
        ORDER BY MAX(runs.started_at) DESC
        """
    ).fetchall()
    return [
        RepoHistory(
            name_with_owner=name,
            url=url,
            runs=runs,
            last_run_at=transform.parse_time(last_run),
            open_count=open_count,
        )
        for name, url, runs, last_run, open_count in rows
    ]


def _upsert_issue(
    connection: sqlite3.Connection, repo_id: str, issue: transform.Issue
) -> None:
    """Inserts or updates one issue and replaces its comments."""
    connection.execute(
        """
        INSERT INTO issues (id, repo_id, number, title, body, url, state,
                            created_at, updated_at, closed_at, author,
                            author_type, author_association, labels_json,
                            comment_count, reaction_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET
            title = excluded.title,
            body = excluded.body,
            state = excluded.state,
            updated_at = excluded.updated_at,
            closed_at = excluded.closed_at,
            labels_json = excluded.labels_json,
            comment_count = excluded.comment_count,
            reaction_count = excluded.reaction_count
        """,
        (
            issue.id,
            repo_id,
            issue.number,
            issue.title,
            issue.body,
            issue.url,
            issue.state,
            _iso(issue.created_at),
            _iso(issue.updated_at),
            _iso(issue.closed_at) if issue.closed_at else None,
            issue.author,
            issue.author_type,
            issue.author_association,
            json.dumps(issue.labels),
            issue.comment_count,
            issue.reaction_count,
        ),
    )
    connection.execute("DELETE FROM comments WHERE issue_id = ?", (issue.id,))
    connection.executemany(
        """
        INSERT INTO comments (id, issue_id, author, author_type,
                              author_association, created_at, body)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                comment.id,
                issue.id,
                comment.author,
                comment.author_type,
                comment.author_association,
                _iso(comment.created_at),
                comment.body,
            )
            for comment in issue.comments
        ],
    )


def _issue(
    row: sqlite3.Row, comments: list[transform.Comment]
) -> transform.Issue:
    """Builds an issue record from a stored row."""
    return transform.Issue(
        id=row["id"],
        number=row["number"],
        title=row["title"],
        body=row["body"],
        url=row["url"],
        state=row["state"],
        created_at=transform.parse_time(row["created_at"]),
        updated_at=transform.parse_time(row["updated_at"]),
        closed_at=(
            transform.parse_time(row["closed_at"]) if row["closed_at"] else None
        ),
        author=row["author"],
        author_type=row["author_type"],
        author_association=row["author_association"],
        labels=json.loads(row["labels_json"]),
        comment_count=row["comment_count"],
        reaction_count=row["reaction_count"],
        comments=comments,
    )


def _value(member: enum.Enum | None) -> str | None:
    """Returns an enum member's stored value, or None."""
    return None if member is None else str(member.value)


def _iso(moment: datetime.datetime) -> str:
    """Formats a moment as ISO 8601 UTC, the way GitHub does."""
    return (
        moment.astimezone(datetime.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
