"""Shows what a run is doing on one terminal line that keeps redrawing.

The line goes to stderr and only when stderr is a terminal, so piped or
redirected output stays clean. Log lines printed during a run erase the
line first and draw it again after, so they don't mix with it.
"""

import contextlib
import logging
import shutil
import sys
from collections.abc import Iterator
from typing import TextIO

BAR_WIDTH = 24
_ERASE = "\r\x1b[K"


class Status:
    """One status line: a message or a progress bar."""

    def __init__(self, stream: TextIO | None = None, live: bool | None = None):
        """Prepares the line; nothing is drawn until ``show`` or ``bar``.

        Args:
            stream: Where to draw; defaults to stderr.
            live: Whether to draw at all; defaults to whether ``stream``
                is a terminal.
        """
        self._stream = stream or sys.stderr
        self._live = self._stream.isatty() if live is None else live
        self._text = ""

    def show(self, text: str) -> None:
        """Replaces the line with a message.

        Args:
            text: The message; cut to the terminal's width.
        """
        self._text = text
        self._draw()

    def bar(self, label: str, done: int, total: int) -> None:
        """Replaces the line with a progress bar, e.g. ``Tagging [███░░] 3/5``.

        Args:
            label: What is in progress.
            done: Steps finished.
            total: Steps in all.
        """
        filled = BAR_WIDTH * min(done, total) // total if total else BAR_WIDTH
        self.show(
            f"{label:<9}[{'█' * filled}{'░' * (BAR_WIDTH - filled)}] "
            f"{done}/{total}"
        )

    def clear(self) -> None:
        """Erases the line."""
        if self._live and self._text:
            self._write(_ERASE)
        self._text = ""

    @contextlib.contextmanager
    def paused(self) -> Iterator[None]:
        """Erases the line for the block, then draws it again."""
        if self._live and self._text:
            self._write(_ERASE)
        try:
            yield
        finally:
            self._draw()

    def _draw(self) -> None:
        if not self._live or not self._text:
            return
        room = shutil.get_terminal_size().columns - 1
        self._write(_ERASE + self._text[:room])

    def _write(self, text: str) -> None:
        self._stream.write(text)
        self._stream.flush()


class LogHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """Prints log lines around the status line and counts them.

    Attributes:
        printed: How many log lines it printed.
    """

    def __init__(self, status: Status, stream: TextIO | None = None):
        """Prints to ``stream`` (stderr by default), pausing ``status``.

        Args:
            status: The status line to keep log lines clear of.
            stream: Where log lines go; defaults to stderr.
        """
        super().__init__(stream or sys.stderr)
        self.status = status
        self.printed = 0

    def emit(self, record: logging.LogRecord) -> None:
        """Erases the status line, prints the record and redraws the line.

        Args:
            record: The log record.
        """
        with self.status.paused():
            super().emit(record)
        self.printed += 1
