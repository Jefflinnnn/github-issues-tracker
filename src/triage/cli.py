"""Command-line entry point for ``triage``.

The CLI only parses arguments and calls the pipeline. Two commands:
``summary`` runs the pipeline on a repository and prints what needs you;
``list`` shows the repositories you've run it on.
"""

import argparse
import datetime
import importlib.metadata
import logging
import shutil
import sqlite3
import sys
from collections.abc import Sequence

from triage import config
from triage import repo_ref
from triage import report
from triage.pipeline import extract
from triage.pipeline import load
from triage.pipeline import run

logger = logging.getLogger(__name__)

# Errors a user can fix; anything else is a bug and keeps its traceback.
_USER_ERRORS = (
    config.ConfigError,
    extract.GitHubError,
    load.LoadError,
    repo_ref.InvalidRepoRefError,
)


def main(argv: Sequence[str] | None = None) -> int:
    """Runs the ``triage`` command.

    Args:
        argv: Command-line arguments without the program name. None reads
            them from ``sys.argv``.

    Returns:
        The process exit code.
    """
    args = _build_parser().parse_args(argv)
    _configure_logging(args.verbose)
    try:
        connection = load.connect(config.database_path())
        try:
            if args.command == "summary":
                _summary(args, connection)
            else:
                sys.stdout.write(report.history(load.history(connection)))
        finally:
            connection.close()
    except _USER_ERRORS as error:
        print(f"triage: error: {error}", file=sys.stderr)
        return 1
    return 0


def _summary(args: argparse.Namespace, connection: sqlite3.Connection) -> None:
    """Runs the pipeline on one repository and prints its summary."""
    result = run.run(
        repo=repo_ref.parse(args.repo),
        limit=args.limit,
        token=config.github_token(),
        connection=connection,
        now=datetime.datetime.now(datetime.timezone.utc),
    )
    width = shutil.get_terminal_size().columns
    sys.stdout.write(report.summary(result, width))


def _build_parser() -> argparse.ArgumentParser:
    """Returns the argument parser for ``triage``."""
    parser = argparse.ArgumentParser(
        prog="triage",
        description=(
            "Summarize a GitHub repository's open issues: what needs you "
            "first, and what changed since your last run."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=importlib.metadata.version("github-issues-tracker"),
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="show debug logs"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    summary = commands.add_parser(
        "summary",
        help="fetch a repository's issues and summarize them",
        description=(
            "Fetch a repository's issues and summarize them. The first run "
            "fetches open issues; later runs fetch what changed since."
        ),
    )
    summary.add_argument("repo", help="GitHub link, SSH remote or owner/name")
    summary.add_argument(
        "--limit",
        type=_non_negative_int,
        default=0,
        metavar="N",
        help=(
            "first run only: fetch the N most recently updated open issues "
            "(default: 0, all of them)"
        ),
    )

    commands.add_parser(
        "list",
        help="show the repositories you've summarized",
        description="Show the repositories you've summarized.",
    )
    return parser


def _non_negative_int(text: str) -> int:
    """Parses a whole number that is 0 or more, for argparse."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"not a whole number: {text!r}"
        ) from None
    if value < 0:
        raise argparse.ArgumentTypeError(f"must be 0 or more, got {value}")
    return value


def _configure_logging(verbose: bool) -> None:
    """Sends progress lines to stderr, and debug logs with ``-v``."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            "%(levelname)s %(name)s: %(message)s" if verbose else "%(message)s"
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.DEBUG if verbose else logging.WARNING)
    logging.getLogger("triage").setLevel(
        logging.DEBUG if verbose else logging.INFO
    )
