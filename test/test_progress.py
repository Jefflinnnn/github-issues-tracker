import io
import logging

from triage import progress

ERASE = "\r\x1b[K"


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_a_bar_redraws_one_line_and_clear_erases_it() -> None:
    screen = Terminal()
    status = progress.Status(screen)

    status.bar("Tagging", 0, 4)
    status.bar("Tagging", 3, 4)
    status.clear()

    drawn = screen.getvalue().split(ERASE)
    assert drawn[1] == "Tagging  [" + "░" * 24 + "] 0/4"
    assert drawn[2] == "Tagging  [" + "█" * 18 + "░" * 6 + "] 3/4"
    assert drawn[3:] == [""]
    assert "\n" not in screen.getvalue()


def test_nothing_is_drawn_when_stderr_is_not_a_terminal() -> None:
    piped = io.StringIO()
    status = progress.Status(piped)

    status.show("Fetching…")
    status.clear()

    assert piped.getvalue() == ""


def test_a_log_line_erases_the_status_and_redraws_it_after() -> None:
    screen = Terminal()
    status = progress.Status(screen)
    handler = progress.LogHandler(status, screen)
    logger = logging.getLogger("test_progress")
    logger.addHandler(handler)
    try:
        status.show("Fetching…")
        logger.warning("slow down")
    finally:
        logger.removeHandler(handler)

    assert screen.getvalue().endswith(
        f"{ERASE}Fetching…{ERASE}slow down\n{ERASE}Fetching…"
    )
    assert handler.printed == 1
