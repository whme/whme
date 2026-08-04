"""Tests for the pydantic language cache module."""

from __future__ import annotations

from typing import TYPE_CHECKING

from readme_updater.cache import (
    LanguageCache,
    RepoStats,
    load_cache,
    repo_key,
    save_cache,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_repo_key_is_a_stable_opaque_hash() -> None:
    key = repo_key("whme/super-secret")
    assert key == repo_key("whme/super-secret")
    assert "secret" not in key
    assert len(key) == 16


def test_missing_file_loads_an_empty_cache(tmp_path: Path) -> None:
    assert load_cache(tmp_path / "absent.json") == LanguageCache()


def test_round_trips_through_disk_without_leaking_private_names(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cache.json"
    cache = LanguageCache(
        repos={
            repo_key("whme/super-secret"): RepoStats(
                head="h", all_time={"Rust": 5}, recent={"2026-08-01": {"Rust": 5}}
            )
        }
    )
    save_cache(path, cache)
    assert "whme/super-secret" not in path.read_text()
    assert load_cache(path) == cache
