# AGENTS.md

Repo-specific conventions for the shared `workflows` skills (`commit`,
`github-pr`, `scrutinize`) from the [`whmade` marketplace](https://github.com/whmade/claude-marketplace),
enabled in `.claude/settings.json`. The skills are generic and read the sections
below for this repo's specifics.

This repository is a GitHub profile README: the `README.md` root file plus
`readme_updater/`, a Python tool that regenerates the README's marker-delimited
dynamic sections from the GitHub API.

## Code style

All Python lives in `readme_updater/`; run its tooling from that directory.

- Fix your edits with `cd readme_updater && uv run autofix` — runs `ruff
  format`, `ruff check --fix`, `ty check`, then `pytest`, stopping at the first
  failure.
- Do NOT use `uv run check` to fix edits: it is CI's non-mutating gate (`ruff
  format --check`, `ruff check`, `ty check`, `pytest`) and only reports.
- ruff lints with `select = ["ALL"]` (line length 88) and enforces Google-style
  docstrings (D100–D107) on every non-test module, class, and function; only
  `tests/**` is relaxed (there just the module docstring, D100, is required).
  Keep mandated docstrings but trim each entry to the minimum; never drop a
  required one.
- ASCII-only punctuation — use `--` and `->`, never em-dashes or smart quotes.

## Testing

- `pytest` runs via `uv run` (wired into both `autofix` and `check`). Tests live
  in `readme_updater/tests`; coverage is collected automatically
  (`--cov=readme_updater`).
- CI enforces changed-line coverage >= 80% on pull requests (`diff-cover
  coverage.xml --compare-branch origin/<base> --fail-under 80` in
  `.github/workflows/ci.yml`). Do not leave newly touched code untested.

## Commit conventions

- Imperative subject, first word capitalized, no trailing period, under 72
  characters.
- Do NOT append `(#N)` to the subject — GitHub adds it on squash-merge.
- Body explains WHY (and before/after when relevant), wrapped at ~72; ASCII-only
  punctuation.
- References in the footer: `Sentry: <issue-id>` for a Sentry issue (e.g.
  `Sentry: whme-6`); `Closes #<n>` when a change fully resolves a GitHub issue,
  otherwise `GitHub: #<n>`.
- Exactly one `Co-authored-by:` trailer, lowercase, naming the exact model in
  use — e.g. `Co-authored-by: Claude Opus 4.8 (1M context)
  <noreply@anthropic.com>`. Mandatory for AI-generated commits; never hardcode a
  version.

## Pull requests

- CI (`.github/workflows/ci.yml`) must be green: `uv run check` plus the
  changed-line coverage gate above.
- PRs are squash-merged. No required labels and no changelog/news-fragment file,
  so no extra `gh pr create` flags are needed.
