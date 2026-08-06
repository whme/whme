"""Tests for folding local, private repositories into the all-time bar."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from readme_updater.cache import LanguageCache, RepoStats
from readme_updater.languages import MAX_COUNTED_FILE_ADDITIONS
from readme_updater.local import (
    LOCAL_PREFIX,
    fetch_local_additions,
    local_key,
    numstat_additions,
    update_local_repos,
)

if TYPE_CHECKING:
    import pytest

NUMSTAT = "\n".join(  # noqa: FLY002 - one row per line reads better than a blob
    [
        "40\t2\tsrc/main.rs",
        "12\t0\tapp/view.tsx",
        "",  # blank line between commits, skipped
        "-\t-\tres/logo.png",  # binary, skipped
        "8\t1\tsrc/{old => new}.rs",  # rename, follows to new.rs
        "3\t0\tlib.py => pkg/lib.py",  # whole-path rename
        "500\t0\tCargo.lock",  # generated, skipped
    ]
)


def test_numstat_sums_per_language_following_renames() -> None:
    assert numstat_additions(NUMSTAT) == {"Rust": 48, "TypeScript": 12, "Python": 3}


def test_numstat_skips_a_single_oversized_file() -> None:
    numstat = "\n".join(
        [
            "40\t0\tsrc/main.rs",
            f"{MAX_COUNTED_FILE_ADDITIONS + 1}\t0\tdata/dump.py",
        ]
    )
    assert numstat_additions(numstat) == {"Rust": 40}


def test_local_key_is_opaque_and_prefixed() -> None:
    key = local_key(Path("/home/me/super-secret-project"))
    assert "secret" not in key
    assert key.startswith(LOCAL_PREFIX)


def test_rebuild_replaces_local_slices_all_time_only_and_keeps_github(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "readme_updater.local.fetch_local_additions",
        lambda _path, _authors: {"Python": 5},
    )
    github_slice = RepoStats(head="sha", all_time={"Rust": 1})
    cache = LanguageCache(
        repos={
            "0123456789abcdef": github_slice,  # a GitHub slice, must survive
            f"{LOCAL_PREFIX}stale": RepoStats(all_time={"Go": 999}),  # moved-away
        }
    )
    update_local_repos(cache, [tmp_path], [])
    assert cache.repos["0123456789abcdef"] is github_slice
    (key,) = [key for key in cache.repos if key.startswith(LOCAL_PREFIX)]
    assert cache.repos[key].all_time == {"Python": 5}  # folds into all-time
    assert cache.repos[key].recent == {}  # but never the recent window
    assert not any("Go" in stats.all_time for stats in cache.repos.values())


def test_unreadable_paths_are_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def boom(_path: Path, _authors: list[str]) -> dict[str, int]:
        raise subprocess.CalledProcessError(128, "git")

    monkeypatch.setattr("readme_updater.local.fetch_local_additions", boom)
    cache = LanguageCache()
    update_local_repos(cache, [tmp_path], [])
    assert cache.repos == {}


def _git(path: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603 - a fixed local `git` invocation, no shell
        [shutil.which("git") or "git", "-C", str(path), *args],
        check=True,
        capture_output=True,
    )


def test_counts_only_the_matching_author_in_a_real_local_repo(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    (tmp_path / "main.rs").write_text("fn main() {}\n" * 10)
    (tmp_path / "notes.md").write_text("hello\n" * 4)
    _git(tmp_path, "add", "-A")
    _git(
        tmp_path,
        "-c",
        "user.name=Ada Lovelace",
        "-c",
        "user.email=ada@example.com",
        "commit",
        "-qm",
        "seed",
    )
    assert fetch_local_additions(tmp_path, []) == {"Rust": 10}  # every commit
    assert fetch_local_additions(tmp_path, ["ada@example.com"]) == {"Rust": 10}
    assert fetch_local_additions(tmp_path, ["nobody"]) == {}
