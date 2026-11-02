import pathlib

import pytest
from conftest import FakeGitHub
from conftest import FakeModel
from conftest import comment
from conftest import issue
from conftest import reply
from conftest import serve

from triage import cli
from triage.model import providers


def test_version_exits_cleanly(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == "0.1.0"


def test_summary_then_rerun_then_list(
    isolated: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    model = FakeModel(replies=[reply("question"), reply("feature")])
    monkeypatch.setattr(providers, "from_settings", lambda name: model)
    serve(
        monkeypatch,
        FakeGitHub(
            [[issue(1), issue(2, opened_days_ago=200), issue(3, reactions=9)]],
            total_open=3,
        ),
    )
    assert cli.main(["summary", "https://github.com/owner/repo"]) == 0
    first = capsys.readouterr().out
    assert first.startswith("Owner/Repo · 3 open · 1 hot · 1 stale")
    assert "Tags: bug 1 · feature 1 · question 1" in first
    assert "First run: fetched 3 open issues." in first
    assert len(model.requests) == 3

    # The rerun asks only for changes: #1 got a comment, #2 was closed.
    # Only #1 goes back to the model; #3 keeps its tag.
    rerun = serve(
        monkeypatch,
        FakeGitHub(
            [
                [
                    issue(
                        1,
                        comments=[comment("maintainer", 0)],
                        updated_days_ago=0,
                    ),
                    issue(2, state="CLOSED"),
                ]
            ],
            total_open=2,
        ),
    )
    assert cli.main(["summary", "owner/repo"]) == 0
    second = capsys.readouterr().out
    assert rerun.calls[0][0] == "changed"
    assert len(model.requests) == 4
    assert second.startswith("Owner/Repo · 2 open · 1 hot · 0 stale")
    assert "2 issues changed since the last run" in second
    assert "tagged 1 this run" in second

    assert cli.main(["list"]) == 0
    assert "Owner/Repo  2" in capsys.readouterr().out
    assert cli.main(["list", "--names"]) == 0
    assert capsys.readouterr().out == "Owner/Repo\n"


def test_missing_token_explains_what_to_do(
    isolated: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN")

    assert cli.main(["summary", "owner/repo"]) == 1
    assert "Run triage setup" in capsys.readouterr().err
