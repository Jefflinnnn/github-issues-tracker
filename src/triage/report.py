"""Renders the summary and the run history as plain text."""

import datetime
from collections.abc import Callable
from collections.abc import Sequence

from triage.model import prompt
from triage.pipeline import load
from triage.pipeline import run
from triage.pipeline import transform

# Issues listed per section; the rest are counted.
SECTION_LIMIT = 10
# Subtags that mean an issue can likely be closed.
_CLOSABLE = {
    prompt.Subtag.FIX_RELEASED,
    prompt.Subtag.FIX_CONFIRMED,
    prompt.Subtag.DUPLICATE,
}
# Issue numbers the Run line names when replies were unusable.
SKIPPED_SHOWN = 5


def summary(result: run.RunResult, width: int) -> str:
    """Returns the Monday summary of a repository's open issues.

    Args:
        result: The run to summarize.
        width: Terminal width; long titles are cut to fit.

    Returns:
        The summary, ending with a newline.
    """
    flagged = result.flagged

    def having(flag: transform.Flag) -> list[transform.FlaggedIssue]:
        return [item for item in flagged if flag in item.flags]

    def tagging(item: transform.FlaggedIssue) -> prompt.Tagging | None:
        return result.tags.get(item.issue.id)

    def label(item: transform.FlaggedIssue) -> str:
        return _label(tagging(item), item.issue.id in result.raised)

    def headline(item: transform.FlaggedIssue) -> str:
        found = tagging(item)
        return found.summary if found and found.summary else item.issue.title

    def subtag(item: transform.FlaggedIssue) -> prompt.Subtag | None:
        found = tagging(item)
        return found.subtag if found else None

    hot = sorted(having(transform.Flag.HOT), key=lambda i: -i.engagement)
    fix_failed = sorted(
        (i for i in flagged if subtag(i) is prompt.Subtag.FIX_FAILED),
        key=lambda i: -i.engagement,
    )
    closable = sorted(
        (i for i in flagged if subtag(i) in _CLOSABLE),
        key=lambda i: -i.idle_days,
    )
    stale = sorted(having(transform.Flag.STALE), key=lambda i: -i.idle_days)
    urgent_bugs = sorted(
        (
            item
            for item in flagged
            if (found := tagging(item)) is not None
            and found.tag is prompt.Tag.BUG
            and found.priority is prompt.Level.HIGH
            # Someone already holds it: a fix, the opener or another issue.
            and found.subtag is None
        ),
        # High severity first, then the busiest.
        key=lambda i: (
            result.tags[i.issue.id].severity is not prompt.Level.HIGH,
            -i.engagement,
        ),
    )
    wanted = sorted(
        (
            item
            for item in flagged
            if (found := tagging(item)) is not None
            and found.moscow is prompt.Moscow.MUST
            and found.subtag not in _CLOSABLE
        ),
        key=lambda i: -transform.demand(i.issue),
    )

    taggings = [tagging(item) for item in flagged]
    header = [
        f"{result.name_with_owner} · {len(flagged)} open · "
        f"{len(hot)} hot · {len(stale)} stale",
        _tag_counts(taggings),
        _subtag_counts(taggings),
        _run_line(result),
    ]
    lines = [line for line in header if line] + [""]
    lines += _section(
        "FIX DIDN'T WORK: a released fix, still reported",
        fix_failed,
        lambda i: f"{label(i):<15} {i.engagement} reacts+comments",
        headline,
        width,
    )
    lines += _section(
        "BUGS TO FIX FIRST: high priority",
        urgent_bugs,
        lambda i: f"{label(i):<15} {i.engagement} reacts+comments",
        headline,
        width,
    )
    lines += _section(
        "MOST-WANTED FEATURES: must-have",
        wanted,
        lambda i: f"{label(i):<15} demand {transform.demand(i.issue)}",
        headline,
        width,
    )
    lines += _section(
        "PROBABLY CLOSABLE: fix released or confirmed, or duplicate",
        closable,
        lambda i: f"{label(i):<15} idle {i.idle_days}d",
        headline,
        width,
        subtag,
    )
    lines += _section(
        "HOT",
        hot,
        lambda i: f"{label(i):<15} {i.engagement} reacts+comments",
        headline,
        width,
        subtag,
    )
    lines += _section(
        "STALE",
        stale,
        lambda i: f"{label(i):<15} idle {i.idle_days}d",
        headline,
        width,
        subtag,
    )
    if result.raised:
        lines.append(
            "↑ marks features raised to match their demand (reactions plus "
            "people commenting)."
        )
    return "\n".join(lines) + "\n"


def _label(tagging: prompt.Tagging | None, raised: bool) -> str:
    """Names an issue's tags in a few characters, e.g. ``bug high/low``."""
    if tagging is None:
        return "untagged"
    if tagging.tag is prompt.Tag.BUG and tagging.severity and tagging.priority:
        return f"bug {tagging.severity.value}/{tagging.priority.value}"
    if tagging.tag is prompt.Tag.FEATURE and tagging.moscow:
        return tagging.moscow.value + (" ↑" if raised else "")
    return tagging.tag.value


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


def _tag_counts(taggings: Sequence[prompt.Tagging | None]) -> str:
    """Counts open issues per main tag, with the bugs and features to act on."""
    parts = []
    for tag in prompt.Tag:
        mine = [t for t in taggings if t is not None and t.tag is tag]
        if not mine:
            continue
        part = f"{tag.value} {len(mine)}"
        if tag is prompt.Tag.BUG:
            high = sum(t.priority is prompt.Level.HIGH for t in mine)
            part += f" ({high} high priority)"
        elif tag is prompt.Tag.FEATURE:
            must = sum(t.moscow is prompt.Moscow.MUST for t in mine)
            part += f" ({must} must-have)"
        parts.append(part)
    untagged = sum(t is None for t in taggings)
    if untagged:
        parts.append(f"untagged {untagged}")
    return "Tags: " + " · ".join(parts)


def _subtag_counts(taggings: Sequence[prompt.Tagging | None]) -> str:
    """Counts open issues per subtag; empty when none has one."""
    parts = []
    for subtag in prompt.Subtag:
        count = sum(t is not None and t.subtag is subtag for t in taggings)
        if count:
            parts.append(f"{subtag.value} {count}")
    return "Subtags: " + " · ".join(parts) if parts else ""


def _run_line(result: run.RunResult) -> str:
    """Says in one line what this run fetched, tagged and spent."""
    outcome = result.outcome
    parts = [
        _fetched(result),
        f"{outcome.tagged} tagged",
        f"{_dollars(outcome.spent_usd)} of {_budget(result.budget_usd)}",
    ]
    if outcome.left_for_budget:
        parts.append(f"{outcome.left_for_budget} left for next run")
    if outcome.skipped:
        shown = outcome.skipped[:SKIPPED_SHOWN]
        numbers = ", ".join(f"#{number}" for number in shown)
        more = len(outcome.skipped) - len(shown)
        parts.append(
            f"unusable replies: {numbers}" + (f" +{more} more" if more else "")
        )
    return "Run: " + " · ".join(parts)


def _fetched(result: run.RunResult) -> str:
    """Says what this run fetched, in a few words."""
    if result.fetch == "changes":
        return f"{result.fetched} changed since last run"
    if result.fetch == "limited":
        line = f"newest {result.limit} of {result.open_on_github} open"
        return line + (" + changes" if result.previous_run_at else "")
    if result.previous_run_at is None:
        return f"first run, {result.fetched} fetched"
    return f"all {result.fetched} open fetched"


def _budget(amount: float) -> str:
    """Formats the budget to the cent, or as given if it has more digits."""
    return f"${amount:.2f}" if round(amount, 2) == amount else f"${amount:g}"


def _dollars(amount: float) -> str:
    """Formats a cost to the cent; a cost under a cent shows as <$0.01."""
    return "<$0.01" if 0 < amount < 0.005 else f"${amount:.2f}"


def _section(
    title: str,
    items: Sequence[transform.FlaggedIssue],
    detail: Callable[[transform.FlaggedIssue], str],
    headline: Callable[[transform.FlaggedIssue], str],
    width: int,
    subtag: Callable[[transform.FlaggedIssue], prompt.Subtag | None]
    | None = None,
) -> list[str]:
    """Returns one section: a heading, up to SECTION_LIMIT issues, a count.

    Each issue shows ``headline``: the model's summary, or the title of an
    issue without one. With ``subtag``, an issue that has one shows it last.
    """
    if not items:
        return []
    lines = [f"{title} ({len(items)})"]
    for item in items[:SECTION_LIMIT]:
        prefix = f"  #{item.issue.number:<6} {detail(item):<34} "
        found = subtag(item) if subtag else None
        suffix = f" [{found.value}]" if found else ""
        room = width - len(prefix) - len(suffix)
        lines.append(prefix + _cut(headline(item), room) + suffix)
    if len(items) > SECTION_LIMIT:
        lines.append(f"  … and {len(items) - SECTION_LIMIT} more")
    lines.append("")
    return lines


def _cut(text: str, room: int) -> str:
    """Cuts text to fit, marking the cut with an ellipsis."""
    room = max(room, 10)
    return text if len(text) <= room else text[: room - 1] + "…"


def _local(moment: datetime.datetime) -> str:
    """Formats a moment in the computer's timezone."""
    return moment.astimezone().strftime("%Y-%m-%d %H:%M")
