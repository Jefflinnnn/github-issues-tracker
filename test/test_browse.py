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
    opened and #5 was closed. #2 is an urgent bug; #3, #4 and #6 are
    features, and #3 and #6 are stale.
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
            # A drifting model: a leading label and stacked facts.
            severity_reason="Severe: crashes on every launch · no workaround",
            priority_reason="A released fix failed.",
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
    assert "IDLE  FLAG   STATUS" in header
    assert cursor == 0
    # Headings carry no description; the card says why an issue is there.
    assert lines[0].text == "1 ▾ FIX DIDN'T WORK (1)"
    assert lines[1].text.startswith("● #1 ")
    # The group's title already names the status.
    assert "fix-failed" not in lines[1].text
    assert lines[2].text == "2 ▸ BUGS TO FIX (1)"
    # Red only for urgent bugs; a failed fix is white.
    assert lines[0].kind == "bold" and lines[1].kind == "normal"

    # The FLAG column says which issues are hot or stale; Hot and Stale
    # aren't groups, so every issue stays in the group of its tag.
    assert "  hot  " in lines[1].text
    assert lines[3].text == "3 ▸ FEATURES (3)"
    assert [g.title for g in browser.groups] == [
        "FIX DIDN'T WORK",
        "BUGS TO FIX",
        "FEATURES",
    ]
    browser.key("3")
    features = _text(browser.body(120)[0]).split("FEATURES")[1]
    assert "#6 " in features and "stale" in features

    # c moves to the next changed issue not opened yet: #1, then #4.
    browser.key("1")
    browser.key("c")
    lines, cursor = browser.body(120)
    assert lines[cursor].text.startswith("● #1 ")
    browser.key("enter")
    browser.key("left")
    browser.key("c")
    lines, cursor = browser.body(120)
    assert lines[cursor].text.startswith("● #4 ")


def test_the_card_shows_the_brief_then_info(browser: browse.Browser) -> None:
    browser.key("j")
    browser.key("enter")
    header = _text(browser.header())
    lines, cursor = browser.body(120)
    text = _text(lines)

    assert cursor == -1
    assert header == "Owner/Repo › Fix didn't work › #1  (1 of 1)"
    # Title and summary start at the left edge, a blank line apart and
    # below the breadcrumb.
    assert [line.text for line in lines[:3]] == [
        "",
        "Title         Issue 1",
        "",
    ]
    assert lines[3].text.startswith("Summary       The app crashes on start")
    # One fact per row; a value and its reason are set apart by spaces.
    assert "\nInfo:\n  Type          bug\n" in text
    assert (
        "  Severe        yes   crashes on every launch, no workaround" in text
    )
    assert "  Urgent        yes   A released fix failed." in text
    assert "  Status        fix failed" in text
    assert "  Hot           among the busiest tenth, 11 reactions and" in text
    # The bot's comment counts nowhere.
    assert "  Activity      9 reactions, 2 comments, 2 people" in text
    assert "  Last          1d ago, by the reporter" in text
    assert "  Maintainers   replied 6d ago" in text
    day = (NOW - datetime.timedelta(days=3)).astimezone().strftime("%a")
    since = f"  Since {day:<8}1 new comment. The reporter says the fix failed"
    assert since in text
    assert (
        "  Why here      fix failed, a released fix is still reported" in text
    )
    # Dots join facts in the list, never on a card.
    assert "·" not in header + text
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


def test_a_bug_being_fixed_sorts_with_the_other_bugs(
    isolated: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(
        monkeypatch,
        FakeGitHub(
            [[issue(n, updated_days_ago=n) for n in (1, 2, 3)]], total_open=3
        ),
    )
    model = FakeModel(
        replies=[
            reply("bug", priority="high", subtag="fix-in-progress"),
            reply("bug"),
            reply("question"),
        ]
    )
    monkeypatch.setattr(providers, "from_settings", lambda name: model)
    monkeypatch.setattr(cli, "_now", lambda: NOW)
    assert cli.main(["summary", "owner/repo", "--workers", "1"]) == 0
    connection = load.connect(isolated)
    result = run.last_result(connection, repo_ref.parse("owner/repo"), NOW)
    connection.close()
    assert result is not None
    browser = browse.Browser(result, NOW)

    bugs, other = browser.groups[0], browser.groups[1]
    # Being fixed doesn't push an urgent bug down; it stays red.
    assert [i.issue.number for i in bugs.issues] == [1, 2]
    lines, _ = browser.body(120)
    held = next(line for line in lines if line.text.startswith("  #1 "))
    assert "fix-in-progress" in held.text and held.kind == "accent"
    # The question is in the last group, so every issue is listed.
    assert other.title == "OTHER ISSUES"
    assert [i.issue.number for i in other.issues] == [3]


def test_n_and_p_step_through_a_group(browser: browse.Browser) -> None:
    browser.key("3")
    browser.key("j")
    browser.key("enter")
    first = _text(browser.header())

    browser.key("n")
    second = _text(browser.header())
    browser.key("p")

    assert first.endswith("(1 of 3)") and second.endswith("(2 of 3)")
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

    assert "TAGS       ACT IDLE  FLAG  STATUS     SUMMARY" in header
    assert all(len(line.text) <= 79 for line in lines)
    row = next(line.text for line in lines if "#1 " in line.text)
    assert row.startswith("● #1     urgent")
    # 100 columns and up get the full labels.
    wide = next(
        line.text for line in browser.body(120)[0] if "#1 " in line.text
    )
    assert "bug: severe, urgent" in wide


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


def test_a_hot_and_stale_issue_gets_one_row_that_says_both(
    isolated: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(
        monkeypatch,
        FakeGitHub(
            [[issue(1, opened_days_ago=200, reactions=24), issue(2)]],
            total_open=2,
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

    browser.key("j")
    browser.key("enter")
    text = _text(browser.body(120)[0])

    opened = (NOW - datetime.timedelta(days=200)).astimezone()
    month = f"{opened:%B}" if opened.year == NOW.year else f"{opened:%B %Y}"
    assert (
        "  Hot           24 reactions and comments in total, but no comment "
        f"from a person since {month}"
    ) in text
    assert "Stale" not in text
    assert "  Maintainers   none\n" in text
    assert "no maintainer reply" in text
