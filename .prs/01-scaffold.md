## Summary

Starts the rewrite of the issue-triage tool as a smaller project. This PR
adds only the skeleton: packaging, CI, Claude Code rules and a `triage`
command that prints its help. The summary, model tags and run history
arrive in the stacked PRs that follow.

Changes from the old repository's setup:

- CI runs on every pull request, not only PRs into `main`, so stacked PRs
  that target a feature branch get checked too.
- `CLAUDE.md` gains a Stacked PRs section (rebase and retarget after a
  squash merge), a Secrets section, and a smaller test policy.
- `.gitignore` replaces GitHub's default Python template with the old
  repository's curated one, plus `*.local.md` for personal notes.

## Type of change

- [x] `docs` / `test` / `refactor` / `chore` / `build` / `ci`: no behavior change

## How has this been tested?

- [ ] `uv run pytest`
- [ ] `ruff check` and `ruff format --check`
- [ ] `uv run mypy`
- [ ] Manual check: `uv tool install --editable .` then `triage --help` and `triage --version`

**Environment:** Python 3.10+, macOS, LLM provider/model n/a

## AI assistance

- **Delegated:** Claude drafted `pyproject.toml`, the `triage` package
  skeleton, the README outline, the CI trigger change, the `CLAUDE.md`
  additions and the guardrail test updates, and ran the checks in a clean
  copy.
- **Changed or overrode:**
- **Verified by hand:**

## Checklist

- [x] Title follows Conventional Commits; one logical change per PR
- [x] Code follows the house style (Python 3.10+, Google Python Style Guide)
- [x] Public functions have Google-style docstrings and type annotations
- [x] No secrets, tokens, or `.env` files committed; new settings added to `.env.example`
- [ ] Self-reviewed the full diff
- [x] Tests added or updated for the change; all tests pass locally
- [x] No new warnings from Ruff, mypy, or pytest
- [x] Docs updated where behavior or setup changed
- [ ] CI is green
