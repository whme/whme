"""The all-time and last-30-days language bars."""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from readme_updater.cache import LanguageCache, RepoStats, repo_key
from readme_updater.markup import ASSET_DIR, abbreviate, image
from readme_updater.monitoring import FINGERPRINT_LOG_KEY

if TYPE_CHECKING:
    from collections.abc import Callable

    from readme_updater.github import Contribution, Profile

logger = logging.getLogger(__name__)

# The vendored linguist color map (see assets/README.md); unknown
# languages fall back to gray, like GitHub renders them.
COLORS_PATH = f"{ASSET_DIR}/language-colors.json"
FALLBACK_COLOR = "#ededed"
LANGUAGE_ICONS = {
    "Python": f"{ASSET_DIR}/python.svg",
    "TypeScript": f"{ASSET_DIR}/typescript.svg",
    "JavaScript": f"{ASSET_DIR}/javascript.svg",
    "Vue": f"{ASSET_DIR}/vue.svg",
    "Lua": f"{ASSET_DIR}/lua.svg",
    "Rust": f"{ASSET_DIR}/rust.svg",
    "PowerShell": f"{ASSET_DIR}/powershell.svg",
    "CSS": f"{ASSET_DIR}/css.svg",
}
OTHER = "Other"
MIN_SHARE = 5.0  # smaller languages are grouped into Other, keeping the legend short
# The legend folds everything under MIN_SHARE into one Other entry, but the bar
# keeps each language's real color down to this finer floor so the grouped region
# still shows how it splits; the thinner tail below it becomes a single gray cell.
BAR_MIN_SHARE = 1.0
# But never let Other dominate: if the grouped tail exceeds this, its largest
# members are promoted back into the legend until Other drops below it.
OTHER_MAX_SHARE = 20.0
# Wider than any real markdown container, so max-width:100% always clamps
# the bar to exactly the available width, flush with the legend beneath.
BAR_WIDTH, BAR_HEIGHT, BAR_RADIUS = 1200, 14, 7
# White border drawn *inside* the bar (never past its edges) framing the grouped
# Other region so it reads as one block; its languages sit edge to edge within,
# their own colors — not white lines — marking where they split.
BAR_BORDER = 3.0
BAR_BORDER_COLOR = "#ffffff"
# A thin white gap between adjacent legend segments, so same-hue neighbours
# (Python and TypeScript are both mid-blue) read as separate blocks.
BAR_GAP = 3.0
TOTAL_BAR_PATH = f"{ASSET_DIR}/languages.svg"
RECENT_BAR_PATH = f"{ASSET_DIR}/languages-recent.svg"
TOTAL_RANKED_PATH = f"{ASSET_DIR}/languages-ranked.svg"
RECENT_RANKED_PATH = f"{ASSET_DIR}/languages-recent-ranked.svg"
# The ranked chart's text inks (primary, muted) per GitHub theme. The README
# offers the dark file through <picture>, which GitHub matches to the viewer's
# theme, so the labels read as native text on either.
CHART_INKS = {"light": ("#1f2328", "#59636e"), "dark": ("#f0f6fc", "#9198a1")}
CHART_FONT = (
    '-apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica,'
    " Arial, sans-serif"
)
CHART_WIDTH, CHART_ROW, CHART_ICON, CHART_FONT_SIZE = 640, 28, 16, 14
CHART_GUTTER = 8
# A generous average glyph width, to size the name column without a font.
CHART_CHAR_WIDTH = 8
# Bars stay thin, with a rounded data end and a square baseline; even a sliver
# keeps a visible stub. The value column leaves room for the top bar's label.
CHART_BAR, CHART_BAR_RADIUS, CHART_MIN_BAR, CHART_VALUE_COLUMN = 12, 4, 2.0, 110
RECENT_DAYS = 30
RECENT_KEEP_DAYS = RECENT_DAYS + 5  # a little slack before pruning old buckets
CACHE_PATH = f"{ASSET_DIR}/languages-cache.json"
# Persist a large repository's progress part way through, so an interrupted run
# resumes near where it stopped instead of re-fetching the whole repository.
# The commit count triggers a checkpoint; the minimum interval caps how often
# the whole cache file is rewritten on a repository whose commits fetch quickly.
CHECKPOINT_EVERY_N_COMMITS = 50
CHECKPOINT_MIN_SECONDS = 10

# Languages recognized by file extension; both bars count added lines, so a
# file's language is inferred from its name.
EXTENSION_LANGUAGES = {
    ".py": "Python",
    ".rs": "Rust",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".vue": "Vue",
    ".sh": "Shell",
    ".bash": "Shell",
    ".lua": "Lua",
    ".html": "HTML",
    ".css": "CSS",
    ".scss": "CSS",
    ".java": "Java",
    ".c": "C",
    ".h": "C",
    ".cpp": "C++",
    ".go": "Go",
    ".rb": "Ruby",
    ".ps1": "PowerShell",
    ".kt": "Kotlin",
}

# Generated and vendored files are not authored code, so they do not count,
# in the same spirit as linguist's own vendored/generated exclusions.
EXCLUDED_NAMES = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "Cargo.lock",
        "poetry.lock",
        "uv.lock",
        "composer.lock",
        "Gemfile.lock",
    }
)
EXCLUDED_PATH_PARTS = (
    "node_modules/",
    "vendor/",
    "dist/",
    "build/",
    "target/",
    "generated/",
    "testdata/",
    "fixtures/",
    "tests/inputs/",
    ".min.",
    ".generated.",
    ".pb.go",
)

# A file adding more than this in one commit is a blob, not authored code.
MAX_COUNTED_FILE_ADDITIONS = 10_000


@dataclass(frozen=True)
class LanguageShare:
    """One legend entry: a language, its percentage share and its line count."""

    language: str
    share: float  # percentage, 0-100
    count: int  # added lines backing the share


@dataclass(frozen=True)
class BarSegment:
    """One drawn bar slice: a language, its share, and whether it is in Other.

    Segments flagged ``in_other`` make up the grouped region the legend shows as
    a single ``Other`` entry; the bar still colors them but frames the region
    with a white border so it reads as one block.
    """

    language: str
    share: float  # percentage, 0-100
    in_other: bool


def load_colors(base: Path) -> dict[str, str]:
    """Loads the vendored linguist color map.

    Args:
      base:  Directory the asset paths are resolved against.

    Returns:
      The language-to-color map.
    """
    return json.loads((base / COLORS_PATH).read_text(encoding="utf-8"))


def is_countable(path: str) -> bool:
    """Whether a file counts as authored code, not generated or vendored.

    Args:
      path:  Repository-relative path of the file.

    Returns:
      True when the file is authored code that should be counted.
    """
    if path.rsplit("/", 1)[-1] in EXCLUDED_NAMES:
        return False
    low = path.lower()
    return not any(part in low for part in EXCLUDED_PATH_PARTS)


def commit_additions(commit: dict[str, Any]) -> dict[str, int]:
    """Sums the lines a commit added per language, from its file list.

    Args:
      commit:  A detailed commit payload carrying a per-file ``files`` list.

    Returns:
      The lines added per language, skipping files without a known language and
      non-authored blobs (generated, vendored, fixture, or oversized).
    """
    counts: dict[str, int] = {}
    for file in commit.get("files", []):
        path = file["filename"]
        additions = file.get("additions", 0)
        language = EXTENSION_LANGUAGES.get(Path(path).suffix.lower())
        if language and is_countable(path) and additions <= MAX_COUNTED_FILE_ADDITIONS:
            counts[language] = counts.get(language, 0) + additions
    return counts


def contributed_repos(
    owned: list[str], contributions: list[Contribution], profile_repo: str
) -> list[str]:
    """Lists every repository to draw from: the owned and the contributed-to.

    Args:
      owned:          Repositories the tracked accounts own.
      contributions:  Recent contributions whose repositories are also drawn.
      profile_repo:   The profile repository, always excluded.

    Returns:
      The sorted, de-duplicated repositories, without the profile repository.
    """
    repos = set(owned)
    repos.update(contribution.repo for contribution in contributions)
    repos.discard(profile_repo)
    return sorted(repos)


def ingest_commit(stats: RepoStats, commit: dict[str, Any]) -> None:
    """Folds one commit's additions into a repository's totals and buckets.

    Args:
      stats:   Repository slice updated in place.
      commit:  A detailed commit payload with a committer date and file list.
    """
    day = commit["commit"]["committer"]["date"][:10]
    bucket = stats.recent.setdefault(day, {})
    for language, additions in commit_additions(commit).items():
        stats.all_time[language] = stats.all_time.get(language, 0) + additions
        bucket[language] = bucket.get(language, 0) + additions


def update_repo(  # noqa: PLR0913, PLR0917 - one parameter per refresh input
    profile: Profile,
    cache: LanguageCache,
    repo: str,
    checkpoint: Callable[[], None] | None = None,
    concurrency: int = 1,
    skip_shas: set[str] | None = None,
) -> Literal["unchanged", "incremental", "rebuilt"]:
    """Refreshes one repository's slice from the commits added since last run.

    New commits on top of a known head are added incrementally; a first run
    or a rewritten history rebuilds the whole slice and replaces it, so a
    vanished head can never double-count. Commits are consumed oldest first and
    the head advances per commit, so ``checkpoint`` runs every
    ``CHECKPOINT_EVERY_N_COMMITS`` commits — but no more often than
    ``CHECKPOINT_MIN_SECONDS`` — persisting a partial slice from which the next
    run resumes.

    Args:
      profile:      Profile whose commits are fetched.
      cache:        Cache whose slice for ``repo`` is updated in place.
      repo:         ``owner/name`` repository to refresh.
      checkpoint:   Hook run periodically mid-repository to persist progress.
      concurrency:  Number of commit details to fetch in parallel.
      skip_shas:    Commit SHAs to exclude, such as a fork's shared ancestor
                    history counted under its parent. Applied at every build, so
                    a rewritten history cannot reintroduce them; harmless on an
                    incremental run, whose listing stops at the head before the
                    shared ancestors.

    Returns:
      What the run did: ``"unchanged"``, ``"incremental"`` or ``"rebuilt"``.
    """
    key = repo_key(repo)
    previous = cache.repos.get(key)
    head = previous.head if previous else None
    commits, found = profile.fetch_commits_since(repo, head, concurrency, skip_shas)
    incremental = found and previous is not None
    stats = previous if incremental and previous else RepoStats()
    # Put the slice into the in-memory cache before consuming the stream, so a
    # mid-repository checkpoint writes this partial slice to disk rather than the
    # pre-run one. (Nothing here touches git; the workflow commits the file.)
    cache.repos[key] = stats
    ingested = 0
    since_checkpoint = 0
    last_checkpoint = time.monotonic()
    for commit in commits:
        ingest_commit(stats, commit)
        # Commits arrive oldest first, so each is the newest counted; advancing
        # the head per commit is what lets a checkpoint or a stopped fetch
        # resume here, leaving the un-fetched newer commits for the next run.
        stats.head = commit["sha"]
        ingested += 1
        since_checkpoint += 1
        now = time.monotonic()
        if (
            checkpoint is not None
            and since_checkpoint >= CHECKPOINT_EVERY_N_COMMITS
            and now - last_checkpoint >= CHECKPOINT_MIN_SECONDS
        ):
            checkpoint()
            since_checkpoint = 0
            last_checkpoint = now
    return "unchanged" if not ingested else "incremental" if incremental else "rebuilt"


def update_language_cache(  # noqa: PLR0913, PLR0917 - one parameter per refresh input
    profile: Profile,
    cache: LanguageCache,
    repos: list[str],
    after_repo: Callable[[], None] | None = None,
    concurrency: int = 1,
    forks: dict[str, str] | None = None,
) -> None:
    """Refreshes every repository's slice, calling ``after_repo`` after each.

    Args:
      profile:      Profile whose commits are fetched.
      cache:        Cache whose slices are updated in place.
      repos:        Repositories to refresh.
      after_repo:   Hook run to persist progress — both mid-repository as a
                    checkpoint and after each repository completes — so an
                    interrupted run resumes rather than restarting the backfill.
      concurrency:  Number of commit details to fetch in parallel.
      forks:        Maps a ``successor`` repository to the ``parent`` it branched
                    off. When both are refreshed, the shared ancestor commits are
                    counted once under the parent and dropped from the successor,
                    so a fork's inherited history is not double-counted. A parent
                    that is not itself refreshed is ignored, so its lines are
                    never lost.
    """
    forks = forks or {}
    counted = set(repos)
    logger.info(
        "updating language cache across %(count)d repositories",
        {"count": len(repos)},
    )
    for index, repo in enumerate(repos, start=1):
        # Skip shared history only when the parent is counted, else its lines vanish.
        parent = forks.get(repo)
        skip_shas = (
            profile.fetch_commit_shas(parent) if parent and parent in counted else None
        )
        # The same hook persists progress mid-repository (checkpoint) and after
        # each repository completes, so a large repository resumes rather than
        # restarting when a run is interrupted.
        mode = update_repo(
            profile,
            cache,
            repo,
            checkpoint=after_repo,
            concurrency=concurrency,
            skip_shas=skip_shas,
        )
        # The commit count stays in github's per-repo line to avoid double-logging.
        logger.info(
            "[%(index)d/%(total)d] %(repo)s: %(mode)s",
            {"index": index, "total": len(repos), "repo": repo, "mode": mode},
        )
        if after_repo is not None:
            after_repo()


def prune_recent(cache: LanguageCache, cutoff: date) -> None:
    """Drops day buckets older than the cutoff to keep the cache small.

    Args:
      cache:   Cache whose slices are pruned in place.
      cutoff:  Earliest day to keep; older buckets are dropped.
    """
    for stats in cache.repos.values():
        stats.recent = {
            day: bucket
            for day, bucket in stats.recent.items()
            if date.fromisoformat(day) >= cutoff
        }


def total_counts(cache: LanguageCache) -> dict[str, int]:
    """Merges every repository's all-time additions per language.

    Args:
      cache:  Cache whose slices are summed.

    Returns:
      The all-time lines added per language, across all repositories.
    """
    counts: dict[str, int] = {}
    for stats in cache.repos.values():
        for language, additions in stats.all_time.items():
            counts[language] = counts.get(language, 0) + additions
    return counts


def recent_counts(cache: LanguageCache, cutoff: date) -> dict[str, int]:
    """Merges the additions per language from every bucket on or after cutoff.

    Args:
      cache:   Cache whose recent buckets are summed.
      cutoff:  Earliest day to include.

    Returns:
      The lines added per language within the window.
    """
    counts: dict[str, int] = {}
    for stats in cache.repos.values():
        for day, bucket in stats.recent.items():
            if date.fromisoformat(day) >= cutoff:
                for language, additions in bucket.items():
                    counts[language] = counts.get(language, 0) + additions
    return counts


def _grouped(
    counts: dict[str, int],
) -> tuple[list[LanguageShare], list[LanguageShare]]:
    """Splits the languages into the legend's own entries and the grouped tail.

    Args:
      counts:  Lines added per language.

    Returns:
      A ``(main, tail)`` pair, both largest first: ``main`` are the languages the
      legend lists individually — those at or above ``MIN_SHARE``, plus any
      promoted so ``Other`` stays under ``OTHER_MAX_SHARE`` — and ``tail`` are the
      smaller ones it folds into ``Other``. Both are empty without data.
    """
    total = sum(counts.values())
    if not total:
        return [], []
    shares = sorted(
        (
            LanguageShare(language, 100 * count / total, count)
            for language, count in counts.items()
        ),
        key=lambda share: share.share,
        reverse=True,
    )
    main = [share for share in shares if share.share >= MIN_SHARE]
    tail = [share for share in shares if share.share < MIN_SHARE]
    # Promote the largest grouped languages (the tail is sorted largest first)
    # back into the legend until what remains for Other sits below the cap. A
    # promoted language becomes a normal segment, no longer part of Other.
    while tail and sum(share.share for share in tail) >= OTHER_MAX_SHARE:
        main.append(tail.pop(0))
    return main, tail


def language_shares(counts: dict[str, int]) -> list[LanguageShare]:
    """Turns additions per language into percentages, grouping the tail as Other.

    Args:
      counts:  Lines added per language.

    Returns:
      The languages with their percentage shares and line counts, largest
      first, with shares below ``MIN_SHARE`` collapsed into a trailing
      ``Other`` entry whose count sums the languages it absorbs. Should that
      ``Other`` entry exceed ``OTHER_MAX_SHARE``, its largest members are
      promoted back into the legend until it drops below the cap.
    """
    main, tail = _grouped(counts)
    # Warn on a legend language with no icon so Sentry flags it and we vendor one.
    for entry in main:
        if entry.language not in LANGUAGE_ICONS:
            logger.warning(
                "no icon for %(language)s (%(share).1f%%) in the legend",
                {"language": entry.language, "share": entry.share},
                # One new Sentry issue per language without icon.
                extra={FINGERPRINT_LOG_KEY: ["no-icon-for-language", entry.language]},
            )
    if tail:
        main.append(
            LanguageShare(
                OTHER,
                sum(share.share for share in tail),
                sum(share.count for share in tail),
            )
        )
    return main


def bar_segments(counts: dict[str, int]) -> list[BarSegment]:
    """Breaks the counts into the bar's draw order, finer than the legend.

    The legend folds everything under ``MIN_SHARE`` into one ``Other`` entry, but
    the bar keeps those languages' real colors down to ``BAR_MIN_SHARE`` so the
    grouped region shows how it splits; the thinner tail below ``BAR_MIN_SHARE``
    becomes a single gray cell, matching the legend's ``Other``.

    Args:
      counts:  Lines added per language.

    Returns:
      The segments largest first: the legend's own languages, then the grouped
      languages the bar still colors, then one gray tail cell for the remainder
      — the latter two flagged ``in_other``.
    """
    main, tail = _grouped(counts)
    segments = [BarSegment(s.language, s.share, in_other=False) for s in main]
    colored = [share for share in tail if share.share >= BAR_MIN_SHARE]
    remainder = [share for share in tail if share.share < BAR_MIN_SHARE]
    segments += [BarSegment(s.language, s.share, in_other=True) for s in colored]
    if remainder:
        segments.append(
            BarSegment(OTHER, sum(share.share for share in remainder), in_other=True)
        )
    return segments


def language_bar(segments: list[BarSegment], colors: dict[str, str]) -> str:
    """Draws the segments as a rounded horizontal bar, GitHub-repo style.

    The legend's own languages are split by a thin white gap. The grouped ``Other``
    languages keep their colors too but are inset by a white border that stays
    inside the bar, so the region reads as one block while still showing how it
    splits — its sub-``BAR_MIN_SHARE`` tail is the single gray cell at the end.

    Args:
      segments:  Bar slices in draw order, from ``bar_segments``.
      colors:    Language-to-color map; unknown languages fall back to gray.

    Returns:
      The bar as a self-contained SVG document.
    """
    placed = []
    x = 0.0
    for segment in segments:
        width = BAR_WIDTH * segment.share / 100
        placed.append((segment, x, width))
        x += width
    # A white base shows through the gaps between the legend segments.
    rects = [
        f'<rect width="{BAR_WIDTH}" height="{BAR_HEIGHT}" fill="{BAR_BORDER_COLOR}"/>'
    ]
    main = [item for item in placed if not item[0].in_other]
    # The gaps cost (len(main) - 1) * BAR_GAP of width in total; charge every
    # segment an equal share so none is favoured. The first still starts at 0 and
    # the last still meets the bar's end or the Other region's own frame.
    gap_per_segment = (len(main) - 1) * BAR_GAP / len(main) if main else 0.0
    cursor = 0.0
    for segment, _, width in main:
        drawn = width - gap_per_segment
        color = colors.get(segment.language, FALLBACK_COLOR)
        rects.append(
            f'<rect x="{cursor:.1f}" width="{drawn:.1f}"'
            f' height="{BAR_HEIGHT}" fill="{color}"/>'
        )
        cursor += drawn + BAR_GAP
    others = [item for item in placed if item[0].in_other]
    if others:
        region_start = others[0][1]
        region_width = others[-1][1] + others[-1][2] - region_start
        # A white block behind the whole grouped region shows through as the
        # border framing it — top, bottom, and left, all inside the bar (the
        # right is the bar's own rounded end). The languages sit on top edge to
        # edge, so their colors, not white lines, mark where they split.
        rects.append(
            f'<rect x="{region_start:.1f}" width="{region_width:.1f}"'
            f' height="{BAR_HEIGHT}" fill="{BAR_BORDER_COLOR}"/>'
        )
        inner_height = BAR_HEIGHT - 2 * BAR_BORDER
        for index, (segment, start, width) in enumerate(others):
            # Only the first cell is inset on the left, for the region's left
            # frame; the rest butt against their neighbor, no border between.
            left = start + BAR_BORDER if index == 0 else start
            inner = start + width - left
            if inner <= 0:  # a slice too thin to inset stays pure border
                continue
            color = colors.get(segment.language, FALLBACK_COLOR)
            rects.append(
                f'<rect x="{left:.1f}" y="{BAR_BORDER:.1f}"'
                f' width="{inner:.1f}" height="{inner_height:.1f}" fill="{color}"/>'
            )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg"'
        f' width="{BAR_WIDTH}" height="{BAR_HEIGHT}">'
        f'<clipPath id="round"><rect width="{BAR_WIDTH}"'
        f' height="{BAR_HEIGHT}" rx="{BAR_RADIUS}"/></clipPath>'
        f'<g clip-path="url(#round)">{"".join(rects)}</g></svg>'
    )


def language_line(shares: list[LanguageShare]) -> str:
    """Renders the legend: icon (where there is one), language, percent, lines.

    Args:
      shares:  Languages with their percentage shares and counts, in legend
               order.

    Returns:
      The legend line, entries separated by a middle dot.
    """
    parts = []
    for entry in shares:
        icon = LANGUAGE_ICONS.get(entry.language)
        prefix = f"{image(icon, alt='')} " if icon else ""
        parts.append(
            f"{prefix}{entry.language} {entry.share:.1f}% ({abbreviate(entry.count)})"
        )
    return " · ".join(parts)


def full_shares(counts: dict[str, int]) -> list[LanguageShare]:
    """Lists every language with its share, largest first, nothing grouped.

    Args:
      counts:  Lines added per language.

    Returns:
      Every language, including those the legend folds into ``Other``.
    """
    main, tail = _grouped(counts)
    return [*main, *tail]


def language_title(counts: dict[str, int]) -> str:
    """Builds the bar's hover text: every language in full, largest first.

    Unlike the legend, nothing is folded into ``Other`` — the tooltip spells out
    the whole distribution, including the small languages the legend groups away,
    so hovering the bar reveals what ``Other`` contains.

    Args:
      counts:  Lines added per language.

    Returns:
      A plain-text ``language share% (count)`` list joined by a middle dot,
      empty when there is no data.
    """
    return " · ".join(
        f"{share.language} {share.share:.1f}% ({abbreviate(share.count)})"
        for share in full_shares(counts)
    )


def dark_variant(path: str) -> str:
    """Names the dark-theme twin of a chart file.

    Args:
      path:  The light-theme chart's path.

    Returns:
      The same path with ``-dark`` before the ``.svg`` suffix.
    """
    return f"{path.removesuffix('.svg')}-dark.svg"


def load_icons(base: Path) -> dict[str, str]:
    """Reads the language icons, to inline them into the ranked chart.

    An SVG shown through ``<img>`` loads no external files, so the chart has to
    carry its icons inside.

    Args:
      base:  Directory the asset paths are resolved against.

    Returns:
      Each language's icon SVG; languages whose file is missing are left out.
    """
    return {
        language: (base / path).read_text(encoding="utf-8")
        for language, path in LANGUAGE_ICONS.items()
        if (base / path).is_file()
    }


_ROOT_TAG = re.compile(r"<svg\b([^>]*)>")
_ATTRIBUTE = re.compile(r'([\w:-]+)="([^"]*)"')
_REFERENCE = re.compile(r'(\bid="|url\(#|href="#)')
# Root attributes the inlined copy sets itself or has no use for.
_DROPPED_ROOT_ATTRIBUTES = frozenset(
    {"xmlns", "xmlns:xlink", "width", "height", "x", "y", "role", "xml:space"}
)


def _inlined_icon(icon: str, prefix: str, x: float, y: float) -> str:
    """Re-roots an icon SVG as a nested ``<svg>`` at a spot in the chart.

    Args:
      icon:    The icon's SVG document.
      prefix:  Prepended to the icon's ids and references, which would clash
               with another icon's once they share one document.
      x:       Left edge of the icon in the chart.
      y:       Top edge of the icon in the chart.

    Returns:
      The nested ``<svg>`` element, ``CHART_ICON`` pixels square; empty when
      the icon has no ``<svg>`` root.
    """
    root = _ROOT_TAG.search(icon)
    if root is None:
        return ""
    kept = "".join(
        f' {name}="{value}"'
        for name, value in _ATTRIBUTE.findall(root.group(1))
        if name not in _DROPPED_ROOT_ATTRIBUTES
    )
    body = re.sub(r"<title>.*?</title>", "", icon[root.end() : icon.rindex("</svg>")])
    body = _REFERENCE.sub(lambda match: f"{match.group(1)}{prefix}-", body)
    return (
        f'<svg x="{x:g}" y="{y:g}" width="{CHART_ICON}" height="{CHART_ICON}"'
        f"{kept}>{body}</svg>"
    )


def _bar_path(x: float, y: float, width: float) -> str:
    """Outlines a bar with a square baseline and a rounded data end.

    Args:
      x:      Baseline, where the bar starts.
      y:      Top edge of the bar.
      width:  Length of the bar.

    Returns:
      The bar's SVG path data.
    """
    radius = min(CHART_BAR_RADIUS, width)
    straight = width - radius
    return (
        f"M{x:g},{y:g}h{straight:.1f}"
        f"a{radius:g},{radius:g} 0 0 1 {radius:g},{radius:g}"
        f"v{CHART_BAR - 2 * radius:g}"
        f"a{radius:g},{radius:g} 0 0 1 -{radius:g},{radius:g}"
        f"h-{straight:.1f}z"
    )


def ranked_chart(
    shares: list[LanguageShare],
    colors: dict[str, str],
    icons: dict[str, str],
    theme: str,
) -> str:
    """Draws every language as a ranked bar, largest first, GitHub-themed.

    Each row is an icon (a color dot where there is none), the language, a bar
    scaled against the largest language's, then its share and line count.

    Args:
      shares:  Languages in rank order, from ``full_shares``.
      colors:  Language-to-color map; unknown languages fall back to gray.
      icons:   Icon SVGs to inline, from ``load_icons``.
      theme:   ``"light"`` or ``"dark"``, picking the text inks.

    Returns:
      The chart as a self-contained SVG document, empty without data.
    """
    if not shares:
        return ""
    ink, muted = CHART_INKS[theme]
    name_x = CHART_ICON + CHART_GUTTER
    longest = max(len(s.language) for s in shares)
    bar_x = name_x + longest * CHART_CHAR_WIDTH + CHART_GUTTER
    span = CHART_WIDTH - bar_x - CHART_VALUE_COLUMN
    rows = []
    for index, share in enumerate(shares):
        middle = index * CHART_ROW + CHART_ROW / 2
        color = colors.get(share.language, FALLBACK_COLOR)
        icon = icons.get(share.language)
        badge = (
            _inlined_icon(icon, f"i{index}", 0, middle - CHART_ICON / 2)
            if icon
            else f'<circle cx="{CHART_ICON / 2:g}" cy="{middle:g}" r="5"'
            f' fill="{color}"/>'
        )
        width = max(CHART_MIN_BAR, span * share.share / shares[0].share)
        bar = _bar_path(bar_x, middle - CHART_BAR / 2, width)
        value_x = bar_x + width + CHART_GUTTER
        rows.append(
            f"{badge}"
            f'<text x="{name_x}" y="{middle:g}" dy="0.35em">{share.language}</text>'
            f'<path d="{bar}" fill="{color}"/>'
            f'<text x="{value_x:.1f}" y="{middle:g}" dy="0.35em" class="share">'
            f"{share.share:.1f}%"
            f'<tspan class="count" dx="6">{abbreviate(share.count)}</tspan></text>'
        )
    height = len(shares) * CHART_ROW
    # Each label's 0.35em drop centers it on its row, more portably than
    # dominant-baseline does.
    style = (
        f"text{{font:{CHART_FONT_SIZE}px {CHART_FONT};fill:{ink};"
        "font-variant-numeric:tabular-nums}"
        f".share{{font-weight:600}}.count{{font-weight:400;fill:{muted}}}"
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{CHART_WIDTH}"'
        f' height="{height}" viewBox="0 0 {CHART_WIDTH} {height}">'
        f"<style>{style}</style>{''.join(rows)}</svg>"
    )


def write_ranked_charts(
    base: Path,
    shares: list[LanguageShare],
    colors: dict[str, str],
    icons: dict[str, str],
    path: str,
) -> None:
    """Writes the ranked chart's light file and its dark twin.

    Args:
      base:    Directory the asset paths are resolved against.
      shares:  Languages in rank order, from ``full_shares``.
      colors:  Language-to-color map; unknown languages fall back to gray.
      icons:   Icon SVGs to inline, from ``load_icons``.
      path:    Where the light file is written; the dark one sits beside it.
    """
    for theme, target in (("light", path), ("dark", dark_variant(path))):
        (base / target).write_text(
            ranked_chart(shares, colors, icons, theme), encoding="utf-8"
        )


def ranked_picture(label: str, path: str, shares: list[LanguageShare]) -> str:
    """Embeds the ranked chart, switching to its dark twin on dark themes.

    Args:
      label:   Heading of the section, such as "All time".
      path:    Source path of the light chart.
      shares:  Languages in rank order, spelled out in the alt text.

    Returns:
      The ``<picture>`` markup, or an empty string without data.
    """
    if not shares:
        return ""
    ranked = ", ".join(f"{s.language} {s.share:.1f}%" for s in shares)
    return (
        f'<picture><source media="(prefers-color-scheme: dark)"'
        f' srcset="{dark_variant(path)}">'
        f'<img src="{path}" alt="{label} languages, ranked: {ranked}"></picture>'
    )


def language_section(
    label: str,
    path: str,
    shares: list[LanguageShare],
    title: str = "",
    breakdown: str = "",
) -> str:
    """Renders one labeled language bar and legend, empty when there is no data.

    Each bar is its own README section; the template decides where the recent
    and all-time bars sit relative to each other. With a breakdown, the label
    and bar become the summary of a ``<details>`` that unfolds it on click; the
    legend stays outside, so clicking it does nothing.

    Args:
      label:      Heading shown above the bar, such as "All time".
      path:       Source path of the rendered bar image.
      shares:     Languages and their percentage shares; empty renders nothing.
      title:      Hover text for the bar image, listing the full distribution;
                  omitted when empty.
      breakdown:  Markup unfolded by clicking the bar; omitted when empty.

    Returns:
      The section markup, or an empty string when there are no shares.
    """
    if not shares:
        return ""
    tooltip = f' title="{title}"' if title else ""
    # <picture> keeps GitHub from linking the bar image to its own source, which
    # inside <summary> would also swallow the click that unfolds the breakdown.
    bar = (
        f'<picture><img src="{path}" alt="{label} language distribution"'
        f"{tooltip}></picture>"
    )
    legend = language_line(shares)
    if not breakdown:
        return f"<sub>{label}</sub>\\\n{bar}\\\n{legend}"
    # GitHub leaves <summary> as raw HTML, hence <br> over a trailing backslash;
    # the blank lines hand the breakdown back to its markdown parser. The <p>
    # spaces the legend off the next section as far as <details> spaces it off
    # the bar.
    return (
        f"<details>\n<summary><sub>{label}</sub><br>\n{bar}</summary>\n\n"
        f"{breakdown}\n\n</details>\n<p>{legend}</p>"
    )
