# readme_updater

Regenerates the dynamic parts of the profile [README](../README.md), treating
it as a template and filling marker-delimited sections in place. Every run
rewrites the file from the GitHub API, so it is idempotent and safe to run at
any time; the only persisted state is a language cache (see below) that keeps
each run cheap. It has no scheduling logic — it is meant to be run on a
schedule, for example from a GitHub Actions workflow. The sections below
explain how each is computed.

## Recent activity

A couple of public contributions to owned repositories and a couple to others,
newest first. It is drawn from the GitHub search API across pull requests,
issues and commits, dated by committer so it matches GitHub's own timeline.

**Private activity never appears.**

## Language bars

Both bars count the lines **added** per language — over the last 30 days and
all time — across every repository committed to, private ones, forks and large
monorepos included: the list-commits API returns only the author's own commits
and every page is enumerated, so the count stays both cheap and complete
regardless of repository size. A file's language comes from its extension;
generated and vendored files are skipped. A per-repository cache, keyed by an
opaque hash so private names never leak, lets each run read only the commits
added since last time and survives rewritten history.
