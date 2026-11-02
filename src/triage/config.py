"""Reads settings from a ``.env`` file or the environment."""

import os
import pathlib

ENV_FILE = pathlib.Path(".env")


class ConfigError(RuntimeError):
    """Raised when a required setting is missing or invalid."""


def get(key: str, env_file: pathlib.Path = ENV_FILE) -> str:
    """Returns a setting, preferring the ``.env`` file over the environment.

    ``.env`` wins because several keys share names with Claude Code's
    settings, which a shell often exports for that other tool. A key that
    ``.env`` leaves empty or doesn't list falls back to the environment, so
    CI and one-off runs can still pass values that way.

    Args:
        key: The variable name, e.g. ``GITHUB_TOKEN``.
        env_file: The dotenv file to read first.

    Returns:
        The value without surrounding whitespace or quotes, or an empty
        string when it is set nowhere.
    """
    value = _read_env_file(env_file).get(key, "")
    return value or os.environ.get(key, "").strip()


def github_token(env_file: pathlib.Path = ENV_FILE) -> str:
    """Returns the GitHub token.

    Args:
        env_file: The dotenv file to read first.

    Returns:
        The token.

    Raises:
        ConfigError: No token is set.
    """
    token = get("GITHUB_TOKEN", env_file)
    if not token:
        raise ConfigError(
            "No GitHub token found. Set GITHUB_TOKEN in .env in this folder "
            "or export it in your shell; the README shows how to create a "
            "read-only token."
        )
    return token


def database_path(env_file: pathlib.Path = ENV_FILE) -> pathlib.Path:
    """Returns where the tool keeps its SQLite file.

    One file per user, so ``triage list`` shows the same history from any
    folder. ``TRIAGE_DB`` overrides the location.

    Args:
        env_file: The dotenv file to read first.

    Returns:
        The path; its folder may not exist yet.
    """
    override = get("TRIAGE_DB", env_file)
    if override:
        return pathlib.Path(override).expanduser()
    data_home = os.environ.get("XDG_DATA_HOME") or "~/.local/share"
    return pathlib.Path(data_home).expanduser() / "triage" / "triage.db"


def _read_env_file(env_file: pathlib.Path) -> dict[str, str]:
    """Returns the ``KEY=value`` lines of a simple dotenv file.

    When a key appears twice, the first line wins. A missing file sets
    nothing.
    """
    if not env_file.is_file():
        return {}
    values: dict[str, str] = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.strip().partition("=")
        if separator and not name.startswith("#"):
            key = name.strip().removeprefix("export ")
            values.setdefault(key, value.strip().strip("'\""))
    return values
