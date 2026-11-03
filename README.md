# github-issues-tracker

`triage` is a command-line tool for maintainers who face a long list of
open GitHub issues on Monday morning. Point it at a public repository and
it shows what needs you first. Run it again next week and it shows what
changed since then.

It fetches and stores issues, flags them with plain code, and tags each
one with a model from Anthropic, an OpenAI-compatible endpoint or Amazon
Bedrock: what the issue is, how urgent, and what is happening with it.
The summary groups what needs a decision, and `triage browse` lets you
look through every issue the last run found, one at a time.

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
| `other` | A discussion, tracker, roadmap or announcement |
| `spam` | Spam, gibberish or off-topic noise; counted, never listed |

Bugs and features get a second level:

- **Bugs**: severity (how bad it is when it happens) and priority (how
  soon to fix it), each `high` or `low`. The summary calls a high
  severity `severe` and a high priority `urgent`.
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

          TAGS                  ACTIVITY  IDLE  STATUS          SUMMARY
FIX DIDN'T WORK (1)  a released fix, still reported
  #16726  bug: severe, urgent         39    9d                  Managed Python is killed by Gateke…

BUGS TO FIX (2)  urgent first, then severe
  #21428  bug: urgent                 12    3d                  pip sync segfaults now and then on…
  #22941  bug: severe                  4   11d                  Install fails behind a corporate p…

MOST-WANTED FEATURES (1)  must-have, most demand first
  #6830   must-have                   38   18d  fix-in-progress Named lockfiles for several resolu…

READY TO CLOSE (3)  fix confirmed, duplicate, or no answer in 30 days
  #5731   should-have                 14   81d  fix-confirmed   Cache size limit with automatic pr…
  #10219  could-have                  33   63d  duplicate       JSON output for the tool list comm…
  #12739  question                    34   54d  needs-info      Why does a new venv see global pac…

ASK TO CONFIRM (1)  fix released, nobody has confirmed it
  #5190   could-have                  35   45d                  Lock build dependencies of sdists

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
  the issues opened and closed since the previous run, and the older ones
  with new comments from people (bots don't count), the same counts
  `triage browse` shows. The model writes one or two sentences on what
  matters most among them, such as a fix that failed or a new urgent bug,
  naming issue numbers. The digest is one extra small request; when the
  budget left can't cover it, the section says so instead. The open count
  shows its change since the previous run, and when a tag's count changed,
  the tag table gets a change column:

  ```text
  941 open (+3)

                  count  change   breakdown
    bug              15      +2   1 urgent · 2 severe
    feature          34      -1   2 must · 10 should · 22 could

  SINCE LAST RUN (6 days ago)  4 new · 3 closed · 9 with new comments
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
(reactions plus comments), days since a person last commented, its
status (subtag), and the model's summary. `bug: severe, urgent` means high
severity and high priority, and a plain `bug` means neither; `must-have ↑`
means demand raised a feature's priority. An issue the model hasn't tagged
yet shows its title. The status is blank in groups whose title already
names it. The reason for the tags is stored, not shown. Rows are cut to
the terminal's width, at most 100 characters. Below 100 columns, rows use
one-word labels (`urgent`, `severe`, `must`) and short status names
(`fixing`, `needs info`), the same layout as `triage browse`.

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
| --- | --- |
| ↑ / ↓ (or `k` / `j`) | Move, or scroll a line |
| Space / `b` | Open or close a group; on a card, page down or up |
| Enter | Show the issue's card |
| ← (or Esc) | Back |
| `n` / `p` | Next or previous issue in the group, from a card |
| `]` / `[` | Next or previous page of an open group |
| `1`-`9` | Jump to a group |
| `c` | Jump to the next changed issue |
| `o` | Open the issue on GitHub |
| `y` | Copy its link |
| `q` | Quit, from the list |

The browser fits an 80-column window, the size of a new macOS Terminal
window: below 100 columns, rows name tags in a word (`urgent`, `must`)
and statuses in short (`failed`, `fixing`), and the card shows the rest.
It uses the terminal's own 16 colors, so it follows your profile, light
or dark; `NO_COLOR` turns color off. It doesn't capture the mouse, so you
can still select text. Space and `b` page because Page Up and Page Down
need Fn on a MacBook.

When you quit, triage prints the summary. Without a terminal, or on
Windows, which lacks `curses`, `triage browse` prints the summary only. It
makes no requests, so it costs nothing.

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
