"""Command-line entry point for ``triage``."""

import argparse
import importlib.metadata
import logging
import sys
from collections.abc import Sequence

_DESCRIPTION = (
    "Summarize a GitHub repository's open issues: what needs you first, "
    "and what changed since your last run."
)


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
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    parser.print_help()
    return 0


def _build_parser() -> argparse.ArgumentParser:
    """Returns the argument parser for ``triage``."""
    parser = argparse.ArgumentParser(prog="triage", description=_DESCRIPTION)
    parser.add_argument(
        "--version",
        action="version",
        version=importlib.metadata.version("github-issues-tracker"),
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="show debug logs"
    )
    return parser
