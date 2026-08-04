"""The all-time and last-30-days language bars."""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from readme_updater.cache import LanguageCache, RepoStats, repo_key
from readme_updater.markup import ASSET_DIR, image

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
    "Rust": f"{ASSET_DIR}/rust.svg",
}
OTHER = "Other"
MIN_SHARE = 1.0  # smaller languages are grouped, like GitHub's own bar
# Wider than any real markdown container, so max-width:100% always clamps
# the bar to exactly the available width, flush with the legend beneath.
BAR_WIDTH, BAR_HEIGHT, BAR_RADIUS = 1200, 14, 7
TOTAL_BAR_PATH = f"{ASSET_DIR}/languages.svg"
RECENT_BAR_PATH = f"{ASSET_DIR}/languages-recent.svg"
RECENT_DAYS = 30
RECENT_KEEP_DAYS = RECENT_DAYS + 5  # a little slack before pruning old buckets
CACHE_PATH = f"{ASSET_DIR}/languages-cache.json"

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


def load_colors(base: Path) -> dict[str, str]:
    """Loads the vendored linguist color map.

    Args:
      base:  Directory the asset paths are resolved against.

    Returns:
      The language-to-color map.
    """
    return json.loads((base / COLORS_PATH).read_text())


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


def update_repo(profile: Profile, cache: LanguageCache, repo: str) -> None:
    """Refreshes one repository's slice from the commits added since last run.

    New commits on top of a known head are added incrementally; a first run
    or a rewritten history rebuilds the whole slice and replaces it, so a
    vanished head can never double-count.

    Args:
      profile:  Profile whose commits are fetched.
      cache:    Cache whose slice for ``repo`` is updated in place.
      repo:     ``owner/name`` repository to refresh.
    """
    key = repo_key(repo)
    previous = cache.repos.get(key)
    head = previous.head if previous else None
    commits, found = profile.fetch_commits_since(repo, head)
    incremental = found and previous is not None
    if commits:
        logger.debug(
            "%(repo)s: %(count)d new commits (%(mode)s)",
            {
                "repo": repo,
                "count": len(commits),
                "mode": "incremental" if incremental else "full rebuild",
            },
        )
    stats = previous if incremental and previous else RepoStats()
    for commit in commits:
        ingest_commit(stats, commit)
    if commits:
        # Commits arrive oldest first, so the last is the newest counted; it
        # becomes the head even on a partial fetch, leaving the un-fetched
        # newer commits for the next run.
        stats.head = commits[-1]["sha"]
    cache.repos[key] = stats


def update_language_cache(
    profile: Profile,
    cache: LanguageCache,
    repos: list[str],
    after_repo: Callable[[], None] | None = None,
) -> None:
    """Refreshes every repository's slice, calling ``after_repo`` after each.

    Args:
      profile:     Profile whose commits are fetched.
      cache:       Cache whose slices are updated in place.
      repos:       Repositories to refresh.
      after_repo:  Hook run after each repository to persist progress, so an
                   interrupted run resumes rather than restarting the backfill.
    """
    logger.info(
        "updating language cache across %(count)d repositories",
        {"count": len(repos)},
    )
    for repo in repos:
        update_repo(profile, cache, repo)
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


def language_shares(counts: dict[str, int]) -> list[tuple[str, float]]:
    """Turns additions per language into percentages, grouping the tail as Other.

    Args:
      counts:  Lines added per language.

    Returns:
      The languages and their percentage shares, largest first, with shares
      below ``MIN_SHARE`` collapsed into a trailing ``Other`` entry.
    """
    total = sum(counts.values())
    if not total:
        return []
    shares = sorted(
        ((language, 100 * count / total) for language, count in counts.items()),
        key=lambda share: share[1],
        reverse=True,
    )
    main = [(language, share) for language, share in shares if share >= MIN_SHARE]
    tail = sum(share for _, share in shares if share < MIN_SHARE)
    if tail:
        main.append((OTHER, tail))
    return main


def language_bar(shares: list[tuple[str, float]], colors: dict[str, str]) -> str:
    """Draws the shares as a rounded horizontal bar, GitHub-repo style.

    Args:
      shares:  Languages and their percentage shares, in draw order.
      colors:  Language-to-color map; unknown languages fall back to gray.

    Returns:
      The bar as a self-contained SVG document.
    """
    segments = []
    x = 0.0
    for language, share in shares:
        width = BAR_WIDTH * share / 100
        color = colors.get(language, FALLBACK_COLOR)
        segments.append(
            f'<rect x="{x:.1f}" width="{width:.1f}"'
            f' height="{BAR_HEIGHT}" fill="{color}"/>'
        )
        x += width
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg"'
        f' width="{BAR_WIDTH}" height="{BAR_HEIGHT}">'
        f'<clipPath id="round"><rect width="{BAR_WIDTH}"'
        f' height="{BAR_HEIGHT}" rx="{BAR_RADIUS}"/></clipPath>'
        f'<g clip-path="url(#round)">{"".join(segments)}</g></svg>'
    )


def language_line(shares: list[tuple[str, float]]) -> str:
    """Renders the legend: icon (where there is one), language and percent.

    Args:
      shares:  Languages and their percentage shares, in legend order.

    Returns:
      The legend line, entries separated by a middle dot.
    """
    parts = []
    for language, share in shares:
        icon = LANGUAGE_ICONS.get(language)
        prefix = f"{image(icon, alt='')} " if icon else ""
        parts.append(f"{prefix}{language} {share:.1f}%")
    return " · ".join(parts)


def language_section(label: str, path: str, shares: list[tuple[str, float]]) -> str:
    """Renders one labeled language bar and legend, empty when there is no data.

    Each bar is its own README section; the template decides where the recent
    and all-time bars sit relative to each other.

    Args:
      label:   Heading shown above the bar, such as "All time".
      path:    Source path of the rendered bar image.
      shares:  Languages and their percentage shares; empty renders nothing.

    Returns:
      The section markup, or an empty string when there are no shares.
    """
    if not shares:
        return ""
    # <picture> keeps GitHub from linking the bar image to its own source.
    return (
        f"<sub>{label}</sub>\\\n"
        f'<picture><img src="{path}" alt="{label} language distribution"></picture>\\\n'
        f"{language_line(shares)}"
    )
