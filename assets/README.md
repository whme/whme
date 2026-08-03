# Assets

The profile README embeds a handful of images. The default is to
**reference** an image at its source, not copy it here — a copy is dead
weight and drifts out of date. An asset earns a place in this folder
only for one of three concrete reasons:

1. **We modified it.** GitHub renders README images through `<img>` tags
   with no CSS inheritance, so an icon has to carry its own color. The
   octicons ship colorless (they rely on `currentColor`), so we tint
   them here; there is no upstream URL that serves the tinted version.
2. **It is third-party and we want to be insulated from it.** Upstream
   repositories rename, restructure and delete files on their own
   schedule. Vendoring a small third-party icon trades a few hundred
   bytes for a guarantee that the README never breaks because someone
   else moved a file.
3. **We generate it.** These are outputs of the `readme-updater`
   package and have no source to reference.

By that rule:

- `git-pull-request.svg`, `issue-opened.svg`, `git-commit.svg`, `mark-github.svg` — **modified.** [Primer Octicons](https://github.com/primer/octicons), MIT ([LICENSE-octicons](LICENSE-octicons)), tinted `#8b949e` to stay visible on both themes.
- `rust.svg` — **modified.** [Simple Icons](https://github.com/simple-icons/simple-icons), CC0. Rust's mark is monochrome, so it is tinted `#dea584` (GitHub's own language color for Rust) to stay visible on dark.
- `python.svg`, `typescript.svg` — **third-party, insulated.** [Devicon](https://github.com/devicons/devicon) originals, MIT ([LICENSE-devicon](LICENSE-devicon)), in their real brand colors.
- `language-colors.json` — **third-party, insulated.** The language → color map from [github-linguist](https://github.com/github-linguist/linguist), MIT.
- `languages.svg`, `languages-recent.svg`, `languages-cache.json` — **generated** by `readme-updater`; do not edit by hand.

Everything else is referenced, not copied. The csshW logo, for example,
loads straight from [whme/csshw](https://github.com/whme/csshw/blob/main/res/csshw.svg):
it is my own actively-maintained repository, so a reference stays in
sync if I ever rebrand, and the only way it breaks is if I delete the
repository myself — at which point I would be revising this profile
anyway. (If that trade ever feels wrong, vendoring it is a one-line
change.)

## How the language bars are computed

Both bars measure the **lines I added** per language — all time and over
the last 30 days — across every repository I commit to, private ones
included. A file's language comes from its extension; generated and
vendored files are skipped.

`languages-cache.json` is what keeps this cheap. It stores one
independent slice per repository — an opaque hash key (never the repo
name), the newest commit already counted, and the per-language totals —
so each run only fetches the commits added since last time. Storing
slices independently also makes rewritten history safe: a vanished head
commit rebuilds just that slice instead of double-counting.
