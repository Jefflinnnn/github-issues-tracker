"""Reads and writes settings: a project ``.env``, a per-user file, the shell.

``triage setup`` writes the per-user file, so triage finds its keys from
any folder. A ``.env`` in the current folder still wins, for a one-off
override.
"""

import os
import pathlib
from collections.abc import Mapping

ENV_FILE = pathlib.Path(".env")


class ConfigError(RuntimeError):
    """Raised when a required setting is missing or invalid."""


def user_env_file() -> pathlib.Path:
    """Returns the per-user settings file ``triage setup`` writes.

    Returns:
        ``$XDG_CONFIG_HOME/triage/.env``, by default
        ``~/.config/triage/.env``.
    """
    config_home = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return pathlib.Path(config_home).expanduser() / "triage" / ".env"


def get(key: str, env_file: pathlib.Path = ENV_FILE) -> str:
    """Returns a setting from the first place that sets it.

    The order is the project ``.env``, the per-user file, then the
    environment. Files win over the environment because several keys share
    names with Claude Code's settings, which a shell often exports for that
    other tool. An empty value counts as unset.

    Args:
        key: The variable name, e.g. ``GITHUB_TOKEN``.
        env_file: The project dotenv file.

    Returns:
        The value without surrounding whitespace or quotes, or an empty
        string when it is set nowhere.
    """
    for path in (env_file, user_env_file()):
        value = read_env_file(path).get(key, "")
        if value:
            return value
    return os.environ.get(key, "").strip()


def github_token(env_file: pathlib.Path = ENV_FILE) -> str:
    """Returns the GitHub token.

    Args:
        env_file: The project dotenv file.

    Returns:
        The token.

    Raises:
        ConfigError: No token is set.
    """
    token = get("GITHUB_TOKEN", env_file)
    if not token:
        raise ConfigError(
            "No GitHub token found. Run triage setup, or set GITHUB_TOKEN "
            "in .env; the README shows how to create a read-only token."
        )
    return token


def database_path(env_file: pathlib.Path = ENV_FILE) -> pathlib.Path:
    """Returns where the tool keeps its SQLite file.

    One file per user, so ``triage list`` shows the same history from any
    folder. ``TRIAGE_DB`` overrides the location.

    Args:
        env_file: The project dotenv file.

    Returns:
        The path; its folder may not exist yet.
    """
    override = get("TRIAGE_DB", env_file)
    if override:
        return pathlib.Path(override).expanduser()
    data_home = os.environ.get("XDG_DATA_HOME") or "~/.local/share"
    return pathlib.Path(data_home).expanduser() / "triage" / "triage.db"


def read_env_file(env_file: pathlib.Path) -> dict[str, str]:
    """Returns the ``KEY=value`` lines of a simple dotenv file.

    Args:
        env_file: The file; a missing one sets nothing.

    Returns:
        Values without surrounding whitespace or quotes. When a key appears
        twice, the first line wins.
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


def write_env_file(values: Mapping[str, str], env_file: pathlib.Path) -> None:
    """Sets keys in a dotenv file, keeping its other lines as they are.

    A key already in the file is replaced where it stands; a new key is
    added at the end. The file and its folder are readable only by you,
    since they hold secrets.

    Args:
        values: The keys and values to set.
        env_file: The file to change.
    """
    env_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lines = (
        env_file.read_text(encoding="utf-8").splitlines()
        if env_file.is_file()
        else []
    )
    remaining = dict(values)
    for index, line in enumerate(lines):
        name, separator, _ = line.strip().partition("=")
        key = name.strip().removeprefix("export ")
        if separator and key in remaining:
            lines[index] = f"{key}={remaining.pop(key)}"
    lines += [f"{key}={value}" for key, value in remaining.items()]
    env_file.touch(mode=0o600)
    env_file.chmod(0o600)
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
