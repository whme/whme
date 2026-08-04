# readme_updater

Regenerates the dynamic parts of the profile [README](../README.md), treating
it as a template and filling marker-delimited sections in place. It holds no
state of its own: every run recomputes each section from the GitHub API and
rewrites the file, so it is idempotent and safe to run at any time. It has no
scheduling logic — it is meant to be run on a schedule, for example from a
GitHub Actions workflow. The sections below explain how each is computed.

## Recent activity

The most recent public contribution to each of the last few active
repositories, a couple owned and a couple not, newest first. It is drawn from
the GitHub search API across pull requests, issues and commits, dated by
committer so it matches GitHub's own timeline.

**Private activity never appears.**
