import pathlib
import shutil
import subprocess

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
