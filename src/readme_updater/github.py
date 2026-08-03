"""Talking to the GitHub API — the one place that makes network calls."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

USER = "whme"
MY_ACCOUNTS = frozenset({"whme", "whmade"})
PROFILE_REPO = "whme/whme"
API = "https://api.github.com"

REQUEST_TIMEOUT = 30
RETRIES = 3
RETRY_BACKOFF = 3


def fetch(url: str) -> Any:
    """GET a JSON resource, retrying transient network failures.

    A dropped connection or timeout is retried with a growing backoff so
    it can't hang or abort a long backfill; a real HTTP status (404, 422,
    …) is raised straight away for the caller to handle.
    """
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
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == RETRIES - 1:
                raise
            time.sleep(RETRY_BACKOFF * (attempt + 1))
    raise RuntimeError("unreachable")


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
