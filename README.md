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
- [Usage](#usage)
  - [Summarize a repository](#summarize-a-repository)
    - [Options](#options)
  - [Browse every issue](#browse-every-issue)
  - [List past runs](#list-past-runs)
- [Reading the output](#reading-the-output)
- [Limits and cost](#limits-and-cost)

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
2. **Tab completion.** Setup shows the two lines it would add to `~/.zshrc`
   (or `~/.bashrc`) and asks before adding them.
3. **What do you have?** An Anthropic API key, an OpenAI API key, AWS keys
   for Amazon Bedrock, a local model through Ollama or LM Studio, or
   another OpenAI-compatible service such as Groq, OpenRouter or vLLM.
4. **Which model?** Setup suggests common models with their prices, or
   lets you type any name. It then offers a tiny test request.

### Model providers

Pick one in setup; it asks for what that provider needs and shows each
model's price.

| Provider | You need | Models |
| --- | --- | --- |
| Anthropic API | An API key from https://platform.claude.com | `haiku`, `sonnet`, `opus` |
| OpenAI-compatible | An OpenAI key, or a local server such as [Ollama](https://ollama.com), which needs none | Any model the server has |
| Amazon Bedrock | An AWS access key pair | `haiku`, `sonnet`, `opus` |

## Usage

```bash
triage summary acme/widgets
triage browse acme/widgets
triage list
```

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

<<<<<<< Updated upstream
The model reads the title, labels, opening post and the 10 latest
comments. Along with its tags, it writes a one-line summary of what the
issue is about, a brief of one to three sentences on what the issue is
and the leads so far (a suspected cause, a workaround, a linked pull
request), a phrase each on why a bug is or isn't severe and urgent, and a
one-sentence reason for the main tag. An issue goes
back to the model only when it changes, so a rerun costs little. When it
does, triage marks the comments posted since the last run, and the model
adds a one-sentence change note, which feeds the "since last run" digest. Hot
issues go to the model first, busiest first, then the rest, newest first,
so if the budget runs out, what's left untagged is the least pressing.

While it runs, triage shows its progress on one line of the terminal: a
bar for fetching, then one for tagging. The line disappears when the run
ends, and the summary follows:

```text
astral-sh/uv
941 open · scanned the newest 15 (2%) · 9 tagged (60%)

                count   breakdown
  bug               4   2 urgent · 3 severe
  feature           4   1 must · 1 should · 2 could
  question          1
  ───────────────────
  not yet tagged    6

          TAGS                  ACTIVITY  IDLE
FIX DIDN'T WORK (1)  a released fix, still reported
  #16726  bug: severe, urgent         39    9d  Managed Python is killed by Gatekeeper on macOS

BUGS TO FIX (2)  urgent first, then severe
  #21428  bug: urgent                 12    3d  pip sync segfaults now and then on glibc
  #22941  bug: severe                  4   11d  Install fails behind a corporate proxy

MOST-WANTED FEATURES (1)  must-have, most demand first
  #6830   must-have                   38   18d  Named lockfiles for several resol… [fix-in-progress]

READY TO CLOSE (3)  fix confirmed, duplicate, or no answer in 30 days
  #5731   should-have                 14   81d  Cache size limit with automatic pru… [fix-confirmed]
  #10219  could-have                  33   63d  JSON output for the tool list command [duplicate]
  #12739  question                    34   54d  Why does a new venv see global package… [needs-info]

ASK TO CONFIRM (1)  fix released, nobody has confirmed it
  #5190   could-have                  35   45d  Lock build dependencies of sdists

Run `triage browse` in the terminal to view every issue.
This run tagged 9. Used $0.01 of $0.50 budget.
6 queued for next run.
```

Read it top down:

- **Scope** comes first: how many issues are open, how many triage scanned
  when `--limit` left some out, and how many of those are tagged. Every
  count below covers the scanned issues.
- **The tag table** counts issues per main tag; its rows add up to the
  issues scanned. The breakdown splits bugs into urgent (high priority)
  and severe (high severity), and features into all three MoSCoW values,
  so you can see whether the model puts everything in one bucket. Issues
  not tagged yet sit dimmed below the rule. Spam is counted here and listed
  nowhere.
- **Since last run** appears from the second run on. Its heading counts
  the issues opened, changed and closed since the previous run, and the
  model writes one or two sentences on what matters most among them, such
  as a fix that failed or a new urgent bug, naming issue numbers. The
  digest is one extra small request; when the budget left can't cover it,
  the section says so instead. The tag table also gets a change column,
  and the open count shows its change:

  ```text
  941 open (+3)

                  count  change   breakdown
    bug              15      +2   1 urgent · 2 severe
    feature          34      -1   2 must · 10 should · 22 could

  SINCE LAST RUN (6 days ago)  4 new · 9 changed · 3 closed
    The volume fix for #2637 failed again, and #3802 is a new severe bug:
    Dynamic Island freezes after waking.
  ```

  A tag's change counts only what happened on GitHub: issues opened,
  closed, or tagged differently since the last run. An older issue tagged
  for the first time, such as one a `--limit` run reaches later, isn't a
  change.
- **The groups** list the issues that need a decision, up to 10 each,
  ordered by what it costs to leave an issue alone. An issue fits several
  groups but is listed once, in the first one; later headings say how many
  are listed above. Empty groups are left out.
- **The footer** points to `triage browse`, which lists every issue, then
  says how many issues the model tagged, what that cost against
  `--budget-usd`, and how many the budget queued for the next run. Hot
  issues are tagged first, so the queue holds the least pressing ones.

The groups:

- **Fix didn't work**: `fix-failed` issues, busiest first.
- **Bugs to fix**: every bug nobody holds yet: urgent ones first (in red),
  then severe ones, then the rest, each by activity, then longest idle.
  Bugs with a subtag are left out, since a fix, the opener or another
  issue already holds them. When no bug is left, one line says so and
  where the bugs went.
- **Most-wanted features**: must-have features, by demand, except those
  already released or duplicated.
- **Ready to close**: `fix-confirmed` and `duplicate` issues, and
  `needs-info` issues with no answer in 30 days, idle longest first.
- **Ask to confirm**: `fix-released` issues nobody has confirmed yet.

Two more groups are listed by `triage browse` only:

- **Hot**: among the busiest tenth of open issues by reactions plus
  comments, with at least 3.
- **Stale**: no comment from a person in 60 days. Bot comments and label
  edits don't count. A partial scan leaves staleness out, since the
  newest issues are rarely stale.

Each row shows the issue number, its tags in words, its activity
(reactions plus comments), days since a person last commented, and the
model's summary. `bug: severe, urgent` means high severity and high
priority, and a plain `bug` means neither; `must-have ↑` means demand
raised a feature's priority. An issue the model hasn't tagged yet shows
its title. A row ends with its subtag, except in groups where every row
has the same one. The reason for the tags is stored, not shown. Rows are
cut to the terminal's width, at most 100 characters.

On a terminal, triage dims labels, marks urgent bugs and must-have
features in red, and makes issue numbers
links you can click, in terminals that support them. Set `NO_COLOR` to
turn color off. Piped or redirected, the summary is plain text. Warnings
print above the summary, separated by a blank line.

Look through what the last run found, without fetching or tagging again:

```bash
triage browse owner/name
```

It has two levels:

- **The list**: the summary's groups in the same order, Hot and Stale
  included. An open group shows 10 issues at a time; `]` and `[` page
  through the rest. A line under the
  repository name says what changed since the run before the last ("Since
  Mon: 3 new · 1 closed · 5 with new comments"), and ● marks each issue
  that is new or has new comments. Opening an issue clears its mark for
  the session.
- **An issue's card** (Enter): its title and the model's brief, then
  **Info**: one row each for its type, severe and urgent (each with the
  model's reason), priority and status; when it was opened; reactions,
  comments and people; who commented last; whether a maintainer replied;
  what changed since the last run; and which rule put it in its group.
  `o` opens the issue on GitHub.

Bot comments count nowhere: not in comments, activity, people or "last
commented". triage treats an account as a bot when GitHub marks it as
one or its login ends in `-bot`, `_bot` or `[bot]`. The model still sees
bot comments, since release bots are how it knows a fix shipped.

| Key | Does |
=======
| Term | Means |
>>>>>>> Stashed changes
| --- | --- |
| ACTIVITY | Reactions plus comments |
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
  than 20,000 issues to fetch; pass `--limit` for those. It keeps each
  issue's 10 latest comments and sends the model at most 4,000 characters
  of the opening post and 800 of each comment.
- **Rate limits.** triage sends 4 model requests at once. If the provider
  answers with rate-limit errors, lower `--workers`.
- **Bad replies.** A reply that doesn't fit the tags is retried once. If
  the retry fails too, the issue stays untagged and the summary names it.
- **Secrets.** Keys and the token live in `~/.config/triage/.env`, which
  only you can read, or in a git-ignored `.env`; triage never logs them.
