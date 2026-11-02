# github-issues-tracker

`triage` is a command-line tool for maintainers who face a long list of
open GitHub issues on Monday morning. Point it at a public repository and
it shows what needs you first. Run it again next week and it shows what
changed since then.

This is the project skeleton. Later pull requests add the summary, model
tags and run history.

## Requirements

- Python 3.10 or newer
- [uv](https://docs.astral.sh/uv/)

## Install

```bash
git clone https://github.com/Jefflinnnn/github-issues-tracker.git
cd github-issues-tracker
uv tool install --editable .
triage --help
```

This puts a `triage` command on your PATH. `--editable` runs the code in
this folder, so a `git pull` takes effect without reinstalling. If your
shell can't find `triage`, run `uv tool update-shell` and open a new
terminal.

## Development

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest
```
