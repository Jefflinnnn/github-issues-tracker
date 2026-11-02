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
    assert "(2 earlier comments not shown)" in text
    roles = [line for line in text.splitlines() if line.startswith("Comment")]
    assert roles == [
        "Comment by the opener:",
        "Comment by a maintainer:",
        "Comment by bot:",
    ]
