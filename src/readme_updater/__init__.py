"""Keep the dynamic section of my profile README fresh.

Queries the GitHub search API for my most recent public contributions
(pull requests, issues and commits), picks the most recent one for each
of the repositories I contributed to last, both my own and other
people's, and rewrites the marker-delimited block in the README.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Callable

USER = "whme"
MY_ACCOUNTS = frozenset({"whme", "whmade"})
PROFILE_REPO = "whme/whme"
REPOS_PER_GROUP = 2
API = "https://api.github.com"

Kind = Literal["pr", "issue", "commit"]

ICONS: dict[Kind, str] = {
    "pr": "assets/git-pull-request.svg",
    "issue": "assets/issue-opened.svg",
    "commit": "assets/git-commit.svg",
}


@dataclass(frozen=True)
class RepoTotals:
    """How much I contributed to one repository, in total."""

    commits: int
    pull_requests: int
    issues: int


@dataclass(frozen=True)
class Contribution:
    """A single public contribution: a pull request, an issue or a commit."""

    repo: str
    title: str
    url: str
    date: str
    kind: Kind

    @property
    def owned(self) -> bool:
        """Whether the contribution went to a repository I own."""
        return self.repo.partition("/")[0].lower() in MY_ACCOUNTS

    @property
    def timestamp(self) -> datetime:
        """The contribution date, parsed for sorting across timezones."""
        return datetime.fromisoformat(self.date)


REQUEST_TIMEOUT = 30
RETRIES = 3
RETRY_BACKOFF = 3


def _fetch(url: str) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{USER}-readme-updater",
    }
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)  # noqa: S310 (always https, see API)
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(  # noqa: S310
                request, timeout=REQUEST_TIMEOUT
            ) as response:
                return json.load(response)
        except urllib.error.HTTPError:
            raise  # a real HTTP status (404, 422, …); callers handle these
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            # A transient network problem: back off and try again, so a
            # dropped connection can't hang or abort a long backfill.
            if attempt == RETRIES - 1:
                raise
            time.sleep(RETRY_BACKOFF * (attempt + 1))
    raise RuntimeError("unreachable")


def public_query(qualifiers: str = "") -> str:
    """Build a search query for my contributions in public repositories only.

    The README must never leak private activity, no matter how much the
    token running the script is allowed to see.
    """
    return " ".join(filter(None, [f"author:{USER}", "is:public", qualifiers]))


def public_commits(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop commits from private repositories, whatever the search returned."""
    return [item for item in items if not item["repository"].get("private")]


def _search(
    endpoint: str, sort: str, qualifiers: str = "", per_page: int = 50
) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode(
        {
            "q": public_query(qualifiers),
            "sort": sort,
            "order": "desc",
            "per_page": per_page,
        }
    )
    return list(_fetch(f"{API}/search/{endpoint}?{params}")["items"])


def _count(endpoint: str, qualifiers: str) -> int:
    params = urllib.parse.urlencode({"q": public_query(qualifiers), "per_page": 1})
    return int(_fetch(f"{API}/search/{endpoint}?{params}")["total_count"])


def fetch_totals(repo: str) -> RepoTotals:
    """Count all my commits, pull requests and issues in one repository."""
    return RepoTotals(
        commits=_count("commits", f"repo:{repo}"),
        pull_requests=_count("issues", f"type:pr repo:{repo}"),
        issues=_count("issues", f"type:issue repo:{repo}"),
    )


def issue_contribution(item: dict[str, Any]) -> Contribution:
    """Map a pull request or issue from the issue search API to a contribution."""
    return Contribution(
        repo=item["repository_url"].removeprefix(f"{API}/repos/"),
        title=item["title"],
        url=item["html_url"],
        date=item["created_at"],
        kind="pr" if "pull_request" in item else "issue",
    )


def commit_contribution(item: dict[str, Any]) -> Contribution:
    """Map a commit from the commit search API to a contribution.

    Uses the committer date, not the author date: it is what the GitHub UI
    shows, and commits landing through a review pipeline are committed well
    after they are authored.
    """
    return Contribution(
        repo=item["repository"]["full_name"],
        title=item["commit"]["message"].splitlines()[0],
        url=item["html_url"],
        date=item["commit"]["committer"]["date"],
        kind="commit",
    )


def fetch_contributions() -> list[Contribution]:
    """Collect my recent public contributions from the GitHub search API."""
    # My own repos flood the plain author query, so foreign repos
    # additionally get a query of their own.
    foreign = " ".join(f"-user:{account}" for account in sorted(MY_ACCOUNTS))
    issues = _search("issues", sort="created") + _search(
        "issues", sort="created", qualifiers=foreign
    )
    commits = public_commits(_search("commits", sort="committer-date"))
    return [issue_contribution(item) for item in issues] + [
        commit_contribution(item) for item in commits
    ]


def select_highlights(
    contributions: list[Contribution], per_group: int = REPOS_PER_GROUP
) -> list[Contribution]:
    """Pick the most recent contribution to each of the last distinct repositories.

    Picks up to ``per_group`` repositories I don't own and the same number
    of repositories I do, listing the ones I don't own first.
    """
    seen: set[str] = set()
    groups: dict[bool, list[Contribution]] = {False: [], True: []}
    for contribution in sorted(
        contributions, key=lambda contribution: contribution.timestamp, reverse=True
    ):
        if contribution.repo in seen or contribution.repo == PROFILE_REPO:
            continue
        group = groups[contribution.owned]
        if len(group) < per_group:
            seen.add(contribution.repo)
            group.append(contribution)
    return groups[False] + groups[True]


def _escape(text: str) -> str:
    return text.replace("[", "\\[").replace("]", "\\]")


def _image(src: str, alt: str) -> str:
    return f'<img src="{src}" width="16" height="16" alt="{alt}">'


STAMP_FORMAT = "%Y-%m-%d %H:%M UTC"
STAMP_WIDTH = len("2026-08-01 09:01 UTC")
TOTAL_ICON = "assets/mark-github.svg"
# Longer contribution titles wrap into the next line and break the log's
# column layout, so they get truncated with an ellipsis.
TITLE_LIMIT = 72


def _shorten(title: str) -> str:
    if len(title) <= TITLE_LIMIT:
        return title
    on_word_boundary = textwrap.shorten(title, width=TITLE_LIMIT, placeholder="…")
    if len(on_word_boundary) >= TITLE_LIMIT // 2:
        return on_word_boundary
    # A long unbroken token (function_names_like_this) would leave little or
    # nothing to a word-boundary cut, so cut mid-token instead.
    return title[: TITLE_LIMIT - 1].rstrip() + "…"


WEEKS_PER_MONTH = 5  # beyond this many calendar weeks, count in months
MONTHS_PER_YEAR = 12


def relative_label(timestamp: datetime, now: datetime) -> str:
    """Name the age of a contribution the way the GitHub UI would."""
    date, today = timestamp.astimezone(UTC).date(), now.astimezone(UTC).date()
    monday = today - timedelta(days=today.weekday())
    weeks = (monday - (date - timedelta(days=date.weekday()))).days // 7
    months = (today.year - date.year) * MONTHS_PER_YEAR + today.month - date.month
    years = months // MONTHS_PER_YEAR
    ladder = [
        (date >= today, "today"),
        ((today - date).days == 1, "yesterday"),
        (weeks == 0, "this week"),
        (weeks == 1, "last week"),
        (weeks < WEEKS_PER_MONTH, f"{weeks} weeks ago"),
        (months == 1, "last month"),
        (months < MONTHS_PER_YEAR, f"{months} months ago"),
        (years == 1, "last year"),
    ]
    return next((label for applies, label in ladder if applies), f"{years} years ago")


def _pad(count: int) -> str:
    return f"<samp>{'&nbsp;' * count}</samp>" if count > 0 else ""


def _totals_cell(repo: str, totals: RepoTotals) -> str:
    """List my total contributions to the repository, each count linked."""
    counted = {
        "commit": (totals.commits, f"https://github.com/{repo}/commits?author={USER}"),
        "pull request": (
            totals.pull_requests,
            f"https://github.com/{repo}/pulls?q=is%3Apr+author%3A{USER}",
        ),
        "issue": (
            totals.issues,
            f"https://github.com/{repo}/issues?q=is%3Aissue+author%3A{USER}",
        ),
    }
    parts = [
        f"[{count} {noun}{'s' if count != 1 else ''}]({url})"
        for noun, (count, url) in counted.items()
        if count
    ]
    return " · ".join(parts)


def render(
    highlights: list[Contribution],
    totals: dict[str, RepoTotals] | None = None,
    now: datetime | None = None,
) -> str:
    """Render the highlights as a log: newest first, one entry per repository.

    Each entry is two lines sharing the same column layout, so they align
    by construction: a timestamp slot, the repository (avatar and name),
    an icon and the content. The first line carries the timestamp and the
    contribution; the second the age of the contribution in parentheses
    and my total contributions to that repository behind the octocat.
    Both slots are ``<code>`` elements padded to the same character count,
    which is what makes the columns line up: the pill's own horizontal
    padding cannot be replicated with whitespace. Entries are separated
    as paragraphs so they don't crowd each other.
    """
    totals = totals or {}
    ordered = sorted(
        highlights, key=lambda contribution: contribution.timestamp, reverse=True
    )
    if not ordered:
        return ""
    width = max(len(contribution.repo) for contribution in ordered)
    entries = []
    for contribution in ordered:
        owner = contribution.repo.partition("/")[0]
        avatar = _image(f"https://github.com/{owner}.png?size=32", alt="")
        icon = _image(ICONS[contribution.kind], alt=contribution.kind)
        stamp = contribution.timestamp.astimezone(UTC).strftime(STAMP_FORMAT)
        repo_url = f"https://github.com/{contribution.repo}"
        repo_cell = (
            f'{avatar} <a href="{repo_url}"><code>{contribution.repo}</code></a>'
            f"{_pad(width - len(contribution.repo))}"
        )
        lines = [
            (
                f"<code>{stamp}</code>&emsp;{repo_cell} "
                f"{icon} [{_escape(_shorten(contribution.title))}]({contribution.url})"
            )
        ]
        if contribution.repo in totals:
            if now:
                label = f"({relative_label(contribution.timestamp, now)})"
                slot = f"<code>{label}</code>{_pad(STAMP_WIDTH - len(label))}"
            else:
                slot = _pad(STAMP_WIDTH)
            octocat = _image(TOTAL_ICON, alt="total")
            counts = _totals_cell(contribution.repo, totals[contribution.repo])
            lines.append(f"{slot}&emsp;{repo_cell} {octocat} <sub>{counts}</sub>")
        entries.append("\\\n".join(lines))
    return "\n\n".join(entries)


# The vendored linguist color map (see assets/README.md); unknown
# languages fall back to gray, like GitHub renders them.
COLORS_PATH = "assets/language-colors.json"
FALLBACK_COLOR = "#ededed"
LANGUAGE_ICONS = {
    "Python": "assets/python.svg",
    "TypeScript": "assets/typescript.svg",
    "Rust": "assets/rust.svg",
}
OTHER = "Other"
MIN_SHARE = 1.0  # smaller languages are grouped, like GitHub's own bar
# Wider than any real markdown container, so max-width:100% always clamps
# the bar to exactly the available width, flush with the legend beneath.
BAR_WIDTH, BAR_HEIGHT, BAR_RADIUS = 1200, 14, 7
TOTAL_BAR_PATH = "assets/languages.svg"
RECENT_BAR_PATH = "assets/languages-recent.svg"
RECENT_DAYS = 30
RECENT_KEEP_DAYS = RECENT_DAYS + 5  # a little slack before pruning old buckets
CACHE_PATH = "assets/languages-cache.json"
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
        repos = _fetch(
            f"{API}/user/repos?affiliation=owner,organization_member&per_page=100"
        )
    except urllib.error.HTTPError:
        # No user context (e.g. the workflow's installation token):
        # fall back to the public listings.
        repos = [
            repo
            for account in sorted(MY_ACCOUNTS)
            for repo in _fetch(f"{API}/users/{account}/repos?per_page=100")
        ]
    return [
        repo
        for repo in repos
        if not repo["fork"] and repo["owner"]["login"].lower() in MY_ACCOUNTS
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
    repos.discard(PROFILE_REPO)
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
            {"author": USER, "per_page": COMMITS_PER_PAGE, "page": page}
        )
        try:
            batch = _fetch(f"{API}/repos/{repo}/commits?{params}")
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
        ingest_commit(stats, _fetch(item["url"]))
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
    """Turn byte counts into percentages, grouping the tail as Other."""
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
        prefix = f"{_image(icon, alt='')} " if icon else ""
        parts.append(f"{prefix}{language} {share:.1f}%")
    return " · ".join(parts)


def _labeled_bar(label: str, path: str, shares: list[tuple[str, float]]) -> str:
    return (
        f"<sub>{label}</sub>\\\n"
        f'<img src="{path}" alt="{label} language distribution">\\\n'
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


def replace_block(content: str, marker: str, replacement: str) -> str:
    """Replace the block between the start and end comments of ``marker``."""
    pattern = re.compile(
        rf"<!-- {marker}:start -->\n.*?<!-- {marker}:end -->", re.DOTALL
    )
    if not pattern.search(content):
        raise ValueError(f"marker {marker!r} not found in README")
    block = f"<!-- {marker}:start -->\n{replacement}\n<!-- {marker}:end -->"
    return pattern.sub(lambda _: block, content)


def update_languages(base: Path) -> tuple[list[tuple[str, float]], ...]:
    """Refresh the cache from new commits and redraw both language bars."""
    colors = load_colors(base)
    cache = load_cache(base)
    contributions = fetch_contributions()
    repos = contributed_repos(fetch_owned_repos(), contributions)
    update_language_cache(cache, repos, after_repo=lambda: save_cache(base, cache))
    today = datetime.now(UTC).date()
    prune_recent(cache, today - timedelta(days=RECENT_KEEP_DAYS))
    save_cache(base, cache)
    total_shares = language_shares(total_counts(cache))
    recent_shares = language_shares(
        recent_counts(cache, today - timedelta(days=RECENT_DAYS))
    )
    (base / TOTAL_BAR_PATH).write_text(language_bar(total_shares, colors))
    if recent_shares:
        (base / RECENT_BAR_PATH).write_text(language_bar(recent_shares, colors))
    return total_shares, recent_shares


def main(argv: list[str] | None = None) -> None:
    """Rewrite the dynamic sections of the README with fresh data."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("readme", nargs="?", type=Path, default=Path("README.md"))
    args = parser.parse_args(argv)
    highlights = select_highlights(fetch_contributions())
    totals = {
        contribution.repo: fetch_totals(contribution.repo)
        for contribution in highlights
    }
    total_shares, recent_shares = update_languages(args.readme.parent)
    content = replace_block(
        args.readme.read_text(),
        "activity",
        render(highlights, totals, now=datetime.now(UTC)),
    )
    content = replace_block(
        content, "languages", render_languages(total_shares, recent_shares)
    )
    args.readme.write_text(content)
