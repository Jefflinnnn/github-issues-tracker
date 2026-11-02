import pytest

from triage import menu


def _choose(keys: list[str], default: int = 0) -> int:
    return menu.choose(
        "Pick one", ["a", "b", "c"], default, keys.pop, lambda _: None
    )


def test_arrows_move_and_wrap_then_enter_picks() -> None:
    # Keys are popped from the end: up from the first wraps to the last.
    assert _choose([menu.ENTER, menu.UP]) == 2
    assert _choose([menu.ENTER, menu.DOWN, "j"], default=0) == 2


def test_a_number_picks_directly() -> None:
    assert _choose(["2"]) == 1


def test_escape_cancels() -> None:
    with pytest.raises(menu.CancelledError):
        _choose([menu.CANCEL])


def _line(keys: list[str], secret: bool = False) -> tuple[str, str]:
    shown: list[str] = []
    keys.reverse()
    answer = menu.read_line("> ", secret, keys.pop, shown.append)
    return answer, "".join(shown)


def test_a_line_takes_typing_and_backspace() -> None:
    answer, shown = _line(["a", "b", menu.BACKSPACE, "c", menu.ENTER])

    assert answer == "ac"
    assert shown == "> ab\b \bc\n"


def test_a_secret_line_shows_nothing() -> None:
    answer, shown = _line(["s", "k", menu.ENTER], secret=True)

    assert answer == "sk"
    assert shown == "> \n"


def test_left_on_an_empty_line_goes_back() -> None:
    with pytest.raises(menu.BackError):
        _line([menu.LEFT])
    # With text typed, Left does nothing: there's no cursor to move.
    assert _line(["x", menu.LEFT, menu.ENTER])[0] == "x"
