"""An arrow-key selection menu for the terminal, using only the stdlib.

Up/Down (or k/j) move, Enter picks, a number picks that option directly,
Left (or h) goes back, Esc or q cancel, and Ctrl-C interrupts as usual.
``read_line`` reads a typed answer the same way, so Left on an empty line
goes back there too. It works in macOS and Linux terminals (``termios``)
and in the Windows console (``msvcrt``); where neither is there, or input
isn't a terminal, the caller should use ``input`` instead.
"""

import codecs
import contextlib
import functools
import os
import select
import sys
from collections.abc import Callable
from collections.abc import Iterator
from collections.abc import Sequence

UP = "up"
DOWN = "down"
LEFT = "left"
RIGHT = "right"
ENTER = "enter"
BACKSPACE = "backspace"
CANCEL = "cancel"

_HIGHLIGHT = "\x1b[7m"
_RESET = "\x1b[0m"
# Seconds to wait after Esc for the rest of an arrow key's sequence.
_ESC_WAIT = 0.05


class CancelledError(Exception):
    """The user left the menu without picking."""


class BackError(Exception):
    """The user asked to go back to the previous question."""


def supported() -> bool:
    """Says whether this terminal can run the arrow-key menu."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    if sys.platform == "win32":
        return enable_ansi()
    try:
        import termios  # noqa: F401
        import tty  # noqa: F401
    except ImportError:
        return False
    return True


@functools.cache
def enable_ansi() -> bool:
    """Lets the Windows console draw ANSI escape codes, once per process.

    The menu, the second screen and the summary's colors use them. Windows
    Terminal draws them already; the classic console needs this switch.

    Returns:
        Whether escape codes work: always on macOS and Linux; on Windows,
        whether the console accepted the switch.
    """
    if sys.platform != "win32":
        return True
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.GetStdHandle(-11)  # Standard output.
    mode = wintypes.DWORD()
    if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        return False
    virtual_terminal_processing = 0x0004
    return bool(
        kernel32.SetConsoleMode(
            handle, mode.value | virtual_terminal_processing
        )
    )


def choose(
    title: str,
    options: Sequence[str],
    default: int = 0,
    read_key: Callable[[], str] | None = None,
    write: Callable[[str], None] | None = None,
) -> int:
    """Shows a menu and returns the index of the option picked.

    Args:
        title: The question above the options.
        options: One line per option; each should fit the terminal's
            width, or redrawing moves up too few lines.
        default: The option highlighted first.
        read_key: Returns the next key as ``up``, ``down``, ``enter``,
            ``cancel`` or a character; defaults to reading the terminal.
        write: Writes text to the screen; defaults to stdout.

    Returns:
        The picked option's index.

    Raises:
        CancelledError: The user pressed Esc or q.
        BackError: The user pressed Left or h.
    """
    write = write or _write_stdout
    if read_key is None:
        # One mode switch for the whole menu: switching per key can drop
        # keys that arrive in between, such as a held-down arrow.
        with _terminal_keys(utf8=False) as read:
            return _run(title, options, default, read, write)
    return _run(title, options, default, read_key, write)


def _run(
    title: str,
    options: Sequence[str],
    default: int,
    read_key: Callable[[], str],
    write: Callable[[str], None],
) -> int:
    """Draws the menu and handles keys until one picks or cancels."""
    current = default
    write(f"{title}\n")
    _draw(options, current, write)
    while True:
        key = read_key()
        if key == ENTER:
            return current
        if key in (CANCEL, "q"):
            raise CancelledError
        if key in (LEFT, "h"):
            raise BackError
        if key in (UP, "k"):
            current = (current - 1) % len(options)
        elif key in (DOWN, "j"):
            current = (current + 1) % len(options)
        elif key.isdigit() and 1 <= int(key) <= len(options):
            current = int(key) - 1
            _redraw(options, current, write)
            return current
        else:
            continue
        _redraw(options, current, write)


def read_line(
    prompt: str,
    secret: bool = False,
    read_key: Callable[[], str] | None = None,
    write: Callable[[str], None] | None = None,
) -> str:
    """Reads one typed line, like ``input``, but Left can go back.

    Backspace deletes; other arrow keys are ignored, since there is no
    cursor to move.

    Args:
        prompt: Shown before the answer.
        secret: Don't show what is typed, like ``getpass``.
        read_key: Returns the next key as ``enter``, ``left``,
            ``backspace``, ``cancel`` or a character; defaults to reading
            the terminal.
        write: Writes text to the screen; defaults to stdout.

    Returns:
        The line, without the newline.

    Raises:
        BackError: The user pressed Left on an empty line.
        EOFError: The user pressed Ctrl-D on an empty line.
    """
    write = write or _write_stdout
    if read_key is None:
        with _terminal_keys(utf8=True) as read:
            return _line(prompt, secret, read, write)
    return _line(prompt, secret, read_key, write)


def _line(
    prompt: str,
    secret: bool,
    read_key: Callable[[], str],
    write: Callable[[str], None],
) -> str:
    """Collects typed characters until Enter."""
    typed: list[str] = []
    write(prompt)
    while True:
        key = read_key()
        if key == ENTER:
            write("\n")
            return "".join(typed)
        if key == LEFT and not typed:
            write("\n")
            raise BackError
        if key == CANCEL and not typed:
            write("\n")
            raise EOFError
        if key == BACKSPACE:
            if typed:
                typed.pop()
                if not secret:
                    write("\b \b")
        elif len(key) == 1 and key.isprintable():
            typed.append(key)
            if not secret:
                write(key)


class Screen:
    """A second terminal screen, as pagers and editors use.

    Entering it hides what was on the terminal; leaving it brings that back
    exactly as it was. Clearing it wipes the previous view, so steps don't
    pile up.
    """

    def __init__(self, write: Callable[[str], None] | None = None) -> None:
        """Prepares the screen; nothing is written until it's entered.

        Args:
            write: Writes text to the terminal; defaults to stdout.
        """
        self._write = write or _write_stdout

    def enter(self) -> None:
        """Switches to the second screen, cleared."""
        self._write("\x1b[?1049h\x1b[2J\x1b[H")

    def clear(self) -> None:
        """Wipes the second screen and returns to its top line."""
        self._write("\x1b[2J\x1b[H")

    def leave(self) -> None:
        """Returns to the screen as it was before ``enter``."""
        self._write("\x1b[?1049l")


def _draw(
    options: Sequence[str], current: int, write: Callable[[str], None]
) -> None:
    """Writes the options, marking and highlighting the current one."""
    for index, option in enumerate(options):
        if index == current:
            write(f"  {_HIGHLIGHT}> {option}{_RESET}\n")
        else:
            write(f"    {option}\n")


def _redraw(
    options: Sequence[str], current: int, write: Callable[[str], None]
) -> None:
    """Moves back up over the options and draws them again."""
    write(f"\x1b[{len(options)}F\x1b[J")
    _draw(options, current, write)


def _write_stdout(text: str) -> None:
    """Writes to stdout right away."""
    sys.stdout.write(text)
    sys.stdout.flush()


@contextlib.contextmanager
def _terminal_keys(utf8: bool) -> Iterator[Callable[[], str]]:
    """Reads keys from the terminal, one at a time, until the block ends.

    Args:
        utf8: Decode characters outside ASCII, for typed answers.

    Yields:
        A function that returns the next key's name or character.
    """
    if sys.platform == "win32":
        import msvcrt

        # The Windows console delivers keys unechoed as they come; no mode
        # to switch.
        yield lambda: windows_key(msvcrt.getwch)
        return
    with _keys_unbuffered() as descriptor:
        decoder = (
            codecs.getincrementaldecoder("utf-8")(errors="ignore")
            if utf8
            else None
        )
        yield lambda: _read_key(descriptor, decoder)


def windows_key(read: Callable[[], str]) -> str:
    """Reads one keypress from the Windows console.

    Args:
        read: Returns the console's next character, like ``msvcrt.getwch``.
            An arrow key arrives as two: a prefix character, then a letter.

    Returns:
        The key's name, such as ``up`` or ``enter``, or the character.

    Raises:
        KeyboardInterrupt: Ctrl-C, which the console passes on as a key.
    """
    while True:
        key = read()
        if key in ("\x00", "\xe0"):
            name = _WINDOWS_ARROWS.get(read())
            if name:
                return name
            continue  # Another special key, such as F1: ignored.
        if key == "\x03":
            raise KeyboardInterrupt
        if key == "\r":
            return ENTER
        if key == "\x08":
            return BACKSPACE
        if key in ("\x1b", "\x1a"):  # Esc, or Ctrl-Z for end of input.
            return CANCEL
        return key


@contextlib.contextmanager
def _keys_unbuffered() -> Iterator[int]:
    """Delivers keys one at a time, unechoed, until the block ends.

    Cbreak mode, not raw: output still turns newlines into line breaks, and
    Ctrl-C still interrupts.
    """
    if sys.platform == "win32":
        raise OSError("Windows reads keys with msvcrt, not termios.")
    import termios
    import tty

    descriptor = sys.stdin.fileno()
    saved = termios.tcgetattr(descriptor)
    try:
        tty.setcbreak(descriptor)
        yield descriptor
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, saved)


def _read_key(
    descriptor: int, decoder: codecs.IncrementalDecoder | None = None
) -> str:
    """Reads one keypress from a terminal in cbreak mode."""
    first = os.read(descriptor, 1)
    if first == b"\x1b":
        # Arrow keys arrive as Esc [ A-D at once; a lone Esc cancels.
        ready, _, _ = select.select([descriptor], [], [], _ESC_WAIT)
        rest = os.read(descriptor, 2) if ready else b""
        return _ARROWS.get(rest, CANCEL)
    if first in (b"\r", b"\n"):
        return ENTER
    if first in (b"\x7f", b"\x08"):
        return BACKSPACE
    if first == b"\x04":
        return CANCEL
    if decoder is None:
        return first.decode(errors="ignore")
    # A character outside ASCII spans several bytes; read until it's whole.
    text = decoder.decode(first)
    while not text:
        text = decoder.decode(os.read(descriptor, 1))
    return text


_WINDOWS_ARROWS = {"H": UP, "P": DOWN, "M": RIGHT, "K": LEFT}
_ARROWS = {
    b"[A": UP,
    b"[B": DOWN,
    b"[C": RIGHT,
    b"[D": LEFT,
    b"OA": UP,
    b"OB": DOWN,
    b"OC": RIGHT,
    b"OD": LEFT,
}
