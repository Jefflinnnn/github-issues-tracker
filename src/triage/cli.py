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

from triage import completion
from triage import config
from triage import menu
from triage import progress
from triage import repo_ref
from triage import report
from triage import setup
from triage.model import classifier
from triage.model import provider
from triage.model import providers
from triage.pipeline import extract
from triage.pipeline import load
from triage.pipeline import run

logger = logging.getLogger(__name__)

# Errors a user can fix; anything else is a bug and keeps its traceback.
_USER_ERRORS = (
    config.ConfigError,
    extract.GitHubError,
    load.LoadError,
    provider.ProviderError,
    repo_ref.InvalidRepoRefError,
)
DEFAULT_BUDGET_USD = 0.50


def main(argv: Sequence[str] | None = None) -> int:
    """Runs the ``triage`` command.

    Args:
        argv: Command-line arguments without the program name. None reads
            them from ``sys.argv``.

    Returns:
        The process exit code.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.error("choose a command: setup, summary or list")
    status = progress.Status()
    log = _configure_logging(args.verbose, status)
    if args.command == "completion":
        sys.stdout.write(completion.SCRIPTS[args.shell])
        return 0
    if args.command == "setup":
        if not sys.stdin.isatty():
            print(
                "triage: error: triage setup asks questions; run it in a "
                "terminal, or set the values in .env (see .env.example).",
                file=sys.stderr,
            )
            return 1
        try:
            setup.run()
        except (KeyboardInterrupt, EOFError, menu.CancelledError):
            print("\nSetup stopped; nothing was saved.", file=sys.stderr)
            return 1
        return 0
    try:
        connection = load.connect(config.database_path())
        try:
            if args.command == "summary":
                _summary(args, connection, status, log)
            elif args.names:
                names = [
                    repo.name_with_owner for repo in load.history(connection)
                ]
                sys.stdout.write("".join(f"{name}\n" for name in names))
            else:
                sys.stdout.write(report.history(load.history(connection)))
        finally:
            connection.close()
    except _USER_ERRORS as error:
        print(f"triage: error: {error}", file=sys.stderr)
        return 1
    return 0


def _summary(
    args: argparse.Namespace,
    connection: sqlite3.Connection,
    status: progress.Status,
    log: progress.LogHandler,
) -> None:
    """Runs the pipeline on one repository and prints its summary.

    Progress shows on one status line, erased when the run ends. If any
    log lines were printed during the run, a blank line separates them
    from the summary.
    """
    repo = repo_ref.parse(args.repo)
    token = config.github_token()
    # Settings are checked before fetching, so a missing key fails fast.
    model = providers.from_settings(args.model)
    try:
        result = run.run(
            repo=repo,
            limit=args.limit,
            token=token,
            connection=connection,
            model=model,
            budget_usd=args.budget_usd,
            workers=args.workers,
            now=_now(),
            status=status,
        )
    finally:
        status.clear()
    if log.printed:
        print(file=sys.stderr)
    width = shutil.get_terminal_size().columns
    sys.stdout.write(
        report.summary(
            result,
            width,
            report.Style.for_stream(sys.stdout),
            hot=args.hot,
            stale=args.stale,
        )
    )


def _now() -> datetime.datetime:
    """Returns the current time; tests replace it to control the clock."""
    return datetime.datetime.now(datetime.timezone.utc)


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
    # An empty metavar drops the {setup,summary,...} line above the command
    # list; usage shows <command> instead.
    # Not required=True: with an empty metavar, argparse's "required"
    # message would name nothing, so main says which commands exist.
    commands = parser.add_subparsers(dest="command", metavar="")
    parser.usage = "triage [-h] [--version] [-v] <command> ..."

    commands.add_parser(
        "setup",
        help="save your GitHub token, AWS keys and model",
        description=(
            "Ask for your GitHub token, AWS key pair, gateway and model, and "
            "save them where only you can read them. Safe to run again."
        ),
    )

    summary = commands.add_parser(
        "summary",
        help="fetch a repository's issues and summarize them",
        description=(
            "Fetch a repository's issues and summarize them. The first run "
            "fetches every open issue; later runs fetch what changed since. "
            "--limit caps how many open issues a run fetches."
        ),
    )
    summary.add_argument("repo", help="GitHub link, SSH remote or owner/name")
    summary.add_argument(
        "--limit",
        type=_non_negative_int,
        default=0,
        metavar="N",
        help=(
            "fetch the N most recently updated open issues, plus whatever "
            "changed since the last run (default: 0, every open issue)"
        ),
    )
    summary.add_argument(
        "--model",
        default="",
        metavar="NAME",
        help=(
            "model to tag with: haiku, sonnet or opus for Anthropic and "
            "Bedrock, or the model name for an OpenAI-compatible endpoint "
            "(default: TRIAGE_MODEL, then haiku)"
        ),
    )
    summary.add_argument(
        "--workers",
        type=_worker_count,
        default=classifier.DEFAULT_WORKERS,
        metavar="N",
        help=(
            "model requests sent at once, 1 to "
            f"{classifier.MAX_WORKERS}; lower it if the provider answers "
            f"with rate-limit errors (default: {classifier.DEFAULT_WORKERS})"
        ),
    )
    summary.add_argument(
        "--hot",
        action="store_true",
        help="list hot issues, not just count them",
    )
    summary.add_argument(
        "--stale",
        action="store_true",
        help="list stale issues, not just count them",
    )
    summary.add_argument(
        "--budget-usd",
        type=_positive_dollars,
        default=DEFAULT_BUDGET_USD,
        metavar="USD",
        help=(
            "most the model may spend this run; issues left over are "
            f"tagged next run (default: {DEFAULT_BUDGET_USD:.2f})"
        ),
    )

    list_command = commands.add_parser(
        "list",
        help="show the repositories you've summarized",
        description="Show the repositories you've summarized.",
    )
    list_command.add_argument(
        "--names",
        action="store_true",
        help="print only the names, one per line (Tab completion uses it)",
    )

    # No help=, so argparse leaves it out of the command list; it is only
    # for the eval line setup writes to the shell's startup file.
    completion_command = commands.add_parser(
        "completion",
        description=(
            "Print the Tab completion script for bash or zsh. triage setup "
            "can add it to your shell's startup file; to add it yourself, "
            'put eval "$(triage completion zsh)" in ~/.zshrc.'
        ),
    )
    completion_command.add_argument("shell", choices=sorted(completion.SCRIPTS))
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


def _worker_count(text: str) -> int:
    """Parses a worker count from 1 to MAX_WORKERS, for argparse."""
    value = _non_negative_int(text)
    if not 1 <= value <= classifier.MAX_WORKERS:
        raise argparse.ArgumentTypeError(
            f"must be 1 to {classifier.MAX_WORKERS}, got {value}"
        )
    return value


def _positive_dollars(text: str) -> float:
    """Parses a dollar amount above 0, for argparse."""
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"not a dollar amount: {text!r}"
        ) from None
    if not value > 0:
        raise argparse.ArgumentTypeError(f"must be above 0, got {text}")
    return value


def _configure_logging(
    verbose: bool, status: progress.Status
) -> progress.LogHandler:
    """Sends warnings to stderr, and debug logs too with ``-v``.

    Args:
        verbose: Whether to show debug logs.
        status: The status line log lines must keep clear of.

    Returns:
        The handler, which counts the lines it printed.
    """
    handler = progress.LogHandler(status)
    handler.setFormatter(
        logging.Formatter(
            "%(levelname)s %(name)s: %(message)s"
            if verbose
            else "triage: warning: %(message)s"
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.WARNING)
    logging.getLogger("triage").setLevel(
        logging.DEBUG if verbose else logging.WARNING
    )
    return handler
