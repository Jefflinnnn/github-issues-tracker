## Summary

Adds the extract, transform and load pipeline and the first usable
commands: `triage summary <repo>` and `triage list`.

- **Extract** (`pipeline/extract.py`) reads issues from GitHub's GraphQL
  API, 100 per request, with each issue's 10 latest comments. The first
  run fetches every open issue; later runs fetch only issues changed since
  the previous run, closed ones included.
- **Transform** (`pipeline/transform.py`) turns GitHub's nodes into issue
  records and computes two flags with plain code: hot and stale. These
  are pure functions. Tags from the model come in a later PR.
- **Load** (`pipeline/load.py`) upserts issues into one SQLite file per
  user (`~/.local/share/triage/triage.db`, or `TRIAGE_DB`) and records
  each run. A schema change asks the user to delete the file instead of
  migrating it.
- **CLI**: two commands and two flags (`--limit`, `-v`). `--limit`
  defaults to 0, meaning all open issues.

The report is static for now. Model tags, the interactive drill-down and
change notes come in the next PRs.

Limits: a hard stop at 20,000 issues per fetch (pass `--limit` above
that), 10 comments per issue, a 30-second timeout per request. Errors a
user can fix print one line and exit 1.

## Type of change

- [x] `feat`: new feature (non-breaking)
- [x] Requires a documentation update (README, `.env.example`, CLAUDE.md)

## How has this been tested?

- [ ] `uv run pytest`
- [ ] `ruff check` and `ruff format --check`
- [ ] `uv run mypy`
- [ ] Manual check: `triage summary <owner/repo>` twice on a real
  repository, then `triage list`

**Environment:** Python 3.10+, macOS, LLM provider/model n/a

## AI assistance

- **Delegated:** Claude wrote the pipeline, report, CLI and tests, reusing
  the GraphQL query, repository parsing and flag logic from the previous
  project, and checked the output on made-up data.
- **Changed or overrode:**
- **Verified by hand:**

## Checklist

- [x] Title follows Conventional Commits; one logical change per PR
- [x] Code follows the house style (Python 3.10+, Google Python Style Guide)
- [x] Public functions have Google-style docstrings and type annotations
- [x] Logging uses `%`-style messages and never logs secrets
- [x] No secrets, tokens, or `.env` files committed; new settings added to `.env.example`
- [ ] Self-reviewed the full diff
- [x] Tests added or updated for the change; all tests pass locally
- [x] No new warnings from Ruff, mypy, or pytest
- [x] Docs updated where behavior or setup changed
- [ ] CI is green
