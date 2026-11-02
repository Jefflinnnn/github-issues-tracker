"""Renders the summary and the run history as plain text."""

import datetime
from collections.abc import Callable
from collections.abc import Sequence

from triage.pipeline import load
from triage.pipeline import run
from triage.pipeline import transform

# Issues listed per section; the rest are counted.
SECTION_LIMIT = 10


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

    hot = sorted(having(transform.Flag.HOT), key=lambda i: -i.engagement)
    stale = sorted(having(transform.Flag.STALE), key=lambda i: -i.idle_days)

    lines = [
        f"{result.name_with_owner} · {len(flagged)} open · "
        f"{len(hot)} hot · {len(stale)} stale",
        _run_line(result),
        "",
    ]
    lines += _section(
        "HOT", hot, lambda i: f"{i.engagement} reacts+comments", width
    )
    lines += _section("STALE", stale, lambda i: f"idle {i.idle_days}d", width)
    lines.append(f"Open an issue: {result.url}/issues/<number>")
    return "\n".join(lines) + "\n"


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


def _run_line(result: run.RunResult) -> str:
    """Says what this run fetched."""
    if result.previous_run_at is None:
        return f"First run: fetched {result.fetched} open issues."
    return (
        f"{result.fetched} issues changed since the last run "
        f"({_local(result.previous_run_at)})."
    )


def _section(
    title: str,
    items: Sequence[transform.FlaggedIssue],
    detail: Callable[[transform.FlaggedIssue], str],
    width: int,
) -> list[str]:
    """Returns one section: a heading, up to SECTION_LIMIT issues, a count."""
    if not items:
        return []
    lines = [f"{title} ({len(items)})"]
    for item in items[:SECTION_LIMIT]:
        prefix = f"  #{item.issue.number:<6} {detail(item):<20} "
        lines.append(prefix + _cut(item.issue.title, width - len(prefix)))
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
