"""Counting lines I added in local, private repositories.

Some of my work lives in repositories that will never be on GitHub. When
their paths are given in ``README_UPDATER_LOCAL_REPOS`` (an ``os.pathsep``
separated list), each is read with local ``git`` and folded into the
**all-time** totals only — never the rolling recent window, which stays a
public-GitHub view. Only aggregated line counts enter the cache, behind an
opaque key, so nothing about a private repository is published.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from readme_updater.languages import (
    EXTENSION_LANGUAGES,
    LanguageCache,
    RepoStats,
    is_countable,
)

logger = logging.getLogger(__name__)

ENV_REPOS = "README_UPDATER_LOCAL_REPOS"
ENV_AUTHORS = "README_UPDATER_AUTHORS"
DEFAULT_AUTHORS = ("hoehl", "Höhl", "whme")
LOCAL_PREFIX = "local-"
NUMSTAT_FIELDS = 3  # each git numstat row is added, deleted, path


def local_repos() -> list[Path]:
    """Read the local repository paths configured via the environment."""
    raw = os.environ.get(ENV_REPOS, "")
    return [Path(part) for part in raw.split(os.pathsep) if part]


def author_patterns() -> list[str]:
    """Return the author patterns that identify my commits in a local repo."""
    raw = os.environ.get(ENV_AUTHORS)
    if raw:
        return [part.strip() for part in raw.split(",") if part.strip()]
    return list(DEFAULT_AUTHORS)


def local_key(path: Path) -> str:
    """Derive an opaque, local-only cache key for a repository path.

    The path may be private, so only its hash is stored; the ``local-``
    prefix marks the slice as local so it can be rebuilt each run.
    """
    return LOCAL_PREFIX + hashlib.sha256(str(path).encode()).hexdigest()[:16]


def _new_path(path: str) -> str:
    """Resolve a git numstat path, following renames to the new name."""
    path = re.sub(r"\{.*? => (.*?)\}", r"\1", path)
    if " => " in path:
        path = path.split(" => ", 1)[1]
    return path


def numstat_additions(output: str) -> dict[str, int]:
    """Sum the lines I added per language from ``git log --numstat`` output."""
    counts: dict[str, int] = {}
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) != NUMSTAT_FIELDS:
            continue
        added, _deleted, path = fields
        if added == "-":  # a binary file
            continue
        path = _new_path(path)
        language = EXTENSION_LANGUAGES.get(Path(path).suffix.lower())
        if language and is_countable(path):
            counts[language] = counts.get(language, 0) + int(added)
    return counts


def fetch_local_additions(path: Path) -> dict[str, int]:
    """Count the lines I added per language across a local repository."""
    git = shutil.which("git") or "git"
    authors = [arg for pattern in author_patterns() for arg in ("--author", pattern)]
    result = subprocess.run(  # noqa: S603 - a fixed local `git log`, no shell
        [
            git,
            "-C",
            str(path),
            "log",
            "--no-merges",
            "--numstat",
            "--pretty=tformat:",
            "-i",  # match the author patterns case-insensitively
            *authors,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return numstat_additions(result.stdout)


def update_local_repos(cache: LanguageCache, paths: list[Path]) -> None:
    """Rebuild the local slices of the cache from the given repository paths.

    Local slices are purged and recomputed every run, so a repository that
    moved or was dropped can't linger and double-count. Each slice carries
    only all-time totals: local work never reaches the recent-window bar.
    """
    logger.info("counting %(count)d local repositories", {"count": len(paths)})
    for key in [key for key in cache.repos if key.startswith(LOCAL_PREFIX)]:
        del cache.repos[key]
    for path in paths:
        try:
            counts = fetch_local_additions(path)
        except subprocess.CalledProcessError, OSError:
            logger.warning(
                "skipping unreadable local repository %(path)s", {"path": path}
            )
            continue
        logger.debug(
            "local %(path)s: %(lines)d lines across %(languages)d languages",
            {"path": path, "lines": sum(counts.values()), "languages": len(counts)},
        )
        if counts:
            cache.repos[local_key(path)] = RepoStats(
                head="local", all_time=counts, recent={}
            )
