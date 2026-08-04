"""Talking to the GitHub API — the one place that makes network calls."""

from __future__ import annotations

import json
import logging
import os
import urllib.parse
from typing import Any

import urllib3

logger = logging.getLogger(__name__)

USER = "whme"
MY_ACCOUNTS = frozenset({"whme", "whmade"})
PROFILE_REPO = "whme/whme"
API = "https://api.github.com"

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


class GitHubError(Exception):
    """A GitHub API request came back with an error status."""

    def __init__(self, status: int, url: str) -> None:
        """Record the HTTP status and the URL that produced it."""
        self.status = status
        super().__init__(f"GitHub returned {status} for {url}")


def fetch(url: str) -> Any:
    """GET a JSON resource; urllib3 retries transient failures for us."""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{USER}-readme-updater",
    }
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    logger.debug("GET %s", url)
    response = _http.request("GET", url, headers=headers)
    if response.status >= 400:  # noqa: PLR2004 - HTTP client-error threshold
        logger.warning("GitHub returned %s for %s", response.status, url)
        raise GitHubError(response.status, url)
    return json.loads(response.data)


def public_query(qualifiers: str = "") -> str:
    """Build a search query restricted to my public contributions.

    The README must never leak private activity, no matter how much the
    token running the script is allowed to see.
    """
    return " ".join(filter(None, [f"author:{USER}", "is:public", qualifiers]))


def public_commits(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop commits from private repositories, whatever the search returned."""
    return [item for item in items if not item["repository"].get("private")]


def search(
    endpoint: str, sort: str, qualifiers: str = "", per_page: int = 50
) -> list[dict[str, Any]]:
    """Return the items of a public GitHub search."""
    params = urllib.parse.urlencode(
        {
            "q": public_query(qualifiers),
            "sort": sort,
            "order": "desc",
            "per_page": per_page,
        }
    )
    return list(fetch(f"{API}/search/{endpoint}?{params}")["items"])


def count(endpoint: str, qualifiers: str) -> int:
    """Return the total number of results for a public GitHub search."""
    params = urllib.parse.urlencode({"q": public_query(qualifiers), "per_page": 1})
    return int(fetch(f"{API}/search/{endpoint}?{params}")["total_count"])
