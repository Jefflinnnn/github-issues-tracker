from typing import Any

from conftest import NOW
from conftest import comment
from conftest import issue

from triage.pipeline import transform


def _flags(*nodes: dict[str, Any]) -> dict[int, list[str]]:
    issues = [transform.to_issue(node) for node in nodes]
    return {
        item.issue.number: [flag.value for flag in item.flags]
        for item in transform.flag_issues(issues, NOW)
    }


def test_deleted_accounts_parse_as_no_author() -> None:
    node = issue(1, comments=[comment(None, 0.5)])
    node["author"] = None

    parsed = transform.to_issue(node)

    assert parsed.author is None
    assert parsed.comments[0].author is None
    assert parsed.created_at.tzinfo is not None


def test_bot_comments_dont_reset_the_stale_clock() -> None:
    flags = _flags(
        issue(1, opened_days_ago=90, comments=[comment("stalebot", 1, "Bot")]),
        issue(2, opened_days_ago=90, comments=[comment("helper", 1)]),
    )

    assert "stale" in flags[1]
    assert "stale" not in flags[2]


def test_hot_is_the_busiest_tenth_with_a_floor() -> None:
    busy = _flags(*[issue(n, reactions=n) for n in range(1, 21)])
    quiet = _flags(issue(1, reactions=2), issue(2, reactions=1))

    assert [n for n, flags in busy.items() if "hot" in flags] == [19, 20]
    assert not any("hot" in flags for flags in quiet.values())
