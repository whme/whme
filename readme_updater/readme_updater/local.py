"""Count lines added in local, private repositories.

Some past work lives in repositories that will never be on GitHub. Each path
given with ``--local-repo`` is read with local ``git`` and folded into the
**all-time** totals only: being finished work, it yields no new commits and so
never reaches the rolling recent window. Only aggregated line counts enter the
cache, behind an opaque key, so nothing about a private repository is published.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path

from readme_updater.cache import LanguageCache, RepoStats, repo_key
from readme_updater.languages import (
    EXTENSION_LANGUAGES,
    MAX_COUNTED_FILE_ADDITIONS,
    is_countable,
)

logger = logging.getLogger(__name__)

LOCAL_PREFIX = "local-"
NUMSTAT_FIELDS = 3  # each git numstat row is added, deleted, path


def local_key(path: Path) -> str:
    """Derives an opaque, local-only cache key for a repository path.

    The path itself may be private, so only its hash is stored; the ``local-``
    prefix marks the slice as local so it can be rebuilt on every run.

    Args:
      path:  Filesystem path of the local repository to key.

    Returns:
      The opaque cache key, prefixed to mark it as a local slice.
    """
    return LOCAL_PREFIX + repo_key(str(path))


def _new_path(path: str) -> str:
    """Resolves a git numstat rename token to the file's new name.

    Args:
      path:  Numstat path field, possibly a ``{old => new}`` rename token.

    Returns:
      The file's new name, or the path unchanged when it is not a rename.
    """
    path = re.sub(r"\{.*? => (.*?)\}", r"\1", path)
    if " => " in path:
        path = path.split(" => ", 1)[1]
    return path


def numstat_additions(output: str) -> dict[str, int]:
    """Sums the added lines per language from ``git log --numstat`` output.

    Args:
      output:  Raw ``git log --numstat`` output, one tab-separated row per file.

    Returns:
      The lines added per language, skipping binary files and files without a
      known, countable language.
    """
    counts: dict[str, int] = {}
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) != NUMSTAT_FIELDS:
            continue
        added, _deleted, path = fields
        if added == "-":  # a binary file
            continue
        path = _new_path(path)
        added_lines = int(added)
        language = EXTENSION_LANGUAGES.get(Path(path).suffix.lower())
        if (
            language
            and is_countable(path)
            and added_lines <= MAX_COUNTED_FILE_ADDITIONS
        ):
            counts[language] = counts.get(language, 0) + added_lines
    return counts


def fetch_local_additions(path: Path, authors: list[str]) -> dict[str, int]:
    """Counts the added lines per language across a local repository's history.

    Args:
      path:     Filesystem path of the local git repository.
      authors:  Author identifiers; a commit counts when its git author name or
                email contains one as a case-insensitive substring. An empty
                list counts every commit.

    Returns:
      The lines added per language across the matching commits.
    """
    author_args = [arg for pattern in authors for arg in ("--author", pattern)]
    result = subprocess.run(  # noqa: S603 - a fixed local `git log`, no shell
        [
            shutil.which("git") or "git",
            "-C",
            str(path),
            "log",
            "--no-merges",
            "--numstat",
            "--pretty=tformat:",
            "-i",  # match the author patterns case-insensitively
            *author_args,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return numstat_additions(result.stdout)


def update_local_repos(
    cache: LanguageCache, paths: list[Path], authors: list[str]
) -> None:
    """Rebuilds the cache's local slices from the given repository paths.

    Local slices are purged and recomputed on every run, so a repository that
    moved or was dropped cannot linger and double-count. Each slice carries only
    all-time totals: local work never reaches the recent-window bar.

    Args:
      cache:    Language cache whose local slices are replaced in place.
      paths:    Filesystem paths of the local git repositories to count.
      authors:  Author identifiers whose commits are counted; empty counts every
                commit.
    """
    logger.info("counting %(count)d local repositories", {"count": len(paths)})
    for key in [key for key in cache.repos if key.startswith(LOCAL_PREFIX)]:
        del cache.repos[key]
    for path in paths:
        try:
            counts = fetch_local_additions(path, authors)
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
            cache.repos[local_key(path)] = RepoStats(all_time=counts)
