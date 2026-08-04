from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from readme_updater.languages import LanguageCache, RepoStats
from readme_updater.local import (
    LOCAL_PREFIX,
    author_patterns,
    fetch_local_additions,
    local_key,
    local_repos,
    numstat_additions,
    update_local_repos,
)

if TYPE_CHECKING:
    import pytest

NUMSTAT = "\n".join(  # noqa: FLY002 - one row per line reads better than a blob
    [
        "40\t2\tsrc/main.rs",
        "12\t0\tapp/view.tsx",
        "-\t-\tres/logo.png",  # binary, skipped
        "8\t1\tsrc/{old => new}.rs",  # rename, follows to new.rs
        "3\t0\tlib.py => pkg/lib.py",  # whole-path rename
        "500\t0\tCargo.lock",  # generated, skipped
        "",  # blank line between commits
    ]
)


class TestNumstatAdditions:
    def test_sums_additions_per_language_following_renames(self) -> None:
        assert numstat_additions(NUMSTAT) == {"Rust": 48, "TypeScript": 12, "Python": 3}

    def test_skips_binary_and_generated_files(self) -> None:
        counts = numstat_additions(NUMSTAT)
        assert "Cargo.lock" not in counts  # generated
        assert counts.get("Rust") == 48  # png binary contributed nothing


class TestConfig:
    def test_local_repos_reads_pathsep_separated_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("README_UPDATER_LOCAL_REPOS", "/a/b::/c/d")
        assert [str(p) for p in local_repos()] == ["/a/b", "/c/d"]

    def test_local_repos_is_empty_without_the_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("README_UPDATER_LOCAL_REPOS", raising=False)
        assert local_repos() == []

    def test_author_patterns_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("README_UPDATER_AUTHORS", "alice, bob")
        assert author_patterns() == ["alice", "bob"]

    def test_author_patterns_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("README_UPDATER_AUTHORS", raising=False)
        assert "hoehl" in author_patterns()


class TestUpdateLocalRepos:
    def test_local_stats_go_to_all_time_only_never_recent(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(
            "readme_updater.local.fetch_local_additions",
            lambda _path: {"Rust": 100},
        )
        cache = LanguageCache(repos={})
        update_local_repos(cache, [tmp_path])
        (key,) = cache.repos
        assert key.startswith(LOCAL_PREFIX)
        assert cache.repos[key].all_time == {"Rust": 100}
        assert cache.repos[key].recent == {}

    def test_rebuild_purges_stale_local_slices_but_keeps_github_ones(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(
            "readme_updater.local.fetch_local_additions",
            lambda _path: {"Python": 5},
        )
        github_slice = RepoStats(head="sha", all_time={"Rust": 1}, recent={})
        cache = LanguageCache(
            repos={
                "0123456789abcdef": github_slice,  # a GitHub slice, must survive
                f"{LOCAL_PREFIX}stale": RepoStats(  # a moved-away local, must go
                    head="local", all_time={"Go": 999}, recent={}
                ),
            }
        )
        update_local_repos(cache, [tmp_path])
        assert cache.repos["0123456789abcdef"] is github_slice
        assert not any("Go" in s.all_time for s in cache.repos.values())
        assert any(s.all_time == {"Python": 5} for s in cache.repos.values())

    def test_unreadable_paths_are_skipped(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        def boom(_path: Path) -> dict[str, int]:
            raise subprocess.CalledProcessError(128, "git")

        monkeypatch.setattr("readme_updater.local.fetch_local_additions", boom)
        cache = LanguageCache(repos={})
        update_local_repos(cache, [tmp_path])
        assert cache.repos == {}


class TestPrivatePathsNeverLeak:
    def test_local_key_is_opaque(self) -> None:
        key = local_key(Path("/home/me/super-secret-project"))
        assert "secret" not in key
        assert key.startswith(LOCAL_PREFIX)


def _git(path: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603
        [shutil.which("git") or "git", "-C", str(path), *args],
        check=True,
        capture_output=True,
    )


class TestIntegration:
    def test_counts_my_additions_in_a_real_local_repo(self, tmp_path: Path) -> None:
        _git(tmp_path, "init", "-q")
        (tmp_path / "main.rs").write_text("fn main() {}\n" * 10)
        (tmp_path / "notes.md").write_text("hello\n" * 4)
        _git(tmp_path, "add", "-A")
        _git(
            tmp_path,
            "-c",
            "user.name=Max Höhl",
            "-c",
            "user.email=max.hoehl@example.com",
            "commit",
            "-qm",
            "seed",
        )
        assert fetch_local_additions(tmp_path) == {"Rust": 10}
