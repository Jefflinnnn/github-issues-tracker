import datetime
import pathlib
import re

import pytest
from conftest import NOW
from conftest import FakeGitHub
from conftest import FakeModel
from conftest import comment
from conftest import issue
from conftest import reply
from conftest import serve

from triage import cli
from triage import report
from triage.model import providers
from triage.pipeline import run


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
    assert first.startswith("Owner/Repo\n3 open\n")
    for tag in ("bug", "feature", "question"):
        assert re.search(rf"^  {tag} +1( |$)", first, re.M)
    # MoSCoW is a closed set, so every value shows, zeros included.
    assert re.search(
        r"^  feature +1   0 must · 0 should · 1 could$", first, re.M
    )
    # Hot and stale are counted in a footer, not listed.
    assert (
        "\nRun `triage browse` in the terminal to view every issue.\n"
        "This run tagged 3. Used <$0.01 of $0.50 budget.\n"
    ) in first
    assert "hot" not in first
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
    assert second.startswith("Owner/Repo\n2 open (-1)\n")
    # Zero counts are left out.
    assert "This run tagged 1. Used" in second

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
    assert out.startswith("Owner/Repo\n2 open (-1)\n")


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
    assert out.startswith("Owner/Repo\n2 open (-1)\n")


def test_hot_issues_are_tagged_first(
    isolated: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    serve(
        monkeypatch,
        FakeGitHub(
            [
                [
                    issue(1, updated_days_ago=0),
                    issue(2, updated_days_ago=30, reactions=9),
                    issue(3, updated_days_ago=1),
                ]
            ],
            total_open=3,
        ),
    )
    model = FakeModel()
    monkeypatch.setattr(providers, "from_settings", lambda name: model)

    # The budget covers one request: it goes to hot #2, not newest #1.
    assert cli.main(["summary", "owner/repo", "--budget-usd", "0.002"]) == 0

    assert len(model.requests) == 1
    assert "Title: Issue 2" in model.requests[0].user


def test_limit_also_caps_what_the_model_tags(
    isolated: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    model = FakeModel()
    monkeypatch.setattr(providers, "from_settings", lambda name: model)
    # The budget covers one request, so #2 and #3 stay untagged.
    serve(
        monkeypatch,
        FakeGitHub(
            [[issue(n, updated_days_ago=n) for n in (1, 2, 3)]], total_open=3
        ),
    )
    assert cli.main(["summary", "owner/repo", "--budget-usd", "0.002"]) == 0
    capsys.readouterr()
    assert len(model.requests) == 1

    # --limit 1 fetches only #3, so #2 waits even though it is untagged.
    serve(
        monkeypatch,
        FakeGitHub(
            [[issue(3, updated_days_ago=3)]], total_open=3, changed=[[]]
        ),
    )
    assert cli.main(["summary", "owner/repo", "--limit", "1"]) == 0
    out = capsys.readouterr().out

    assert len(model.requests) == 2
    assert "Title: Issue 3" in model.requests[-1].user
    assert re.search(r"^  not yet tagged +1$", out, re.M)
    # Tagging #3 for the first time is catching up, not a change: no +1.
    assert re.search(r"^  bug +2$", out, re.M)
    assert "This run tagged 1. Used" in out


def test_subtags_sort_issues_into_their_own_groups(
    isolated: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    serve(
        monkeypatch,
        FakeGitHub(
            [[issue(n, updated_days_ago=n) for n in (1, 2, 3, 4)]], total_open=4
        ),
    )
    model = FakeModel(
        replies=[
            reply("bug", severity="high", priority="high", subtag="fix-failed"),
            reply(
                "bug",
                severity="high",
                priority="high",
                subtag="fix-in-progress",
            ),
            reply("bug", severity="high", priority="high"),
            reply("feature", subtag="duplicate", summary="Same as #9"),
        ]
    )
    monkeypatch.setattr(providers, "from_settings", lambda name: model)

    # One worker, so replies go to issues newest first: #1, #2, #3, #4.
    assert cli.main(["summary", "owner/repo", "--workers", "1"]) == 0
    out = capsys.readouterr().out

    # Each count is said once: in a group heading, or in the footer.
    assert "FIX DIDN'T WORK (1)" in out
    # The footer no longer counts waiting issues.
    assert "waiting on" not in out
    fix_failed = out.split("FIX DIDN'T WORK")[1].split("\n\n")[0]
    assert "#1 " in fix_failed
    # A bug someone is already fixing stays on the list, with its status.
    to_fix = out.split("BUGS TO FIX")[1].split("\n\n")[0]
    assert sorted(re.findall(r"^  #(\d+)", to_fix, re.M)) == ["2", "3"]
    assert "fixing" in to_fix
    closable = out.split("READY TO CLOSE")[1].split("\n\n")[0]
    # The model's summary replaces the title; the status says why it's here.
    assert "duplicate  Same as #9" in closable


def test_a_wide_summary_uses_full_labels_and_status_names(
    isolated: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # No answer for 90 days: ready to close.
    serve(
        monkeypatch,
        FakeGitHub([[issue(1, opened_days_ago=90)]], total_open=1),
    )
    model = FakeModel(
        replies=[reply("question", subtag="needs-info", summary="No answer")]
    )
    monkeypatch.setattr(providers, "from_settings", lambda name: model)
    monkeypatch.setenv("COLUMNS", "120")

    assert cli.main(["summary", "owner/repo"]) == 0
    out = capsys.readouterr().out

    # Rows fit 100 columns at most, the same layout triage browse uses.
    assert re.search(r"TAGS +ACTIVITY +IDLE  STATUS +SUMMARY$", out, re.M)
    assert re.search(
        r"^  #1      question +0 +\d+d  needs-info +No answer$", out, re.M
    )
    assert all(len(line) < 100 for line in out.splitlines())


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

    assert re.search(
        r"^  feature +20   1 must · 0 should · 19 could$", out, re.M
    )
    # Every feature is listed, must-have first; the summary shows 10.
    features = out.split("FEATURES (20)")[1].split("\n\n")[0]
    assert features.splitlines()[1].startswith("  #20    must ↑ ")
    assert "… and 10 more" in features


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

    assert (
        "This run tagged 2. Used <$0.01 of $0.003 budget.\n"
        "1 queued for next run.\n" in captured.out
    )
    assert re.search(r"^  not yet tagged +1$", captured.out, re.M)
    # The summary says it, so no warning repeats it.
    assert captured.err == ""


def test_each_issue_is_listed_once_in_its_first_group(
    isolated: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    quiet = [issue(n) for n in range(1, 20)]
    serve(
        monkeypatch,
        FakeGitHub([quiet + [issue(20, reactions=40)]], total_open=20),
    )
    model = FakeModel(default=reply("bug", severity="high", priority="high"))
    monkeypatch.setattr(providers, "from_settings", lambda name: model)

    assert cli.main(["summary", "owner/repo"]) == 0
    out = capsys.readouterr().out

    # #20 is hot and an urgent bug: listed once, under bugs.
    assert out.count("#20 ") == 1
    assert "Run `triage browse` in the terminal" in out


def test_a_partial_scan_says_so_and_leaves_out_staleness(
    isolated: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old = [issue(n, opened_days_ago=200) for n in (1, 2)]
    serve(monkeypatch, FakeGitHub([old], total_open=50))
    monkeypatch.setattr(providers, "from_settings", lambda name: FakeModel())

    assert cli.main(["summary", "owner/repo", "--limit", "2"]) == 0
    out = capsys.readouterr().out

    assert out.startswith("Owner/Repo\n50 open · scanned the newest 2 (4%)\n")
    assert "stale" not in out.lower()


def test_color_and_links_leave_the_columns_where_they_were(
    isolated: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nodes = [issue(n, reactions=12 - n) for n in range(1, 12)]
    serve(monkeypatch, FakeGitHub([nodes], total_open=11))
    model = FakeModel(
        replies=[
            reply("bug", severity="high", priority="high", subtag="fix-failed")
        ],
        default=reply("feature", moscow="must-have"),
    )
    monkeypatch.setattr(providers, "from_settings", lambda name: model)
    results = []
    render = report.summary

    def keep(
        result: run.RunResult, width: int, style: report.Style, **_: bool
    ) -> str:
        results.append(result)
        return ""

    monkeypatch.setattr(report, "summary", keep)
    assert cli.main(["summary", "owner/repo", "--workers", "1"]) == 0

    plain = render(results[0], 100)
    styled = render(results[0], 100, report.Style(color=True, links=True))

    assert "\x1b[" in styled
    assert "\x1b]8;;https://github.com/Owner/Repo/issues/1\x1b\\" in styled
    escapes = re.compile(r"\x1b\[[0-9;]*m|\x1b\]8;;[^\x1b]*\x1b\\")
    assert escapes.sub("", styled) == plain


def test_bug_groups_sort_and_count_what_they_leave_out(
    isolated: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    serve(
        monkeypatch,
        FakeGitHub(
            [
                [
                    issue(1, updated_days_ago=1, reactions=1),
                    issue(2, updated_days_ago=2, reactions=5),
                    issue(3, updated_days_ago=3),
                    issue(4, updated_days_ago=4),
                    issue(5, updated_days_ago=5),
                    issue(6, updated_days_ago=6, opened_days_ago=90),
                ]
            ],
            total_open=6,
        ),
    )
    model = FakeModel(
        replies=[
            reply("bug", severity="low", priority="high"),
            reply("bug", severity="low", priority="high"),
            reply("bug", severity="high", priority="high"),
            reply("bug", severity="high", priority="low"),
            reply("spam", summary="Buy followers"),
            reply("question", subtag="needs-info", summary="No answer"),
        ]
    )
    monkeypatch.setattr(providers, "from_settings", lambda name: model)

    assert cli.main(["summary", "owner/repo", "--workers", "1"]) == 0
    out = capsys.readouterr().out

    bugs = out.split("BUGS TO FIX")[1].split("\n\n")[0]
    numbers = re.findall(r"^  #(\d+)", bugs, re.M)
    # Urgent first, severe first among them, then the busiest: #3, #2 (5
    # reactions), #1; then the severe bug that isn't urgent, #4.
    assert numbers == ["3", "2", "1", "4"]
    assert re.search(r"^  bug +4   3 urgent · 2 severe$", out, re.M)
    # Spam is counted, never listed.
    assert re.search(r"^  spam +1   not listed$", out, re.M)
    assert "#5 " not in out
    # A question nobody answered for 30 days is ready to close.
    closable = out.split("READY TO CLOSE")[1].split("\n\n")[0]
    assert "#6 " in closable and "needs info" in closable


def test_an_empty_bug_group_says_where_the_bugs_went(
    isolated: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    serve(monkeypatch, FakeGitHub([[issue(1), issue(2)]], total_open=2))
    model = FakeModel(
        replies=[
            reply(
                "bug", severity="low", priority="high", subtag="fix-released"
            ),
            reply("feature"),
        ]
    )
    monkeypatch.setattr(providers, "from_settings", lambda name: model)

    assert cli.main(["summary", "owner/repo", "--workers", "1"]) == 0
    out = capsys.readouterr().out

    assert "BUGS TO FIX" not in out
    assert "No bugs to fix · 1 bug with a released fix" in out
    # Every row in Ask to confirm is fix-released, so rows don't repeat it.
    assert "[fix-released]" not in out


def test_since_last_run_counts_changes_and_writes_a_digest(
    isolated: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Newest first: #2 (opened 8 days ago), then #1.
    model = FakeModel(
        replies=[
            reply("question"),
            reply(
                "bug", severity="high", priority="high", subtag="fix-released"
            ),
        ],
        digest="The fix for #1 failed.",
    )
    monkeypatch.setattr(providers, "from_settings", lambda name: model)
    two_days_ago = NOW - datetime.timedelta(days=2)
    monkeypatch.setattr(cli, "_now", lambda: two_days_ago)
    serve(
        monkeypatch,
        FakeGitHub(
            [[issue(1, opened_days_ago=9), issue(2, opened_days_ago=8)]],
            total_open=2,
        ),
    )
    assert cli.main(["summary", "owner/repo", "--workers", "1"]) == 0
    first = capsys.readouterr().out
    assert "SINCE LAST RUN" not in first
    assert model.digest_requests == []

    # Since then: #1 got a comment saying the fix failed, #2 was closed and
    # #3 was opened.
    model.replies = [
        reply(
            "bug",
            severity="high",
            priority="high",
            subtag="fix-failed",
            change="The reporter says the fix in 3.4.3 failed",
        ),
        reply("feature"),
    ]
    monkeypatch.setattr(cli, "_now", lambda: NOW)
    serve(
        monkeypatch,
        FakeGitHub(
            [
                [
                    # Updated most recently, so it is tagged first.
                    issue(
                        1,
                        opened_days_ago=9,
                        comments=[comment("opener", 0.5)],
                        updated_days_ago=0.5,
                    ),
                    issue(2, opened_days_ago=8, state="CLOSED"),
                    issue(3, opened_days_ago=1),
                ]
            ],
            total_open=2,
        ),
    )
    assert cli.main(["summary", "owner/repo", "--workers", "1"]) == 0
    out = capsys.readouterr().out

    heading = (
        "SINCE LAST RUN (2 days ago)  1 new · 1 closed · 1 with new comments"
    )
    assert heading in out
    assert "\n  The fix for #1 failed.\n" in out
    # The changed issue's prompt marks it, and its new comment.
    changed = model.requests[2].user
    assert "Changed since the last run: yes" in changed
    assert "Comment by the opener (new):" in changed
    # The digest sees the change note and the subtag transition.
    material = model.digest_requests[0].user
    assert "fix-released → fix-failed" in material
    assert "The reporter says the fix in 3.4.3 failed" in material
    # The tag table shows each count's change.
    # #2 was the only question and it closed; #3 is a new feature.
    assert re.search(r"^  question +0 +-1$", out, re.M)
    assert re.search(r"^  feature +1 +\+1", out, re.M)
    assert "This run tagged 2. Used <$0.01 of $0.50 budget." in out


def test_no_digest_when_the_budget_runs_out(
    isolated: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    model = FakeModel()
    monkeypatch.setattr(providers, "from_settings", lambda name: model)
    monkeypatch.setattr(cli, "_now", lambda: NOW - datetime.timedelta(days=2))
    serve(
        monkeypatch, FakeGitHub([[issue(1, opened_days_ago=9)]], total_open=1)
    )
    assert cli.main(["summary", "owner/repo"]) == 0
    capsys.readouterr()

    monkeypatch.setattr(cli, "_now", lambda: NOW)
    serve(
        monkeypatch,
        FakeGitHub(
            [[issue(1, opened_days_ago=9), issue(2, opened_days_ago=1)]],
            total_open=2,
        ),
    )
    assert cli.main(["summary", "owner/repo", "--budget-usd", "0.002"]) == 0
    out = capsys.readouterr().out

    assert "SINCE LAST RUN (2 days ago)  1 new" in out
    assert "No digest: the budget ran out." in out
    assert model.digest_requests == []
