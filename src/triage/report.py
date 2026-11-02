"""Renders the summary and the run history as text.

The summary reads top down: what it covers, the tag counts, then the
issues that need a decision, in groups. Backlog counts (hot, stale,
waiting) and the run's cost close it as one-line footers. On a terminal
it uses bold, dim and one accent color for what needs action, and makes
issue numbers clickable; piped or redirected, it is plain text.
"""

import dataclasses
import datetime
import os
from collections.abc import Mapping
from collections.abc import Sequence
from typing import TextIO

from triage.model import prompt
from triage.pipeline import load
from triage.pipeline import run
from triage.pipeline import transform

# Issues listed per group; the rest are counted.
SECTION_LIMIT = 10
# Issue numbers named when replies were unusable.
SKIPPED_SHOWN = 5
# Days without an answer after which a needs-info issue is ready to close.
NEEDS_INFO_CLOSE_DAYS = 30
# Rows stay readable in a wide terminal.
MAX_WIDTH = 100
# Subtags that mean the issue is done or covered elsewhere.
_DONE = {
    prompt.Subtag.FIX_RELEASED,
    prompt.Subtag.FIX_CONFIRMED,
    prompt.Subtag.DUPLICATE,
}
_WAITING = {prompt.Subtag.FIX_IN_PROGRESS, prompt.Subtag.NEEDS_INFO}
# Groups whose rows all share one subtag.
_ONE_SUBTAG = {"FIX DIDN'T WORK", "ASK TO CONFIRM"}
# How an urgent bug that a subtag keeps out of "Bugs to fix first" is
# described in the line that replaces the empty group.
_HELD = {
    prompt.Subtag.FIX_FAILED: "with a failed fix",
    prompt.Subtag.FIX_RELEASED: "with a released fix",
    prompt.Subtag.FIX_CONFIRMED: "confirmed fixed",
    prompt.Subtag.FIX_IN_PROGRESS: "being fixed",
    prompt.Subtag.NEEDS_INFO: "waiting on the reporter",
    prompt.Subtag.DUPLICATE: "marked duplicate",
}
# Column widths of an issue row.
_NUMBER = 7
_LABEL = 21
_ACTIVITY = 9
_IDLE = 6
_PREFIX = 2 + _NUMBER + 1 + _LABEL + _ACTIVITY + _IDLE + 2
# Width of the tag name column in the tag table.
_TAG_NAME = 14


@dataclasses.dataclass(frozen=True)
class Style:
    """Terminal styling; the default is plain text.

    Attributes:
        color: Use bold, dim and the accent color.
        links: Make issue numbers clickable (OSC 8 hyperlinks).
    """

    color: bool = False
    links: bool = False

    @classmethod
    def for_stream(
        cls, stream: TextIO, environ: Mapping[str, str] = os.environ
    ) -> "Style":
        """Returns the styling a stream supports.

        Args:
            stream: Where the summary goes.
            environ: The environment; ``NO_COLOR`` turns color off.

        Returns:
            Color and links on a terminal, plain text otherwise.
        """
        terminal = stream.isatty() and environ.get("TERM") != "dumb"
        return cls(
            color=terminal and not environ.get("NO_COLOR"), links=terminal
        )

    def bold(self, text: str) -> str:
        """Returns text in bold."""
        return self._sgr("1", text)

    def dim(self, text: str) -> str:
        """Returns text dimmed, for labels and secondary detail."""
        return self._sgr("2", text)

    def accent(self, text: str) -> str:
        """Returns text in the accent color, for what needs action."""
        return self._sgr("1;31", text)

    def link(self, text: str, url: str) -> str:
        """Returns text that opens ``url`` when clicked."""
        if not self.links:
            return text
        return f"\x1b]8;;{url}\x1b\\{text}\x1b]8;;\x1b\\"

    def _sgr(self, code: str, text: str) -> str:
        if not self.color or not text:
            return text
        return f"\x1b[{code}m{text}\x1b[0m"


PLAIN = Style()


def summary(
    result: run.RunResult,
    width: int,
    style: Style = PLAIN,
    hot: bool = False,
    stale: bool = False,
) -> str:
    """Returns the Monday summary of a repository's open issues.

    Args:
        result: The run to summarize.
        width: Terminal width; rows are cut to fit, and to ``MAX_WIDTH``.
        style: How to style the text.
        hot: List hot issues, not just count them.
        stale: List stale issues, not just count them.

    Returns:
        The summary, ending with a newline.
    """
    return _Summary(result, min(width, MAX_WIDTH), style, hot, stale).render()


class _Summary:
    """Builds one summary; split into steps so each stays short."""

    def __init__(
        self,
        result: run.RunResult,
        width: int,
        style: Style,
        list_hot: bool,
        list_stale: bool,
    ):
        self.result = result
        self.width = width
        self.style = style
        self.list_hot = list_hot
        self.flagged = result.flagged
        # Spam is counted in the tag table but listed nowhere.
        self.listable = [
            item
            for item in result.flagged
            if (found := result.tags.get(item.issue.id)) is None
            or found.tag is not prompt.Tag.SPAM
        ]
        # A partial scan's counts cover only the scanned issues, and
        # staleness isn't shown: the newest issues are rarely stale.
        self.partial = len(self.flagged) < result.open_on_github
        self.list_stale = list_stale and not self.partial
        self.hot = sorted(
            self.having(transform.Flag.HOT), key=lambda i: -i.engagement
        )
        self.stale = (
            []
            if self.partial
            else sorted(
                self.having(transform.Flag.STALE), key=lambda i: -i.idle_days
            )
        )

    def tagging(self, item: transform.FlaggedIssue) -> prompt.Tagging | None:
        return self.result.tags.get(item.issue.id)

    def subtag(self, item: transform.FlaggedIssue) -> prompt.Subtag | None:
        found = self.tagging(item)
        return found.subtag if found else None

    def having(self, flag: transform.Flag) -> list[transform.FlaggedIssue]:
        return [item for item in self.listable if flag in item.flags]

    def render(self) -> str:
        s = self.style
        lines = [s.bold(self.result.name_with_owner), self._scope(), ""]
        lines += self._tag_table()
        lines += self._groups()
        lines += self._footer()
        return "\n".join(lines).rstrip("\n") + "\n"

    def _scope(self) -> str:
        """Says what the numbers cover, before any of them."""
        result = self.result
        scanned = len(self.flagged)
        parts = [f"{max(scanned, result.open_on_github)} open"]
        if self.partial:
            parts.append(
                f"scanned the newest {scanned} "
                f"({_share(scanned, result.open_on_github)})"
            )
        tagged = sum(self.tagging(item) is not None for item in self.flagged)
        if tagged < scanned:
            parts.append(f"{tagged} tagged ({_share(tagged, scanned)})")
        return " · ".join(parts)

    def _tag_table(self) -> list[str]:
        """Counts issues per main tag, with each tag's breakdown.

        The rows add up to the issues scanned. Issues not tagged yet are a
        pipeline state, not a label, so they sit dimmed below a rule.
        """
        s = self.style
        taggings = [self.tagging(item) for item in self.flagged]
        rows = []
        for tag in prompt.Tag:
            mine = [t for t in taggings if t is not None and t.tag is tag]
            if mine:
                rows.append((tag.value, len(mine), self._breakdown(tag, mine)))
        untagged = sum(t is None for t in taggings)
        if not rows and not untagged:
            return []
        lines = ["  " + s.dim(f"{'':<{_TAG_NAME}}{'count':>5}   breakdown")]
        for name, count, breakdown in rows:
            lines.append(
                f"  {name:<{_TAG_NAME}}{count:>5}   {breakdown}".rstrip()
            )
        if untagged:
            if rows:
                lines.append(s.dim("  " + "─" * (_TAG_NAME + 5)))
            lines.append(
                s.dim(f"  {'not yet tagged':<{_TAG_NAME}}{untagged:>5}")
            )
        return lines + [""]

    def _breakdown(self, tag: prompt.Tag, mine: list[prompt.Tagging]) -> str:
        """Splits a tag's count by its second-level field."""
        s = self.style
        if tag is prompt.Tag.BUG:
            urgent = sum(t.priority is prompt.Level.HIGH for t in mine)
            severe = sum(t.severity is prompt.Level.HIGH for t in mine)
            parts = []
            if urgent:
                parts.append(s.accent(f"{urgent} urgent"))
            if severe:
                parts.append(f"{severe} severe")
            return " · ".join(parts)
        if tag is prompt.Tag.FEATURE:
            # A closed set: showing every value shows whether the model
            # puts everything in one bucket.
            counts = {
                m: sum(t.moscow is m for t in mine) for m in prompt.Moscow
            }
            must = f"{counts[prompt.Moscow.MUST]} must"
            return " · ".join(
                [
                    s.accent(must) if counts[prompt.Moscow.MUST] else must,
                    f"{counts[prompt.Moscow.SHOULD]} should",
                    f"{counts[prompt.Moscow.COULD]} could",
                ]
            )
        if tag is prompt.Tag.SPAM:
            return s.dim("not listed")
        return ""

    def _groups(self) -> list[str]:
        """Lists issues that need a decision, each once, in its first group.

        Groups go from costliest to leave alone to cleanup. Hot and stale
        issues are listed only when asked for.
        """
        groups = [
            (
                "FIX DIDN'T WORK",
                "a released fix, still reported",
                sorted(
                    self._with(prompt.Subtag.FIX_FAILED),
                    key=lambda i: -i.engagement,
                ),
            ),
            ("BUGS TO FIX FIRST", "urgent, severe first", self._urgent_bugs()),
            (
                "MOST-WANTED FEATURES",
                "must-have, most demand first",
                self._wanted(),
            ),
            (
                "READY TO CLOSE",
                "fix confirmed, duplicate, or no answer in "
                f"{NEEDS_INFO_CLOSE_DAYS} days",
                sorted(self._closable(), key=lambda i: -i.idle_days),
            ),
            (
                "ASK TO CONFIRM",
                "fix released, nobody has confirmed it",
                sorted(
                    self._with(prompt.Subtag.FIX_RELEASED),
                    key=lambda i: -i.idle_days,
                ),
            ),
        ]
        if self.list_hot:
            groups.append(
                ("HOT", "busiest by reactions and comments", self.hot)
            )
        if self.list_stale:
            groups.append(
                (
                    "STALE",
                    f"no comment from a person in {transform.STALE_DAYS} days",
                    self.stale,
                )
            )
        lines: list[str] = []
        shown: set[str] = set()
        header_shown = False
        for title, description, items in groups:
            if title == "BUGS TO FIX FIRST" and not items:
                lines += [self._no_urgent_bugs(), ""]
                continue
            if not items:
                continue
            if not header_shown:
                lines.append(self._column_header())
                header_shown = True
            lines += self._group(title, description, items, shown)
            if title == "BUGS TO FIX FIRST":
                lines += self._severe_note("  + ")
            lines.append("")
        return lines

    def _with(self, subtag: prompt.Subtag) -> list[transform.FlaggedIssue]:
        return [item for item in self.listable if self.subtag(item) is subtag]

    def _closable(self) -> list[transform.FlaggedIssue]:
        """Confirmed fixes, duplicates, and questions nobody answered."""
        return [
            item
            for item in self.listable
            if (found := self.subtag(item)) is not None
            and (
                found in (prompt.Subtag.FIX_CONFIRMED, prompt.Subtag.DUPLICATE)
                or (
                    found is prompt.Subtag.NEEDS_INFO
                    and item.idle_days >= NEEDS_INFO_CLOSE_DAYS
                )
            )
        ]

    def _bugs(self) -> list[tuple[transform.FlaggedIssue, prompt.Tagging]]:
        return [
            (item, found)
            for item in self.listable
            if (found := self.tagging(item)) is not None
            and found.tag is prompt.Tag.BUG
        ]

    def _urgent_bugs(self) -> list[transform.FlaggedIssue]:
        """Urgent bugs nobody holds yet.

        Severe before not; then reactions plus comments, the one input the
        model didn't judge; then the longest idle, so a quiet bug doesn't
        sink.
        """
        bugs = [
            (item, found)
            for item, found in self._bugs()
            if found.priority is prompt.Level.HIGH
            # Someone already holds it: a fix, the opener or another issue.
            and found.subtag is None
        ]
        bugs.sort(
            key=lambda pair: (
                pair[1].severity is not prompt.Level.HIGH,
                -pair[0].engagement,
                -pair[0].idle_days,
            )
        )
        return [item for item, _ in bugs]

    def _no_urgent_bugs(self) -> str:
        """The line that replaces an empty "Bugs to fix first" group.

        It says why the tag table's urgent bugs aren't listed there.
        """
        held: dict[prompt.Subtag, int] = {}
        for _, found in self._bugs():
            if found.priority is prompt.Level.HIGH and found.subtag:
                held[found.subtag] = held.get(found.subtag, 0) + 1
        parts = ["No urgent bugs to fix"]
        parts += [
            f"{count} urgent {_HELD[subtag]}" for subtag, count in held.items()
        ]
        parts += self._severe_note("")
        return self.style.dim(" · ".join(parts))

    def _severe_note(self, prefix: str) -> list[str]:
        """Counts severe bugs left out because they aren't urgent."""
        count = sum(
            found.severity is prompt.Level.HIGH
            and found.priority is not prompt.Level.HIGH
            and found.subtag is None
            for _, found in self._bugs()
        )
        if not count:
            return []
        text = f"{prefix}{count} severe, not urgent"
        return [self.style.dim(text) if prefix else text]

    def _wanted(self) -> list[transform.FlaggedIssue]:
        """Must-have features not yet released or duplicated, by demand."""
        return sorted(
            (
                item
                for item in self.listable
                if (found := self.tagging(item)) is not None
                and found.moscow is prompt.Moscow.MUST
                and found.subtag not in _DONE
            ),
            key=lambda i: -transform.demand(i.issue),
        )

    def _column_header(self) -> str:
        indent = " " * (2 + _NUMBER + 1)
        return self.style.dim(
            f"{indent}{'TAGS':<{_LABEL}}{'ACTIVITY':>{_ACTIVITY}}"
            f"{'IDLE':>{_IDLE}}"
        )

    def _group(
        self,
        title: str,
        description: str,
        items: Sequence[transform.FlaggedIssue],
        shown: set[str],
    ) -> list[str]:
        """Returns a group's heading and rows, skipping issues shown above.

        Rows show their subtag, except in a group made of one subtag.
        """
        s = self.style
        fresh = [item for item in items if item.issue.id not in shown]
        above = len(items) - len(fresh)
        if not fresh:
            count = f"{len(items)}, all listed above"
        elif above:
            count = f"{len(items)}, {above} listed above"
        else:
            count = str(len(items))
        heading = (
            s.accent(title) if title == "FIX DIDN'T WORK" else s.bold(title)
        )
        lines = [f"{heading} ({count})  {s.dim(description)}"]
        # A group made of one subtag says it in its heading.
        implied = title in _ONE_SUBTAG
        for item in fresh[:SECTION_LIMIT]:
            lines.append(self._row(item, not implied))
            shown.add(item.issue.id)
        if len(fresh) > SECTION_LIMIT:
            lines.append(s.dim(f"  … and {len(fresh) - SECTION_LIMIT} more"))
        return lines

    def _row(self, item: transform.FlaggedIssue, with_subtag: bool) -> str:
        """One issue: number, tags, activity, idle days and summary."""
        s = self.style
        issue = item.issue
        number = f"#{issue.number}"
        label, urgent = self._label(item)
        if urgent:
            styled_label = s.accent(label)
        elif label == "untagged":
            styled_label = s.dim(label)
        else:
            styled_label = label
        found = self.subtag(item) if with_subtag else None
        suffix = f" [{found.value}]" if found else ""
        headline = _cut(
            self._headline(item), self.width - _PREFIX - len(suffix)
        )
        return (
            "  "
            + s.link(number, issue.url)
            + " " * (_NUMBER - len(number) + 1)
            + styled_label
            + " " * max(1, _LABEL - len(label))
            + f"{item.engagement:>{_ACTIVITY}}"
            + f"{str(item.idle_days) + 'd':>{_IDLE}}"
            + "  "
            + headline
            + s.dim(suffix)
        )

    def _label(self, item: transform.FlaggedIssue) -> tuple[str, bool]:
        """Names an issue's tags in plain words; says if it needs action."""
        tagging = self.tagging(item)
        if tagging is None:
            return "untagged", False
        if tagging.tag is prompt.Tag.BUG:
            severe = tagging.severity is prompt.Level.HIGH
            urgent = tagging.priority is prompt.Level.HIGH
            words = [
                w for w, on in (("severe", severe), ("urgent", urgent)) if on
            ]
            return "bug: " + ", ".join(words) if words else "bug", urgent
        if tagging.tag is prompt.Tag.FEATURE and tagging.moscow:
            raised = item.issue.id in self.result.raised
            return tagging.moscow.value + (" ↑" if raised else ""), False
        return tagging.tag.value, False

    def _headline(self, item: transform.FlaggedIssue) -> str:
        found = self.tagging(item)
        return found.summary if found and found.summary else item.issue.title

    def _footer(self) -> list[str]:
        """Backlog counts and the run's cost, one dim line each."""
        s = self.style
        lines = []
        backlog = []
        if self.hot and not self.list_hot:
            backlog.append((len(self.hot), "hot", "--hot", self.hot))
        if self.stale and not self.list_stale:
            backlog.append((len(self.stale), "stale", "--stale", self.stale))
        if backlog:
            counted = {
                i.issue.id: i for _, _, _, items in backlog for i in items
            }
            untagged = sum(self.tagging(i) is None for i in counted.values())
            text = " · ".join(
                f"{count} {name}" for count, name, _, _ in backlog
            )
            if untagged:
                text += f" ({untagged} untagged)"
            flags = " / ".join(flag for _, _, flag, _ in backlog)
            lines.append(f"{text} · {flags} to list")
        waiting = sum(self.subtag(item) in _WAITING for item in self.listable)
        notes = []
        if waiting:
            notes.append(f"{waiting} waiting on a fix or the reporter")
        if any(item.issue.id in self.result.raised for item in self.listable):
            notes.append("↑ raised to match demand")
        if notes:
            lines.append(" · ".join(notes))
        lines.append(self._this_run())
        outcome = self.result.outcome
        if outcome.skipped:
            shown = outcome.skipped[:SKIPPED_SHOWN]
            numbers = ", ".join(f"#{number}" for number in shown)
            more = len(outcome.skipped) - len(shown)
            lines.append(
                f"Unusable replies for {numbers}"
                + (f" and {more} more" if more else "")
            )
        return [s.dim(line) for line in lines]

    def _this_run(self) -> str:
        outcome = self.result.outcome
        parts = [
            f"This run tagged {outcome.tagged}",
            f"{_dollars(outcome.spent_usd)} of "
            f"{_budget(self.result.budget_usd)}",
        ]
        if outcome.left_for_budget:
            parts.append(
                f"{outcome.left_for_budget} queued for next run, hot first"
            )
        return " · ".join(parts)


def history(repos: Sequence[load.RepoHistory]) -> str:
    """Returns the table ``triage list`` prints.

    Args:
        repos: The repositories the tool has run on.

    Returns:
        The table, ending with a newline.
    """
    if not repos:
        return "No runs yet. Try: triage summary owner/name\n"
    rows = [("REPOSITORY", "RUNS", "LAST RUN", "OPEN")] + [
        (
            repo.name_with_owner,
            str(repo.runs),
            _local(repo.last_run_at),
            str(repo.open_count),
        )
        for repo in repos
    ]
    widths = [max(len(row[i]) for row in rows) for i in range(4)]
    return "".join(
        "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip()
        + "\n"
        for row in rows
    )


def _share(part: int, whole: int) -> str:
    """Returns a share as a whole percentage, at least 1% when not zero."""
    if not whole or not part:
        return "0%"
    return f"{max(1, round(100 * part / whole))}%"


def _budget(amount: float) -> str:
    """Formats the budget to the cent, or as given if it has more digits."""
    return f"${amount:.2f}" if round(amount, 2) == amount else f"${amount:g}"


def _dollars(amount: float) -> str:
    """Formats a cost to the cent; a cost under a cent shows as <$0.01."""
    return "<$0.01" if 0 < amount < 0.005 else f"${amount:.2f}"


def _cut(text: str, room: int) -> str:
    """Cuts text to fit, marking the cut with an ellipsis."""
    room = max(room, 10)
    return text if len(text) <= room else text[: room - 1] + "…"


def _local(moment: datetime.datetime) -> str:
    """Formats a moment in the computer's timezone."""
    return moment.astimezone().strftime("%Y-%m-%d %H:%M")
