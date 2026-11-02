"""Sanity checks for the repository's guardrail configuration.

These tests confirm that secrets and personal files stay out of git, that
Claude Code's permission rules block risky actions, and that CI runs on
pull requests. They catch silent regressions when config files are edited.
"""

import json
import pathlib
import subprocess

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
CLAUDE_SETTINGS_PATH = REPO_ROOT / ".claude" / "settings.json"
CLAUDE_MD_PATH = REPO_ROOT / "CLAUDE.md"
CI_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
PR_TEMPLATE_PATH = REPO_ROOT / ".github" / "pull_request_template.md"


def _is_git_ignored(relative_path: str) -> bool:
    """Returns whether git's ignore rules match a repo-relative path.

    Args:
        relative_path: Path relative to the repository root. The file does
            not need to exist.

    Returns:
        True if the path would be ignored by git.
    """
    result = subprocess.run(
        ["git", "check-ignore", "--quiet", "--no-index", relative_path],
        cwd=REPO_ROOT,
        check=False,
    )
    return result.returncode == 0


def _claude_deny_rules() -> list[str]:
    """Returns the deny rules from the shared Claude Code settings."""
    settings = json.loads(CLAUDE_SETTINGS_PATH.read_text(encoding="utf-8"))
    deny_rules: list[str] = settings["permissions"]["deny"]
    return deny_rules


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.local",
        "deploy.pem",
        ".claude/settings.local.json",
        "CLAUDE.local.md",
        "notes.local.md",
        ".venv/bin/python",
        "__pycache__/module.cpython-310.pyc",
    ],
)
def test_secrets_and_local_files_are_ignored(path: str) -> None:
    """Secrets, personal settings, and build artifacts never get committed."""
    assert _is_git_ignored(path)


@pytest.mark.parametrize(
    "path",
    [
        ".env.example",
        ".claude/settings.json",
        "CLAUDE.md",
        ".github/workflows/ci.yml",
    ],
)
def test_shared_config_is_not_ignored(path: str) -> None:
    """Shared project config stays committable."""
    assert not _is_git_ignored(path)


def test_no_env_file_is_tracked() -> None:
    """No real .env file has been committed by mistake."""
    tracked_files = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    tracked_env_files = [
        path
        for path in tracked_files
        if pathlib.PurePosixPath(path).name.startswith(".env")
        and not path.endswith(".env.example")
    ]
    assert tracked_env_files == []


@pytest.mark.parametrize(
    "rule",
    [
        "Read(./.env)",
        "Read(./.env.*)",
        "Bash(git push --force:*)",
        "Bash(git push origin main:*)",
        "Bash(gh pr merge:*)",
    ],
)
def test_claude_is_denied_risky_actions(rule: str) -> None:
    """Claude can't read secrets, force-push, push to main, or merge PRs."""
    assert rule in _claude_deny_rules()


@pytest.mark.parametrize(
    "heading",
    [
        "## Code style",
        "## Logging",
        "## Git workflow",
        "## Commit messages",
        "## Checks",
        "## Secrets",
        "## Tests",
    ],
)
def test_claude_md_has_required_sections(heading: str) -> None:
    """CLAUDE.md keeps every section of the house rules."""
    assert heading in CLAUDE_MD_PATH.read_text(encoding="utf-8")


def test_ci_runs_on_every_pull_request() -> None:
    """CI triggers on all PRs, so stacked PRs get checked too."""
    workflow = yaml.safe_load(CI_WORKFLOW_PATH.read_text(encoding="utf-8"))
    # PyYAML reads the bare key `on` as the boolean True.
    triggers = workflow.get("on", workflow.get(True))
    assert "pull_request" in triggers
    assert not (triggers["pull_request"] or {}).get("branches")


def test_ci_scans_for_secrets() -> None:
    """CI includes the gitleaks secret scan."""
    workflow_text = CI_WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "gitleaks/gitleaks-action" in workflow_text


@pytest.mark.parametrize(
    "heading",
    [
        "## Summary",
        "## Type of change",
        "## How has this been tested?",
        "## AI assistance",
        "## Checklist",
    ],
)
def test_pr_template_has_required_sections(heading: str) -> None:
    """The PR template keeps every section reviewers rely on."""
    assert heading in PR_TEMPLATE_PATH.read_text(encoding="utf-8")
