"""Transform: turns GitHub's issue nodes into records and computes flags.

Everything here is a pure function of its inputs, so it needs no network
or database. Flags are computed from stored issues on every run, not
stored, so a flag such as ``stale`` can't go out of date.
"""

import dataclasses
import datetime
import enum
import math
from collections.abc import Sequence
from typing import Any

STALE_DAYS = 60
# The busiest tenth of a repository's open issues counts as hot.
HOT_FRACTION = 0.10
# Below this, an issue isn't hot even when it ranks near the top of a
# quiet repository.
HOT_MIN_ENGAGEMENT = 3

_BOT = "Bot"
_BOT_SUFFIXES = ("-bot", "_bot", "[bot]")


class Flag(str, enum.Enum):
    """A fact about an open issue that code computes."""

    HOT = "hot"
    STALE = "stale"


@dataclasses.dataclass(frozen=True)
class Comment:
    """One issue comment.

    Attributes:
        id: GitHub's global node ID.
        author: Login of the author, or None for a deleted account.
        author_type: ``User``, ``Bot`` and so on, or None for a deleted
            account.
        author_association: The author's role, e.g. ``OWNER`` or ``NONE``.
        created_at: When it was posted.
        body: Markdown text.
    """

    id: str
    author: str | None
    author_type: str | None
    author_association: str
    created_at: datetime.datetime
    body: str


@dataclasses.dataclass(frozen=True)
class Issue:
    """One issue with the fields the tool uses.

    Attributes:
        id: GitHub's global node ID.
        number: The issue number within its repository.
        title: Issue title.
        body: Markdown text.
        url: Link to the issue on github.com.
        state: ``OPEN`` or ``CLOSED``.
        created_at: When it was opened.
        updated_at: When anything on it last changed.
        closed_at: When it was closed, or None while open.
        author: Login of the opener, or None for a deleted account.
        author_type: ``User``, ``Bot`` and so on, or None.
        author_association: The opener's role, e.g. ``OWNER`` or ``NONE``.
        labels: Names of the repository labels on it.
        comment_count: All comments, including ones not fetched.
        reaction_count: Reactions of every kind on the opening post.
        comments: The latest comments, oldest first.
    """

    id: str
    number: int
    title: str
    body: str
    url: str
    state: str
    created_at: datetime.datetime
    updated_at: datetime.datetime
    closed_at: datetime.datetime | None
    author: str | None
    author_type: str | None
    author_association: str
    labels: list[str]
    comment_count: int
    reaction_count: int
    comments: list[Comment]


@dataclasses.dataclass(frozen=True)
class FlaggedIssue:
    """An open issue with its computed facts.

    Attributes:
        issue: The issue.
        idle_days: Whole days since a person last opened or commented on it.
        engagement: Reactions plus comments.
        flags: The flags that apply, in ``Flag`` order.
    """

    issue: Issue
    idle_days: int
    engagement: int
    flags: list[Flag]


def parse_time(iso: str) -> datetime.datetime:
    """Parses one of GitHub's ISO 8601 UTC timestamps.

    Args:
        iso: A timestamp such as ``2026-10-20T00:44:43Z``.

    Returns:
        The moment as a timezone-aware datetime.
    """
    # Python 3.10's fromisoformat doesn't accept the "Z" suffix.
    return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))


def to_issue(node: dict[str, Any]) -> Issue:
    """Converts one issue node from a GraphQL response.

    Args:
        node: The node as ``extract`` returned it.

    Returns:
        The issue record.
    """
    comments = node["comments"]
    return Issue(
        id=node["id"],
        number=node["number"],
        title=node["title"],
        body=node["body"],
        url=node["url"],
        state=node["state"],
        created_at=parse_time(node["createdAt"]),
        updated_at=parse_time(node["updatedAt"]),
        closed_at=parse_time(node["closedAt"]) if node["closedAt"] else None,
        author=_login(node["author"]),
        author_type=_account_type(node["author"]),
        author_association=node["authorAssociation"],
        labels=[label["name"] for label in node["labels"]["nodes"]],
        comment_count=comments["totalCount"],
        reaction_count=node["reactions"]["totalCount"],
        comments=[
            Comment(
                id=comment["id"],
                author=_login(comment["author"]),
                author_type=_account_type(comment["author"]),
                author_association=comment["authorAssociation"],
                created_at=parse_time(comment["createdAt"]),
                body=comment["body"],
            )
            for comment in comments["nodes"]
        ],
    )


def flag_issues(
    issues: Sequence[Issue],
    now: datetime.datetime,
    stale_days: int = STALE_DAYS,
) -> list[FlaggedIssue]:
    """Computes facts and flags for a repository's open issues.

    Args:
        issues: All the repository's open issues. ``hot`` ranks each issue
            against the others, so pass the full set.
        now: The current time, timezone-aware.
        stale_days: Days without human activity before an issue is stale.

    Returns:
        One entry per issue, in the order given.
    """
    threshold = _hot_threshold([engagement(issue) for issue in issues])
    flagged = []
    for issue in issues:
        idle_days = (now - last_human_activity(issue)).days
        score = engagement(issue)
        flags = []
        if score >= threshold:
            flags.append(Flag.HOT)
        if idle_days >= stale_days:
            flags.append(Flag.STALE)
        flagged.append(
            FlaggedIssue(
                issue=issue,
                idle_days=idle_days,
                engagement=score,
                flags=flags,
            )
        )
    return flagged


def last_human_activity(issue: Issue) -> datetime.datetime:
    """Returns when a person last opened or commented on an issue.

    Bot comments, such as a stale-bot's warning, don't count, and neither
    do label or title edits, which also move GitHub's ``updatedAt``. A
    deleted account counts as a person. Only fetched comments are seen.

    Args:
        issue: The issue to check.

    Returns:
        The latest human comment's time, or when the issue was opened.
    """
    return max(
        [issue.created_at, *(c.created_at for c in human_comments(issue))]
    )


def is_bot(comment: Comment) -> bool:
    """Says whether a comment comes from a bot.

    GitHub marks apps as bots, but some automation runs as an ordinary
    account named like one, such as ``astral-automations-bot``; a login
    ending in ``-bot``, ``_bot`` or ``[bot]`` counts too.

    Args:
        comment: The comment.

    Returns:
        Whether a bot wrote it.
    """
    if comment.author_type == _BOT:
        return True
    login = (comment.author or "").lower()
    return login.endswith(_BOT_SUFFIXES)


def human_comments(issue: Issue) -> list[Comment]:
    """Returns an issue's fetched comments from people, oldest first.

    Args:
        issue: The issue.

    Returns:
        The comments, bots left out.
    """
    return [comment for comment in issue.comments if not is_bot(comment)]


def comments_by_people(issue: Issue) -> int:
    """Counts an issue's comments from people.

    GitHub counts every comment; the bot comments among the fetched ones
    are taken off. Older comments that weren't fetched can't be checked,
    so a long thread may still count a few bots.

    Args:
        issue: The issue.

    Returns:
        The count.
    """
    bots = len(issue.comments) - len(human_comments(issue))
    return max(issue.comment_count - bots, 0)


def other_commenters(issue: Issue) -> int:
    """Counts the people besides the opener who commented, bots aside.

    Only fetched comments are seen, so a long thread may have more.

    Args:
        issue: The issue.

    Returns:
        How many distinct people commented.
    """
    return len(
        {
            comment.author
            for comment in human_comments(issue)
            if comment.author != issue.author
        }
    )


def demand(issue: Issue) -> int:
    """Scores how many people want an issue: reactions plus commenters.

    Distinct commenters count rather than comments, so a long exchange
    between two people doesn't look like demand.

    Args:
        issue: The issue.

    Returns:
        The demand score.
    """
    return issue.reaction_count + other_commenters(issue)


def engagement(issue: Issue) -> int:
    """Returns an issue's reactions plus comments from people.

    Args:
        issue: The issue to score.

    Returns:
        The engagement score.
    """
    return issue.reaction_count + comments_by_people(issue)


def _hot_threshold(scores: Sequence[int]) -> int:
    """Returns the lowest engagement that makes an issue hot.

    Ties at the cut-off are all hot, so the hot share can exceed
    ``HOT_FRACTION`` when several issues share a score.
    """
    if not scores:
        return HOT_MIN_ENGAGEMENT
    hot_count = math.ceil(len(scores) * HOT_FRACTION)
    cutoff = sorted(scores, reverse=True)[hot_count - 1]
    return max(cutoff, HOT_MIN_ENGAGEMENT)


def _login(author: dict[str, Any] | None) -> str | None:
    """Returns an author's login; GitHub sends null for deleted accounts."""
    return author["login"] if author else None


def _account_type(author: dict[str, Any] | None) -> str | None:
    """Returns an author's account type, such as User or Bot."""
    return author["__typename"] if author else None
