import datetime

from conftest import NOW
from conftest import comment
from conftest import issue

from triage.model import prompt
from triage.pipeline import transform


def test_issue_is_fenced_cut_and_marks_who_wrote_what() -> None:
    node = issue(
        1,
        comments=[
            comment("opener", 2),
            comment("dev", 1, association="MEMBER"),
            comment("ci", 1, author_type="Bot"),
        ],
        total_comments=5,
    )
    node["body"] = "x" * (prompt.BODY_MAX_CHARS + 50)

    text = prompt.render(transform.to_issue(node))

    assert text.startswith("<issue>") and text.endswith("</issue>")
    assert "[cut]" in text
    # The model weighs priority by these, so it gets them as plain facts.
    assert (
        "Engagement: 0 reactions on the opening post, 4 comments from "
        "people, 1 other people commenting" in text
    )
    assert "(2 earlier comments not shown)" in text
    roles = [line for line in text.splitlines() if line.startswith("Comment")]
    assert roles == [
        "Comment by the opener:",
        "Comment by a maintainer:",
        "Comment by bot:",
    ]


def test_every_subtag_is_defined_and_in_the_schema() -> None:
    for subtag in prompt.Subtag:
        assert f"- {subtag.value}: " in prompt.SYSTEM
    assert prompt.SCHEMA["properties"]["subtag"]["enum"] == [
        subtag.value for subtag in prompt.Subtag
    ]
    assert "subtag" not in prompt.SCHEMA["required"]


def test_an_issue_from_before_the_last_run_is_marked_changed() -> None:
    node = issue(
        1,
        opened_days_ago=9,
        comments=[comment("maintainer", 5), comment("opener", 1)],
    )
    since = NOW - datetime.timedelta(days=2)

    marked = prompt.render(transform.to_issue(node), since)
    new_issue = prompt.render(
        transform.to_issue(issue(2, opened_days_ago=1)), since
    )

    assert "Changed since the last run: yes" in marked
    assert "Comment by another user:" in marked
    assert "Comment by the opener (new):" in marked
    assert "Changed since the last run" not in new_issue
