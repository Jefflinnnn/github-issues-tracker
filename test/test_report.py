import io

from triage import report


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_a_terminal_gets_color_and_links() -> None:
    style = report.Style.for_stream(Terminal(), {"TERM": "xterm"})

    assert style.bold("x") == "\x1b[1mx\x1b[0m"
    assert style.link("#1", "https://example.com/1") == (
        "\x1b]8;;https://example.com/1\x1b\\#1\x1b]8;;\x1b\\"
    )


def test_no_color_keeps_links_and_a_pipe_gets_plain_text() -> None:
    no_color = report.Style.for_stream(Terminal(), {"NO_COLOR": "1"})
    piped = report.Style.for_stream(io.StringIO(), {})

    assert no_color.accent("x") == "x"
    assert no_color.links
    assert piped == report.Style()
    assert piped.link("#1", "https://example.com/1") == "#1"
