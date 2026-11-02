<!--
PR title: Conventional Commits format, e.g. `feat(fetch): paginate issue requests`.
Delete any section or option that doesn't apply.
-->

## Summary

<!-- What changed and why. Include motivation and context a reviewer needs. -->

Closes #<!-- issue number, or delete this line -->

## Type of change

- [ ] `fix`: bug fix (non-breaking)
- [ ] `feat`: new feature (non-breaking)
- [ ] Breaking change (existing behavior changes; explain below)
- [ ] `docs` / `test` / `refactor` / `chore` / `build` / `ci`: no behavior change
- [ ] Requires a documentation update (README, `.env.example`, CLAUDE.md)

## How has this been tested?

<!--
Commands you ran and what you checked by hand, so a reviewer can reproduce.
-->

- [ ] `uv run pytest`
- [ ] `ruff check` and `ruff format --check`
- [ ] `uv run mypy`
- [ ] Manual check: <!-- e.g. ran the tool against owner/repo and reviewed the output -->

**Environment:** Python <!-- 3.x -->, OS <!-- macOS / Linux -->, LLM provider/model <!-- if relevant, or n/a -->

## AI assistance

<!-- How the assistant was used for this PR. Be specific and honest. -->

- **Delegated:** <!-- what Claude wrote or did -->
- **Changed or overrode:** <!-- suggestions you rejected or edited, and why -->
- **Verified by hand:** <!-- what you double-checked rather than trusted -->

## Checklist

- [ ] Title follows Conventional Commits; one logical change per PR
- [ ] Code follows the house style (Python 3.10+, Google Python Style Guide)
- [ ] Names and logic are clear; comments explain *why* or complex code only
- [ ] Public functions have Google-style docstrings and type annotations
- [ ] Logging uses `%`-style messages and never logs secrets
- [ ] No secrets, tokens, or `.env` files committed; new settings added to `.env.example`
- [ ] Self-reviewed the full diff
- [ ] Tests added or updated for the change; all tests pass locally
- [ ] No new warnings from Ruff, mypy, or pytest
- [ ] Docs updated where behavior or setup changed
- [ ] CI is green
