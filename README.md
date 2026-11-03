# GitHub Issues Tracker

`triage` is a command-line tool for maintainers who face a long list of
open GitHub issues on Monday morning. Point it at a public repository and
it shows what needs you first. Run it again next week and it shows what
changed since then.

It fetches the open issues, stores them in a local SQLite file, and has a
model tag each one: what the issue is, how urgent, and what is happening
with it. Anthropic, any OpenAI-compatible endpoint and Amazon Bedrock all
work. On its second run on a made-up repository, `triage summary` prints:

```text
acme/widgets
8 open

                count  change   breakdown
  bug               5      +1   2 urgent · 3 severe
  feature           2           1 must · 0 should · 1 could
  question          1      -1

SINCE LAST RUN (7 days ago)  1 new · 1 closed · 1 with new comments
  The PDF export fix for #142 didn't work, and #151 is a new urgent bug: the app freezes on large
  files.

          TAGS                  ACTIVITY  IDLE  STATUS          SUMMARY
FIX DIDN'T WORK (1)  a released fix, still reported
  #142    bug: severe, urgent         14    1d                  Export to PDF crashes

BUGS TO FIX (2)  urgent first, then severe
  #151    bug: severe, urgent          6    2d                  App freezes on files over 1 GB
  #148    bug                          0   20d                  Typo in the Settings menu

FEATURES (2)  must-have, then should, then could; most demand first
  #120    must-have                   35   90d                  Add a dark mode
  #133    could-have                   2   40d                  Keyboard shortcut for search

READY TO CLOSE (1)  fix confirmed, duplicate, or no answer in 30 days
  #97     bug: severe                  1   35d  fix-confirmed   Login fails with single sign-on

ASK TO CONFIRM (1)  fix released, nobody has confirmed it
  #110    bug                          1   12d                  Sync button does nothing on Android

Run `triage browse` in the terminal to view every issue.
This run tagged 2. Used <$0.01 of $0.50 budget.
```

`triage browse` then lets you look through every issue, one at a time.

## Contents

- [Requirements](#requirements)
- [Install](#install)
- [Configure](#configure)
  - [Create a read-only GitHub token](#create-a-read-only-github-token)
  - [Run setup](#run-setup)
  - [Model providers](#model-providers)
  - [Run a local model with Ollama](#run-a-local-model-with-ollama)
- [Usage](#usage)
  - [Summarize a repository](#summarize-a-repository)
    - [Options](#options)
  - [Browse every issue](#browse-every-issue)
  - [List past runs](#list-past-runs)
- [Reading the output](#reading-the-output)
- [Limits and cost](#limits-and-cost)
- [How I thought about this & AI Collaboration Notes](#notes)

## Requirements

- Python 3.10 or newer
- [uv](https://docs.astral.sh/uv/)
- A GitHub account, for a read-only token
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

This installs `triage` as a command, so you can run it from any folder.
It runs on macOS, Linux and Windows. On Windows, use PowerShell, in
Windows Terminal or the classic console.

## Configure

### Create a read-only GitHub token

triage reads issues through GitHub's GraphQL API, which needs a token.
Give it the least access possible:

1. On github.com, open **Settings → Developer settings → Personal access
   tokens → Fine-grained tokens → Generate new token**.
2. Name it (for example `triage`) and pick a short expiration, such as 30
   days.
3. Under **Repository access**, choose **Public repositories (read-only)**.
4. Leave every permission off. Reading public issues needs none.
5. Click **Generate token** and copy it. GitHub shows it only once.

### Run setup

```bash
triage setup
```

Setup asks for each setting and saves the answers to
`~/.config/triage/.env`, which only you can read, so triage works from any
folder:

1. **GitHub token.** Setup checks it with GitHub before saving it.
2. **Tab completion.** Setup shows the two lines it would add to `~/.zshrc`,
   `~/.bashrc`, or your PowerShell profile on Windows, and asks before
   adding them.
3. **What do you have?** An Anthropic API key, an OpenAI API key, AWS keys
   for Amazon Bedrock, a local model through Ollama or LM Studio, or
   another OpenAI-compatible service such as Groq, OpenRouter or vLLM.
4. **Which model?** Setup suggests common models with their prices, or
   lets you type any name. It then offers a tiny test request.

### Model providers

Pick one in setup; it asks for what that provider needs and shows each
model's ID and price. For Claude, triage supports only the three 5.5
models below, since spending limits need each model's price; other
Claude models, such as Sonnet 4.5, can't be picked.

| Provider | You need | Models |
| --- | --- | --- |
| Anthropic API | An API key from https://platform.claude.com | `haiku`, `sonnet`, `opus`: Claude Haiku 5.5, Sonnet 5.5, Opus 5.5 |
| OpenAI-compatible | An OpenAI key, or a local server such as [Ollama](https://ollama.com), which needs none | Any model the server has |
| Amazon Bedrock | An AWS access key pair | `haiku`, `sonnet`, `opus`: Claude Haiku 5.5, Sonnet 5.5, Opus 5.5 |

### Run a local model with Ollama

A local model costs nothing and needs no key.

1. Install [Ollama](https://ollama.com), for example with
   `brew install ollama`.
2. Start it with room for triage's longer prompts, and leave it running:

   ```bash
   OLLAMA_CONTEXT_LENGTH=8192 ollama serve
   ```

   In PowerShell: `$env:OLLAMA_CONTEXT_LENGTH=8192; ollama serve`.

3. In another terminal, download the model and load it once:

   ```bash
   ollama pull llama3.1:8b
   ollama run llama3.1:8b "Say OK"
   ```

4. Run `triage setup`, pick **A local model**, and press Enter to keep the
   base URL. Setup lists the models Ollama has; pick `llama3.1:8b`.
5. Start with a small run and one worker:

   ```bash
   triage summary owner/name --limit 5 --workers 1
   ```

Use `llama3.1:8b`, or `qwen2.5:14b` with 16 GB of memory or more. Avoid
`qwen3` models for now: they think before answering and can run out of
room before they tag the issue. If a `.env` in the folder sets another
model, setup offers to comment it out, since it would win over setup.

## Usage

1. `triage summary acme/widgets` fetches the open issues, tags them and
   prints the summary.
2. `triage browse acme/widgets` then looks through every issue from that
   run.
3. Optional: `triage list` shows the repositories you're tracking.

Press Tab after `triage summary ` or `triage browse ` to complete the name
of a repository you've summarized before.

### Summarize a repository

Name the repository as `owner/name` or any GitHub link inside it. The
first run fetches every open issue and tags as many as the budget allows.
Later runs fetch and tag only what changed, so they're quick and cheap.

```bash
triage summary acme/widgets
triage summary https://github.com/acme/widgets/issues
```

#### Options

| Flag | Default | Meaning |
| --- | --- | --- |
| `--limit N` | 0 (all) | Fetch the N most recently updated open issues, plus what changed since the last run |
| `--model NAME` | the model from setup | `haiku`, `sonnet` or `opus`, or a model name your endpoint knows |
| `--budget-usd USD` | 0.50 | Most the model may spend this run |
| `--workers N` | 4 | Model requests sent at once, 1 to 64 |
| `-v` | off | Show debug logs; goes before the command, e.g. `triage -v summary` |

Type `--` and press Tab to list them. For example:

```bash
triage summary acme/widgets --limit 100
triage summary acme/widgets --model sonnet --budget-usd 2
triage -v summary acme/widgets --workers 8
```

- `--limit 100` looks at the 100 most recently updated issues only: a
  quick first look at a big repository.
- `--model sonnet --budget-usd 2` uses a stronger model and lets it spend
  up to $2.
- `-v` with `--workers 8` sends 8 requests at once and shows each fetch
  and reply as it happens.

### Browse every issue

```bash
triage browse acme/widgets
```

Looks through every issue from the last run, without fetching or
spending. Press Enter on an issue to see its card:

```text
Info:
  Type          bug
  Severe        yes   Crashes every time and loses unsaved work.
  Urgent        yes   Many users report it, and a released fix failed.
  Status        fix failed
  Opened        30d ago (2026-10-03)
  Activity      12 reactions, 2 comments, 2 people
  Model read    the post and all 2 comments
  Last          1d ago, by the reporter
  Maintainers   replied 10d ago
  Since Oct 26  1 new comment. The reporter says 2.3.1 still crashes
  Why here      fix failed, a released fix is still reported
```

| Key | Does |
| --- | --- |
| ↑ / ↓ | Move |
| Space | Open or close a group |
| Enter / ← | Open a card / go back |
| `n` / `p` | Next or previous issue, from a card |
| `]` / `[` | Next or previous page of a group |
| `c` | Next changed issue |
| `o` / `y` | Open the issue on GitHub / copy its link |
| `q` | Quit |

### List past runs

```bash
triage list
```

```text
REPOSITORY    RUNS  LAST RUN          OPEN
acme/widgets  2     2026-11-02 09:00  8
acme/gadgets  1     2026-10-28 14:12  41
```

## Reading the output

The summary reads top down: scope (open, scanned and tagged), the tag
counts, what changed since the last run, the groups, then what the run
did and cost.

**Groups.** Each issue is listed once, in the first group it fits. The
summary lists up to 10 issues per group; `triage browse` lists them all.

| Group | Lists |
| --- | --- |
| Fix didn't work | `fix-failed` issues, busiest first |
| Bugs to fix | Every open bug: urgent first (in red), then severe, then by activity |
| Features | Every open feature: must-have, then should, then could, by demand |
| Ready to close | `fix-confirmed`, `duplicate`, and `needs-info` with no answer in 30 days |
| Ask to confirm | `fix-released` issues nobody has confirmed yet |
| Other issues | Questions, docs, other, and issues not tagged yet (`triage browse` only) |

**Tags.** Each open issue gets one main tag:

| Tag | Meaning |
| --- | --- |
| `bug` | Something is broken or behaves wrongly; also `severe` (high severity) and `urgent` (high priority), or neither |
| `feature` | Asks for new behavior; `must-have`, `should-have` or `could-have` |
| `question` | Asks how to do something; nothing is wrong |
| `docs` | Documentation is missing, wrong or unclear |
| `other` | A discussion, tracker, roadmap or announcement |
| `spam` | Spam, gibberish or off-topic noise; counted, never listed |

**Subtags.** An issue may also get one, shown in the STATUS column:

| Subtag | Meaning |
| --- | --- |
| `fix-failed` | A fix was released, and someone says the problem still happens |
| `fix-released` | A fix or the feature was released; nobody has confirmed it yet |
| `fix-confirmed` | A fix or the feature was released, and someone says it works |
| `fix-in-progress` | A pull request is open, or a fix is merged but not released |
| `needs-info` | Someone asked the opener for details, with no answer since |
| `duplicate` | Someone pointed to another issue that covers the same thing |

**Columns and flags.**

| Term | Means |
| --- | --- |
| ACTIVITY | Reactions on the issue plus comments, from GitHub's totals |
| IDLE | Days since a person last commented |
| ↑ | Demand raised a feature's priority, e.g. `must-have ↑` |
| hot | Among the busiest tenth by activity, with at least 3 |
| stale | No comment from a person in 60 days |

Bot comments aren't counted.

## Limits and cost

- **Budget.** A run stops sending model requests before it would pass
  `--budget-usd` (default $0.50), counting each model's list price, and
  can end over by at most about one request. Issues left over are tagged
  first on the next run.
- **Fetching.** triage stops with an error when a repository has more
  than 20,000 issues to fetch; pass `--limit` for those.
- **What the model reads.** The opening post, up to 4,000 characters, and
  the 10 latest comments, up to 800 characters each. A card's "Model read"
  row says what it read of that issue and what was cut.
- **Counts.** Reactions and comments are GitHub's totals, so they cover
  the whole thread. Bot comments are taken off only among the 10 fetched,
  so a long thread can still count a few older ones. People and "last
  commented" come from the 10 fetched comments; the card says so when a
  thread has more.
- **Rate limits.** triage sends 4 model requests at once. If the provider
  answers with rate-limit errors, lower `--workers`.
- **Bad replies.** A reply that doesn't fit the tags is retried once. If
  the retry fails too, the issue stays untagged and the summary names it.
- **Secrets.** Keys and the token live in `~/.config/triage/.env`, which
  only you can read, or in a git-ignored `.env`; triage never logs them.

## Notes

How I scoped and built this, and how I used an AI assistant:
[How I thought about this and the AI collaboration log](docs/notes.md).
