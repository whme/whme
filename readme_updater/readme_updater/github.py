"""Talking to the GitHub API — the one place that makes network calls.

Every method that reaches the network is named ``fetch_*`` (or ``_fetch_*``);
the ``_build_*``, ``_parse_*`` and ``_is_*`` helpers are pure. All requests go
through :class:`Profile`, which turns the parsed JSON into typed
:class:`Contribution` and :class:`RepoTotals` values at this boundary and keeps
the number of distinct API calls to a minimum.
"""

from __future__ import annotations

import json
import logging
import subprocess
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

import urllib3

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)

API = "https://api.github.com"
SEARCH_LIMIT = 50
# Page size for the list endpoints; GitHub caps it at 100. Every page is
# enumerated until exhausted, so the results stay complete at any repo size.
PER_PAGE = 100

REQUEST_TIMEOUT = 30
RETRIES = 3
RETRY_BACKOFF = 2
# Transient statuses worth retrying; a dropped connection is retried too.
RETRY_STATUSES = (429, 500, 502, 503, 504)

_http = urllib3.PoolManager(
    timeout=REQUEST_TIMEOUT,
    retries=urllib3.Retry(
        total=RETRIES,
        backoff_factor=RETRY_BACKOFF,
        status_forcelist=RETRY_STATUSES,
    ),
)

# A contribution's state, named to match the octicon GitHub draws for it.
Status = Literal[
    "commit",
    "pr_open",
    "pr_draft",
    "pr_merged",
    "pr_closed",
    "issue_open",
    "issue_closed",
    "issue_not_planned",
]


def gh_auth_token() -> str | None:
    """Returns the token the ``gh`` CLI is authenticated with, or ``None``.

    Runs ``gh auth token``, the very credential the GitHub CLI itself uses, so
    a developer already logged in with ``gh`` needs no separate token. Any
    failure — ``gh`` not installed or not logged in — yields ``None``.

    Returns:
      The token the ``gh`` CLI is logged in with, or ``None`` when it cannot
      supply one.
    """
    try:
        completed = subprocess.run(
            ("gh", "auth", "token"),  # noqa: S607 - gh is a dev tool resolved from PATH
            capture_output=True,
            text=True,
            check=True,
        )
    except OSError, subprocess.SubprocessError:
        return None
    return completed.stdout.strip() or None


class GitHubError(Exception):
    """A GitHub API request came back with an error status."""


@dataclass(frozen=True)
class RepoTotals:
    """Counts of the contributions to one repository."""

    commits: int
    pull_requests: int
    issues: int


@dataclass(frozen=True)
class Contribution:
    """A single public contribution: a pull request, an issue or a commit.

    ``status`` names its exact state so the activity log can draw the matching
    octicon.
    """

    repo: str
    title: str
    url: str
    date: datetime
    status: Status
    owned: bool


def _issue_status(item: dict[str, Any]) -> Status:
    """Names the state of one issues-search item.

    A merged pull request is also reported ``closed``, so merged is checked
    before closed; a reopened issue is reported ``open`` and needs no case of
    its own. Every field is already on the search item, so no extra request.

    Args:
      item:  A single result from the issues search endpoint.

    Returns:
      The matching :data:`Status`.
    """
    if "pull_request" in item:
        if item["pull_request"].get("merged_at"):
            return "pr_merged"
        if item.get("state") == "closed":
            return "pr_closed"
        return "pr_draft" if item.get("draft") else "pr_open"
    if item.get("state") == "closed":
        if item.get("state_reason") == "not_planned":
            return "issue_not_planned"
        return "issue_closed"
    return "issue_open"


@dataclass(frozen=True)
class Profile:
    """A GitHub identity whose public contributions the README reflects."""

    username: str
    owned_usernames: frozenset[str]
    api_url: str = API
    token: str | None = None

    @property
    def profile_repo(self) -> str:
        """The ``username/username`` repository that hosts the profile README."""
        return f"{self.username}/{self.username}"

    def fetch_recent_contributions(self) -> list[Contribution]:
        """Fetches the recent public contributions across issues, PRs and commits.

        Three GitHub searches back this, the fewest the feature allows: issues
        and commits are distinct search endpoints that cannot be combined, so
        one unrestricted search of each surfaces the owned repositories, which
        dominate any search by volume. A second issues search excludes the
        owned accounts so their volume cannot crowd external repositories out
        of the recent window. Pull requests and issues arrive from the issues
        endpoint and are told apart in Python by the ``pull_request`` key.

        Returns:
          The public contributions, with private commits and the profile
          repository removed.
        """
        external = " ".join(
            f"-user:{account}" for account in sorted(self.owned_usernames)
        )
        issues = self._fetch_search("issues", "created") + self._fetch_search(
            "issues", "created", external
        )
        commits = self._fetch_search("commits", "committer-date")
        contributions = [
            *(self._parse_issue(item) for item in issues),
            *(
                self._parse_commit(item)
                for item in commits
                if not item["repository"].get("private")
            ),
        ]
        contributions = [
            contribution
            for contribution in contributions
            if contribution.repo != self.profile_repo
        ]
        logger.info(
            "fetched %(total)d contributions (%(issues)d issue/PR, %(commits)d commit)",
            {
                "total": len(contributions),
                "issues": len(issues),
                "commits": len(commits),
            },
        )
        return contributions

    def fetch_totals(self, repo: str) -> RepoTotals:
        """Fetches the commit, pull request and issue counts for one repository.

        Args:
          repo:  ``owner/name`` repository whose totals are counted.

        Returns:
          The user's commit, pull request and issue counts in the repository.
        """
        return RepoTotals(
            commits=self._fetch_count("commits", f"repo:{repo}"),
            pull_requests=self._fetch_count("issues", f"type:pr repo:{repo}"),
            issues=self._fetch_count("issues", f"type:issue repo:{repo}"),
        )

    def fetch_owned_repos(self) -> list[str]:
        """Fetches every repository held by the owned accounts, forks included.

        Prefers the authenticated ``/user/repos`` listing, which reaches
        private repositories too, and falls back to each account's public
        listing without a user context. Forks are kept: work done on a fork
        still counts towards the statistics.

        Returns:
          The ``owner/name`` repositories owned by the tracked accounts.
        """
        try:
            listing = list(
                self._fetch_pages(
                    f"{self.api_url}/user/repos?affiliation=owner,organization_member"
                )
            )
        except GitHubError:
            logger.warning("no user context for /user/repos; using public listings")
            listing = [
                repo
                for account in sorted(self.owned_usernames)
                for repo in self._fetch_pages(f"{self.api_url}/users/{account}/repos")
            ]
        return [
            repo["full_name"] for repo in listing if self._is_owned(repo["full_name"])
        ]

    def fetch_commits_since(
        self, repo: str, head: str | None
    ) -> tuple[Iterator[dict[str, Any]], bool]:
        """Streams the author's commits in a repository newer than a known head.

        Uses the list-commits API rather than search, so it reaches private
        repositories and stays cheap on huge ones: only the author's commits
        come back regardless of the repository's size. The listing is walked
        eagerly up to ``head`` (or the whole history on the first run); each
        commit's full detail is then fetched **lazily**, oldest first, as the
        returned iterator is consumed, so the caller can persist progress part
        way through a large repository.

        Args:
          repo:  ``owner/name`` repository whose commits are listed.
          head:  Newest commit already counted, or ``None`` on a first run.

        Returns:
          A lazy oldest-first iterator of detailed commits newer than ``head``,
          and a flag that is true when ``head`` was reached. A false flag on a
          non-empty history means the head is gone (rewritten history) and the
          caller must rebuild the repository from scratch.
        """
        refs, found = self._list_new_commit_refs(repo, head)
        if refs:
            logger.info(
                "%(repo)s: fetching %(count)d commit details",
                {"repo": repo, "count": len(refs)},
            )
        return self._stream_commit_details(repo, refs), found

    def _list_new_commit_refs(
        self, repo: str, head: str | None
    ) -> tuple[list[dict[str, Any]], bool]:
        """Lists the author's commit refs newer than ``head``, newest first.

        Args:
          repo:  ``owner/name`` repository whose commits are listed.
          head:  Newest commit already counted, or ``None`` on a first run.

        Returns:
          The commit refs newer than ``head`` (newest first) and a flag that is
          true when ``head`` was reached before the listing ran out.
        """
        refs: list[dict[str, Any]] = []
        found = False
        try:
            for item in self._fetch_pages(
                f"{self.api_url}/repos/{repo}/commits?author={self.username}"
            ):
                if item["sha"] == head:
                    found = True
                    break
                refs.append(item)
        except GitHubError:
            logger.debug("no accessible commits for %(repo)s", {"repo": repo})
        return refs, found

    def _stream_commit_details(
        self, repo: str, refs: list[dict[str, Any]]
    ) -> Iterator[dict[str, Any]]:
        """Yields each commit's full detail oldest first, stopping on failure.

        Fetching oldest first means a fetch that fails part way — a rate limit,
        a transient error — still yields a contiguous run of the oldest
        commits, so the caller advances the stored head to the newest one it
        received and the next run resumes from there. The failure is logged at
        ERROR, not swallowed, because a repository that trips it every run
        never advances and that is worth surfacing loudly.

        Args:
          repo:  ``owner/name`` repository the refs belong to.
          refs:  Commit refs to fetch in full, newest first as listed.

        Yields:
          Each commit's detailed payload, oldest first, up to the first failure.
        """
        total = len(refs)
        for done, ref in enumerate(reversed(refs)):
            try:
                yield self._fetch_json(ref["url"])
            except GitHubError:
                logger.error(  # noqa: TRY400 - a stalled backfill is worth its own ERROR
                    "stopped fetching %(repo)s at %(sha)s after %(done)d of "
                    "%(total)d commits; resuming next run",
                    {"repo": repo, "sha": ref["sha"], "done": done, "total": total},
                )
                return

    def _fetch_pages(self, url: str) -> Iterator[dict[str, Any]]:
        """Yields every item across all pages of a paginated list endpoint.

        Enumerates pages of ``PER_PAGE`` items until one comes back short, so
        the listing is complete however large it is. Items are yielded lazily,
        so a caller that stops early spares the remaining requests.

        Args:
          url:  List endpoint URL, with its own query parameters if any.

        Yields:
          Each item in turn, page by page, until a short page ends the listing.
        """
        separator = "&" if "?" in url else "?"
        page = 1
        while True:
            batch = self._fetch_json(f"{url}{separator}per_page={PER_PAGE}&page={page}")
            yield from batch
            if len(batch) < PER_PAGE:
                return
            page += 1

    def _build_query(self, qualifiers: str = "") -> str:
        """Builds a search query pinned to the user's public activity.

        Restricting every search to public activity keeps anything the running
        token may privately see from leaking into the README.

        Args:
          qualifiers:  Extra search qualifiers appended to the query.

        Returns:
          The GitHub search query string.
        """
        return " ".join(
            filter(None, [f"author:{self.username}", "is:public", qualifiers])
        )

    def _fetch_search(
        self, endpoint: str, sort: str, qualifiers: str = ""
    ) -> list[dict[str, Any]]:
        """Fetches one page of results from a GitHub search endpoint.

        Args:
          endpoint:    Search endpoint, ``issues`` or ``commits``.
          sort:        Field the results are sorted by, newest first.
          qualifiers:  Extra search qualifiers narrowing the query.

        Returns:
          The result items, at most ``SEARCH_LIMIT`` of them.
        """
        params = urllib.parse.urlencode(
            {
                "q": self._build_query(qualifiers),
                "sort": sort,
                "order": "desc",
                "per_page": SEARCH_LIMIT,
            }
        )
        url = f"{self.api_url}/search/{endpoint}?{params}"
        return list(self._fetch_json(url)["items"])

    def _fetch_count(self, endpoint: str, qualifiers: str) -> int:
        """Fetches the total number of search results matching a query.

        Args:
          endpoint:    Search endpoint, ``issues`` or ``commits``.
          qualifiers:  Extra search qualifiers narrowing the query.

        Returns:
          The total match count the endpoint reports.
        """
        params = urllib.parse.urlencode(
            {"q": self._build_query(qualifiers), "per_page": 1}
        )
        url = f"{self.api_url}/search/{endpoint}?{params}"
        return int(self._fetch_json(url)["total_count"])

    def _parse_issue(self, item: dict[str, Any]) -> Contribution:
        """Parses one issues-search item into a contribution.

        Args:
          item:  A single result from the issues search endpoint.

        Returns:
          The contribution, a pull request when the item carries a
          ``pull_request`` key and an issue otherwise.
        """
        repo = item["repository_url"].removeprefix(f"{self.api_url}/repos/")
        return Contribution(
            repo=repo,
            title=item["title"],
            url=item["html_url"],
            date=datetime.fromisoformat(item["created_at"]),
            status=_issue_status(item),
            owned=self._is_owned(repo),
        )

    def _parse_commit(self, item: dict[str, Any]) -> Contribution:
        """Parses one commits-search item into a contribution.

        The committer date is used, not the author date: it is what the GitHub
        UI shows, and reviewed commits land well after they are authored.

        Args:
          item:  A single result from the commits search endpoint.

        Returns:
          The commit as a contribution.
        """
        repo = item["repository"]["full_name"]
        return Contribution(
            repo=repo,
            title=item["commit"]["message"].splitlines()[0],
            url=item["html_url"],
            date=datetime.fromisoformat(item["commit"]["committer"]["date"]),
            status="commit",
            owned=self._is_owned(repo),
        )

    def _is_owned(self, repo: str) -> bool:
        return repo.partition("/")[0].lower() in self.owned_usernames

    def _fetch_json(self, url: str) -> Any:
        """Fetches and parses the JSON body of a GitHub API URL.

        Sends the GitHub JSON accept header and, when the profile carries a
        token, a bearer token.

        Args:
          url:  Absolute GitHub API URL to GET.

        Returns:
          The parsed JSON response body.

        Raises:
          GitHubError:  If the API responds with a client or server error.
        """
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": f"{self.username}-readme-updater",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        logger.debug("GET %(url)s", {"url": url})
        response = _http.request("GET", url, headers=headers)
        if response.status >= 400:  # noqa: PLR2004 - HTTP client-error threshold
            logger.warning(
                "GitHub returned %(status)s for %(url)s",
                {"status": response.status, "url": url},
            )
            raise GitHubError(f"GitHub returned {response.status} for {url}")
        return json.loads(response.data)
