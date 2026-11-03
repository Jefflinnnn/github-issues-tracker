from triage import text


def test_tidy_turns_stacked_facts_into_one_plain_line() -> None:
    assert text.tidy("Urgent because many users · no workaround") == (
        "many users, no workaround"
    )
    assert text.tidy("- **Severe**:  data loss |  every run") == (
        "data loss, every run"
    )
    # A hyphenated word isn't a label, and case stays as written.
    assert (
        text.tidy("Low-level crash in uv  pip") == "Low-level crash in uv pip"
    )
    assert text.tidy(" · ") == ""
