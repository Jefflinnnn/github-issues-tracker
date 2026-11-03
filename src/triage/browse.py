"""``triage browse``: look through the last run's issues, one at a time.

The summary lists the same groups, but only their first rows, and counts
Hot and Stale without listing them. The browser reads the last run back
from storage, with no fetching and no model requests, and has two levels:

- the list: every group, expandable to every issue, Hot and Stale
  included, with changed issues marked ●;
- an issue's card: its title, the model's brief on what the issue is and
  the leads so far, then its info: tags one per row with the model's
  reasons, facts worked out from its comments (who spoke last, whether a
  maintainer replied), what changed, and why it's in its group.

``o`` opens the issue on GitHub and ``y`` copies its link. ``Browser``
holds the state and turns keys into screen lines, so it can be tested
without a terminal; ``run`` draws it with ``curses``.
"""

import dataclasses
import datetime
import os
import shutil
import signal
import subprocess
import sys
import textwrap
import webbrowser
from collections.abc import Callable
from typing import TYPE_CHECKING

from triage import report
from triage.model import prompt
from triage.pipeline import run as pipeline
from triage.pipeline import transform

if TYPE_CHECKING:
    import curses

_FIELD = 12
_MAINTAINERS = {"OWNER", "MEMBER", "COLLABORATOR"}
_COPIERS = (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"])
# Below this width, rows use short labels and status names, so an 80-column
# window, the default for a new macOS Terminal window, keeps room for the
# summary.
WIDE = 100
# Lines that space and b scroll when the screen size isn't known.
PAGE = 10
# Issues an open group shows at once; ] and [ page through the rest.
GROUP_PAGE = 10
# Cards wrap at this width, so long text stays readable in a wide window.
CARD_WIDTH = 100


@dataclasses.dataclass(frozen=True)
class _Columns:
    """Widths of a list row's columns, and its column header's names."""

    number: int
    label: int
    activity: int
    idle: int
    status: int
    activity_name: str

    @property
    def prefix(self) -> int:
        """Width of everything before the summary."""
        return (
            2
            + self.number
            + self.label
            + self.activity
            + self.idle
            + 2
            + self.status
        )


_WIDE_COLUMNS = _Columns(8, 21, 9, 6, 16, "ACTIVITY")
_NARROW_COLUMNS = _Columns(7, 9, 5, 5, 11, "ACT")
# Short status names for narrow rows.
_SHORT_STATUS = {
    prompt.Subtag.FIX_FAILED: "failed",
    prompt.Subtag.FIX_RELEASED: "released",
    prompt.Subtag.FIX_CONFIRMED: "confirmed",
    prompt.Subtag.FIX_IN_PROGRESS: "fixing",
    prompt.Subtag.NEEDS_INFO: "needs info",
    prompt.Subtag.DUPLICATE: "duplicate",
}

HELP = {
    "list": (
        "↑↓ move  space expand  enter card  ] [ page  1-9 jump  "
        "o open gh issue  q quit"
    ),
    "card": "n/p next/prev  ↑↓ space b scroll  o open gh issue  y copy  ← back",
}


@dataclasses.dataclass(frozen=True)
class Line:
    """One screen line.

    Attributes:
        text: What it says.
        kind: How to draw it: ``normal``, ``bold``, ``dim`` or ``accent``.
    """

    text: str
    kind: str = "normal"


@dataclasses.dataclass(frozen=True)
class _Entry:
    """A selectable line of the list: a heading, an issue or a page line."""

    group: int
    item: transform.FlaggedIssue | None = None
    page_line: bool = False


class Browser:
    """The browser's state, and how keys change it."""

    def __init__(
        self,
        result: pipeline.RunResult,
        now: datetime.datetime,
        opener: Callable[[str], bool] | None = None,
        copier: Callable[[str], bool] | None = None,
    ):
        """Builds the groups from a run's result.

        Args:
            result: The run to browse.
            now: The current time, for ages.
            opener: Opens a link; returns whether it could. Defaults to
                ``open_url``.
            copier: Copies text to the clipboard; returns whether it could.
                Defaults to the system's clipboard command.
        """
        self.result = result
        self.now = now
        self.opener = opener or open_url
        self.copier = copier or copy_to_clipboard
        self.report = report.Report(
            result, report.MAX_WIDTH, report.PLAIN, True, True
        )
        self.groups = [g for g in self.report.groups(None) if g.total]
        # Changes are measured from the run before the last, the same
        # baseline as the summary's "since last run".
        self.since = result.previous_run_at
        self.changed = {
            item.issue.id for item in result.flagged if self._is_changed(item)
        }
        # Opening an issue's card clears its mark, for this session only.
        self.unread = set(self.changed)
        self.expanded = {0} if self.groups else set()
        # Which page of its issues each open group shows.
        self.pages: dict[int, int] = {}
        self.cursor = 0
        self.view = "list"
        self.viewing: tuple[int, int] = (0, 0)
        self.scroll = 0
        self.message = ""
        # Lines space and b scroll; the screen sets it to its height.
        self.page = PAGE

    # What the screen shows.

    def header(self, width: int = WIDE) -> list[Line]:
        """Returns the fixed lines at the top of the current view.

        Args:
            width: The screen's width.

        Returns:
            The lines.
        """
        result = self.result
        if self.view == "list":
            open_count = max(len(result.flagged), result.open_on_github)
            lines = [
                Line(f"{result.name_with_owner} · {open_count} open", "bold")
            ]
            since = self.since_line()
            if since:
                lines.append(Line(since))
            lines.append(Line(self._column_header(width), "bold"))
            return lines
        group_index, position = self.viewing
        group = self.groups[group_index]
        item = group.issues[position]
        crumb = (
            f"{result.name_with_owner} › {group.title.capitalize()} › "
            f"#{item.issue.number}"
        )
        return [
            Line(f"{crumb}  ·  {position + 1} of {len(group.issues)}", "bold")
        ]

    def since_line(self) -> str:
        """Says what changed since the baseline; empty when nothing did."""
        if self.since is None:
            return ""
        new = sum(
            item.issue.created_at >= self.since for item in self.result.flagged
        )
        commented = len(self.changed) - new
        closed = (
            self.result.since_last_run.closed
            if self.result.since_last_run
            else 0
        )
        parts = [
            f"{count} {name}"
            for count, name in (
                (new, "new"),
                (closed, "closed"),
                (commented, "with new comments"),
            )
            if count
        ]
        if not parts:
            return ""
        return f"Since {self._day(self.since)}: " + " · ".join(parts)

    def footer(self) -> list[Line]:
        """Returns the fixed lines at the bottom of the current view."""
        lines = []
        if self.view == "list":
            tagged = sum(
                self.report.tagging(item) is not None
                for item in self.result.flagged
            )
            scanned = len(self.result.flagged)
            if tagged < scanned:
                lines.append(
                    Line(f"{tagged} of {scanned} scanned tagged", "dim")
                )
        lines.append(Line(self.message or HELP[self.view], "dim"))
        return lines

    def body(self, width: int) -> tuple[list[Line], int]:
        """Returns the scrolling lines and which one the cursor is on.

        Args:
            width: The screen's width.

        Returns:
            The lines, and the cursor's line (-1 outside the list).
        """
        if self.view == "card":
            return self._card(width)[self.scroll :], -1
        entries = self.entries()
        if not entries:
            return [
                Line(
                    "Nothing to list yet. Run triage summary to tag issues.",
                    "dim",
                )
            ], -1
        self.cursor = min(self.cursor, len(entries) - 1)
        focused = entries[self.cursor].group
        lines = []
        for entry in entries:
            group = self.groups[entry.group]
            if entry.page_line:
                page = self.pages.get(entry.group, 0)
                pages = _page_count(len(group.issues))
                lines.append(
                    Line(
                        f"    page {page + 1} of {pages} · ] next, [ previous",
                        "dim",
                    )
                )
            elif entry.item is None:
                arrow = "▾" if entry.group in self.expanded else "▸"
                # Only the focused group says what puts issues in it.
                heading = self.report.heading(
                    group, described=entry.group == focused
                )
                lines.append(
                    Line(f"{entry.group + 1} {arrow} {heading}", "bold")
                )
            else:
                # Red only in "Bugs to fix", for the urgent ones.
                red = group.title == "BUGS TO FIX"
                lines.append(self._row(entry.item, width, red))
        return lines, self.cursor

    def entries(self) -> list[_Entry]:
        """Returns the list's selectable lines, in order."""
        result = []
        for index, group in enumerate(self.groups):
            result.append(_Entry(index))
            if index in self.expanded:
                start = self.pages.get(index, 0) * GROUP_PAGE
                shown = group.issues[start : start + GROUP_PAGE]
                result += [_Entry(index, item) for item in shown]
                if len(group.issues) > GROUP_PAGE:
                    result.append(_Entry(index, page_line=True))
        return result

    # Keys.

    def key(self, key: str) -> bool:
        """Handles one key.

        Args:
            key: A character, or ``up``, ``down``, ``left``, ``right``,
                ``enter`` or ``esc``.

        Returns:
            False when the browser should close.
        """
        self.message = ""
        if self.view == "card":
            return self._card_key(key)
        entries = self.entries()
        if key in ("q", "esc"):
            return False
        if not entries:
            return True
        current = entries[min(self.cursor, len(entries) - 1)]
        if key in ("j", "down"):
            self.cursor = min(self.cursor + 1, len(entries) - 1)
        elif key in ("k", "up"):
            self.cursor = max(self.cursor - 1, 0)
        elif key == "g":
            self.cursor = 0
        elif key == "G":
            self.cursor = len(entries) - 1
        elif key.isdigit() and 1 <= int(key) <= len(self.groups):
            self._jump(int(key) - 1)
        elif key in ("]", "["):
            self._turn_page(current.group, 1 if key == "]" else -1)
        elif current.page_line and key in ("enter", "right", "l", " "):
            self._turn_page(current.group, 1)
        elif key == "c":
            self._next_changed()
        elif current.item is None and key in (" ", "enter", "right", "l"):
            self.expanded ^= {current.group}
        elif current.item is None and key in ("left", "h"):
            self.expanded.discard(current.group)
        elif current.item is not None and key in ("enter", "right", "l"):
            group = self.groups[current.group]
            self._open_card(current.group, group.issues.index(current.item))
        elif current.item is not None and key in (" ", "left", "h"):
            self.expanded.discard(current.group)
            self._jump(current.group)
        elif current.item is not None and key in ("o", "y"):
            self._act(key, current.item)
        return True

    def _card_key(self, key: str) -> bool:
        group_index, position = self.viewing
        issues = self.groups[group_index].issues
        if key in ("q", "esc", "left", "h"):
            self.view = "list"
            self._point_at(group_index, issues[position])
        elif key == "n" and position + 1 < len(issues):
            self._open_card(group_index, position + 1)
        elif key == "p" and position > 0:
            self._open_card(group_index, position - 1)
        elif key in ("j", "down", " ", "k", "up", "b"):
            self._scroll(key)
        elif key in ("o", "y"):
            self._act(key, issues[position])
        return True

    def _scroll(self, key: str) -> None:
        """Scrolls a line with arrows or j/k, a page with space or b.

        Space and b stand in for Page Down and Page Up, which need Fn on a
        MacBook and which Terminal keeps for its own scrollback.
        """
        step = {"j": 1, "down": 1, "k": -1, "up": -1, " ": self.page}
        self.scroll = max(self.scroll + step.get(key, -self.page), 0)

    def _open_card(self, group_index: int, position: int) -> None:
        self.view = "card"
        self.viewing = (group_index, position)
        self.scroll = 0
        self.unread.discard(self.groups[group_index].issues[position].issue.id)

    def _jump(self, group: int) -> None:
        self.expanded.add(group)
        self.cursor = next(
            i
            for i, entry in enumerate(self.entries())
            if entry.group == group and entry.item is None
        )

    def _turn_page(self, group: int, step: int) -> None:
        """Shows the next or previous page of a group, wrapping around."""
        pages = _page_count(len(self.groups[group].issues))
        if group not in self.expanded or pages == 1:
            return
        self.pages[group] = (self.pages.get(group, 0) + step) % pages
        self._jump(group)
        self.cursor += 1

    def _point_at(self, group: int, item: transform.FlaggedIssue) -> None:
        self.expanded.add(group)
        position = self.groups[group].issues.index(item)
        self.pages[group] = position // GROUP_PAGE
        for index, entry in enumerate(self.entries()):
            if entry.group == group and entry.item is item:
                self.cursor = index
                return

    def _next_changed(self) -> None:
        """Moves to the next changed issue not opened yet, opening its group."""
        everything = [
            (index, item)
            for index, group in enumerate(self.groups)
            for item in group.issues
        ]
        entries = self.entries()
        current = entries[min(self.cursor, len(entries) - 1)]
        start = 0
        for position, (index, item) in enumerate(everything):
            if index == current.group and (
                current.item is None or item is current.item
            ):
                start = position + (current.item is not None)
                break
        ordered = everything[start:] + everything[:start]
        for index, item in ordered:
            if item.issue.id in self.unread:
                self._point_at(index, item)
                return
        self.message = "No more changed issues to read."

    def _act(self, key: str, item: transform.FlaggedIssue) -> None:
        number, url = item.issue.number, item.issue.url
        if key == "o":
            opened = self.opener(url)
            self.message = (
                f"Opened #{number} on GitHub" if opened else f"Open: {url}"
            )
        else:
            copied = self.copier(url)
            self.message = f"Copied {url}" if copied else f"Link: {url}"

    # The list.

    def _column_header(self, width: int) -> str:
        columns = _columns(width)
        return (
            " " * (2 + columns.number)
            + f"{'TAGS':<{columns.label}}"
            + f"{columns.activity_name:>{columns.activity}}"
            + f"{'IDLE':>{columns.idle}}  "
            + f"{'STATUS':<{columns.status}}SUMMARY"
        )

    def _row(
        self, item: transform.FlaggedIssue, width: int, red: bool = False
    ) -> Line:
        """One issue's row, with short labels below ``WIDE`` columns.

        With ``red``, an urgent bug's row is in the accent color.
        """
        columns = _columns(width)
        label, urgent = self.report.label(item)
        subtag = self.report.subtag(item)
        if columns is _NARROW_COLUMNS:
            label = self._short_label(item)
            status = _SHORT_STATUS[subtag] if subtag else ""
        else:
            status = subtag.value if subtag else ""
        mark = "● " if item.issue.id in self.unread else "  "
        headline = self.report.headline(item)
        room = max(width - 1 - columns.prefix, 10)
        if len(headline) > room:
            headline = headline[: room - 1] + "…"
        text = (
            mark
            + f"{'#' + str(item.issue.number):<{columns.number}}"
            + f"{label:<{columns.label}}"
            + f"{item.engagement:>{columns.activity}}"
            + f"{str(item.idle_days) + 'd':>{columns.idle}}  "
            + f"{status:<{columns.status}}"
            + headline
        )
        return Line(text, "accent" if red and urgent else "normal")

    def _short_label(self, item: transform.FlaggedIssue) -> str:
        """Names an issue's tags in one word, for narrow rows.

        A bug reads ``urgent``, ``severe`` or ``bug``, and a feature its
        MoSCoW word; the card shows the rest.
        """
        tagging = self.report.tagging(item)
        if tagging is None:
            return "untagged"
        if tagging.tag is prompt.Tag.BUG:
            if tagging.priority is prompt.Level.HIGH:
                return "urgent"
            return "severe" if tagging.severity is prompt.Level.HIGH else "bug"
        if tagging.tag is prompt.Tag.FEATURE and tagging.moscow:
            word = tagging.moscow.value.split("-")[0]
            return word + (" ↑" if item.issue.id in self.result.raised else "")
        return str(tagging.tag.value)

    # The card.

    def _card(self, width: int) -> list[Line]:
        """One issue: its title, the model's brief, then its info rows."""
        group_index, position = self.viewing
        group = self.groups[group_index]
        item = group.issues[position]
        issue = item.issue
        tagging = self.report.tagging(item)
        facts = _Facts(issue, self.now)
        room = min(width, CARD_WIDTH)
        lines: list[Line] = []

        def field(
            name: str, text: str, kind: str = "normal", indent: int = 2
        ) -> None:
            wrap = max(room - indent - _FIELD - 1, 30)
            pad = " " * indent
            for index, part in enumerate(textwrap.wrap(text, wrap) or [""]):
                label = name if index == 0 else ""
                lines.append(Line(f"{pad}{label:<{_FIELD}}{part}", kind))

        field("Title", issue.title, "bold", indent=0)
        if tagging is not None and tagging.brief:
            field("Summary", tagging.brief, indent=0)
        lines += [Line(""), Line("Info:", "bold")]
        if tagging is None:
            field("Type", "not tagged yet")
        else:
            field("Type", tagging.tag.value)
            if tagging.tag is prompt.Tag.BUG:
                severe = tagging.severity is prompt.Level.HIGH
                urgent = tagging.priority is prompt.Level.HIGH
                field("Severe", _level(severe, tagging.severity_reason))
                field(
                    "Urgent",
                    _level(urgent, tagging.priority_reason),
                    "accent" if urgent else "normal",
                )
            elif tagging.tag is prompt.Tag.FEATURE and tagging.moscow:
                raised = issue.id in self.result.raised
                field(
                    "Priority",
                    tagging.moscow.value
                    + (", raised by demand" if raised else "")
                    + f" · {tagging.rationale}",
                )
            else:
                field("Reason", tagging.rationale)
            if tagging.subtag:
                field("Status", tagging.subtag.value)
        opened = issue.created_at
        field("Opened", f"{_ago(self.now - opened)} ({opened:%Y-%m-%d})")
        field(
            "Activity",
            " · ".join(
                [
                    _count(issue.reaction_count, "reaction"),
                    _count(transform.comments_by_people(issue), "comment"),
                    _count(facts.people, "person", "people"),
                ]
            ),
        )
        field("Last", facts.last)
        field("Maintainers", facts.maintainers)
        if self.since is not None and issue.id in self.changed:
            field(
                f"Since {self._day(self.since)}",
                self._since_text(item, tagging),
            )
        field("Why here", self._why(group, item, tagging, facts))
        return lines

    def _since_text(
        self, item: transform.FlaggedIssue, tagging: prompt.Tagging | None
    ) -> str:
        """Says what changed since the baseline, from the comments."""
        assert self.since is not None
        issue = item.issue
        if issue.created_at >= self.since:
            parts = ["opened"]
        else:
            new = [
                c
                for c in transform.human_comments(issue)
                if c.created_at >= self.since
            ]
            parts = [_count(len(new), "new comment")]
        if tagging is not None and tagging.change_note:
            parts.append(tagging.change_note)
        return " · ".join(parts)

    def _why(
        self,
        group: report.Group,
        item: transform.FlaggedIssue,
        tagging: prompt.Tagging | None,
        facts: "_Facts",
    ) -> str:
        """Says which rule put the issue in its group, from the rule itself."""
        subtag = tagging.subtag if tagging else None
        if group.title == "FIX DIDN'T WORK":
            return "fix-failed · a released fix, still reported"
        if group.title == "BUGS TO FIX":
            parts = [
                word
                for word, on in (
                    (
                        "urgent",
                        tagging and tagging.priority is prompt.Level.HIGH,
                    ),
                    (
                        "severe",
                        tagging and tagging.severity is prompt.Level.HIGH,
                    ),
                )
                if on
            ] or ["bug"]
            return " · ".join([*parts, "no fix status", facts.maintainers])
        if group.title == "MOST-WANTED FEATURES":
            parts = ["must-have"]
            if item.issue.id in self.result.raised:
                parts.append(
                    f"raised by demand ({transform.demand(item.issue)})"
                )
            return " · ".join([*parts, "not released or duplicated"])
        if group.title == "READY TO CLOSE":
            if subtag is prompt.Subtag.FIX_CONFIRMED:
                return "fix-confirmed · someone says the released fix works"
            if subtag is prompt.Subtag.DUPLICATE:
                return "duplicate · points to another issue"
            return f"needs-info · no answer in {item.idle_days} days"
        if group.title == "ASK TO CONFIRM":
            return "fix-released · nobody has said it works"
        if group.title == "HOT":
            return f"among the busiest tenth · activity {item.engagement}"
        return f"no comment from a person in {item.idle_days} days"

    # Helpers.

    def _is_changed(self, item: transform.FlaggedIssue) -> bool:
        """Says whether an issue is new, or has comments from people, since."""
        if self.since is None:
            return False
        issue = item.issue
        return issue.created_at >= self.since or any(
            c.created_at >= self.since for c in transform.human_comments(issue)
        )

    def _day(self, moment: datetime.datetime) -> str:
        """Names a day briefly: ``Mon`` within a week, else ``Oct 26``."""
        local = moment.astimezone()
        if (self.now - moment).days < 7:
            return f"{local:%a}"
        return f"{local:%b} {local.day}"


class _Facts:
    """What an issue's comments say, worked out without the model.

    Attributes:
        humans: Fetched comments from people, oldest first.
        people: Distinct people in the thread, the opener included.
        last: Who commented last and when, e.g. ``7d ago, by the
            reporter``.
        maintainers: Whether a maintainer replied, e.g. ``no maintainer
            reply``.
    """

    def __init__(self, issue: transform.Issue, now: datetime.datetime):
        self.humans = transform.human_comments(issue)
        self.people = (
            len({issue.author, *(c.author for c in self.humans)} - {None}) or 1
        )
        if self.humans:
            latest = self.humans[-1]
            self.last = (
                f"{_ago(now - latest.created_at)}, by {_role(latest, issue)}"
            )
        else:
            self.last = "no comments from people yet"
        replies = [
            c
            for c in self.humans
            if c.author_association in _MAINTAINERS and c.author != issue.author
        ]
        complete = len(issue.comments) >= issue.comment_count
        if replies:
            self.maintainers = f"replied {_ago(now - replies[-1].created_at)}"
        elif complete:
            self.maintainers = "no maintainer reply"
        else:
            self.maintainers = (
                f"no maintainer reply in the latest {len(issue.comments)} "
                "comments"
            )


def _level(high: bool, reason: str | None) -> str:
    """Returns e.g. ``yes · crashes on every launch``."""
    answer = "yes" if high else "no"
    return f"{answer} · {reason}" if reason else answer


def _role(comment: transform.Comment, issue: transform.Issue) -> str:
    if comment.author == issue.author:
        return "the reporter"
    if comment.author_association in _MAINTAINERS:
        return "a maintainer"
    return "another user"


def _ago(delta: datetime.timedelta) -> str:
    """Says how long ago, e.g. ``today``, ``7d ago`` or ``5 months ago``."""
    days = delta.days
    if days < 1:
        return "today"
    if days < 60:
        return f"{days}d ago"
    if days < 730:
        return f"{days // 30} months ago"
    return f"{days // 365} years ago"


def _count(number: int, noun: str, plural: str = "") -> str:
    """Returns e.g. ``1 reaction`` or ``6 comments``."""
    return f"{number} {noun if number == 1 else plural or noun + 's'}"


def _page_count(issues: int) -> int:
    """Returns how many pages a group's issues fill."""
    return max(1, -(-issues // GROUP_PAGE))


def _columns(width: int) -> _Columns:
    """Returns the row layout for a screen width."""
    return _WIDE_COLUMNS if width >= WIDE else _NARROW_COLUMNS


def open_url(url: str) -> bool:
    """Opens a link in the web browser: with ``open`` on macOS.

    Args:
        url: The link.

    Returns:
        Whether it could.
    """
    if sys.platform == "darwin" and shutil.which("open"):
        try:
            subprocess.run(["open", url], check=True)
        except (OSError, subprocess.CalledProcessError):
            return False
        return True
    return webbrowser.open(url)


def copy_to_clipboard(text: str) -> bool:
    """Copies text with the system's clipboard command, if there is one.

    Args:
        text: What to copy.

    Returns:
        Whether it was copied.
    """
    for command in _COPIERS:
        if shutil.which(command[0]):
            try:
                subprocess.run(command, input=text, text=True, check=True)
            except (OSError, subprocess.CalledProcessError):
                continue
            return True
    return False


def supported() -> bool:
    """Says whether this system can draw the browser (has ``curses``)."""
    try:
        import curses  # noqa: F401
    except ImportError:
        return False
    return True


def run(browser: Browser) -> None:
    """Draws the browser full screen until it closes.

    Args:
        browser: The state to draw.
    """
    import curses

    keys = {
        curses.KEY_UP: "up",
        curses.KEY_DOWN: "down",
        curses.KEY_LEFT: "left",
        curses.KEY_RIGHT: "right",
        curses.KEY_ENTER: "enter",
        10: "enter",
        13: "enter",
        27: "esc",
    }

    def main(screen: "curses.window") -> None:
        curses.curs_set(0)
        # The 16-color palette only, so the screen follows the person's
        # Terminal profile; bold carries the emphasis without color.
        accent = curses.A_BOLD
        if curses.has_colors() and not os.environ.get("NO_COLOR"):
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_RED, -1)
            accent |= curses.color_pair(1)
        kinds = {
            "normal": curses.A_NORMAL,
            "bold": curses.A_BOLD,
            "dim": curses.A_DIM,
            "accent": accent,
        }
        top = 0
        while True:
            height, width = screen.getmaxyx()
            header = browser.header(width)
            footer = browser.footer()
            body, cursor = browser.body(width)
            room = max(height - len(header) - len(footer), 1)
            browser.page = max(room - 1, 1)
            if cursor >= 0:
                top = min(max(top, cursor - room + 1), cursor)
            else:
                top = 0
            screen.erase()
            row = 0
            for line in header:
                _put(screen, row, line.text, kinds[line.kind], width)
                row += 1
            for index, line in enumerate(body[top : top + room]):
                attr = kinds.get(line.kind, curses.A_NORMAL)
                if top + index == cursor:
                    attr |= curses.A_REVERSE
                _put(screen, row + index, line.text, attr, width)
            for index, line in enumerate(footer):
                _put(
                    screen,
                    height - len(footer) + index,
                    line.text,
                    kinds[line.kind],
                    width,
                )
            screen.refresh()
            code = screen.getch()
            if code == curses.KEY_RESIZE:
                continue
            name = keys.get(code, chr(code) if 0 <= code < 256 else "")
            if not browser.key(name):
                return

    # Esc alone waits for the rest of a possible key sequence; shorten the
    # wait so Esc, kept as a second key for back, doesn't feel stuck.
    os.environ.setdefault("ESCDELAY", "25")

    def stop(signum: int, frame: object) -> None:
        raise SystemExit(128 + signum)

    # SIGTERM would kill the process before curses restores the screen;
    # raising instead lets curses.wrapper clean up first.
    previous = signal.signal(signal.SIGTERM, stop)
    try:
        curses.wrapper(main)
    finally:
        signal.signal(signal.SIGTERM, previous)


def _put(
    screen: "curses.window", row: int, text: str, attr: int, width: int
) -> None:
    """Writes one line, padded to the screen's width and cut to fit.

    Padding overwrites the whole line, which leaves no leftovers behind in
    terminals that redraw only part of it. Curses can't write the last
    cell, so the line stops one short.
    """
    import curses

    room = max(width - 1, 0)
    try:
        screen.addnstr(row, 0, text.ljust(room), room, attr)
    except curses.error:
        pass
