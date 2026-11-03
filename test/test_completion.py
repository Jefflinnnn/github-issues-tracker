import pathlib
import shutil
import subprocess
import sys

import pytest

from triage import completion


def test_install_appends_once_after_existing_lines(
    tmp_path: pathlib.Path,
) -> None:
    zshrc = tmp_path / ".zshrc"
    zshrc.write_text("export EDITOR=vim", encoding="utf-8")

    completion.install(zshrc, "zsh")

    assert completion.is_installed(zshrc, "zsh")
    assert zshrc.read_text(encoding="utf-8") == (
        "export EDITOR=vim\n\n"
        "# triage: Tab completion\n"
        'eval "$(triage completion zsh)"\n'
    )


@pytest.mark.skipif(
    sys.platform == "win32", reason="bash on Windows may be WSL's launcher"
)
@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_scripts_parse(shell: str, tmp_path: pathlib.Path) -> None:
    if shutil.which(shell) is None:
        pytest.skip(f"{shell} isn't installed")
    script = tmp_path / f"triage.{shell}"
    script.write_text(completion.SCRIPTS[shell], encoding="utf-8")

    result = subprocess.run(
        [shell, "-n", str(script)], capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr


def test_windows_gets_powershell_unless_a_unix_shell_is_set() -> None:
    assert completion.detect_shell({}, "win32") == "powershell"
    # Git Bash sets SHELL, so it gets the bash script.
    assert completion.detect_shell({"SHELL": "/usr/bin/bash"}, "win32") == (
        "bash"
    )
    assert completion.detect_shell({}, "linux") is None
    assert completion.detect_shell({"SHELL": "/usr/bin/fish"}, "linux") is None


def test_powershell_lines_go_into_its_profile_folder_made_if_needed(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without pwsh or powershell to ask, the default profile is used.
    monkeypatch.setattr(shutil, "which", lambda _: None)
    profile = completion.startup_file("powershell", {}, tmp_path)
    assert profile == (
        tmp_path / "Documents/PowerShell/Microsoft.PowerShell_profile.ps1"
    )

    completion.install(profile, "powershell")

    assert completion.is_installed(profile, "powershell")
    assert profile.read_text(encoding="utf-8") == (
        "# triage: Tab completion\n"
        "triage completion powershell | Out-String | Invoke-Expression\n"
    )


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="needs pwsh")
@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("triage s", ["setup", "summary"]),
        ("triage summary --m", ["--model"]),
        ("triage summary x --model ", ["haiku", "sonnet", "opus"]),
        # Nothing to suggest after --limit; PowerShell then falls back to
        # file names, and the folder it runs in is empty.
        ("triage summary x --limit ", []),
    ],
)
def test_the_powershell_script_completes(
    line: str, expected: list[str], tmp_path: pathlib.Path
) -> None:
    script = tmp_path / "triage.ps1"
    script.write_text(completion.POWERSHELL_SCRIPT, encoding="utf-8")
    empty = tmp_path / "empty"
    empty.mkdir()
    command = (
        f". '{script}'; "
        f"(TabExpansion2 -inputScript '{line}' -cursorColumn {len(line)})"
        ".CompletionMatches | ForEach-Object { $_.CompletionText }"
    )

    result = subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        check=True,
        cwd=empty,
    )

    assert result.stdout.split() == expected
