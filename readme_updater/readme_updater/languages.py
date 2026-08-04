"""The all-time and last-30-days language bars."""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from readme_updater import github
from readme_updater.markup import ASSET_DIR, image

if TYPE_CHECKING:
    from collections.abc import Callable

    from readme_updater.activity import Contribution

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
COMMITS_PER_PAGE = 100
MAX_PAGES = 50  # backstop for a first-time backfill of a very long history

# The languages I write, keyed by file extension. Both bars count the lines
# I added, so a file's language is inferred from its name.
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

# Generated and vendored files aren't things I wrote, so they don't count.
# The same spirit as linguist's own vendored/generated exclusions.
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


@dataclass
class RepoStats:
    """One repository's slice of the language totals.

    ``head`` is the newest commit already counted; ``all_time`` and
    ``recent`` are that repository's additions per language, the latter
    bucketed per day for the rolling window. Storing each repository
    independently is what makes rewritten history safe: if ``head`` is
    ever gone, the whole slice is rebuilt and replaced rather than added
    to, so nothing is double-counted.
    """

    head: str
    all_time: dict[str, int]
    recent: dict[str, dict[str, int]]

    @classmethod
    def empty(cls) -> RepoStats:
        """Create an empty slice for a repository not yet counted."""
        return cls(head="", all_time={}, recent={})


@dataclass
class LanguageCache:
    """The lines I have added per language, one slice per repository.

    Keyed by a repository's opaque hash, never its possibly-private name,
    since this cache is committed publicly. Nothing here can be traced
    back to a private repository.
    """

    repos: dict[str, RepoStats]


def repo_key(repo: str) -> str:
    """Derive a stable, opaque cache key for a repository.

    The cache is committed to a public repository, so a private repo's
    name must never appear in it; a hash gives a stable key that leaks
    nothing.
    """
    return hashlib.sha256(repo.encode()).hexdigest()[:16]


def fetch_owned_repos() -> list[dict[str, Any]]:
    """List my own repositories, private ones included when the token can."""
    try:
        repos = github.fetch(
            f"{github.API}/user/repos?affiliation=owner,organization_member&per_page=100"
        )
    except urllib.error.HTTPError:
        # No user context (e.g. the workflow's installation token):
        # fall back to the public listings.
        repos = [
            repo
            for account in sorted(github.MY_ACCOUNTS)
            for repo in github.fetch(f"{github.API}/users/{account}/repos?per_page=100")
        ]
    return [
        repo
        for repo in repos
        if not repo["fork"] and repo["owner"]["login"].lower() in github.MY_ACCOUNTS
    ]


def load_colors(base: Path) -> dict[str, str]:
    """Load the vendored linguist color map."""
    return json.loads((base / COLORS_PATH).read_text())


def load_cache(base: Path) -> LanguageCache:
    """Read the language cache, or start an empty one."""
    path = base / CACHE_PATH
    if not path.exists():
        return LanguageCache(repos={})
    data = json.loads(path.read_text())
    return LanguageCache(
        repos={
            key: RepoStats(
                head=slice_["head"],
                all_time=slice_["all_time"],
                recent=slice_["recent"],
            )
            for key, slice_ in data.items()
        }
    )


def save_cache(base: Path, cache: LanguageCache) -> None:
    """Write the language cache back to disk."""
    data = {
        key: {"head": stats.head, "all_time": stats.all_time, "recent": stats.recent}
        for key, stats in sorted(cache.repos.items())
    }
    (base / CACHE_PATH).write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")


def is_countable(path: str) -> bool:
    """Whether a file counts as code I wrote (not generated or vendored)."""
    if path.rsplit("/", 1)[-1] in EXCLUDED_NAMES:
        return False
    low = path.lower()
    return not any(part in low for part in EXCLUDED_PATH_PARTS)


def commit_additions(commit: dict[str, Any]) -> dict[str, int]:
    """Sum the lines a commit added per language, from its file list."""
    counts: dict[str, int] = {}
    for file in commit.get("files", []):
        path = file["filename"]
        language = EXTENSION_LANGUAGES.get(Path(path).suffix.lower())
        if language and is_countable(path):
            counts[language] = counts.get(language, 0) + file.get("additions", 0)
    return counts


def contributed_repos(
    owned: list[dict[str, Any]], contributions: list[Contribution]
) -> list[str]:
    """Every repository I commit to: my own plus the ones I contribute to."""
    repos = {repo["full_name"] for repo in owned}
    repos.update(contribution.repo for contribution in contributions)
    repos.discard(github.PROFILE_REPO)
    return sorted(repos)


def fetch_new_commits(
    repo: str, last_sha: str | None
) -> tuple[list[dict[str, Any]], bool]:
    """My commits in ``repo`` newer than ``last_sha``, plus whether it was found.

    Uses the list-commits API rather than search, so it reaches private
    repositories and stays cheap on huge ones: only my own commits come
    back, regardless of how large the repository is. Returns the commits
    newest first and a flag that is true when ``last_sha`` was reached; a
    false flag on a non-empty history means the marker is gone (rewritten
    history) and the caller must rebuild the repository from scratch.
    """
    commits: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        params = urllib.parse.urlencode(
            {"author": github.USER, "per_page": COMMITS_PER_PAGE, "page": page}
        )
        try:
            batch = github.fetch(f"{github.API}/repos/{repo}/commits?{params}")
        except urllib.error.HTTPError:
            break  # empty repository or no access
        for item in batch:
            if item["sha"] == last_sha:
                return commits, True
            commits.append(item)
        if len(batch) < COMMITS_PER_PAGE:
            break
    return commits, False


def ingest_commit(stats: RepoStats, commit: dict[str, Any]) -> None:
    """Fold one commit's additions into a repository's totals and buckets."""
    day = commit["commit"]["committer"]["date"][:10]
    bucket = stats.recent.setdefault(day, {})
    for language, additions in commit_additions(commit).items():
        stats.all_time[language] = stats.all_time.get(language, 0) + additions
        bucket[language] = bucket.get(language, 0) + additions


def update_repo(cache: LanguageCache, repo: str) -> None:
    """Refresh one repository's slice from the commits added since last run.

    New commits on top of a known head are added incrementally; a first
    run or a rewritten history rebuilds the whole slice and replaces it,
    so a vanished head SHA can never double-count.
    """
    key = repo_key(repo)
    previous = cache.repos.get(key)
    commits, found = fetch_new_commits(repo, previous.head if previous else None)
    incremental = found and previous is not None
    stats = previous if incremental and previous else RepoStats.empty()
    for item in commits:
        ingest_commit(stats, github.fetch(item["url"]))
    if commits:
        stats.head = commits[0]["sha"]
    cache.repos[key] = stats


def update_language_cache(
    cache: LanguageCache, repos: list[str], after_repo: Callable[[], None] | None = None
) -> None:
    """Refresh every repository's slice, calling ``after_repo`` for each.

    ``after_repo`` is a hook to persist progress, so an interrupted run
    resumes where it left off rather than starting the backfill over.
    """
    for repo in repos:
        update_repo(cache, repo)
        if after_repo is not None:
            after_repo()


def prune_recent(cache: LanguageCache, cutoff: date) -> None:
    """Drop day buckets older than the cutoff to keep the cache small."""
    for stats in cache.repos.values():
        stats.recent = {
            day: bucket
            for day, bucket in stats.recent.items()
            if date.fromisoformat(day) >= cutoff
        }


def total_counts(cache: LanguageCache) -> dict[str, int]:
    """Merge every repository's all-time additions per language."""
    counts: dict[str, int] = {}
    for stats in cache.repos.values():
        for language, additions in stats.all_time.items():
            counts[language] = counts.get(language, 0) + additions
    return counts


def recent_counts(cache: LanguageCache, cutoff: date) -> dict[str, int]:
    """Merge the additions per language from all buckets on or after cutoff."""
    counts: dict[str, int] = {}
    for stats in cache.repos.values():
        for day, bucket in stats.recent.items():
            if date.fromisoformat(day) >= cutoff:
                for language, additions in bucket.items():
                    counts[language] = counts.get(language, 0) + additions
    return counts


def language_shares(counts: dict[str, int]) -> list[tuple[str, float]]:
    """Turn additions per language into percentages, grouping the tail as Other."""
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
    """Draw the shares as a rounded horizontal bar, GitHub-repo style."""
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
    """Render the legend: icon (where there is one), language and percent."""
    parts = []
    for language, share in shares:
        icon = LANGUAGE_ICONS.get(language)
        prefix = f"{image(icon, alt='')} " if icon else ""
        parts.append(f"{prefix}{language} {share:.1f}%")
    return " · ".join(parts)


def _labeled_bar(label: str, path: str, shares: list[tuple[str, float]]) -> str:
    # <picture> keeps GitHub from linking the bar image to its own source.
    return (
        f"<sub>{label}</sub>\\\n"
        f'<picture><img src="{path}" alt="{label} language distribution"></picture>\\\n'
        f"{language_line(shares)}"
    )


def render_languages(
    total: list[tuple[str, float]], recent: list[tuple[str, float]]
) -> str:
    """Render the language block: an all-time bar and a recent-work bar."""
    blocks = []
    if total:
        blocks.append(_labeled_bar("All time", TOTAL_BAR_PATH, total))
    if recent:
        blocks.append(_labeled_bar(f"Last {RECENT_DAYS} days", RECENT_BAR_PATH, recent))
    return "\n\n".join(blocks)
