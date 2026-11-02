import pathlib

import pytest

from triage import config


def test_project_env_then_user_file_then_shell(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    config.write_env_file({"A": "user", "B": "user"}, config.user_env_file())
    (tmp_path / ".env").write_text("A=project\nB=\n", encoding="utf-8")
    monkeypatch.setenv("C", "shell")

    assert config.get("A") == "project"
    # An empty value in the project file falls through to the user file.
    assert config.get("B") == "user"
    assert config.get("C") == "shell"


def test_writing_keeps_other_lines_and_hides_the_file(
    tmp_path: pathlib.Path,
) -> None:
    path = tmp_path / "triage" / ".env"
    config.write_env_file({"A": "1", "B": "2"}, path)
    path.write_text("# mine\n" + path.read_text(), encoding="utf-8")

    config.write_env_file({"B": "3", "C": "4"}, path)

    assert path.read_text(encoding="utf-8") == "# mine\nA=1\nB=3\nC=4\n"
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
