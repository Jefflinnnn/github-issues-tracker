"""Runs ``triage setup``: asks for each setting and saves it.

Four steps: the GitHub token, Tab completion, the Bedrock key pair and
gateway, and the model. Secrets are read without echo and never printed
back. Pressing Enter keeps a saved value, so running setup again changes
only what you change. Settings go to the per-user settings file, readable
only by you; Tab completion goes to your shell's startup file, after you
say yes.
"""

import dataclasses
import getpass
import os
import pathlib
from collections.abc import Callable
from collections.abc import Mapping

from triage import completion
from triage import config
from triage.model import bedrock
from triage.model import provider
from triage.pipeline import extract

TOKEN_URL = "https://github.com/settings/personal-access-tokens/new"
# Typed at the gateway prompt to remove a saved gateway.
NO_GATEWAY = "-"


@dataclasses.dataclass(frozen=True)
class Terminal:
    """How setup talks to the user; tests pass scripted stand-ins.

    Attributes:
        ask: Reads a line, showing what is typed.
        ask_secret: Reads a line without showing it.
        say: Prints a line.
    """

    ask: Callable[[str], str] = input
    ask_secret: Callable[[str], str] = getpass.getpass
    say: Callable[[str], None] = print


def run(
    terminal: Terminal | None = None,
    environ: Mapping[str, str] | None = None,
    home: pathlib.Path | None = None,
) -> None:
    """Asks for each setting, saves the answers and offers a test request.

    Args:
        terminal: Where to read answers and print messages; None uses the
            real terminal.
        environ: Environment variables, to find your shell; None uses the
            real ones.
        home: Your home folder, where the shell's startup file lives; None
            uses the real one.
    """
    terminal = terminal or Terminal()
    say = terminal.say
    path = config.user_env_file()
    saved = config.read_env_file(path)
    say(f"triage setup saves to {path}, which only you can read.")
    say("Press Enter to keep a saved value.")

    changes: dict[str, str] = {}
    say("\n[1/4] GitHub token")
    changes |= _github_token(terminal, saved.get("GITHUB_TOKEN", ""))
    say("\n[2/4] Tab completion")
    _tab_completion(
        terminal,
        os.environ if environ is None else environ,
        home or pathlib.Path.home(),
    )
    say("\n[3/4] Amazon Bedrock")
    changes |= _bedrock(terminal, saved)
    say("\n[4/4] Model")
    changes |= _model(terminal, saved.get("TRIAGE_MODEL", ""))

    if changes:
        config.write_env_file(changes, path)
        say(f"\nSaved to {path}.")
    else:
        say("\nNothing changed.")
    overridden = sorted(
        key for key in changes if config.read_env_file(config.ENV_FILE).get(key)
    )
    if overridden:
        say(
            f"Note: .env in this folder sets {', '.join(overridden)}, which "
            "wins over setup's values when you run triage here."
        )

    answer = terminal.ask("\nSend a tiny test request to Bedrock? [Y/n] ")
    if answer.strip().lower() in ("", "y", "yes"):
        try:
            model = bedrock.from_settings()
            model.check()
        except provider.ProviderError as error:
            say(f"✗ {error}")
            say("Run triage setup again to fix it.")
            return
        say(f"✓ {model.model} answered.")
    say("\nReady. Try: triage summary owner/name")


def _github_token(terminal: Terminal, current: str) -> dict[str, str]:
    """Asks for a GitHub token until GitHub accepts one, or keeps the saved."""
    terminal.say(
        f"  Create a read-only token at {TOKEN_URL}: public repositories, "
        "read-only, no permissions."
    )
    keep = f" [Enter keeps …{current[-4:]}]" if current else ""
    while True:
        token = terminal.ask_secret(f"  Token (hidden){keep}: ").strip()
        if not token:
            if current:
                return {}
            terminal.say("  A token is required; triage reads GitHub with it.")
            continue
        try:
            login = extract.check_token(token)
        except extract.GitHubError as error:
            terminal.say(f"  ✗ {error}")
            continue
        terminal.say(f"  ✓ Works for {login}.")
        return {"GITHUB_TOKEN": token}


def _tab_completion(
    terminal: Terminal, environ: Mapping[str, str], home: pathlib.Path
) -> None:
    """Offers to add the completion lines to the shell's startup file."""
    shell = completion.detect_shell(environ)
    if shell is None:
        terminal.say(
            "  Tab completion works in zsh and bash; skipping it for your "
            "shell."
        )
        return
    path = completion.startup_file(shell, environ, home)
    shown = _home_relative(path, home)
    if completion.is_installed(path, shell):
        terminal.say(f"  ✓ Already on in {shown}.")
        return
    terminal.say(
        "  Press Tab to complete commands, repository names and models. "
        f"In {shown}:"
    )
    for line in completion.setup_lines(shell).splitlines():
        terminal.say(f"    {line}")
    answer = terminal.ask(f"  Add these 2 lines to {shown}? [Y/n] ")
    if answer.strip().lower() not in ("", "y", "yes"):
        terminal.say("  Skipped. Run triage setup again to turn it on later.")
        return
    completion.install(path, shell)
    terminal.say(f"  ✓ Added to {shown}. Open a new terminal to use it.")


def _home_relative(path: pathlib.Path, home: pathlib.Path) -> str:
    """Shows a path under the home folder as ``~/...``."""
    try:
        return f"~/{path.relative_to(home)}"
    except ValueError:
        return str(path)


def _bedrock(terminal: Terminal, saved: dict[str, str]) -> dict[str, str]:
    """Asks for the AWS key pair, region and gateway."""
    changes: dict[str, str] = {}
    access = saved.get("AWS_ACCESS_KEY_ID", "")
    keep = f" [Enter keeps …{access[-4:]}]" if access else ""
    value = terminal.ask(f"  AWS access key ID{keep}: ").strip()
    if value:
        changes["AWS_ACCESS_KEY_ID"] = value

    has_secret = bool(saved.get("AWS_SECRET_ACCESS_KEY"))
    keep = " [Enter keeps the saved one]" if has_secret else ""
    value = terminal.ask_secret(f"  AWS secret access key (hidden){keep}: ")
    if value.strip():
        changes["AWS_SECRET_ACCESS_KEY"] = value.strip()

    region = saved.get("AWS_REGION") or bedrock.DEFAULT_REGION
    value = terminal.ask(f"  AWS region [{region}]: ").strip()
    if value or not saved.get("AWS_REGION"):
        changes["AWS_REGION"] = value or region

    has_gateway = bool(saved.get("BEDROCK_ENDPOINT_URL"))
    hint = (
        f"Enter keeps the saved one, {NO_GATEWAY} for none"
        if has_gateway
        else "Enter for none: AWS directly"
    )
    value = terminal.ask_secret(f"  Gateway URL (hidden; {hint}): ").strip()
    if value == NO_GATEWAY:
        changes["BEDROCK_ENDPOINT_URL"] = ""
    elif value:
        changes["BEDROCK_ENDPOINT_URL"] = value
    return changes


def _model(terminal: Terminal, saved: str) -> dict[str, str]:
    """Asks which of the three models to tag with."""
    names = list(bedrock.MODELS)
    current = saved if saved in names else bedrock.DEFAULT_MODEL
    for number, name in enumerate(names, 1):
        marker = "  (current)" if name == current else ""
        terminal.say(f"  {number}. {name:<7} {bedrock.MODELS[name].id}{marker}")
    while True:
        choice = terminal.ask(f"  Pick 1-{len(names)} [{current}]: ").strip()
        if not choice:
            picked = current
        elif choice.isdigit() and 1 <= int(choice) <= len(names):
            picked = names[int(choice) - 1]
        elif choice.lower() in names:
            picked = choice.lower()
        else:
            terminal.say(f"  Type a number from 1 to {len(names)}.")
            continue
        return {} if picked == saved else {"TRIAGE_MODEL": picked}
