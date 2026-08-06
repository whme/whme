"""The all-time and last-30-days language bars."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from readme_updater.cache import LanguageCache, RepoStats, repo_key
from readme_updater.markup import ASSET_DIR, abbreviate, image

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
TOTAL_BAR_PATH = f"{ASSET_DIR}/languages.svg"
RECENT_BAR_PATH = f"{ASSET_DIR}/languages-recent.svg"
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
    ".min.",
    ".generated.",
    ".pb.go",
)


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
      The lines added per language, skipping files without a known language.
    """
    counts: dict[str, int] = {}
    for file in commit.get("files", []):
        path = file["filename"]
        language = EXTENSION_LANGUAGES.get(Path(path).suffix.lower())
        if language and is_countable(path):
            counts[language] = counts.get(language, 0) + file.get("additions", 0)
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


def update_repo(
    profile: Profile,
    cache: LanguageCache,
    repo: str,
    checkpoint: Callable[[], None] | None = None,
    concurrency: int = 1,
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

    Returns:
      What the run did: ``"unchanged"``, ``"incremental"`` or ``"rebuilt"``.
    """
    key = repo_key(repo)
    previous = cache.repos.get(key)
    head = previous.head if previous else None
    commits, found = profile.fetch_commits_since(repo, head, concurrency)
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


def update_language_cache(
    profile: Profile,
    cache: LanguageCache,
    repos: list[str],
    after_repo: Callable[[], None] | None = None,
    concurrency: int = 1,
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
    """
    logger.info(
        "updating language cache across %(count)d repositories",
        {"count": len(repos)},
    )
    for index, repo in enumerate(repos, start=1):
        # The same hook persists progress mid-repository (checkpoint) and after
        # each repository completes, so a large repository resumes rather than
        # restarting when a run is interrupted.
        mode = update_repo(
            profile, cache, repo, checkpoint=after_repo, concurrency=concurrency
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

    The legend's own languages are drawn edge to edge. The grouped ``Other``
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
    rects = []
    for segment, start, width in placed:
        if segment.in_other:
            continue
        color = colors.get(segment.language, FALLBACK_COLOR)
        rects.append(
            f'<rect x="{start:.1f}" width="{width:.1f}"'
            f' height="{BAR_HEIGHT}" fill="{color}"/>'
        )
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
    main, tail = _grouped(counts)
    return " · ".join(
        f"{share.language} {share.share:.1f}% ({abbreviate(share.count)})"
        for share in (*main, *tail)
    )


def language_section(
    label: str, path: str, shares: list[LanguageShare], title: str = ""
) -> str:
    """Renders one labeled language bar and legend, empty when there is no data.

    Each bar is its own README section; the template decides where the recent
    and all-time bars sit relative to each other.

    Args:
      label:   Heading shown above the bar, such as "All time".
      path:    Source path of the rendered bar image.
      shares:  Languages and their percentage shares; empty renders nothing.
      title:   Hover text for the bar image, listing the full distribution;
               omitted when empty.

    Returns:
      The section markup, or an empty string when there are no shares.
    """
    if not shares:
        return ""
    tooltip = f' title="{title}"' if title else ""
    # <picture> keeps GitHub from linking the bar image to its own source.
    return (
        f"<sub>{label}</sub>\\\n"
        f'<picture><img src="{path}" alt="{label} language distribution"'
        f"{tooltip}></picture>\\\n"
        f"{language_line(shares)}"
    )
