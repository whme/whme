"""The language cache committed to the repository as ``cache.json``.

The cache records the lines added per language, one slice per repository, so
each run reads only the commits added since last time. It is committed to the
public profile repository, so every slice is keyed by an opaque hash of the
repository name — never the name itself — and private names never leak.

The pydantic models below own the JSON schema, validation and serialization,
so reading and writing the cache is a single validated round trip rather than
hand-rolled dictionary juggling.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from pathlib import Path


class RepoStats(BaseModel):
    """One repository's slice of the language totals.

    Storing each repository independently is what makes rewritten history
    safe: if ``head`` is ever gone, the whole slice is rebuilt and replaced
    rather than added to, so nothing is double-counted.

    Attributes:
      head:      Newest commit already counted, empty until the first run.
      all_time:  Lines added per language over the repository's whole history.
      recent:    Lines added per language, bucketed by ``YYYY-MM-DD`` day.
    """

    head: str = ""
    all_time: dict[str, int] = Field(default_factory=dict)
    recent: dict[str, dict[str, int]] = Field(default_factory=dict)


class LanguageCache(BaseModel):
    """Added lines per language, one slice per repository.

    Attributes:
      repos:  Repository slices keyed by the opaque hash from :func:`repo_key`.
    """

    repos: dict[str, RepoStats] = Field(default_factory=dict)


def repo_key(repo: str) -> str:
    """Derives a stable, opaque cache key for a repository.

    The cache is committed to a public repository, so a private repository's
    name must never appear in it. A BLAKE2b digest, sized down to eight bytes,
    gives a short, stable key that leaks nothing about the name behind it.

    Args:
      repo:  ``owner/name`` repository to key.

    Returns:
      A sixteen-character BLAKE2b hexadecimal digest that is stable across runs.
    """
    return hashlib.blake2b(repo.encode(), digest_size=8).hexdigest()


def load_cache(path: Path) -> LanguageCache:
    """Reads and validates the language cache, or starts an empty one.

    Args:
      path:  Location of the cache JSON file.

    Returns:
      The parsed cache, empty when the file does not yet exist.
    """
    if not path.exists():
        return LanguageCache()
    return LanguageCache.model_validate_json(path.read_text())


def save_cache(path: Path, cache: LanguageCache) -> None:
    """Writes the language cache as sorted, indented JSON.

    Sorting the keys keeps the committed file's diff stable from run to run.

    Args:
      path:   Location of the cache JSON file.
      cache:  Cache to serialize.
    """
    path.write_text(json.dumps(cache.model_dump(), indent=1, sort_keys=True) + "\n")
