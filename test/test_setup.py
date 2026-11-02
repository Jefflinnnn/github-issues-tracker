import pathlib

import pytest

from triage import config
from triage import setup
from triage.pipeline import extract


def _terminal(answers: list[str], secrets: list[str]) -> setup.Terminal:
    return setup.Terminal(
        ask=lambda _: answers.pop(0),
        ask_secret=lambda _: secrets.pop(0),
        say=lambda _: None,
    )


def _run(home: pathlib.Path, answers: list[str], secrets: list[str]) -> None:
    setup.run(
        _terminal(answers, secrets), environ={"SHELL": "/bin/zsh"}, home=home
    )


def _check_token(token: str) -> str:
    if token != "good":
        raise extract.GitHubError("GitHub rejected the token (HTTP 401).")
    return "octocat"


def test_saves_answers_then_keeps_them_on_enter(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(extract, "check_token", _check_token)
    path = config.user_env_file()

    # A rejected token is asked for again. Answers: Tab completion (Enter
    # for yes), key ID, region (Enter for the default), model 2, no test
    # request.
    _run(
        tmp_path,
        answers=["", "AKIAEXAMPLE", "", "2", "n"],
        secrets=["bad", "good", "secret", "https://gateway.example.com"],
    )
    first = config.read_env_file(path)
    assert first == {
        "GITHUB_TOKEN": "good",
        "AWS_ACCESS_KEY_ID": "AKIAEXAMPLE",
        "AWS_SECRET_ACCESS_KEY": "secret",
        "AWS_REGION": "us-west-2",
        "BEDROCK_ENDPOINT_URL": "https://gateway.example.com",
        "TRIAGE_MODEL": "sonnet",
    }

    # Completion is already on, so it asks nothing this time.
    _run(tmp_path, answers=["", "", "", "n"], secrets=["", "", ""])
    assert config.read_env_file(path) == first
    zshrc = (tmp_path / ".zshrc").read_text(encoding="utf-8")
    assert zshrc.count("triage completion zsh") == 1


def test_a_dash_removes_the_gateway(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = config.user_env_file()
    config.write_env_file(
        {"GITHUB_TOKEN": "good", "BEDROCK_ENDPOINT_URL": "https://g.example"},
        path,
    )

    _run(tmp_path, answers=["n", "", "", "", "n"], secrets=["", "", "-"])

    assert config.read_env_file(path)["BEDROCK_ENDPOINT_URL"] == ""
