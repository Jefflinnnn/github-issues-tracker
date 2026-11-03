import pathlib
from typing import Any

import pytest

from triage import config
from triage import menu
from triage import setup
from triage.model import openai_compat
from triage.model import providers
from triage.pipeline import extract

# In a scripted answer list, presses Left: go back.
BACK = "<back>"


def _next(answers: list[Any], *_: object) -> Any:
    answer = answers.pop(0)
    if answer == BACK:
        raise menu.BackError
    return answer


def _run(
    home: pathlib.Path,
    picks: list[Any],
    answers: list[str],
    secrets: list[str],
) -> None:
    """Runs setup with scripted menu picks, typed answers and secrets."""
    terminal = setup.Terminal(
        ask=lambda _: _next(answers),
        ask_secret=lambda _: _next(secrets),
        say=lambda _: None,
        choose=lambda *_: int(_next(picks)),
    )
    setup.run(terminal, environ={"SHELL": "/bin/zsh"}, home=home)
    assert (picks, answers, secrets) == ([], [], [])


def _check_token(token: str) -> str:
    if token != "good":
        raise extract.GitHubError("GitHub rejected the token (HTTP 401).")
    return "octocat"


@pytest.fixture(autouse=True)
def here(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(extract, "check_token", _check_token)
    # No local model server answers, unless a test says otherwise.
    monkeypatch.setattr(setup, "installed_models", lambda url: None)


def test_bedrock_then_a_rerun_that_keeps_everything(
    tmp_path: pathlib.Path,
) -> None:
    path = config.user_env_file()

    # A rejected token is asked for again. Typed: Tab completion (Enter for
    # yes), key ID, region (Enter for the default), no test request.
    _run(
        tmp_path,
        picks=[2, 1],  # AWS keys for Bedrock, then sonnet
        answers=["", "AKIAEXAMPLE", "", "n"],
        secrets=["bad", "good", "secret", "https://gateway.example.com"],
    )
    first = config.read_env_file(path)
    assert first == {
        "GITHUB_TOKEN": "good",
        "TRIAGE_PROVIDER": "bedrock",
        "AWS_ACCESS_KEY_ID": "AKIAEXAMPLE",
        "AWS_SECRET_ACCESS_KEY": "secret",
        "AWS_REGION": "us-west-2",
        "BEDROCK_ENDPOINT_URL": "https://gateway.example.com",
        "TRIAGE_MODEL": "sonnet",
    }

    # Completion is already on, so it asks nothing this time.
    _run(tmp_path, picks=[2, 1], answers=["", "", "n"], secrets=["", "", ""])
    assert config.read_env_file(path) == first
    zshrc = (tmp_path / ".zshrc").read_text(encoding="utf-8")
    assert zshrc.count("triage completion zsh") == 1


def test_a_dash_removes_the_gateway(tmp_path: pathlib.Path) -> None:
    path = config.user_env_file()
    config.write_env_file(
        {"GITHUB_TOKEN": "good", "BEDROCK_ENDPOINT_URL": "https://g.example"},
        path,
    )

    _run(
        tmp_path,
        picks=[2, 0],
        answers=["n", "", "", "n"],
        secrets=["", "", "-"],
    )

    assert config.read_env_file(path)["BEDROCK_ENDPOINT_URL"] == ""


def test_an_openai_model_from_the_list_needs_no_price(
    tmp_path: pathlib.Path,
) -> None:
    path = config.user_env_file()
    config.write_env_file({"GITHUB_TOKEN": "good"}, path)

    _run(tmp_path, picks=[1, 0], answers=["n", "n"], secrets=["", "sk-test"])

    assert config.read_env_file(path) == {
        "GITHUB_TOKEN": "good",
        "TRIAGE_PROVIDER": "openai",
        "OPENAI_API_KEY": "sk-test",
        "TRIAGE_MODEL": "gpt-6-luna",
    }
    model = providers.from_settings()
    assert model.price == openai_compat.OPENAI_PRICES["gpt-6-luna"]


def test_a_local_model_gets_the_ollama_url_and_no_cost(
    tmp_path: pathlib.Path,
) -> None:
    path = config.user_env_file()
    config.write_env_file({"GITHUB_TOKEN": "good"}, path)

    # Local model, Enter for Ollama's URL, then the first suggestion.
    _run(tmp_path, picks=[3, 0], answers=["n", "", "n"], secrets=[""])

    assert config.read_env_file(path) == {
        "GITHUB_TOKEN": "good",
        "TRIAGE_PROVIDER": "openai",
        "OPENAI_BASE_URL": setup.OLLAMA_URL,
        "TRIAGE_MODEL": "llama3.1:8b",
        "TRIAGE_PRICE_PER_MTOK": "0,0",
    }


def test_the_local_menu_lists_the_models_ollama_has(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config.write_env_file({"GITHUB_TOKEN": "good"}, config.user_env_file())
    monkeypatch.setattr(
        setup, "installed_models", lambda url: ["qwen3:8b", "llama3.1:8b"]
    )
    menus: list[list[str]] = []
    picks = [3, 1]  # A local model, then the second model listed

    def choose(title: str, options: list[str], default: int) -> int:
        menus.append(options)
        return picks.pop(0)

    answers = ["n", "", "n"]
    terminal = setup.Terminal(
        ask=lambda _: answers.pop(0),
        ask_secret=lambda _: "",
        say=lambda _: None,
        choose=choose,
    )
    setup.run(terminal, environ={"SHELL": "/bin/zsh"}, home=tmp_path)

    # What Ollama has comes first, suggested first; a suggestion it lacks
    # follows, to pull. Thinking models carry a warning.
    assert menus[1] == [
        "llama3.1:8b  installed, suggested",
        "qwen3:8b  installed, thinks first, may not tag",
        "qwen2.5:14b  pull first: ollama pull qwen2.5:14b, suggested",
        "Another model (type its name)",
    ]
    saved = config.read_env_file(config.user_env_file())
    assert saved["TRIAGE_MODEL"] == "qwen3:8b"


def test_setup_offers_to_comment_out_a_folder_env_that_overrides_it(
    tmp_path: pathlib.Path,
) -> None:
    config.write_env_file({"GITHUB_TOKEN": "good"}, config.user_env_file())
    folder = tmp_path / ".env"
    folder.write_text(
        "AWS_ACCESS_KEY_ID=AKIAEXAMPLE\nTRIAGE_MODEL=sonnet\n",
        encoding="utf-8",
    )
    said: list[str] = []
    picks = [3, 0]
    # Completion, Enter for Ollama's URL, yes to comment out, no test.
    answers = ["n", "", "", "n"]
    terminal = setup.Terminal(
        ask=lambda _: answers.pop(0),
        ask_secret=lambda _: "",
        say=said.append,
        choose=lambda *_: picks.pop(0),
    )
    setup.run(terminal, environ={"SHELL": "/bin/zsh"}, home=tmp_path)

    # Only the setting that would win is named, without its value, and
    # only it is commented out; Bedrock keys don't matter for a local model.
    assert any("sets TRIAGE_MODEL, which would win" in line for line in said)
    assert not any("sonnet" in line for line in said)
    assert folder.read_text(encoding="utf-8") == (
        "AWS_ACCESS_KEY_ID=AKIAEXAMPLE\n# TRIAGE_MODEL=sonnet\n"
    )
    assert providers.from_settings().model == "llama3.1:8b"


def test_another_service_asks_for_a_price_until_it_parses(
    tmp_path: pathlib.Path,
) -> None:
    path = config.user_env_file()
    config.write_env_file({"GITHUB_TOKEN": "good"}, path)

    _run(
        tmp_path,
        picks=[4],
        answers=[
            "n",
            "https://api.groq.com/openai/v1",
            "llama",
            "cheap",
            "0.5,1",
            "n",
        ],
        secrets=["", "gsk-test"],
    )

    assert config.read_env_file(path) == {
        "GITHUB_TOKEN": "good",
        "TRIAGE_PROVIDER": "openai",
        "OPENAI_BASE_URL": "https://api.groq.com/openai/v1",
        "OPENAI_API_KEY": "gsk-test",
        "TRIAGE_MODEL": "llama",
        "TRIAGE_PRICE_PER_MTOK": "0.5,1",
    }


def test_back_from_the_model_menu_lets_you_switch_access(
    tmp_path: pathlib.Path,
) -> None:
    path = config.user_env_file()
    config.write_env_file({"GITHUB_TOKEN": "good"}, path)

    # Bedrock, then Left at the model menu, then the Anthropic API instead.
    _run(
        tmp_path,
        picks=[2, BACK, 0, 2],
        answers=["n", "AKIAEXAMPLE", "", "n"],
        secrets=["", "secret", "", "sk-ant-test"],
    )

    saved = config.read_env_file(path)
    assert saved["TRIAGE_PROVIDER"] == "anthropic"
    assert saved["ANTHROPIC_API_KEY"] == "sk-ant-test"
    assert saved["TRIAGE_MODEL"] == "opus"


def test_back_from_a_key_prompt_returns_to_the_menu(
    tmp_path: pathlib.Path,
) -> None:
    path = config.user_env_file()
    config.write_env_file({"GITHUB_TOKEN": "good"}, path)

    # OpenAI, then Left at its key prompt, then a local model.
    _run(
        tmp_path,
        picks=[1, 3, 0],
        answers=["n", "", "n"],
        secrets=["", BACK],
    )

    saved = config.read_env_file(path)
    assert saved["OPENAI_BASE_URL"] == setup.OLLAMA_URL
    assert saved["TRIAGE_MODEL"] == "llama3.1:8b"
    assert "OPENAI_API_KEY" not in saved


def test_answers_survive_going_back_a_step(tmp_path: pathlib.Path) -> None:
    path = config.user_env_file()

    # A new token, then Left at Tab completion, then Enter keeps the token
    # just typed.
    _run(
        tmp_path,
        picks=[0, 0],
        answers=[BACK, "n", "n"],
        secrets=["good", "", "sk-ant-test"],
    )

    assert config.read_env_file(path)["GITHUB_TOKEN"] == "good"


def test_the_second_screen_is_left_even_when_setup_is_cancelled(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config.write_env_file({"GITHUB_TOKEN": "good"}, config.user_env_file())

    def cancel(*_: object) -> int:
        raise menu.CancelledError

    terminal = setup.Terminal(
        ask=lambda _: "n",
        ask_secret=lambda _: "",
        choose=cancel,
        fullscreen=True,
    )
    with pytest.raises(menu.CancelledError):
        setup.run(terminal, environ={"SHELL": "/bin/zsh"}, home=tmp_path)

    shown = capsys.readouterr().out
    assert shown.index("\x1b[?1049h") < shown.index("\x1b[?1049l")
