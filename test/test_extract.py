import pytest
from conftest import FakeGitHub
from conftest import issue
from conftest import serve

from triage import repo_ref
from triage.pipeline import extract

REPO = repo_ref.RepoRef("owner", "repo")


def test_follows_every_page(monkeypatch: pytest.MonkeyPatch) -> None:
    github = serve(monkeypatch, FakeGitHub([[issue(1)], [issue(2)]], 2))

    extracted = extract.fetch_open_issues(REPO, limit=0, token="t")

    assert [node["number"] for node in extracted.nodes] == [1, 2]
    assert [variables["after"] for _, variables in github.calls] == [None, "1"]
    assert github.calls[0][1]["direction"] == "ASC"
    assert extracted.total_open == 2


def test_limit_takes_the_newest_and_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    github = serve(monkeypatch, FakeGitHub([[issue(1), issue(2)], [issue(3)]]))

    extracted = extract.fetch_open_issues(REPO, limit=2, token="t")

    assert len(extracted.nodes) == 2
    assert len(github.calls) == 1
    assert github.calls[0][1]["direction"] == "DESC"


def test_too_many_issues_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(extract, "MAX_ISSUES", 1)
    serve(monkeypatch, FakeGitHub([[issue(1)], [issue(2)]]))

    with pytest.raises(extract.GitHubError, match="--limit"):
        extract.fetch_open_issues(REPO, limit=0, token="t")


def test_missing_repository_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        extract,
        "post_graphql",
        lambda *_: {"errors": [{"type": "NOT_FOUND", "message": "nope"}]},
    )

    with pytest.raises(extract.GitHubError, match="not found"):
        extract.fetch_changed_issues(
            REPO, since="2026-10-26T09:00:00Z", token="t"
        )
