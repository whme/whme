"""Keep the dynamic section of my profile README fresh.

Queries the GitHub search API for my most recent public contributions
(pull requests, issues and commits), picks the most recent one for each
of the repositories I contributed to last, both my own and other
people's, and rewrites the marker-delimited block in the README.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

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


def _fetch(url: str) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{USER}-readme-updater",
    }
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)  # noqa: S310 (always https, see API)
    with urllib.request.urlopen(request) as response:  # noqa: S310
        return json.load(response)


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

# File extensions of the languages I actually touch, for the recent bar:
# the languages API only knows whole repositories, so recent work is
# measured from the lines I changed per file instead.
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


def fetch_owned_repos() -> list[dict[str, Any]]:
    """List my own repositories, private ones included when the token can.

    The repository names never end up in the README; only the aggregated
    language percentages do.
    """
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


def fetch_language_bytes(repos: list[dict[str, Any]]) -> dict[str, int]:
    """Sum the bytes written per language across the given repositories."""
    counts: dict[str, int] = {}
    for repo in repos:
        for language, count in _fetch(
            f"{API}/repos/{repo['full_name']}/languages"
        ).items():
            counts[language] = counts.get(language, 0) + count
    return counts


def fetch_recent_language_lines(since: datetime) -> dict[str, int]:
    """Sum the lines I changed per language over recent public commits.

    The languages API only knows whole repositories, so recent work is
    measured from the files my commits touched since ``since``. Public
    commits only, behind the same ``is:public`` guard as everything else.
    """
    day = since.astimezone(UTC).date().isoformat()
    commits = _search(
        "commits",
        sort="committer-date",
        qualifiers=f"committer-date:>={day}",
        per_page=100,
    )
    counts: dict[str, int] = {}
    for item in public_commits(commits):
        for language, changes in lines_by_language(_fetch(item["url"])).items():
            counts[language] = counts.get(language, 0) + changes
    return counts


def lines_by_language(commit: dict[str, Any]) -> dict[str, int]:
    """Sum a commit's changed lines per language, keyed by file extension."""
    counts: dict[str, int] = {}
    for file in commit.get("files", []):
        language = EXTENSION_LANGUAGES.get(Path(file["filename"]).suffix.lower())
        if language:
            counts[language] = counts.get(language, 0) + file.get("changes", 0)
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


def main(argv: list[str] | None = None) -> None:
    """Rewrite the activity section of the README with fresh data."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("readme", nargs="?", type=Path, default=Path("README.md"))
    args = parser.parse_args(argv)
    highlights = select_highlights(fetch_contributions())
    totals = {
        contribution.repo: fetch_totals(contribution.repo)
        for contribution in highlights
    }
    base = args.readme.parent
    colors = load_colors(base)
    total_shares = language_shares(fetch_language_bytes(fetch_owned_repos()))
    since = datetime.now(UTC) - timedelta(days=RECENT_DAYS)
    recent_shares = language_shares(fetch_recent_language_lines(since))
    (base / TOTAL_BAR_PATH).write_text(language_bar(total_shares, colors))
    if recent_shares:
        (base / RECENT_BAR_PATH).write_text(language_bar(recent_shares, colors))
    content = replace_block(
        args.readme.read_text(),
        "activity",
        render(highlights, totals, now=datetime.now(UTC)),
    )
    content = replace_block(
        content, "languages", render_languages(total_shares, recent_shares)
    )
    args.readme.write_text(content)
