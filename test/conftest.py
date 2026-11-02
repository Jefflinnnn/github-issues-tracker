"""Shared helpers: GraphQL-shaped issue nodes and a fake GitHub.

The nodes follow the shape of ``extract``'s queries. Their values are made
up to exercise each flag; no test needs the network or a token.
"""

import dataclasses
import datetime
import itertools
import pathlib
from typing import Any

import pytest

from triage.pipeline import extract

NOW = datetime.datetime(2026, 11, 2, 9, 0, tzinfo=datetime.timezone.utc)
REPOSITORY = {
    "id": "R_1",
    "nameWithOwner": "Owner/Repo",
    "url": "https://github.com/Owner/Repo",
}
# GitHub's node IDs are unique across the whole site.
_ids = itertools.count(1)


def iso(days_ago: float) -> str:
    """Returns GitHub's timestamp format for a time before NOW."""
    moment = NOW - datetime.timedelta(days=days_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def comment(
    author: str | None,
    days_ago: float,
    author_type: str = "User",
    association: str = "NONE",
) -> dict[str, Any]:
    """Returns a comment node."""
    return {
        "id": f"C_{next(_ids)}",
        "author": (
            {"login": author, "__typename": author_type} if author else None
        ),
        "authorAssociation": association,
        "createdAt": iso(days_ago),
        "body": f"comment by {author}",
    }


def issue(
    number: int,
    opened_days_ago: float = 1,
    author: str = "opener",
    comments: list[dict[str, Any]] | None = None,
    total_comments: int | None = None,
    reactions: int = 0,
    state: str = "OPEN",
    updated_days_ago: float | None = None,
) -> dict[str, Any]:
    """Returns an issue node."""
    comments = comments or []
    return {
        "id": f"I_{number}",
        "number": number,
        "title": f"Issue {number}",
        "body": "body",
        "url": f"https://github.com/Owner/Repo/issues/{number}",
        "state": state,
        "createdAt": iso(opened_days_ago),
        "updatedAt": iso(
            opened_days_ago if updated_days_ago is None else updated_days_ago
        ),
        "closedAt": iso(0) if state == "CLOSED" else None,
        "author": {"login": author, "__typename": "User"},
        "authorAssociation": "NONE",
        "labels": {"nodes": [{"name": "bug"}]},
        "reactions": {"totalCount": reactions},
        "comments": {
            "totalCount": (
                len(comments) if total_comments is None else total_comments
            ),
            "nodes": comments,
        },
    }


@dataclasses.dataclass
class FakeGitHub:
    """Answers GraphQL requests from canned pages, and records them.

    Attributes:
        pages: Issue nodes per page, served in order for each query.
        total_open: What GitHub reports as the open-issue count.
        calls: The query name and variables of every request.
    """

    pages: list[list[dict[str, Any]]]
    total_open: int = 0
    calls: list[tuple[str, dict[str, Any]]] = dataclasses.field(
        default_factory=list
    )

    def __call__(
        self, query: str, variables: dict[str, Any], token: str
    ) -> dict[str, Any]:
        name = "changed" if "$since" in query else "open"
        self.calls.append((name, variables))
        index = int(variables["after"] or 0)
        has_next = index + 1 < len(self.pages)
        return {
            "data": {
                "repository": {
                    **REPOSITORY,
                    "openIssues": {"totalCount": self.total_open},
                    "issues": {
                        "pageInfo": {
                            "hasNextPage": has_next,
                            "endCursor": str(index + 1) if has_next else None,
                        },
                        "nodes": self.pages[index],
                    },
                }
            }
        }


@pytest.fixture
def isolated(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> pathlib.Path:
    """Runs in an empty folder with a token set and a private database.

    Returns:
        The database path.
    """
    monkeypatch.chdir(tmp_path)
    database = tmp_path / "triage.db"
    monkeypatch.setenv("TRIAGE_DB", str(database))
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    return database


def serve(monkeypatch: pytest.MonkeyPatch, github: FakeGitHub) -> FakeGitHub:
    """Routes the pipeline's GitHub requests to a fake."""
    monkeypatch.setattr(extract, "post_graphql", github)
    return github
