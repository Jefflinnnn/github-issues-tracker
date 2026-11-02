import pytest

from triage import repo_ref


@pytest.mark.parametrize(
    "text",
    [
        "owner/repo",
        "https://github.com/owner/repo",
        "https://github.com/owner/repo/issues",
        "git@github.com:owner/repo.git",
    ],
)
def test_parses_common_forms(text: str) -> None:
    assert repo_ref.parse(text) == repo_ref.RepoRef("owner", "repo")


def test_rejects_a_bare_owner() -> None:
    with pytest.raises(repo_ref.InvalidRepoRefError):
        repo_ref.parse("https://github.com/owner")
