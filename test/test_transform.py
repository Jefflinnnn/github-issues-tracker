import datetime
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


def test_bots_are_left_out_of_counts_even_without_the_bot_type() -> None:
    node = issue(
        1,
        reactions=2,
        comments=[
            comment("person", 3),
            comment("astral-automations-bot", 2),
            comment("dependabot[bot]", 1),
            comment("stale", 1, author_type="Bot"),
        ],
    )
    parsed = transform.to_issue(node)

    assert [c.author for c in transform.human_comments(parsed)] == ["person"]
    assert transform.comments_by_people(parsed) == 1
    assert transform.engagement(parsed) == 3
    # A login that merely ends in "bot" is a person.
    talbot = transform.to_issue(issue(2, comments=[comment("talbot", 1)]))
    assert transform.comments_by_people(talbot) == 1


def test_only_a_person_commenting_on_an_older_issue_counts_as_changed() -> None:
    since = NOW - datetime.timedelta(days=3)

    def changed(node: dict[str, Any]) -> bool:
        return transform.commented_since(transform.to_issue(node), since)

    assert changed(issue(1, opened_days_ago=9, comments=[comment("ann", 1)]))
    # A bot's comment, an older comment, or a new issue doesn't count.
    assert not changed(
        issue(2, opened_days_ago=9, comments=[comment("ci-bot", 1)])
    )
    assert not changed(
        issue(3, opened_days_ago=9, comments=[comment("ann", 5)])
    )
    assert not changed(
        issue(4, opened_days_ago=1, comments=[comment("ann", 0.5)])
    )
