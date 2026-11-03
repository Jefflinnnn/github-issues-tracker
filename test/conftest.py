"""Shared helpers: GraphQL-shaped issue nodes, a fake GitHub and model.

The nodes follow the shape of ``extract``'s queries. Their values are made
up to exercise each flag; no test needs the network or a token.
"""

import dataclasses
import datetime
import itertools
import pathlib
from typing import Any

import pytest

from triage.model import digest
from triage.model import pricing
from triage.model import provider
from triage.pipeline import extract

NOW = datetime.datetime(2026, 11, 2, 9, 0, tzinfo=datetime.timezone.utc)
MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
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
        changed: Pages for the changed-since query, when it should answer
            differently from the open-issues query.
        calls: The query name and variables of every request.
    """

    pages: list[list[dict[str, Any]]]
    total_open: int = 0
    changed: list[list[dict[str, Any]]] | None = None
    calls: list[tuple[str, dict[str, Any]]] = dataclasses.field(
        default_factory=list
    )

    def __call__(
        self, query: str, variables: dict[str, Any], token: str
    ) -> dict[str, Any]:
        name = "changed" if "$since" in query else "open"
        self.calls.append((name, variables))
        pages = (
            self.changed if name == "changed" and self.changed else self.pages
        )
        index = int(variables["after"] or 0)
        has_next = index + 1 < len(pages)
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
                        "nodes": pages[index],
                    },
                }
            }
        }


@pytest.fixture(autouse=True)
def no_user_settings(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Points the per-user settings file at an empty folder for every test."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))


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
    for key in (
        "TRIAGE_MODEL",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "BEDROCK_ENDPOINT_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    return database


def serve(monkeypatch: pytest.MonkeyPatch, github: FakeGitHub) -> FakeGitHub:
    """Routes the pipeline's GitHub requests to a fake."""
    monkeypatch.setattr(extract, "post_graphql", github)
    return github


@dataclasses.dataclass
class FakeModel:
    """A provider that answers from a list of replies, then a default one.

    Attributes:
        replies: Replies to give first, in order.
        default: The reply after ``replies`` run out.
        requests: Every tagging request received.
        digest: The digest it writes when asked for one; None gives an
            unusable reply.
        digest_requests: Every digest request received.
        name: The provider's name.
        model: The model ID.
        price: $1.10 in, $5.50 out per million tokens; None for unknown.
    """

    replies: list[provider.Reply] = dataclasses.field(default_factory=list)
    default: provider.Reply | None = None
    requests: list[provider.Request] = dataclasses.field(default_factory=list)
    digest: str | None = "Things changed."
    digest_requests: list[provider.Request] = dataclasses.field(
        default_factory=list
    )
    name: str = "bedrock"
    model: str = MODEL_ID
    price: pricing.Price | None = pricing.Price(1.10, 5.50)

    def classify(self, request: provider.Request) -> provider.Reply:
        if request.tool_name == digest.TOOL_NAME:
            self.digest_requests.append(request)
            usage = provider.Usage(1_000, 50)
            if self.digest is None:
                return provider.Reply(None, usage, "no tool call")
            return provider.Reply({"digest": self.digest}, usage)
        self.requests.append(request)
        if self.replies:
            return self.replies.pop(0)
        return self.default or reply("bug")


def reply(
    tag: str | None,
    input_tokens: int = 1_000,
    output_tokens: int = 50,
    **fields: str,
) -> provider.Reply:
    """Returns a reply with a tag, or an unusable one when tag is None.

    Bugs and features get the fields their tag needs, low and could-have
    unless ``fields`` says otherwise.
    """
    usage = provider.Usage(input_tokens, output_tokens)
    if tag is None:
        return provider.Reply(None, usage, "the model stopped early")
    data = {
        "tag": tag,
        "summary": "What it is about.",
        "brief": "What it is, and the leads so far.",
        "rationale": "Because.",
    }
    if tag == "bug":
        data |= {"severity": "low", "priority": "low"}
    if tag == "feature":
        data["moscow"] = "could-have"
    return provider.Reply(data | fields, usage)
