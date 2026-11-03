import datetime
import pathlib
from collections.abc import Callable

import pytest
from conftest import NOW
from conftest import FakeGitHub
from conftest import FakeModel
from conftest import comment
from conftest import issue
from conftest import reply
from conftest import serve

from triage import browse
from triage import cli
from triage import repo_ref
from triage.model import providers
from triage.pipeline import load
from triage.pipeline import run

opened: list[str] = []
copied: list[str] = []


def _record(into: list[str]) -> Callable[[str], bool]:
    def record(url: str) -> bool:
        into.append(url)
        return True

    return record


def _failed_fix() -> dict[str, object]:
    """#1: a maintainer replied, a bot announced a fix, the reporter says no."""
    node = issue(
        1,
        opened_days_ago=40,
        reactions=9,
        comments=[
            comment("maintainer", 6, association="OWNER"),
            comment("astral-automations-bot", 5),
            comment("opener", 1),
        ],
        updated_days_ago=1,
    )
    node["comments"]["nodes"][1]["body"] = (
        "The fix for this is out now, in 3.4.3."
    )
    node["comments"]["nodes"][2]["body"] = (
        "Still crashes:\n```\nTraceback (most recent call last):\n```\n"
        "Same as before."
    )
    return node


@pytest.fixture
def browser(
    isolated: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> browse.Browser:
    """A browser after two runs three days apart.

    Since the first run: #1 got a comment saying the fix failed, #4 was
    opened and #5 was closed. #2 is an urgent bug; #3 and #6 are stale.
    """
    # Hot first, then newest: #1, #5, #2, #3, #6.
    model = FakeModel(
        replies=[
            reply("question"),
            reply("question"),
            reply("bug", severity="low", priority="high"),
            reply("feature"),
            reply("feature"),
        ]
    )
    monkeypatch.setattr(providers, "from_settings", lambda name: model)
    earlier = [
        issue(1, opened_days_ago=40, reactions=9),
        issue(2, opened_days_ago=20),
        issue(3, opened_days_ago=200),
        issue(5, opened_days_ago=10),
        issue(6, opened_days_ago=300),
    ]
    monkeypatch.setattr(cli, "_now", lambda: NOW - datetime.timedelta(days=3))
    serve(monkeypatch, FakeGitHub([earlier], total_open=5))
    assert cli.main(["summary", "owner/repo", "--workers", "1"]) == 0

    model.replies = [
        reply(
            "bug",
            severity="high",
            priority="high",
            subtag="fix-failed",
            summary="Crash on start after 3.4.3",
            brief=(
                "The app crashes on start since 3.4.3. The reporter says the "
                "released fix didn't help; no workaround yet."
            ),
            severity_reason="crashes on every launch",
            priority_reason="a released fix failed",
            rationale="The reporter says 3.4.3 still crashes.",
            change="The reporter says the fix failed",
        ),
        reply("feature", summary="Dark mode"),
    ]
    monkeypatch.setattr(cli, "_now", lambda: NOW)
    later = [
        _failed_fix(),
        issue(2, opened_days_ago=20),
        issue(3, opened_days_ago=200),
        issue(4, opened_days_ago=1),
        issue(5, opened_days_ago=10, state="CLOSED"),
        issue(6, opened_days_ago=300),
    ]
    serve(monkeypatch, FakeGitHub([later], total_open=5))
    assert cli.main(["summary", "owner/repo", "--workers", "1"]) == 0

    connection = load.connect(isolated)
    result = run.last_result(connection, repo_ref.parse("owner/repo"), NOW)
    connection.close()
    assert result is not None
    return browse.Browser(
        result, NOW, opener=_record(opened), copier=_record(copied)
    )


def _text(lines: list[browse.Line]) -> str:
    return "\n".join(line.text for line in lines)


def test_the_list_says_what_changed_and_marks_it(
    browser: browse.Browser,
) -> None:
    header = _text(browser.header())
    lines, cursor = browser.body(120)

    assert header.startswith("Owner/Repo · 5 open\n")
    day = (NOW - datetime.timedelta(days=3)).astimezone().strftime("%a")
    assert f"Since {day}: 1 new · 1 closed · 1 with new comments" in header
    assert "TAGS" in header and "ACTIVITY" in header and "STATUS" in header
    assert cursor == 0
    # Only the focused group shows its description.
    assert lines[0].text.startswith("1 ▾ FIX DIDN'T WORK (1)  a released fix")
    assert lines[1].text.startswith("● #1 ")
    assert "fix-failed" in lines[1].text
    assert lines[2].text == "2 ▸ BUGS TO FIX (1)"
    # Red only for urgent bugs; a failed fix is white.
    assert lines[0].kind == "bold" and lines[1].kind == "normal"

    # c moves to the next changed issue not opened yet. #4 is new but in
    # no group, so #1 is the only one to move to.
    browser.key("c")
    lines, cursor = browser.body(120)
    assert lines[cursor].text.startswith("● #1 ")


def test_the_card_shows_the_brief_then_info(browser: browse.Browser) -> None:
    browser.key("j")
    browser.key("enter")
    header = _text(browser.header())
    lines, cursor = browser.body(120)
    text = _text(lines)

    assert cursor == -1
    assert header == "Owner/Repo › Fix didn't work › #1  ·  1 of 1"
    # Title and summary start at the left edge.
    assert lines[0].text == "Title       Issue 1"
    assert "\nSummary     The app crashes on start since 3.4.3." in text
    assert "\nInfo:\n  Type        bug\n" in text
    assert "  Severe      yes · crashes on every launch" in text
    assert "  Urgent      yes · a released fix failed" in text
    assert "  Status      fix-failed" in text
    # The bot's comment counts nowhere.
    assert "  Activity    9 reactions · 2 comments · 2 people" in text
    assert "  Last        1d ago, by the reporter" in text
    assert "  Maintainers replied 6d ago" in text
    day = (NOW - datetime.timedelta(days=3)).astimezone().strftime("%a")
    since = f"  Since {day:<6}1 new comment · The reporter says the fix failed"
    assert since in text
    assert "  Why here    fix-failed · a released fix, still reported" in text
    # No link, events, comments or reason paragraph for a bug.
    assert "https://" not in text
    assert "astral-automations-bot" not in text
    assert "Still crashes" not in text
    assert "Reason" not in text
    # Opening the card clears the issue's mark; Enter stays on the card.
    browser.key("enter")
    assert browser.view == "card"
    browser.key("left")
    assert browser.body(120)[0][1].text.startswith("  #1 ")


def test_n_and_p_step_through_a_group(browser: browse.Browser) -> None:
    browser.key("4")
    browser.key("j")
    browser.key("enter")
    first = _text(browser.header())

    browser.key("n")
    second = _text(browser.header())
    browser.key("p")

    assert first.endswith("1 of 2") and second.endswith("2 of 2")
    assert _text(browser.header()) == first


def test_o_opens_and_y_copies_the_link(browser: browse.Browser) -> None:
    opened.clear()
    copied.clear()
    browser.key("j")

    browser.key("o")
    assert opened == ["https://github.com/Owner/Repo/issues/1"]
    assert browser.message == "Opened #1 on GitHub"

    browser.key("y")
    assert copied == ["https://github.com/Owner/Repo/issues/1"]


def test_without_a_terminal_browse_prints_the_summary(
    browser: browse.Browser,
    capsys: pytest.CaptureFixture[str],
) -> None:
    capsys.readouterr()

    assert cli.main(["browse", "owner/repo"]) == 0
    out = capsys.readouterr().out

    assert out.startswith("Owner/Repo\n5 open\n")
    assert "Last run (" in out and "tagged 2" in out


def test_browse_needs_an_earlier_run(
    isolated: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["browse", "owner/repo"]) == 1
    assert "Run: triage summary owner/repo" in capsys.readouterr().err


def test_rows_fit_80_columns_with_short_labels(browser: browse.Browser) -> None:
    header = _text(browser.header(80))
    lines, _ = browser.body(80)

    assert "TAGS       ACT IDLE  STATUS     SUMMARY" in header
    assert all(len(line.text) <= 79 for line in lines)
    row = next(line.text for line in lines if "#1 " in line.text)
    assert row.startswith("● #1     urgent")
    assert "failed" in row and "fix-failed" not in row
    # 100 columns and up get the full labels.
    wide = next(
        line.text for line in browser.body(120)[0] if "#1 " in line.text
    )
    assert "bug: severe, urgent" in wide and "fix-failed" in wide


def test_space_and_b_page_through_the_card(browser: browse.Browser) -> None:
    browser.key("j")
    browser.key("enter")
    browser.page = 3

    browser.key(" ")
    assert browser.scroll == 3
    browser.key("b")
    assert browser.scroll == 0
    browser.key("left")
    assert browser.view == "list"


def test_a_long_group_pages_ten_at_a_time(
    isolated: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(
        monkeypatch,
        FakeGitHub(
            [[issue(n, updated_days_ago=n) for n in range(1, 24)]],
            total_open=23,
        ),
    )
    model = FakeModel(default=reply("bug"))
    monkeypatch.setattr(providers, "from_settings", lambda name: model)
    monkeypatch.setattr(cli, "_now", lambda: NOW)
    assert cli.main(["summary", "owner/repo"]) == 0
    connection = load.connect(isolated)
    result = run.last_result(connection, repo_ref.parse("owner/repo"), NOW)
    connection.close()
    assert result is not None
    browser = browse.Browser(result, NOW)

    lines, _ = browser.body(120)
    assert lines[0].text.startswith("1 ▾ BUGS TO FIX (23)")
    assert sum(line.text.startswith("  #") for line in lines) == 10
    assert lines[11].text == "    page 1 of 3 · ] next, [ previous"

    issues = browser.groups[0].issues
    browser.key("]")
    lines, cursor = browser.body(120)
    assert "page 2 of 3" in _text(lines)
    assert lines[cursor].text.startswith(f"  #{issues[10].issue.number} ")
    browser.key("[")
    browser.key("[")
    assert "page 3 of 3" in _text(browser.body(120)[0])
    browser.key("]")
    assert "page 1 of 3" in _text(browser.body(120)[0])

    # Stepping with n onto the next page, then back, lands on that page.
    browser.key("enter")
    for _ in range(10):
        browser.key("n")
    browser.key("left")
    lines, cursor = browser.body(120)
    assert "page 2 of 3" in _text(lines)
    assert lines[cursor].text.startswith(f"  #{issues[10].issue.number} ")
