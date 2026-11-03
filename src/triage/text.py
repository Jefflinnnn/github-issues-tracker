"""Fits text into the room it has."""


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
