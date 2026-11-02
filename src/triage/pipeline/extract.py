"""Extract: reads issues from the GitHub GraphQL API.

This stage only talks to GitHub. It returns issue nodes as GitHub sent
them; ``transform`` turns them into records.
"""

import dataclasses
import json
import logging
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from triage import repo_ref

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://api.github.com/graphql"
REQUEST_TIMEOUT_S = 30
# GraphQL caps any connection at 100 nodes per request.
PAGE_SIZE = 100
# Latest comments kept per issue; enough to see who replied last.
COMMENTS_PER_ISSUE = 10
# A hard stop so a typo'd giant repository can't page forever. Larger
# repositories need --limit.
MAX_ISSUES = 20_000

# Called with (issues fetched so far, issues to fetch or 0 if unknown).
Progress = Callable[[int, int], None]

_ISSUE_FIELDS = """
        id
        number
        title
        body
        url
        state
        createdAt
        updatedAt
        closedAt
        author { login __typename }
        authorAssociation
        labels(first: 20) { nodes { name } }
        reactions { totalCount }
        comments(last: $comments) {
          totalCount
          nodes {
            id
            author { login __typename }
            authorAssociation
            createdAt
            body
          }
        }
"""

# Without a limit, oldest change first: an issue edited while paging moves
# to the end and is read again, so none is skipped. With a limit, newest
# change first, so the limit keeps the most active issues.
OPEN_ISSUES_QUERY = (
    """
query($owner: String!, $name: String!, $pageSize: Int!, $comments: Int!,
      $direction: OrderDirection!, $after: String) {
  repository(owner: $owner, name: $name) {
    id
    nameWithOwner
    url
    openIssues: issues(states: OPEN) { totalCount }
    issues(first: $pageSize, after: $after, states: OPEN,
           orderBy: {field: UPDATED_AT, direction: $direction}) {
      pageInfo { hasNextPage endCursor }
      nodes {"""
    + _ISSUE_FIELDS
    + """      }
    }
  }
}
"""
)

# Open and closed issues changed since a time, so closures show up too.
CHANGED_ISSUES_QUERY = (
    """
query($owner: String!, $name: String!, $pageSize: Int!, $comments: Int!,
      $since: DateTime!, $after: String) {
  repository(owner: $owner, name: $name) {
    id
    nameWithOwner
    url
    openIssues: issues(states: OPEN) { totalCount }
    issues(first: $pageSize, after: $after, filterBy: {since: $since},
           orderBy: {field: UPDATED_AT, direction: ASC}) {
      pageInfo { hasNextPage endCursor }
      nodes {"""
    + _ISSUE_FIELDS
    + """      }
    }
  }
}
"""
)


VIEWER_QUERY = "query { viewer { login } }"


class GitHubError(RuntimeError):
    """Raised when GitHub can't return the requested data."""


@dataclasses.dataclass(frozen=True)
class Extracted:
    """What one fetch returned.

    Attributes:
        repository: GitHub's ``id``, ``nameWithOwner`` and ``url``.
        nodes: Issue nodes as GitHub sent them.
        total_open: Open issues in the repository, not just those fetched.
    """

    repository: dict[str, Any]
    nodes: list[dict[str, Any]]
    total_open: int


def fetch_open_issues(
    repo: repo_ref.RepoRef,
    limit: int,
    token: str,
    progress: Progress | None = None,
) -> Extracted:
    """Fetches a repository's open issues, following GitHub's pages.

    Args:
        repo: The repository to read.
        limit: Most issues to fetch, most recently updated first; 0
            fetches all of them.
        token: A GitHub token that can read the repository.
        progress: Called after each page with the issues fetched so far
            and how many there are to fetch.

    Returns:
        The repository and its open issues.

    Raises:
        ValueError: ``limit`` is negative.
        GitHubError: A request failed, or the repository has more open
            issues than ``MAX_ISSUES`` and no limit was given.
    """
    if limit < 0:
        raise ValueError(f"limit must be 0 or more, got {limit}")
    logger.debug("Fetching open issues")
    direction = "DESC" if limit else "ASC"
    return _fetch_pages(
        OPEN_ISSUES_QUERY,
        repo,
        token,
        limit,
        {"direction": direction},
        progress,
        counts_open=True,
    )


def fetch_changed_issues(
    repo: repo_ref.RepoRef,
    since: str,
    token: str,
    progress: Progress | None = None,
) -> Extracted:
    """Fetches every issue created, changed or closed since a time.

    Args:
        repo: The repository to read.
        since: ISO 8601 time; issues updated at or after it are returned.
        token: A GitHub token that can read the repository.
        progress: Called after each page with the issues fetched so far,
            and 0, since GitHub doesn't say how many changed.

    Returns:
        The repository and the changed issues, open or closed.

    Raises:
        GitHubError: A request failed or there are more than
            ``MAX_ISSUES`` changes.
    """
    logger.debug("Fetching issues changed since %s", since)
    return _fetch_pages(
        CHANGED_ISSUES_QUERY, repo, token, 0, {"since": since}, progress
    )


def _fetch_pages(
    query: str,
    repo: repo_ref.RepoRef,
    token: str,
    limit: int,
    extra_variables: dict[str, Any],
    progress: Progress | None = None,
    counts_open: bool = False,
) -> Extracted:
    """Runs a paged issues query until it ends or reaches the limit.

    ``counts_open`` says the query lists open issues, so their total is
    known and ``progress`` gets it.
    """
    cap = limit or MAX_ISSUES
    nodes: dict[str, dict[str, Any]] = {}
    after: str | None = None
    while True:
        variables = {
            "owner": repo.owner,
            "name": repo.name,
            "pageSize": min(PAGE_SIZE, cap - len(nodes)),
            "comments": COMMENTS_PER_ISSUE,
            "after": after,
            **extra_variables,
        }
        repository = _repository(post_graphql(query, variables, token), repo)
        # An issue edited while paging comes back again; keep the newest.
        for node in repository["issues"]["nodes"]:
            nodes[node["id"]] = node
        if progress is not None:
            total = repository["openIssues"]["totalCount"]
            progress(len(nodes), min(cap, total) if counts_open else 0)
        page_info = repository["issues"]["pageInfo"]
        if not page_info["hasNextPage"] or len(nodes) >= cap:
            break
        after = page_info["endCursor"]
    if not limit and page_info["hasNextPage"]:
        raise GitHubError(
            f"{repo.full_name} has more than {MAX_ISSUES:,} issues to fetch. "
            "Pass --limit N to fetch the N most recently updated."
        )
    return Extracted(
        repository={
            key: repository[key] for key in ("id", "nameWithOwner", "url")
        },
        nodes=list(nodes.values()),
        total_open=repository["openIssues"]["totalCount"],
    )


def check_token(token: str) -> str:
    """Confirms a token works with one cheap request.

    Args:
        token: The GitHub token to check.

    Returns:
        The login of the account the token belongs to.

    Raises:
        GitHubError: GitHub rejected the token or couldn't be reached.
    """
    payload = post_graphql(VIEWER_QUERY, {}, token)
    if payload.get("errors"):
        raise GitHubError(f"GitHub returned errors: {payload['errors']}")
    login: str = payload["data"]["viewer"]["login"]
    return login


def post_graphql(
    query: str, variables: dict[str, Any], token: str
) -> dict[str, Any]:
    """Sends one GraphQL request and returns the decoded JSON body.

    Args:
        query: The GraphQL query.
        variables: The query's variables.
        token: A GitHub token.

    Returns:
        The response body.

    Raises:
        GitHubError: GitHub rejected the request or couldn't be reached.
    """
    request = urllib.request.Request(
        GRAPHQL_URL,
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "github-issues-tracker",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=REQUEST_TIMEOUT_S
        ) as response:
            body: dict[str, Any] = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 401:
            raise GitHubError(
                "GitHub rejected the token (HTTP 401). Check GITHUB_TOKEN "
                "and that the token hasn't expired."
            ) from error
        raise GitHubError(
            f"GitHub returned HTTP {error.code} {error.reason}. Try again "
            "later; run with -v for details."
        ) from error
    except urllib.error.URLError as error:
        raise GitHubError(
            f"Couldn't reach GitHub: {error.reason}. Check your connection."
        ) from error
    return body


def _repository(
    payload: dict[str, Any], repo: repo_ref.RepoRef
) -> dict[str, Any]:
    """Returns a response's repository, raising on any GraphQL error."""
    errors = payload.get("errors") or []
    if any(error.get("type") == "NOT_FOUND" for error in errors):
        raise GitHubError(
            f"Repository {repo.full_name} not found. Check the spelling; "
            "private repositories need a token with access to them."
        )
    if errors:
        messages = "; ".join(str(error.get("message")) for error in errors)
        raise GitHubError(f"GitHub returned errors: {messages}")
    repository: dict[str, Any] | None = (payload.get("data") or {}).get(
        "repository"
    )
    if repository is None:
        raise GitHubError(f"GitHub returned no data for {repo.full_name}.")
    return repository
