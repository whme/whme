# readme_updater

Regenerates the dynamic parts of the profile [README](../README.md) on a
schedule, treating it as a template and filling marker-delimited sections in
place. The sections below explain how each is computed.

## Recent activity

The most recent public contribution to each of the last few repositories I
touched, a couple I own and a couple I don't, newest first. It is drawn from the
GitHub search API across pull requests, issues and commits, dated by committer so
it matches GitHub's own timeline. Private activity never appears.

## Language bars

Both bars count the lines I **added** per language, by file extension and skipping
generated and vendored files, over the last 30 days and all time. They cover every
repo I commit to, private repos and huge monorepos included, because list-commits
returns only my commits. A hashed per-repo cache lets each run read only new commits
and survive rewritten history. All time also includes local closed-source statistics.
