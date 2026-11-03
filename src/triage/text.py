"""Fits text into the room it has, and tidies what the model writes."""

import re

# Separators the model sometimes uses to stack facts on one line.
_SEPARATORS = re.compile(r"\s*[·•|]\s*")
# A label the model sometimes repeats before its reason, e.g. "Severe:" or
# "Urgent because". A hyphen counts only with spaces, so "Low-level" stays.
_LEADING_LABEL = re.compile(
    r"^(?:severe|severity|urgent|urgency|priority|reason|rationale)"
    r"(?::\s*|\s+[-–—]\s+|\s+because\s+)",
    re.IGNORECASE,
)
_LIST_MARKER = re.compile(r"^(?:[-*•]|\d+[.)])\s+")


def cut(text: str, room: int) -> str:
    """Cuts text to fit a column, marking the cut with an ellipsis.

    Args:
        text: The text.
        room: Characters available; at least 10 are always kept.

    Returns:
        The text, cut if it was longer than ``room``.
    """
    room = max(room, 10)
    return text if len(text) <= room else text[: room - 1] + "…"


def cut_at_word(text: str, limit: int) -> str:
    """Cuts text to at most ``limit`` characters at a word, marking the cut.

    Args:
        text: The text.
        limit: Most characters to keep.

    Returns:
        The text, cut if it was longer than ``limit``.
    """
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


def tidy(text: str) -> str:
    """Cleans up a line the model wrote, for showing as it is.

    The prompt asks for plain sentences; this holds when a model drifts.

    Args:
        text: The model's text.

    Returns:
        The text without a list marker, a leading label or markdown
        emphasis; with ``·``, ``•`` and ``|`` turned into commas; and with
        runs of whitespace collapsed. The case stays as written, so names
        like ``uv`` keep theirs.
    """
    text = text.replace("**", "").replace("__", "")
    text = _LIST_MARKER.sub("", text.strip())
    text = _LEADING_LABEL.sub("", text)
    text = _SEPARATORS.sub(", ", text)
    return re.sub(r"\s+", " ", text).strip(" ,")
