# readme_updater

Regenerates the dynamic parts of the profile [README](../README.md) on a
schedule, treating it as a template and filling marker-delimited sections in
place. How each is computed:

## Recent activity

The most recent public contribution to each of the last few repositories I
touched — a couple I own, a couple I don't — newest first. Drawn from the GitHub
search API across pull requests, issues and commits, dated by committer so it
matches GitHub's own timeline. Private activity never appears.

## Language bars

Both bars count the lines I **added** per language (by extension, skipping generated
and vendored files) over the last 30 days and all time, across every repo I commit to —
private repos and huge monorepos alike, since list-commits returns only my commits. A
committed per-repository hashed cache lets each run read only new commits and survive
rewritten history; all time also includes local closed-source work (e.g. at SAP).
