"""Runs ``triage setup``: asks for each setting and saves it.

Four steps: the GitHub token, Tab completion, what kind of model access
you have (with its keys), and a model from a short list of common ones.
Menus move with the arrow keys, or take a number where the terminal can't
do arrows. Secrets are read without echo and never printed back. Pressing
Enter keeps a saved value, so running setup again changes only what you
change. Settings go to the per-user settings file, readable only by you;
Tab completion goes to your shell's startup file, after you say yes.
"""

import dataclasses
import getpass
import os
import pathlib
import urllib.parse
from collections.abc import Callable
from collections.abc import Mapping

from triage import completion
from triage import config
from triage import menu
from triage.model import anthropic_api
from triage.model import bedrock
from triage.model import openai_compat
from triage.model import pricing
from triage.model import provider
from triage.model import providers
from triage.pipeline import extract

TOKEN_URL = "https://github.com/settings/personal-access-tokens/new"
# Typed at the gateway prompt to remove a saved gateway.
NO_GATEWAY = "-"
OLLAMA_URL = "http://localhost:11434/v1"
LM_STUDIO_URL = "http://localhost:1234/v1"
# Models known to call tools well on a laptop; pull one before using it.
LOCAL_MODELS = ("qwen3:8b", "llama3.1:8b")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}

# The first question: what the user has. Each maps to a provider; the last
# two are OpenAI-compatible servers other than OpenAI itself.
SOURCES = {
    "anthropic": "Anthropic API key",
    "openai": "OpenAI API key",
    "bedrock": "AWS keys for Amazon Bedrock",
    "local": "A local model (Ollama, LM Studio)",
    "other": "Another OpenAI-compatible service (Groq, OpenRouter, vLLM, ...)",
}
# How the summary at the top of each step names each kind of access.
_SHORT = {
    "anthropic": "Anthropic API",
    "openai": "OpenAI",
    "bedrock": "Amazon Bedrock",
    "local": "Local model",
    "other": "Other OpenAI-compatible service",
}
_PROVIDER = {
    "anthropic": "anthropic",
    "openai": "openai",
    "bedrock": "bedrock",
    "local": "openai",
    "other": "openai",
}


def _ask_line(prompt: str) -> str:
    """Reads a typed answer; Left on an empty line, or "<", goes back."""
    if menu.supported():
        return menu.read_line(prompt)
    return _back_if_asked(input(prompt))


def _ask_secret(prompt: str) -> str:
    """Reads a hidden answer; Left on an empty line, or "<", goes back."""
    if menu.supported():
        return menu.read_line(prompt, secret=True)
    return _back_if_asked(getpass.getpass(prompt))


def _back_if_asked(answer: str) -> str:
    """Turns a typed "<" into going back, where arrow keys don't work."""
    if answer.strip() == "<":
        raise menu.BackError
    return answer


@dataclasses.dataclass(frozen=True)
class Terminal:
    """How setup talks to the user; tests pass scripted stand-ins.

    Any of ``ask``, ``ask_secret`` and ``choose`` may raise
    ``menu.BackError`` to go back to the previous question.

    Attributes:
        ask: Reads a line, showing what is typed.
        ask_secret: Reads a line without showing it.
        say: Prints a line.
        choose: Shows a title and options, starting on the given index, and
            returns the index picked. None uses the arrow-key menu, or
            numbers where the terminal can't do arrows.
        fullscreen: Show each step on a second screen and return to the
            normal one at the end. None decides: yes when the arrow-key
            menu is in use.
    """

    ask: Callable[[str], str] = _ask_line
    ask_secret: Callable[[str], str] = _ask_secret
    say: Callable[[str], None] = print
    choose: Callable[[str, list[str], int], int] | None = None
    fullscreen: bool | None = None

    def uses_fullscreen(self) -> bool:
        """Says whether setup should draw each step on a second screen."""
        if self.fullscreen is not None:
            return self.fullscreen
        return self.choose is None and menu.supported()

    def pick(self, title: str, options: list[str], default: int) -> int:
        """Asks the user to pick one option.

        Args:
            title: The question.
            options: One line per option.
            default: The option Enter picks.

        Returns:
            The picked option's index.

        Raises:
            menu.BackError: The user asked to go back.
        """
        if self.choose is not None:
            return self.choose(title, options, default)
        if menu.supported():
            return menu.choose(f"  {title}", options, default)
        self.say(f"  {title}")
        for number, option in enumerate(options, 1):
            self.say(f"    {number}. {option}")
        while True:
            answer = self.ask(
                f"  Pick 1-{len(options)} [{default + 1}]: "
            ).strip()
            if not answer:
                return default
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                return int(answer) - 1
            self.say(f"  Type a number from 1 to {len(options)}.")


def run(
    terminal: Terminal | None = None,
    environ: Mapping[str, str] | None = None,
    home: pathlib.Path | None = None,
) -> None:
    """Asks for each setting, saves the answers and offers a test request.

    Each step can go back to the one before. Answers given so far are kept
    while going back and forth, so Enter keeps them too; nothing is saved
    until the last step is done.

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
    intro = [f"triage setup saves to {path}, which only you can read."]
    if menu.supported():
        intro.append(
            "Up/Down and Enter pick, Left goes back, and Enter on a prompt "
            "keeps the saved value."
        )
    else:
        intro.append(
            "Type a number to pick, < to go back, and press Enter on a "
            "prompt to keep the saved value."
        )

    # Saved settings plus the answers so far; steps read and update it.
    view = dict(saved)
    progress = {
        "source": _source(saved),
        # Which kind of access the current TRIAGE_MODEL belongs to.
        "model_source": _source(saved) if saved.get("TRIAGE_MODEL") else "",
    }
    screen = menu.Screen() if terminal.uses_fullscreen() else None

    def token_step() -> tuple[bool, str]:
        changes, summary = _github_token(terminal, view.get("GITHUB_TOKEN", ""))
        view.update(changes)
        return True, summary

    def completion_step() -> tuple[bool, str]:
        return _tab_completion(
            terminal,
            os.environ if environ is None else environ,
            home or pathlib.Path.home(),
        )

    def access_step() -> tuple[bool, str]:
        source = progress["source"]
        while True:
            source = _pick_source(terminal, source, _source(saved))
            try:
                view.update(_KEYS[source](terminal, view))
            except menu.BackError:
                show(access_index)
                continue
            view["TRIAGE_PROVIDER"] = _PROVIDER[source]
            progress["source"] = source
            return True, _SHORT[source]

    def model_step() -> tuple[bool, str]:
        source = progress["source"]
        # A model chosen for another kind of access means nothing here.
        current = (
            view.get("TRIAGE_MODEL", "")
            if progress["model_source"] == source
            else ""
        )
        view.update(_MODELS[source](terminal, view, current))
        progress["model_source"] = source
        model = view.get("TRIAGE_MODEL", "")
        if source == "local":
            return True, f"{model} (pull it first: ollama pull {model})"
        return True, model

    steps: list[tuple[str, Callable[[], tuple[bool, str]]]] = [
        ("GitHub token", token_step),
        ("Tab completion", completion_step),
        ("Model access", access_step),
        ("Model", model_step),
    ]
    access_index = 2  # Redrawn when going back inside the access step.
    asked = [False] * len(steps)
    summaries = [""] * len(steps)

    def show(index: int) -> None:
        """Starts a step: on a fresh screen, the answers so far come first."""
        if screen is not None:
            screen.clear()
            for line in intro:
                say(line)
            say("")
            for (title, _), summary in zip(
                steps[:index], summaries, strict=False
            ):
                say(f"  {title:<15} {summary}")
        say(f"\n[{index + 1}/{len(steps)}] {steps[index][0]}")

    if screen is None:
        for line in intro:
            say(line)
    else:
        screen.enter()
    index = 0
    try:
        while index < len(steps):
            show(index)
            try:
                asked[index], summaries[index] = steps[index][1]()
            except menu.BackError:
                # Back to the nearest earlier step that asked something.
                earlier = [i for i in range(index) if asked[i]]
                if earlier:
                    index = earlier[-1]
                continue
            index += 1
    finally:
        if screen is not None:
            screen.leave()

    if screen is not None:
        say("triage setup")
        for (title, _), summary in zip(steps, summaries, strict=True):
            say(f"  {title:<15} {summary}")

    changes = {
        key: value for key, value in view.items() if value != saved.get(key, "")
    }
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

    if _yes(terminal, "\nSend a tiny test request to check the key and model?"):
        try:
            model = providers.from_settings()
            model.check()
        except provider.ProviderError as error:
            say(f"✗ {error}")
            say("Run triage setup again to fix it.")
            return
        say(f"✓ {model.model} answered.")
    say("\nReady. Try: triage summary owner/name")


def _yes(terminal: Terminal, question: str) -> bool:
    """Asks a yes/no question where Enter means yes.

    Going back isn't possible here, so a Left press asks again.
    """
    while True:
        try:
            answer = terminal.ask(f"{question} [Y/n] ")
        except menu.BackError:
            continue
        return answer.strip().lower() in ("", "y", "yes")


def _github_token(
    terminal: Terminal, current: str
) -> tuple[dict[str, str], str]:
    """Asks for a GitHub token until GitHub accepts one, or keeps the saved.

    Returns:
        The settings to change and a one-line summary.
    """
    terminal.say(
        f"  Create a read-only token at {TOKEN_URL}: public repositories, "
        "read-only, no permissions."
    )
    keep = f" [Enter keeps …{current[-4:]}]" if current else ""
    while True:
        token = terminal.ask_secret(f"  Token (hidden){keep}: ").strip()
        if not token:
            if current:
                return {}, f"saved (…{current[-4:]})"
            terminal.say("  A token is required; triage reads GitHub with it.")
            continue
        try:
            login = extract.check_token(token)
        except extract.GitHubError as error:
            terminal.say(f"  ✗ {error}")
            continue
        terminal.say(f"  ✓ Works for {login}.")
        return {"GITHUB_TOKEN": token}, f"✓ works for {login}"


def _tab_completion(
    terminal: Terminal, environ: Mapping[str, str], home: pathlib.Path
) -> tuple[bool, str]:
    """Offers to add the completion lines to the shell's startup file.

    Returns:
        Whether it asked a question, so going back can skip it otherwise,
        and a one-line summary.
    """
    shell = completion.detect_shell(environ)
    if shell is None:
        terminal.say(
            "  Tab completion works in zsh and bash; skipping it for your "
            "shell."
        )
        return False, "not available for your shell"
    path = completion.startup_file(shell, environ, home)
    shown = _home_relative(path, home)
    if completion.is_installed(path, shell):
        terminal.say(f"  ✓ Already on in {shown}.")
        return False, f"✓ on in {shown}"
    terminal.say(
        "  Press Tab to complete commands, repository names and models. "
        f"In {shown}:"
    )
    for line in completion.setup_lines(shell).splitlines():
        terminal.say(f"    {line}")
    answer = terminal.ask(f"  Add these 2 lines to {shown}? [Y/n] ")
    if answer.strip().lower() not in ("", "y", "yes"):
        terminal.say("  Skipped. Run triage setup again to turn it on later.")
        return True, "skipped"
    completion.install(path, shell)
    terminal.say(f"  ✓ Added to {shown}. Open a new terminal to use it.")
    return True, f"✓ added to {shown}; open a new terminal"


def _home_relative(path: pathlib.Path, home: pathlib.Path) -> str:
    """Shows a path under the home folder as ``~/...``."""
    try:
        return f"~/{path.relative_to(home)}"
    except ValueError:
        return str(path)


def _source(saved: Mapping[str, str]) -> str:
    """Works out which kind of access saved settings describe."""
    provider_name = saved.get("TRIAGE_PROVIDER", "")
    if provider_name not in providers.LABELS:
        if saved.get("OPENAI_API_KEY") or saved.get("OPENAI_BASE_URL"):
            provider_name = "openai"
        elif saved.get("AWS_ACCESS_KEY_ID") and not saved.get(
            "ANTHROPIC_API_KEY"
        ):
            provider_name = "bedrock"
        else:
            provider_name = "anthropic"
    if provider_name != "openai":
        return provider_name
    base_url = saved.get("OPENAI_BASE_URL", "")
    if not base_url:
        return "openai"
    host = urllib.parse.urlparse(base_url).hostname or ""
    return "local" if host in _LOCAL_HOSTS else "other"


def _pick_source(terminal: Terminal, default: str, current: str) -> str:
    """Asks what kind of model access the user has.

    Args:
        terminal: Where to ask.
        default: The option highlighted first.
        current: The option the saved settings describe, marked as such.

    Returns:
        The kind picked, one of ``SOURCES``.
    """
    names = list(SOURCES)
    labels = [
        SOURCES[name] + ("  (current)" if name == current else "")
        for name in names
    ]
    index = terminal.pick("What do you have?", labels, names.index(default))
    return names[index]


def _anthropic_keys(
    terminal: Terminal, saved: Mapping[str, str]
) -> dict[str, str]:
    """Asks for the Anthropic API key."""
    terminal.say("  Create a key at https://platform.claude.com.")
    return _required_secret(
        terminal, saved, "ANTHROPIC_API_KEY", "Anthropic API key"
    )


def _openai_keys(
    terminal: Terminal, saved: Mapping[str, str]
) -> dict[str, str]:
    """Asks for an OpenAI key and points the base URL at OpenAI."""
    terminal.say("  Create a key at https://platform.openai.com/api-keys.")
    changes = _required_secret(
        terminal, saved, "OPENAI_API_KEY", "OpenAI API key"
    )
    if saved.get("OPENAI_BASE_URL"):
        changes["OPENAI_BASE_URL"] = ""
    return changes


def _local_keys(terminal: Terminal, saved: Mapping[str, str]) -> dict[str, str]:
    """Asks where the local server listens; a local server needs no key."""
    current = saved.get("OPENAI_BASE_URL", "")
    default = current if _source(saved) == "local" else OLLAMA_URL
    terminal.say(
        f"  Ollama listens on {OLLAMA_URL}; LM Studio on {LM_STUDIO_URL}."
    )
    url = terminal.ask(f"  Base URL [{default}]: ").strip() or default
    changes = {} if url == current else {"OPENAI_BASE_URL": url}
    if saved.get("OPENAI_API_KEY"):
        changes["OPENAI_API_KEY"] = ""
    return changes


def _other_keys(terminal: Terminal, saved: Mapping[str, str]) -> dict[str, str]:
    """Asks for another service's base URL and key."""
    current = (
        saved.get("OPENAI_BASE_URL", "") if _source(saved) == "other" else ""
    )
    keep = f" [{current}]" if current else ""
    while True:
        url = terminal.ask(
            f"  Base URL, e.g. https://api.groq.com/openai/v1{keep}: "
        ).strip()
        if url or current:
            break
        terminal.say("  The service's base URL is required.")
    changes = {"OPENAI_BASE_URL": url} if url and url != current else {}
    return changes | _secret(terminal, saved, "OPENAI_API_KEY", "API key")


def _secret(
    terminal: Terminal, saved: Mapping[str, str], key: str, label: str
) -> dict[str, str]:
    """Asks for one secret without echo; Enter keeps the saved one."""
    keep = " [Enter keeps the saved one]" if saved.get(key) else ""
    value = terminal.ask_secret(f"  {label} (hidden){keep}: ").strip()
    return {key: value} if value else {}


def _required_secret(
    terminal: Terminal, saved: Mapping[str, str], key: str, label: str
) -> dict[str, str]:
    """Like ``_secret``, but asks again until there is a value."""
    while True:
        changes = _secret(terminal, saved, key, label)
        if changes or saved.get(key):
            return changes
        terminal.say(f"  The {label} is required.")


def _bedrock_keys(
    terminal: Terminal, saved: Mapping[str, str]
) -> dict[str, str]:
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


def _claude_model(
    models: Mapping[str, provider.Model],
) -> Callable[[Terminal, Mapping[str, str], str], dict[str, str]]:
    """Returns a step that picks one of a Claude provider's three models."""

    def step(
        terminal: Terminal, saved: Mapping[str, str], saved_model: str
    ) -> dict[str, str]:
        names = list(models)
        labels = [
            f"{name:<7} {models[name].id}  {_price(models[name].price)}"
            for name in names
        ]
        default = names.index(saved_model) if saved_model in names else 0
        picked = names[terminal.pick("Which model?", labels, default)]
        return (
            {}
            if picked == saved.get("TRIAGE_MODEL")
            else {"TRIAGE_MODEL": picked}
        )

    return step


def _openai_model(
    terminal: Terminal, saved: Mapping[str, str], saved_model: str
) -> dict[str, str]:
    """Picks one of OpenAI's suggested models, or another one by name."""
    names = list(openai_compat.OPENAI_PRICES)
    labels = [
        f"{name:<14} {note}  {_price(openai_compat.OPENAI_PRICES[name])}"
        for name, note in zip(names, openai_compat.OPENAI_NOTES, strict=True)
    ] + ["Another model (type its name and price)"]
    if saved_model in names:
        default = names.index(saved_model)
    else:
        default = len(names) if saved_model else 0
    while True:
        index = terminal.pick("Which model?", labels, default)
        if index < len(names):
            # triage knows these prices, so no TRIAGE_PRICE_PER_MTOK.
            return _model_changes(saved, names[index], "")
        try:
            return _typed_model(terminal, saved, saved_model, "gpt-6-luna")
        except menu.BackError:
            default = index


def _local_model(
    terminal: Terminal, saved: Mapping[str, str], saved_model: str
) -> dict[str, str]:
    """Picks a suggested local model, or another one by name."""
    names = list(LOCAL_MODELS)
    labels = [f"{name}  free, runs on your machine" for name in names] + [
        "Another model (type its name)"
    ]
    if saved_model in names:
        default = names.index(saved_model)
    else:
        default = len(names) if saved_model else 0
    while True:
        index = terminal.pick("Which model?", labels, default)
        if index < len(names):
            name = names[index]
            break
        try:
            name = _ask_name(terminal, saved_model, "qwen3:14b")
            break
        except menu.BackError:
            default = index
    return _model_changes(saved, name, "0,0")


def _other_model(
    terminal: Terminal, saved: Mapping[str, str], saved_model: str
) -> dict[str, str]:
    """Asks for the model name and price on another service."""
    return _typed_model(terminal, saved, saved_model, "llama-3.3-70b-versatile")


def _typed_model(
    terminal: Terminal, saved: Mapping[str, str], saved_model: str, example: str
) -> dict[str, str]:
    """Asks for a model name and its price."""
    name = _ask_name(terminal, saved_model, example)
    current_price = saved.get("TRIAGE_PRICE_PER_MTOK", "")
    while True:
        keep = f" [{current_price}]" if current_price else ""
        price = (
            terminal.ask(
                f"  Price in USD per million tokens, input,output{keep}: "
            ).strip()
            or current_price
        )
        try:
            openai_compat.parse_price(price)
        except provider.ProviderError as error:
            terminal.say(f"  ✗ {error}")
            continue
        return _model_changes(saved, name, price)


def _ask_name(terminal: Terminal, saved_model: str, example: str) -> str:
    """Asks for a model name; Enter keeps the saved one."""
    keep = f" [{saved_model}]" if saved_model else ""
    while True:
        name = terminal.ask(f"  Model name, e.g. {example}{keep}: ").strip()
        if name or saved_model:
            return name or saved_model
        terminal.say("  A model name is required.")


def _model_changes(
    saved: Mapping[str, str], model: str, price: str
) -> dict[str, str]:
    """Returns the settings that differ from what's saved."""
    wanted = {"TRIAGE_MODEL": model, "TRIAGE_PRICE_PER_MTOK": price}
    return {
        key: value
        for key, value in wanted.items()
        if value != saved.get(key, "")
    }


def _price(price: "pricing.Price") -> str:
    """Formats a price for a menu line."""
    return (
        f"${price.input_per_million:.2f} / ${price.output_per_million:.2f} "
        "per M tokens"
    )


_KEYS: dict[str, Callable[[Terminal, Mapping[str, str]], dict[str, str]]] = {
    "anthropic": _anthropic_keys,
    "openai": _openai_keys,
    "bedrock": _bedrock_keys,
    "local": _local_keys,
    "other": _other_keys,
}
_MODELS: dict[
    str, Callable[[Terminal, Mapping[str, str], str], dict[str, str]]
] = {
    "anthropic": _claude_model(anthropic_api.MODELS),
    "openai": _openai_model,
    "bedrock": _claude_model(bedrock.MODELS),
    "local": _local_model,
    "other": _other_model,
}
