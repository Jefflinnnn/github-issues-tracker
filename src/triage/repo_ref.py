"""Parses the ways a user can name a GitHub repository."""

import dataclasses
import re

# GitHub user and organization names allow only letters, digits and '-'.
# Rejecting '.' here stops "github.com/owner" parsing as an owner/name pair.
_OWNER = r"[A-Za-z0-9-]+"
# Repository names also allow '_' and '.'.
_NAME = r"[A-Za-z0-9_.-]+"

_PATTERNS = (
    # https://github.com/owner/name, optionally with .git or a deeper path
    # such as /issues or /tree/main.
    re.compile(
        rf"^(?:https?://)?(?:www\.)?github\.com/(?P<owner>{_OWNER})/"
        rf"(?P<name>{_NAME}?)(?:\.git)?(?:/.*)?$"
    ),
    # git@github.com:owner/name.git
    re.compile(
        rf"^git@github\.com:(?P<owner>{_OWNER})/(?P<name>{_NAME}?)(?:\.git)?$"
    ),
    # owner/name
    re.compile(rf"^(?P<owner>{_OWNER})/(?P<name>{_NAME}?)(?:\.git)?$"),
)


class InvalidRepoRefError(ValueError):
    """Raised when text can't be read as a GitHub repository."""


@dataclasses.dataclass(frozen=True)
class RepoRef:
    """A GitHub repository identified by owner and name.

    Attributes:
        owner: The user or organization that owns the repository.
        name: The repository name.
    """

    owner: str
    name: str

    @property
    def full_name(self) -> str:
        """Returns the repository as ``owner/name``."""
        return f"{self.owner}/{self.name}"


def parse(text: str) -> RepoRef:
    """Parses a GitHub link, SSH remote, or ``owner/name`` string.

    Args:
        text: What the user typed, for example
            ``https://github.com/owner/name/issues``.

    Returns:
        The repository the text refers to.

    Raises:
        InvalidRepoRefError: The text isn't a recognizable GitHub
            repository.
    """
    candidate = text.strip().rstrip("/")
    for pattern in _PATTERNS:
        match = pattern.match(candidate)
        if match and match["name"] not in ("", ".", ".."):
            return RepoRef(owner=match["owner"], name=match["name"])
    raise InvalidRepoRefError(
        f"Not a GitHub repository: {text!r}. Use a link such as "
        "https://github.com/owner/name or the form owner/name."
    )
