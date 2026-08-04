"""Tests for the pydantic language cache module."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from readme_updater import cache as cache_module
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


def test_save_is_atomic_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    save_cache(path, LanguageCache(repos={"k": RepoStats(head="h")}))
    save_cache(path, LanguageCache(repos={"k": RepoStats(head="h2")}))  # overwrite
    assert load_cache(path).repos["k"].head == "h2"
    # The temp file is renamed onto the target, never left behind.
    assert list(tmp_path.iterdir()) == [path]


def test_save_cleans_up_the_temp_file_when_the_swap_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_self: Path, _target: Path) -> None:
        raise OSError("rename failed")

    monkeypatch.setattr(cache_module.Path, "replace", boom)
    path = tmp_path / "cache.json"
    with pytest.raises(OSError, match="rename failed"):
        save_cache(path, LanguageCache())
    # The unique temp file is removed and the target is never created.
    assert list(tmp_path.iterdir()) == []
