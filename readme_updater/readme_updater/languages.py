"""The all-time and last-30-days language bars."""

from __future__ import annotations

import json
import logging
import math
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
TOTAL_DONUT_PATH = f"{ASSET_DIR}/languages-donut.svg"
RECENT_DONUT_PATH = f"{ASSET_DIR}/languages-recent-donut.svg"
# The unfolded breakdown draws its ranked bars and legend keys from one flat tile
# per color, sized by the <img> embedding it; GitHub drops data: URIs, so each
# color needs a file of its own.
SWATCH_DIR = f"{ASSET_DIR}/swatches"
SWATCH_RADIUS = 2
LEGEND_KEY_SIZE = 10
# The longest ranked bar (the top language) spans this many pixels.
RANKED_BAR_WIDTH, RANKED_BAR_HEIGHT = 200, 10
# Slices are parted by a transparent gap, so it reads on light and dark themes.
DONUT_SIZE, DONUT_THICKNESS, DONUT_GAP = 160, 28, 2.0
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


def swatch_path(color: str) -> str:
    """Names the swatch file of a color.

    Args:
      color:  ``#rrggbb`` color.

    Returns:
      The swatch's path, named by the lowercase hex digits.
    """
    return f"{SWATCH_DIR}/{color.removeprefix('#').lower()}.svg"


def swatch(color: str) -> str:
    """Draws a flat, slightly rounded tile of one color.

    Without a viewBox the tile fills whatever size its ``<img>`` gives it, and
    the corner radius stays in pixels rather than stretching with the width.

    Args:
      color:  Fill color.

    Returns:
      The tile as a self-contained SVG document.
    """
    return (
        '<svg xmlns="http://www.w3.org/2000/svg">'
        f'<rect width="100%" height="100%" rx="{SWATCH_RADIUS}" fill="{color}"/>'
        "</svg>"
    )


def _swatch_image(
    language: str, colors: dict[str, str], width: int, height: int
) -> str:
    """Embeds a language's color swatch at the given size.

    Args:
      language:  Language whose color is shown.
      colors:    Language-to-color map; unknown languages fall back to gray.
      width:     Rendered width in pixels.
      height:    Rendered height in pixels.

    Returns:
      The ``<picture>``-wrapped image markup.
    """
    color = colors.get(language, FALLBACK_COLOR)
    return image(swatch_path(color), alt="", width=width, height=height)


def _polar(angle: float, radius: float) -> str:
    """Places a point on the donut, clockwise from 12 o'clock.

    Args:
      angle:   Angle in radians, 0 at the top.
      radius:  Distance from the donut's center.

    Returns:
      The point as an SVG ``x,y`` coordinate pair.
    """
    center = DONUT_SIZE / 2
    x = center + radius * math.sin(angle)
    y = center - radius * math.cos(angle)
    return f"{x:.2f},{y:.2f}"


def _slice_path(start: float, end: float) -> str:
    """Outlines one donut slice, inset to leave half a gap on either side.

    Each edge is inset by the angle that keeps it ``DONUT_GAP / 2`` away from
    the slice boundary at both radii, so a gap runs parallel from rim to hole.

    Args:
      start:  Start angle in radians.
      end:    End angle in radians.

    Returns:
      The slice's SVG path data, empty when it is too thin to outlast the gaps.
    """
    outer = DONUT_SIZE / 2
    inner = outer - DONUT_THICKNESS
    outer_inset = math.asin(DONUT_GAP / 2 / outer)
    inner_inset = math.asin(DONUT_GAP / 2 / inner)
    if end - start <= 2 * inner_inset:
        return ""
    outer_start, outer_end = start + outer_inset, end - outer_inset
    inner_start, inner_end = start + inner_inset, end - inner_inset
    outer_large = int(outer_end - outer_start > math.pi)
    inner_large = int(inner_end - inner_start > math.pi)
    return (
        f"M{_polar(outer_start, outer)}"
        f"A{outer},{outer} 0 {outer_large} 1 {_polar(outer_end, outer)}"
        f"L{_polar(inner_end, inner)}"
        f"A{inner},{inner} 0 {inner_large} 0 {_polar(inner_start, inner)}Z"
    )


def language_donut(shares: list[LanguageShare], colors: dict[str, str]) -> str:
    """Draws every language as a donut slice, largest first from 12 o'clock.

    Args:
      shares:  Languages in slice order, from ``full_shares``.
      colors:  Language-to-color map; unknown languages fall back to gray.

    Returns:
      The donut as a self-contained SVG document; slices too thin to show past
      the gaps are left out (the legend beside it still lists them).
    """
    marks = []
    if len(shares) == 1:
        # A lone language closes the ring, leaving no gap to inset against.
        color = colors.get(shares[0].language, FALLBACK_COLOR)
        marks.append(
            f'<circle cx="{DONUT_SIZE / 2}" cy="{DONUT_SIZE / 2}"'
            f' r="{(DONUT_SIZE - DONUT_THICKNESS) / 2}" fill="none"'
            f' stroke="{color}" stroke-width="{DONUT_THICKNESS}"/>'
        )
    else:
        start = 0.0
        for share in shares:
            end = start + 2 * math.pi * share.share / 100
            if path := _slice_path(start, end):
                color = colors.get(share.language, FALLBACK_COLOR)
                marks.append(f'<path d="{path}" fill="{color}"/>')
            start = end
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg"'
        f' width="{DONUT_SIZE}" height="{DONUT_SIZE}">{"".join(marks)}</svg>'
    )


def language_breakdown(
    label: str, counts: dict[str, int], colors: dict[str, str], donut_path: str
) -> str:
    """Renders the full distribution twice: as a donut and as ranked bars.

    Nothing is folded into ``Other``. The donut sits beside a legend keyed by
    color swatches; the ranked bars are a table, largest language first, each
    bar scaled against the largest.

    Args:
      label:       Heading of the section, such as "All time".
      counts:      Lines added per language.
      colors:      Language-to-color map; unknown languages fall back to gray.
      donut_path:  Source path of the rendered donut image.

    Returns:
      The breakdown markup, or an empty string when there is no data.
    """
    shares = full_shares(counts)
    if not shares:
        return ""
    keys = "<br>\n".join(
        f"{_swatch_image(s.language, colors, LEGEND_KEY_SIZE, LEGEND_KEY_SIZE)}"
        f" {s.language} {s.share:.1f}% ({abbreviate(s.count)})"
        for s in shares
    )
    # <picture> keeps GitHub from linking the donut image to its own source.
    donut = (
        "<table><tr>\n"
        f'<td><picture><img src="{donut_path}" width="{DONUT_SIZE}"'
        f' height="{DONUT_SIZE}" alt="{label} language donut chart"></picture></td>\n'
        f"<td>\n{keys}\n</td>\n"
        "</tr></table>"
    )
    rows = ["| | Language | | Share | Lines added |", "|---|:--|:--|--:|--:|"]
    for share in shares:
        icon = LANGUAGE_ICONS.get(share.language)
        width = max(1, round(RANKED_BAR_WIDTH * share.share / shares[0].share))
        bar = _swatch_image(share.language, colors, width, RANKED_BAR_HEIGHT)
        rows.append(
            f"| {image(icon, alt='') if icon else ''} | {share.language} | {bar}"
            f" | {share.share:.1f}% | {share.count:,} |"
        )
    return donut + "\n\n" + "\n".join(rows)


def write_chart_assets(
    base: Path,
    counts: dict[str, int],
    colors: dict[str, str],
    bar_path: str,
    donut_path: str,
) -> None:
    """Writes one distribution's bar, its donut and the swatches they reference.

    Args:
      base:        Directory the asset paths are resolved against.
      counts:      Lines added per language.
      colors:      Language-to-color map; unknown languages fall back to gray.
      bar_path:    Where the bar is written.
      donut_path:  Where the donut is written.
    """
    (base / bar_path).write_text(
        language_bar(bar_segments(counts), colors), encoding="utf-8"
    )
    (base / donut_path).write_text(
        language_donut(full_shares(counts), colors), encoding="utf-8"
    )
    (base / SWATCH_DIR).mkdir(exist_ok=True)
    for language in counts:
        color = colors.get(language, FALLBACK_COLOR)
        (base / swatch_path(color)).write_text(swatch(color), encoding="utf-8")


def language_section(
    label: str,
    path: str,
    shares: list[LanguageShare],
    title: str = "",
    breakdown: str = "",
) -> str:
    """Renders one labeled language bar and legend, empty when there is no data.

    Each bar is its own README section; the template decides where the recent
    and all-time bars sit relative to each other. With a breakdown, the bar and
    legend become the summary of a ``<details>`` that unfolds it on click.

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
    # <br>, not markdown's trailing backslash: GitHub leaves a <summary> as raw
    # HTML. <picture> keeps GitHub from linking the bar image to its own source,
    # which inside <summary> would also swallow the click that unfolds it.
    head = (
        f"<sub>{label}</sub><br>\n"
        f'<picture><img src="{path}" alt="{label} language distribution"'
        f"{tooltip}></picture><br>\n"
        f"{language_line(shares)}"
    )
    if not breakdown:
        return head
    # The blank lines hand the breakdown back to GitHub's markdown parser.
    return f"<details>\n<summary>{head}</summary>\n\n{breakdown}\n\n</details>"
