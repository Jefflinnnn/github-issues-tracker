# github-issues-tracker

`triage` is a command-line tool for maintainers who face a long list of
open GitHub issues on Monday morning. Point it at a public repository and
it shows what needs you first. Run it again next week and it shows what
changed since then.

This version fetches and stores issues, flags them with plain code and
tags each one with a model from Anthropic, an OpenAI-compatible endpoint
or Amazon Bedrock. Later pull requests add finer triage of bugs and
features, an interactive summary and change notes between runs.

## Requirements

- Python 3.10 or newer
- [uv](https://docs.astral.sh/uv/)
- A GitHub account (for a read-only token)
- A key for one model provider: an Anthropic API key, an OpenAI-compatible
  endpoint (OpenAI, or a local server such as Ollama), or an AWS key pair
  for Claude on Amazon Bedrock

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

## Run setup

```bash
triage setup
```

Setup asks for each setting and saves the answers to
`~/.config/triage/.env`, which only you can read, so triage works from any
folder:

1. **GitHub token.** Setup checks it with GitHub before saving it.
2. **Tab completion.** Setup shows the two lines it would add to `~/.zshrc`
   (or `~/.bashrc`) and asks before adding them.
3. **What do you have?** Pick one:
   - an Anthropic API key
   - an OpenAI API key
   - AWS keys for Amazon Bedrock (then the region and an optional gateway)
   - a local model, through Ollama or LM Studio (no key; setup fills in
     Ollama's address)
   - another OpenAI-compatible service, such as Groq, OpenRouter or vLLM
     (its base URL and key)
4. **Which model?** Setup suggests common models for what you picked, with
   their prices: `haiku`, `sonnet` or `opus` for Claude; three current GPT
   models for OpenAI; `qwen3:8b` or `llama3.1:8b` locally. "Another model"
   lets you type any name; for a service triage has no price for, it also
   asks the price.

Setup runs on a second screen, as `less` and `vim` do: each step gets a
fresh page with your answers so far at the top, and when you finish (or
press Esc) your terminal comes back as it was, with a summary of what was
chosen. Menus move with the arrow keys and Enter, or take a number. Press
Left to go back: in a menu it returns to the previous step, and at an empty prompt it
returns to the menu above. Answers you've given are kept when you go back,
and nothing is saved until the last step. (Where arrow keys don't work,
type `<` to go back.) Secrets and the Bedrock gateway URL aren't shown as
you type. Setup then offers one tiny
test request, which costs a few tokens and tells you whether the key and
model work.

Running setup again is safe: pressing Enter keeps each saved value. To
remove a saved gateway, type `-` at its prompt.

To set values by hand instead, copy `.env.example` to `.env` in the folder
you run triage from and fill it in with an editor, not with `echo`, so the
values stay out of your shell history. A `.env` in the current folder wins
over setup's file; your shell's environment comes last. Git ignores `.env`.

## Tab completion

With completion on, press Tab after `triage ` to complete a command, after
`triage summary ` to complete a repository you've summarized before, and
after `--model ` to complete a model name. It works in zsh and bash. Setup
offers to turn it on; to do it yourself, add this line to `~/.zshrc` (or
`~/.bashrc` with `bash`) and open a new terminal:

```bash
eval "$(triage completion zsh)"
```

Completion runs the `triage` on your PATH, so install it with
`uv tool install --editable .` first.

## Model providers

`TRIAGE_PROVIDER` picks the provider: `anthropic`, `openai` or `bedrock`.
Setup saves it. If it's empty, triage uses the first provider whose key is
set, in that order. `--model` (or `TRIAGE_MODEL`) picks the model.

triage keeps spending within the budget using each model's list price, so
it needs a price for every model it uses.

### Anthropic API

Set `ANTHROPIC_API_KEY`; create a key at https://platform.claude.com.

| Name | Model | $ per million tokens (in / out) |
| --- | --- | --- |
| `haiku` | `claude-haiku-4-5` | 1.00 / 5.00 |
| `sonnet` | `claude-sonnet-5-5` | 2.00 / 10.00 |
| `opus` | `claude-opus-5-5` | 4.00 / 20.00 |

### OpenAI-compatible endpoint

Any server that speaks OpenAI's chat completions API with function
calling: OpenAI itself, or servers such as Ollama, vLLM or LM Studio. Set:

- `OPENAI_BASE_URL`: empty means OpenAI (`https://api.openai.com/v1`); a
  local Ollama is `http://localhost:11434/v1`.
- `OPENAI_API_KEY`: required for OpenAI; a local server may need none.
- `TRIAGE_MODEL`: the model name the server knows, e.g. `gpt-6-luna` or
  `qwen3:8b`.
- `TRIAGE_PRICE_PER_MTOK`: the model's input and output price in USD per
  million tokens, e.g. `0.40,1.60`; `0,0` for a local model. triage knows
  the prices of the OpenAI models setup suggests, so those need none:

  | Model | $ per million tokens (in / out) |
  | --- | --- |
  | `gpt-6-luna` | 0.10 / 0.50 |
  | `gpt-6.1-terra` | 1.00 / 5.00 |
  | `gpt-6.1-sol` | 2.00 / 10.00 |

  For any other model or server, triage refuses to run without a price.

To try it without any key, install [Ollama](https://ollama.com), run
`ollama pull qwen3:8b`, and pick "A local model" in setup.

### Amazon Bedrock

triage calls Bedrock's Converse API, signing requests with an AWS key
pair. Set `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and `AWS_REGION`
(default `us-west-2`). Set `BEDROCK_ENDPOINT_URL` only if your
organization routes Bedrock through a gateway; empty reaches AWS directly.

| Name | Bedrock model ID | $ per million tokens (in / out) |
| --- | --- | --- |
| `haiku` | `us.anthropic.claude-haiku-4-5-20251001-v1:0` | 1.10 / 5.50 |
| `sonnet` | `us.anthropic.claude-sonnet-5-5` | 2.20 / 11.00 |
| `opus` | `us.anthropic.claude-opus-5-5` | 4.40 / 22.00 |

A gateway may bill its own rates, which triage can't see. If your shell
exports `AWS_BEARER_TOKEN_BEDROCK`, triage ignores it and signs with the
key pair, so that token never goes to a gateway.

### How replies come back

Every provider gets the same prompt and returns the tag through a
function (tool) call, so the reply is structured data. The function is
offered, not forced: Claude models such as Sonnet 5.5 refuse a forced call,
and the prompt tells the model to use it. A reply that skips it, or doesn't
fit the tags, is retried once, then the issue is skipped.

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

To cap how many open issues a run fetches, pass `--limit`:

```bash
triage summary owner/name --limit 100
```

triage then fetches the 100 most recently updated open issues, plus
whatever changed since the last run, so issues already stored never go
stale. The model tags only those issues, so a small limit means a quick,
cheap run. You can change the limit from run to run. A later run without
`--limit` notices that issues were left out and fetches every open issue
once to fill them in. Whenever a run sees every open issue, issues that
GitHub no longer lists as open are marked closed.

The model then gives each open issue one main tag for what it is at its
core:

| Tag | Meaning |
| --- | --- |
| `bug` | Something is broken or behaves wrongly |
| `feature` | Asks for new behavior or a change |
| `question` | Asks how to do something; nothing is wrong |
| `docs` | Documentation is missing, wrong or unclear |
| `other` | A discussion, tracker, announcement or spam |

Bugs and features get a second level:

- **Bugs**: severity (how bad it is when it happens) and priority (how
  soon to fix it), each `high` or `low`, shown as `bug high/low`.
- **Features**: `must-have`, `should-have` or `could-have`.

The model judges these from evidence in the issue: the report itself,
duplicates, other users affected or asking, and the engagement numbers
triage gives it with each issue. How fast maintainers replied doesn't
count.

For features, demand also sets a floor. Demand is reactions plus the
number of people besides the opener who commented; it counts people, not
comments, so a long back-and-forth between two people isn't demand.
Ranked against the repository's other open feature requests, the top 5%
(with demand of at least 10) can't be below must-have, and the top 20%
(at least 3) can't be below should-have. The floor only ever raises the
model's choice, and it's recomputed on every run, so a request that picks
up support rises without being sent to the model again. The summary marks
raised features with ↑.

Any issue may also get one subtag, for what is happening with it now. The
model judges it from the latest comments:

| Subtag | Meaning |
| --- | --- |
| `fix-failed` | A fix was released, and someone says the problem still happens |
| `fix-released` | A fix or the feature was released; nobody has confirmed it yet |
| `fix-confirmed` | A fix or the feature was released, and someone says it works |
| `fix-in-progress` | A pull request is open, or a fix is merged but not released |
| `needs-info` | Someone asked the opener for details, with no answer since |
| `duplicate` | Someone pointed to another issue that covers the same thing |

Most issues have none.

The model reads the title, labels, opening post and the 10 latest
comments. Along with its tags, it writes a one-sentence summary of what
the issue is about and a one-sentence reason for the tags. An issue goes
back to the model only when it changes, so a rerun costs little. Hot
issues go to the model first, busiest first, then the rest, newest first,
so if the budget runs out, what's left untagged is the least pressing. The
summary counts issues per tag and lists them in groups, up to 10 per
group:

- **Fix didn't work**: `fix-failed` issues, busiest first.
- **Bugs to fix first**: high-priority bugs, high severity first. Bugs
  with a subtag are left out, since a fix, the opener or another issue
  already holds them.
- **Most-wanted features**: must-have features, by demand, except those
  already released or duplicated.
- **Probably closable**: `fix-released`, `fix-confirmed` and `duplicate`
  issues, idle longest first.
- **Hot**: among the busiest tenth of open issues by reactions plus
  comments, with at least 3.
- **Stale**: no comment from a person in 60 days. Bot comments and label
  edits don't count.

Each listed issue shows the model's summary instead of its title, since
titles are often vague; an issue the model hasn't tagged yet shows its
title. In the Probably closable, Hot and Stale groups, an issue's subtag
shows at the end of its line. The reason for the tags is stored, not
shown.

While it runs, triage shows its progress on one line of the terminal: a
bar for fetching, then one for tagging. The line disappears when the run
ends, and the summary starts with a short header:

```text
owner/name · 150 open · 12 hot · 31 stale
Tags: bug 41 (6 high priority) · feature 52 (4 must-have) · question 30 · docs 9 · other 6
Subtags: fix-failed 5 · fix-released 7 · fix-confirmed 3 · fix-in-progress 6 · needs-info 2
Run: first run, 150 fetched · 138 tagged · $0.29 of $0.30 · 12 left for next run
```

The Subtags line appears once any issue has a subtag. The Run line says
what this run fetched, how many issues the model tagged, what that cost
against `--budget-usd`, and how many issues the budget left for the next
run. It names any issues whose replies were unusable twice.
Warnings print above the summary, separated by a blank line.

Show the repositories you've summarized:

```bash
triage list
```

Options:

| Flag | Default | Meaning |
| --- | --- | --- |
| `--limit N` (summary) | 0 (all) | Fetch the N most recently updated open issues, plus what changed since the last run |
| `--model NAME` (summary) | `TRIAGE_MODEL`, then `haiku` | `haiku`, `sonnet` or `opus` |
| `--budget-usd USD` (summary) | 0.50 | Most the model may spend this run |
| `--workers N` (summary) | 4 | Model requests sent at once, 1 to 64 |
| `-v` | off | Show debug logs, such as each fetch and unusable reply |

### Limits

- triage stops with an error when a repository has more than 20,000
  issues to fetch. Pass `--limit` for those.
- triage keeps each issue's 10 latest comments; the stale check and the
  model see only those. It sends at most 4,000 characters of the opening
  post and 800 of each comment.
- triage sends 4 model requests at once; `--workers N` changes that, from
  1 to 64. Most of a run is spent waiting on the API, so more workers
  finish sooner until the provider answers with rate-limit errors; lower
  `--workers` if it does.
- triage adds up what each model request costs from the tokens the
  provider reports. The first request goes out alone, to learn what a
  request costs. After that, a request is sent only if the spend so far,
  plus every request in flight and this one each counted as costing as
  much as the priciest so far, stays within `--budget-usd`. So a run can
  end over budget by at most about one request. Issues left over are
  tagged first on the next run.
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
