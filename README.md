# github-issues-tracker

`triage` is a command-line tool for maintainers who face a long list of
open GitHub issues on Monday morning. Point it at a public repository and
it shows what needs you first. Run it again next week and it shows what
changed since then.

This version fetches and stores issues and flags them with plain code.
Later pull requests add model tags, an interactive summary and change
notes between runs.

## Requirements

- Python 3.10 or newer
- [uv](https://docs.astral.sh/uv/)
- A GitHub account (for a read-only token)

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

## Create a read-only GitHub token

The tool uses GitHub's GraphQL API, which needs a token. Give it the least
access possible: read-only access to public repositories, nothing else.

1. On github.com, open **Settings → Developer settings → Personal access
   tokens → Fine-grained tokens → Generate new token**.
2. Name it (for example `triage`) and pick a short expiration, such as 30
   days.
3. Under **Repository access**, choose **Public repositories (read-only)**.
4. Leave every permission off. Reading public issues needs none.
5. Click **Generate token** and copy it. GitHub shows it only once.

Avoid reusing `gh auth token`: it usually carries far more access than this
tool needs.

Copy `.env.example` to `.env` and paste the token after `GITHUB_TOKEN=` in
an editor, not with `echo`, so it stays out of your shell history:

```bash
cp .env.example .env
```

Git ignores `.env`. triage reads it from the folder you run it in, so run
`triage` from this folder or export `GITHUB_TOKEN` in your shell instead.

## Usage

Summarize a repository's open issues:

```bash
triage summary https://github.com/owner/name
```

`summary` accepts a GitHub link, an SSH remote such as
`git@github.com:owner/name.git`, or `owner/name`. The first run fetches
every open issue, 100 per request. Later runs fetch only the issues that
changed since the previous run, closed ones included, so a rerun is quick.

The summary lists the issues in each group, up to 10 per group:

- **Hot**: among the busiest tenth of open issues by reactions plus
  comments, with at least 3.
- **Stale**: no comment from a person in 60 days. Bot comments and label
  edits don't count.

Show the repositories you've summarized:

```bash
triage list
```

Options:

| Flag | Default | Meaning |
| --- | --- | --- |
| `--limit N` (summary) | 0 (all) | First run only: fetch the N most recently updated open issues |
| `-v` | off | Show debug logs |

### Limits

- triage stops with an error when a repository has more than 20,000
  issues to fetch. Pass `--limit` for those.
- triage keeps each issue's 10 latest comments; the stale check sees only
  those.
- History lives in one SQLite file per user,
  `~/.local/share/triage/triage.db` (set `TRIAGE_DB` to move it). It is a
  cache: if triage reports that the file is from another version, delete
  it and run again.

## Development

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest
```
