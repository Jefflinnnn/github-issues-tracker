"""Allows running the CLI with ``python -m triage``."""

import sys

from triage import cli

sys.exit(cli.main())
