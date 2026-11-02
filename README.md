# github-issues-tracker

`triage` is a command-line tool for maintainers who face a long list of
open GitHub issues on Monday morning. Point it at a public repository and
it shows what needs you first. Run it again next week and it shows what
changed since then.

This version fetches and stores issues, flags them with plain code and
tags each one with a model. Later pull requests add more model providers,
an interactive summary and change notes between runs.

## Requirements

- Python 3.10 or newer
- [uv](https://docs.astral.sh/uv/)
- A GitHub account (for a read-only token)
- An AWS key pair that can use Claude on Amazon Bedrock

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

## Set up the model

triage sends each issue to Claude on Amazon Bedrock through Bedrock's
Converse API, signing requests with an AWS key pair. In `.env`, set:

- `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`.
- `AWS_REGION`; the default is `us-west-2`.
- `BEDROCK_ENDPOINT_URL`, only if your organization routes Bedrock through
  a gateway. Empty sends requests to AWS directly.
- `TRIAGE_MODEL`: `haiku` (the default), `sonnet` or `opus`.

| Name | Bedrock model ID | $ per million tokens (in / out) |
| --- | --- | --- |
| `haiku` | `us.anthropic.claude-haiku-4-5-20251001-v1:0` | 1.10 / 5.50 |
| `sonnet` | `us.anthropic.claude-sonnet-5-5` | 2.20 / 11.00 |
| `opus` | `us.anthropic.claude-opus-5-5` | 4.40 / 22.00 |

triage uses these list prices to stay within the budget. A gateway may
bill its own rates, which triage can't see. If your shell exports
`AWS_BEARER_TOKEN_BEDROCK`, triage ignores it and signs with the key pair,
so that token never goes to a gateway.

## Usage

Summarize a repository's open issues. Name the repository in any of these
forms:

```bash
triage summary https://github.com/owner/name
triage summary https://github.com/owner/name/issues
triage summary git@github.com:owner/name.git
triage summary owner/name
```

A link may point anywhere inside the repository, such as `/issues` or
`/tree/main`.

The first run fetches every open issue, 100 per request. Later runs fetch
only the issues that changed since the previous run, closed ones included,
so a rerun is quick.

The model then gives each open issue one main tag for what it is at its
core:

| Tag | Meaning |
| --- | --- |
| `bug` | Something is broken or behaves wrongly |
| `feature` | Asks for new behavior or a change |
| `question` | Asks how to do something; nothing is wrong |
| `docs` | Documentation is missing, wrong or unclear |
| `other` | A discussion, tracker, announcement or spam |

The model reads the title, labels, opening post and the 10 latest
comments, and gives a one-sentence reason with each tag. An issue goes back
to the model only when it changes, so a rerun costs little. The summary
counts issues per tag and lists them in each group, up to 10 per group:

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
| `--model NAME` (summary) | `TRIAGE_MODEL`, then `haiku` | `haiku`, `sonnet` or `opus` |
| `--budget-usd USD` (summary) | 0.50 | Most the model may spend this run |
| `-v` | off | Show debug logs |

### Limits

- triage stops with an error when a repository has more than 20,000
  issues to fetch. Pass `--limit` for those.
- triage keeps each issue's 10 latest comments; the stale check and the
  model see only those. It sends at most 4,000 characters of the opening
  post and 800 of each comment.
- triage adds up what each model request costs from the tokens Bedrock
  reports. Before each request it stops if the budget is spent, or if a
  request as expensive as the priciest so far would cross it. Issues left
  over are tagged first on the next run. The first request always goes
  out, so a run can end over budget by at most one request.
- A reply that doesn't fit the tags is retried once, telling the model
  what was wrong. If the retry fails too, the issue stays untagged and the
  summary names it.
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
