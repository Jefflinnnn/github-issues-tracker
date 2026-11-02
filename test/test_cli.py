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
    assert (
        "Tags: bug 1 (0 high priority) · feature 1 (0 must-have) · question 1"
        in first
    )
    assert "Run: first run, 3 fetched · 3 tagged · <$0.01 of $0.50" in first
    assert "Open an issue" not in first
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
    assert "Run: 2 changed since last run · 1 tagged" in second

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


def _summary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    github: FakeGitHub,
    *args: str,
) -> str:
    serve(monkeypatch, github)
    monkeypatch.setattr(providers, "from_settings", lambda name: FakeModel())
    assert cli.main(["summary", "owner/repo", *args]) == 0
    return capsys.readouterr().out


def test_a_run_without_limit_fills_in_what_a_limited_run_left_out(
    isolated: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _summary(
        monkeypatch,
        capsys,
        FakeGitHub([[issue(1), issue(2)]], total_open=3),
        "--limit",
        "2",
    )

    # GitHub now lists #2 and #3; #1 was closed in the meantime.
    github = FakeGitHub([[issue(2), issue(3)]], total_open=2)
    out = _summary(monkeypatch, capsys, github)

    assert [name for name, _ in github.calls] == ["open"]
    assert out.startswith("Owner/Repo · 2 open")
    assert "Run: all 2 open fetched" in out


def test_limit_on_a_later_run_also_fetches_what_changed(
    isolated: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _summary(
        monkeypatch,
        capsys,
        FakeGitHub([[issue(1), issue(2), issue(3)]], total_open=3),
    )

    github = FakeGitHub(
        [[issue(3, updated_days_ago=0)]],
        total_open=2,
        changed=[[issue(2, state="CLOSED")]],
    )
    out = _summary(monkeypatch, capsys, github, "--limit", "1")

    assert [name for name, _ in github.calls] == ["open", "changed"]
    assert github.calls[0][1]["direction"] == "DESC"
    assert out.startswith("Owner/Repo · 2 open")
    assert "Run: newest 1 of 2 open + changes" in out


def test_demand_raises_a_feature_to_must_have(
    isolated: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    quiet = [issue(n) for n in range(1, 20)]
    wanted = issue(20, reactions=40)
    serve(monkeypatch, FakeGitHub([quiet + [wanted]], total_open=20))
    # The model calls every request could-have.
    model = FakeModel(default=reply("feature"))
    monkeypatch.setattr(providers, "from_settings", lambda name: model)

    assert cli.main(["summary", "owner/repo"]) == 0
    out = capsys.readouterr().out

    assert "feature 20 (1 must-have)" in out
    assert "MOST-WANTED FEATURES: must-have (1)" in out
    assert "#20     must-have ↑" in out


def test_the_run_line_says_what_the_budget_left_out(
    isolated: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    serve(
        monkeypatch, FakeGitHub([[issue(1), issue(2), issue(3)]], total_open=3)
    )
    model = FakeModel(default=reply("question"))
    monkeypatch.setattr(providers, "from_settings", lambda name: model)

    assert cli.main(["summary", "owner/repo", "--budget-usd", "0.003"]) == 0
    captured = capsys.readouterr()

    assert "· 2 tagged · <$0.01 of $0.003 · 1 left for next run" in captured.out
    # The Run line says it, so no warning repeats it.
    assert captured.err == ""
