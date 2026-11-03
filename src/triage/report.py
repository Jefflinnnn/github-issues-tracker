"""Renders the summary and the run history as text.

The summary reads top down: what it covers, the tag counts, what changed
since the last run, then the issues that need a decision, in groups. A
footer points to ``triage browse`` and says what the run did and cost. On
a terminal it uses bold, dim and one accent color for what needs action,
and makes issue numbers clickable; piped or redirected, it is plain text.
"""

import dataclasses
import datetime
import os
import textwrap
from collections.abc import Mapping
from collections.abc import Sequence
from typing import TextIO

from triage import text
from triage.model import digest
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
# Below this width, rows use short labels and status names, so an 80-column
# window, the default for a new macOS Terminal window, keeps room for the
# summary.
WIDE = 100
# Subtags that leave an issue in "Bugs to fix" or "Features": someone is on
# it, or the reporter has been asked. Other subtags have their own group.
_IN_PROGRESS = {
    None,
    prompt.Subtag.FIX_IN_PROGRESS,
    prompt.Subtag.NEEDS_INFO,
}
# Features by MoSCoW priority, must-have first.
_MOSCOW_ORDER = {
    prompt.Moscow.MUST: 0,
    prompt.Moscow.SHOULD: 1,
    prompt.Moscow.COULD: 2,
}
# Groups whose rows all share one subtag.
_ONE_SUBTAG = {"FIX DIDN'T WORK", "ASK TO CONFIRM"}
# How a bug that a subtag keeps out of "Bugs to fix" is described in the
# line that replaces the empty group.
_HELD = {
    prompt.Subtag.FIX_FAILED: "with a failed fix",
    prompt.Subtag.FIX_RELEASED: "with a released fix",
    prompt.Subtag.FIX_CONFIRMED: "confirmed fixed",
    prompt.Subtag.FIX_IN_PROGRESS: "being fixed",
    prompt.Subtag.NEEDS_INFO: "waiting on the reporter",
    prompt.Subtag.DUPLICATE: "marked duplicate",
}
# Width of the tag name column in the tag table.
_TAG_NAME = 14


@dataclasses.dataclass(frozen=True)
class Columns:
    """Widths of an issue row's columns, and its column header's names."""

    number: int
    label: int
    activity: int
    idle: int
    flag: int
    status: int
    activity_name: str

    @classmethod
    def for_width(cls, width: int) -> "Columns":
        """Returns the row layout for a width: short labels below ``WIDE``."""
        return _WIDE_COLUMNS if width >= WIDE else _NARROW_COLUMNS


_WIDE_COLUMNS = Columns(8, 21, 9, 6, 7, 16, "ACTIVITY")
_NARROW_COLUMNS = Columns(7, 9, 5, 5, 6, 11, "ACT")
# Short status names for narrow rows.
_SHORT_STATUS = {
    prompt.Subtag.FIX_FAILED: "failed",
    prompt.Subtag.FIX_RELEASED: "released",
    prompt.Subtag.FIX_CONFIRMED: "confirmed",
    prompt.Subtag.FIX_IN_PROGRESS: "fixing",
    prompt.Subtag.NEEDS_INFO: "needs info",
    prompt.Subtag.DUPLICATE: "duplicate",
}


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


def summary(result: run.RunResult, width: int, style: Style = PLAIN) -> str:
    """Returns the Monday summary of a repository's open issues.

    Each group lists its first issues; ``triage browse`` lists every
    issue.

    Args:
        result: The run to summarize.
        width: Terminal width; rows are cut to fit, and to ``MAX_WIDTH``.
        style: How to style the text.

    Returns:
        The summary, ending with a newline.
    """
    return Report(result, min(width, MAX_WIDTH), style).render()


@dataclasses.dataclass(frozen=True)
class Group:
    """One group of issues, as the summary and ``triage browse`` list it.

    Attributes:
        title: e.g. ``FIX DIDN'T WORK``.
        description: What puts an issue in it, in a few words.
        issues: Its issues, in order, without those an earlier group
            lists.
        total: Every issue that fits it, those listed earlier included.
        implied: Every issue in it has the same subtag, which the title
            already names.
    """

    title: str
    description: str
    issues: list[transform.FlaggedIssue]
    total: int
    implied: bool


class Report:
    """Builds one summary; split into steps so each stays short.

    ``triage browse`` uses the same groups, rows and labels.
    """

    def __init__(
        self,
        result: run.RunResult,
        width: int,
        style: Style,
        every_issue: bool = False,
    ):
        """Prepares the report.

        Args:
            result: The run to report on.
            width: Width to fit rows to.
            style: How to style the text.
            every_issue: Add a last group with the issues no other group
                takes, as ``triage browse`` does, so every issue is listed.
        """
        self.result = result
        self.width = width
        self.style = style
        self.every_issue = every_issue
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

    def tagging(self, item: transform.FlaggedIssue) -> prompt.Tagging | None:
        """Returns an issue's up-to-date tagging, or None."""
        return self.result.tags.get(item.issue.id)

    def subtag(self, item: transform.FlaggedIssue) -> prompt.Subtag | None:
        """Returns an issue's subtag, or None."""
        found = self.tagging(item)
        return found.subtag if found else None

    def flag(self, item: transform.FlaggedIssue) -> str:
        """Returns ``hot`` or ``stale`` for the FLAG column, or empty.

        An issue both hot and stale shows ``hot``; its IDLE column shows
        it's stale. A partial scan shows no staleness.
        """
        if transform.Flag.HOT in item.flags:
            return "hot"
        if transform.Flag.STALE in item.flags and not self.partial:
            return "stale"
        return ""

    def render(self) -> str:
        """Returns the whole summary."""
        s = self.style
        lines = [s.bold(self.result.name_with_owner), self.scope(), ""]
        lines += self._tag_table()
        lines += self._since_last_run()
        lines += self._groups()
        lines += self._footer()
        return "\n".join(lines).rstrip("\n") + "\n"

    def scope(self) -> str:
        """Says what the numbers cover, before any of them."""
        result = self.result
        scanned = len(self.flagged)
        parts = [f"{max(scanned, result.open_on_github)} open"]
        change = self._change("open")
        if change:
            parts[0] += f" ({change})"
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
            # A tag whose last issue closed keeps its row, to show the -1.
            if mine or self._change(tag.value):
                breakdown = self._breakdown(tag, mine) if mine else ""
                rows.append((tag.value, len(mine), breakdown))
        untagged = sum(t is None for t in taggings)
        if not rows and not untagged:
            return []
        # The column shows only when a tag's count changed.
        since = self.result.since_last_run
        compare = bool(since and since.tag_changes)
        change_header = f"{'change':>8}" if compare else ""
        lines = [
            "  "
            + s.dim(f"{'':<{_TAG_NAME}}{'count':>5}{change_header}   breakdown")
        ]
        for name, count, breakdown in rows:
            change = f"{self._change(name):>8}" if compare else ""
            row = f"  {name:<{_TAG_NAME}}{count:>5}{change}   {breakdown}"
            lines.append(row.rstrip())
        if untagged:
            if rows:
                lines.append(
                    s.dim("  " + "─" * (_TAG_NAME + 5 + len(change_header)))
                )
            change = ""
            lines.append(
                s.dim(
                    f"  {'not yet tagged':<{_TAG_NAME}}{untagged:>5}{change}"
                ).rstrip()
            )
        return lines + [""]

    def _change(self, key: str) -> str:
        """Returns a count's change since the previous run, e.g. ``+2``.

        Tags change only through issues opened, closed or tagged
        differently, not through tagging catching up; the open count
        changes as GitHub's does. Empty when nothing changed, or there's
        nothing to compare with.
        """
        since = self.result.since_last_run
        if since is None:
            return ""
        if key == "open":
            delta = self.result.open_on_github - since.previous_open
        else:
            delta = since.tag_changes.get(key, 0)
        return f"{delta:+d}" if delta else ""

    def _since_last_run(self) -> list[str]:
        """Counts what changed since the previous run, with the digest."""
        since = self.result.since_last_run
        if since is None:
            return []
        s = self.style
        counts = since_counts(since)
        heading = (
            f"{s.bold('SINCE LAST RUN')} ({digest.ago(since.days)})  "
            + s.dim(" · ".join(counts) if counts else "nothing changed")
        )
        lines = [heading]
        if since.digest:
            lines += textwrap.wrap(
                since.digest,
                self.width,
                initial_indent="  ",
                subsequent_indent="  ",
            )
        elif counts and since.skipped:
            lines.append(s.dim(f"  No digest: {since.skipped}."))
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

    def groups(self, limit: int | None = SECTION_LIMIT) -> list[Group]:
        """Returns the groups, in order, each issue in its first group.

        Groups go from costliest to leave alone to cleanup. With
        ``every_issue``, a last group takes the issues no other group does.

        Args:
            limit: How many issues a group lists; an issue counts as listed
                only if it is among them, so one past the limit can show up
                in a later group. None lists every issue.

        Returns:
            Every group, empty ones included.
        """
        found = [
            (
                "FIX DIDN'T WORK",
                "a released fix, still reported",
                sorted(
                    self._with(prompt.Subtag.FIX_FAILED),
                    key=lambda i: -i.engagement,
                ),
            ),
            (
                "BUGS TO FIX",
                "urgent first, then severe",
                self._bugs_to_fix(),
            ),
            (
                "FEATURES",
                "must-have, then should, then could; most demand first",
                self._features(),
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
        if self.every_issue:
            taken = {item.issue.id for _, _, items in found for item in items}
            found.append(
                (
                    "OTHER ISSUES",
                    "questions, docs, other, and not yet tagged",
                    sorted(
                        (i for i in self.listable if i.issue.id not in taken),
                        key=lambda i: -i.engagement,
                    ),
                )
            )
        result = []
        listed: set[str] = set()
        for title, description, items in found:
            fresh = [item for item in items if item.issue.id not in listed]
            listed.update(item.issue.id for item in fresh[:limit])
            result.append(
                Group(
                    title, description, fresh, len(items), title in _ONE_SUBTAG
                )
            )
        return result

    def _groups(self) -> list[str]:
        """Renders the groups that list issues, up to SECTION_LIMIT each."""
        lines: list[str] = []
        header_shown = False
        for group in self.groups():
            if group.title == "BUGS TO FIX" and not group.total:
                lines += [self._no_bugs(), ""]
                continue
            if not group.total:
                continue
            if not header_shown:
                lines.append(self.style.dim(self.column_header(self.width)))
                header_shown = True
            lines += self._group(group)
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

    def _tagged(
        self, tag: prompt.Tag
    ) -> list[tuple[transform.FlaggedIssue, prompt.Tagging]]:
        """Returns the listable issues with a main tag, with their tagging."""
        return [
            (item, found)
            for item in self.listable
            if (found := self.tagging(item)) is not None and found.tag is tag
        ]

    def _in_progress(
        self, item: transform.FlaggedIssue, found: prompt.Tagging
    ) -> bool:
        """Says whether an issue stays with its tag's group.

        True with no subtag, a fix in progress, or a question to the
        reporter not yet 30 days old; the other subtags have their own
        group.
        """
        return found.subtag in _IN_PROGRESS and not (
            found.subtag is prompt.Subtag.NEEDS_INFO
            and item.idle_days >= NEEDS_INFO_CLOSE_DAYS
        )

    def _bugs_to_fix(self) -> list[transform.FlaggedIssue]:
        """Open bugs: urgent first, then severe.

        A bug being fixed or waiting on the reporter sorts like any other;
        its status says so. Within each, reactions plus comments decide,
        the one input the model didn't judge; then the longest idle, so a
        quiet bug doesn't sink.
        """
        bugs = [
            (item, found)
            for item, found in self._tagged(prompt.Tag.BUG)
            if self._in_progress(item, found)
        ]
        bugs.sort(
            key=lambda pair: (
                pair[1].priority is not prompt.Level.HIGH,
                pair[1].severity is not prompt.Level.HIGH,
                -pair[0].engagement,
                -pair[0].idle_days,
            )
        )
        return [item for item, _ in bugs]

    def _no_bugs(self) -> str:
        """The line that replaces an empty "Bugs to fix" group.

        It says why the tag table's bugs aren't listed there.
        """
        held: dict[prompt.Subtag, int] = {}
        for _, found in self._tagged(prompt.Tag.BUG):
            if found.subtag:
                held[found.subtag] = held.get(found.subtag, 0) + 1
        parts = ["No bugs to fix"]
        parts += [
            f"{count} {'bug' if count == 1 else 'bugs'} {_HELD[subtag]}"
            for subtag, count in held.items()
        ]
        return self.style.dim(" · ".join(parts))

    def _features(self) -> list[transform.FlaggedIssue]:
        """Open features: must-have, then should, then could, by demand."""
        features = [
            (item, found)
            for item, found in self._tagged(prompt.Tag.FEATURE)
            if self._in_progress(item, found)
        ]
        features.sort(
            key=lambda pair: (
                _MOSCOW_ORDER[pair[1].moscow] if pair[1].moscow else 3,
                -transform.demand(pair[0].issue),
            )
        )
        return [item for item, _ in features]

    def column_header(self, width: int, flags: bool = False) -> str:
        """Returns the header row above issue rows, unstyled.

        Args:
            width: The width the rows fit, which picks their layout.
            flags: Include the FLAG column, as ``triage browse`` does.

        Returns:
            The header.
        """
        columns = Columns.for_width(width)
        flag = f"{'FLAG':<{columns.flag}}" if flags else ""
        return (
            " " * (2 + columns.number)
            + f"{'TAGS':<{columns.label}}"
            + f"{columns.activity_name:>{columns.activity}}"
            + f"{'IDLE':>{columns.idle}}  "
            + flag
            + f"{'STATUS':<{columns.status}}SUMMARY"
        )

    def _group(self, group: Group) -> list[str]:
        """Returns a group's heading and its first rows."""
        s = self.style
        lines = [self.heading(group, s)]
        for item in group.issues[:SECTION_LIMIT]:
            lines.append(self.row(item, group, self.width))
        if len(group.issues) > SECTION_LIMIT:
            more = len(group.issues) - SECTION_LIMIT
            lines.append(s.dim(f"  … and {more} more"))
        return lines

    def heading(
        self, group: Group, style: Style | None = None, described: bool = True
    ) -> str:
        """Returns a group's heading, e.g. ``FEATURES (14, 2 listed above)``.

        Args:
            group: The group.
            style: How to style it; plain by default.
            described: Add what puts issues in the group.

        Returns:
            The heading.
        """
        s = style or PLAIN
        above = group.total - len(group.issues)
        if not group.issues:
            count = f"{group.total}, all listed above"
        elif above:
            count = f"{group.total}, {above} listed above"
        else:
            count = str(group.total)
        title = s.bold(group.title)
        heading = f"{title} ({count})"
        return (
            f"{heading}  {s.dim(group.description)}" if described else heading
        )

    def row(
        self,
        item: transform.FlaggedIssue,
        group: Group,
        width: int,
        style: Style | None = None,
        mark: str = "  ",
        flags: bool = False,
    ) -> str:
        """One issue: number, tags, activity, idle days, status and summary.

        Rows use short labels and status names below ``WIDE`` columns. The
        status is blank in a group made of one subtag, whose title names it.

        Args:
            item: The issue.
            group: The group it's listed in.
            width: The width to fit; the row stays one short of it, so it
                never touches a terminal's last column.
            style: Styling; defaults to the report's.
            mark: Two characters before the number, e.g. ``● `` for a
                changed issue.
            flags: Add the FLAG column: ``hot`` or ``stale``.

        Returns:
            The row.
        """
        s = style or self.style
        issue = item.issue
        columns = Columns.for_width(width)
        label, _ = self.label(item)
        subtag = None if group.implied else self.subtag(item)
        if columns is _NARROW_COLUMNS:
            label = self.short_label(item)
            status = _SHORT_STATUS[subtag] if subtag else ""
        else:
            status = subtag.value if subtag else ""
        if self.is_red(item, group):
            styled_label = s.accent(label)
        elif label == "untagged":
            styled_label = s.dim(label)
        else:
            styled_label = label
        number = f"#{issue.number}"
        number_gap = " " * max(1, columns.number - len(number))
        label_gap = " " * max(1, columns.label - len(label))
        rest = (
            f"{item.engagement:>{columns.activity}}"
            + f"{str(item.idle_days) + 'd':>{columns.idle}}  "
            + (f"{self.flag(item):<{columns.flag}}" if flags else "")
            + f"{status:<{columns.status}}"
        )
        prefix = len(mark + number + number_gap + label + label_gap + rest)
        headline = text.cut(self.headline(item), width - 1 - prefix)
        return (
            mark
            + s.link(number, issue.url)
            + number_gap
            + styled_label
            + label_gap
            + rest
            + headline
        )

    def is_red(self, item: transform.FlaggedIssue, group: Group) -> bool:
        """Says whether a row shows in the accent color: urgent, to fix."""
        return group.title == "BUGS TO FIX" and self.label(item)[1]

    def short_label(self, item: transform.FlaggedIssue) -> str:
        """Names an issue's tags in one word, for narrow rows.

        A bug reads ``urgent``, ``severe`` or ``bug``, and a feature its
        MoSCoW word; ``triage browse``'s card shows the rest.
        """
        tagging = self.tagging(item)
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

    def label(self, item: transform.FlaggedIssue) -> tuple[str, bool]:
        """Names an issue's tags in plain words; says if it needs action."""
        tagging = self.tagging(item)
        if tagging is None:
            return "untagged", False
        words = prompt.describe(tagging)
        if item.issue.id in self.result.raised:
            words += " ↑"
        return words, tagging.priority is prompt.Level.HIGH

    def headline(self, item: transform.FlaggedIssue) -> str:
        """Returns the model's summary of an issue, or its title."""
        found = self.tagging(item)
        return found.summary if found and found.summary else item.issue.title

    def _footer(self) -> list[str]:
        """Where to see every issue, then what the run did and cost."""
        s = self.style
        lines = ["Run `triage browse` in the terminal to view every issue."]
        lines += self._this_run()
        outcome = self.result.outcome
        if outcome.skipped:
            shown = outcome.skipped[:SKIPPED_SHOWN]
            numbers = ", ".join(f"#{number}" for number in shown)
            more = len(outcome.skipped) - len(shown)
            lines.append(
                f"Unusable replies for {numbers}"
                + (f" and {more} more" if more else "")
                + "."
            )
        return [s.dim(line) for line in lines]

    def _this_run(self) -> list[str]:
        outcome = self.result.outcome
        spent = _dollars(self.result.spent_usd)
        if self.result.budget_usd is None:
            started = self.result.ran_at
            when = f" ({_local(started)})" if started else ""
            return [f"Last run{when} tagged {outcome.tagged}. Used {spent}."]
        lines = [
            f"This run tagged {outcome.tagged}. Used {spent} of "
            f"{_budget(self.result.budget_usd)} budget."
        ]
        if outcome.left_for_budget:
            lines.append(f"{outcome.left_for_budget} queued for next run.")
        return lines


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


def since_counts(since: run.SinceLastRun) -> list[str]:
    """Counts what changed since the previous run, e.g. ``2 new``.

    The summary and ``triage browse`` both show these.

    Args:
        since: What changed.

    Returns:
        The non-zero counts: new, closed, then with new comments.
    """
    return [
        f"{count} {name}"
        for count, name in (
            (since.new, "new"),
            (since.closed, "closed"),
            (since.changed, "with new comments"),
        )
        if count
    ]


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


def _local(moment: datetime.datetime) -> str:
    """Formats a moment in the computer's timezone."""
    return moment.astimezone().strftime("%Y-%m-%d %H:%M")
