import pathlib
import sqlite3
from typing import Any

import pytest
from conftest import NOW
from conftest import REPOSITORY
from conftest import comment
from conftest import issue

from triage.pipeline import load
from triage.pipeline import transform


def _save(connection: sqlite3.Connection, *nodes: dict[str, Any]) -> None:
    issues = [transform.to_issue(node) for node in nodes]
    load.save_run(connection, REPOSITORY, issues, NOW, open_count=len(nodes))


def test_a_closed_issue_leaves_the_open_set(tmp_path: pathlib.Path) -> None:
    connection = load.connect(tmp_path / "triage.db")
    _save(connection, issue(1), issue(2, comments=[comment("a", 1)]))
    _save(
        connection,
        issue(1, state="CLOSED"),
        issue(2, comments=[comment("b", 0.5)]),
    )

    stored = load.open_issues(connection, "owner/repo")

    assert [i.number for i in stored] == [2]
    # Comments are replaced, since GitHub sends only the latest few.
    assert [c.author for c in stored[0].comments] == ["b"]


def test_history_counts_runs(tmp_path: pathlib.Path) -> None:
    connection = load.connect(tmp_path / "triage.db")
    _save(connection, issue(1))
    _save(connection, issue(1), issue(2))

    (repo,) = load.history(connection)

    assert (repo.name_with_owner, repo.runs, repo.open_count) == (
        "Owner/Repo",
        2,
        2,
    )


def test_another_schema_version_asks_to_delete(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "triage.db"
    sqlite3.connect(path).execute("PRAGMA user_version = 99")

    with pytest.raises(load.LoadError, match="Delete it"):
        load.connect(path)
