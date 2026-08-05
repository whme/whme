"""Tests for the command-line entry point."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

from click.testing import CliRunner

from readme_updater import cli, github

if TYPE_CHECKING:
    from pathlib import Path
    from typing import TextIO

    import pytest


def test_main_fills_every_section(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(
        github.Profile,
        "_fetch_json",
        lambda _self, _url: {"items": [], "total_count": 0},
    )
    monkeypatch.setattr(cli, "update_languages", lambda *_: ("recent-bar", "all-bar"))
    readme = tmp_path / "README.md"
    readme.write_text(
        "<!-- activity:start -->\nstale\n<!-- activity:end -->\n"
        "<!-- recent_language_bar:start -->\nx\n<!-- recent_language_bar:end -->\n"
        "<!-- all_time_language_bar:start -->\ny\n<!-- all_time_language_bar:end -->\n"
    )
    with caplog.at_level(logging.INFO):
        result = CliRunner().invoke(
            cli.main, ["--readme-path", str(readme), "--github-username", "whme"]
        )
    assert result.exit_code == 0
    text = readme.read_text()
    assert "recent-bar" in text
    assert "all-bar" in text
    assert "fetching their contribution totals" in caplog.text


def test_update_languages_refreshes_the_cache_and_renders_bars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "language-colors.json").write_text('{"Rust": "#dea584"}')
    today = datetime.now(UTC).date().isoformat()
    commit = {
        "sha": "s",
        "commit": {"committer": {"date": f"{today}T00:00:00Z"}},
        "files": [{"filename": "a.rs", "additions": 10}],
    }
    monkeypatch.setattr(
        github.Profile, "fetch_owned_repos", lambda _self: ["whme/csshw"]
    )
    monkeypatch.setattr(
        github.Profile,
        "fetch_commits_since",
        lambda *_: ([commit], False),
    )
    profile = github.Profile("whme", frozenset({"whme"}))
    recent, all_time = cli.update_languages(tmp_path, profile, [])
    assert (assets / "languages.svg").exists()
    assert (assets / "languages-recent.svg").exists()
    assert "Rust 100.0%" in all_time
    assert "Rust 100.0%" in recent


def test_main_exits_when_no_token_is_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(github, "gh_auth_token", lambda: None)
    readme = tmp_path / "README.md"
    readme.write_text("<!-- activity:start -->\n<!-- activity:end -->\n")
    result = CliRunner().invoke(
        cli.main, ["--readme-path", str(readme), "--github-username", "whme"]
    )
    assert result.exit_code != 0
    assert readme.read_text() == "<!-- activity:start -->\n<!-- activity:end -->\n"


def test_update_languages_folds_local_repos_into_all_time_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "language-colors.json").write_text(
        '{"Rust": "#dea584", "Python": "#3572A5"}'
    )
    today = datetime.now(UTC).date().isoformat()
    commit = {
        "sha": "s",
        "commit": {"committer": {"date": f"{today}T00:00:00Z"}},
        "files": [{"filename": "a.rs", "additions": 10}],
    }
    monkeypatch.setattr(
        github.Profile, "fetch_owned_repos", lambda _self: ["whme/csshw"]
    )
    monkeypatch.setattr(
        github.Profile,
        "fetch_commits_since",
        lambda *_: ([commit], False),
    )
    monkeypatch.setattr(
        "readme_updater.local.fetch_local_additions",
        lambda _path, _authors: {"Python": 90},
    )
    profile = github.Profile("whme", frozenset({"whme"}))
    recent, all_time = cli.update_languages(tmp_path, profile, [], (tmp_path,), ())
    # Local work folds into the all-time bar but never the recent window.
    assert "Rust 10.0%" in all_time
    assert "Python 90.0%" in all_time
    assert "Rust 100.0%" in recent
    assert "Python" not in recent


def test_main_attributes_local_commits_to_github_and_local_authors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, tuple[Path, ...] | tuple[str, ...]] = {}

    def fake_update_languages(
        _base: Path,
        _profile: github.Profile,
        _contributions: list[github.Contribution],
        local_repos: tuple[Path, ...],
        local_authors: tuple[str, ...],
        _concurrency: int,
    ) -> tuple[str, str]:
        captured["repos"] = local_repos
        captured["authors"] = local_authors
        return ("recent-bar", "all-bar")

    monkeypatch.setattr(
        github.Profile,
        "_fetch_json",
        lambda _self, _url: {"items": [], "total_count": 0},
    )
    monkeypatch.setattr(cli, "update_languages", fake_update_languages)
    readme = tmp_path / "README.md"
    readme.write_text(
        "<!-- activity:start -->\n<!-- activity:end -->\n"
        "<!-- recent_language_bar:start -->\n<!-- recent_language_bar:end -->\n"
        "<!-- all_time_language_bar:start -->\n<!-- all_time_language_bar:end -->\n"
    )
    result = CliRunner().invoke(
        cli.main,
        [
            "--readme-path",
            str(readme),
            "--github-username",
            "whme",
            "--other-owned-github-username",
            "whme-bot",
            "--local-repo",
            str(tmp_path),
            "--local-author",
            "me@example.com",
        ],
    )
    assert result.exit_code == 0
    assert captured["repos"] == (tmp_path,)
    assert set(captured["authors"]) >= {"whme", "whme-bot", "me@example.com"}


def test_update_languages_threads_concurrency_to_the_fetch_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "language-colors.json").write_text('{"Rust": "#dea584"}')
    captured: dict[str, int] = {}

    def fake_fetch_commits_since(
        _self: github.Profile, _repo: str, _head: str | None, concurrency: int = 1
    ) -> tuple[object, bool]:
        captured["concurrency"] = concurrency
        return (iter([]), True)

    monkeypatch.setattr(
        github.Profile, "fetch_owned_repos", lambda _self: ["whme/csshw"]
    )
    monkeypatch.setattr(github.Profile, "fetch_commits_since", fake_fetch_commits_since)
    profile = github.Profile("whme", frozenset({"whme"}))
    cli.update_languages(tmp_path, profile, [], concurrency=7)
    assert captured["concurrency"] == 7


def test_main_passes_the_concurrency_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, int] = {}

    def fake_update_languages(
        _base: Path,
        _profile: github.Profile,
        _contributions: list[github.Contribution],
        _local_repos: tuple[Path, ...],
        _local_authors: tuple[str, ...],
        concurrency: int,
    ) -> tuple[str, str]:
        captured["concurrency"] = concurrency
        return ("recent-bar", "all-bar")

    monkeypatch.setattr(
        github.Profile,
        "_fetch_json",
        lambda _self, _url: {"items": [], "total_count": 0},
    )
    monkeypatch.setattr(cli, "update_languages", fake_update_languages)
    readme = tmp_path / "README.md"
    readme.write_text(
        "<!-- activity:start -->\n<!-- activity:end -->\n"
        "<!-- recent_language_bar:start -->\n<!-- recent_language_bar:end -->\n"
        "<!-- all_time_language_bar:start -->\n<!-- all_time_language_bar:end -->\n"
    )
    result = CliRunner().invoke(
        cli.main,
        [
            "--readme-path",
            str(readme),
            "--github-username",
            "whme",
            "--concurrency",
            "6",
        ],
    )
    assert result.exit_code == 0
    assert captured["concurrency"] == 6


def test_concurrency_option_rejects_values_out_of_range(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("<!-- activity:start -->\n<!-- activity:end -->\n")
    for bad in ("0", str(github.MAX_CONCURRENCY + 1)):
        result = CliRunner().invoke(
            cli.main,
            [
                "--readme-path",
                str(readme),
                "--github-username",
                "whme",
                "--concurrency",
                bad,
            ],
        )
        assert result.exit_code != 0


def test_supports_color_prefers_the_environment_then_the_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("NO_COLOR", "FORCE_COLOR", "GITHUB_ACTIONS"):
        monkeypatch.delenv(name, raising=False)
    a_tty = cast("TextIO", SimpleNamespace(isatty=lambda: True))
    not_a_tty = cast("TextIO", SimpleNamespace(isatty=lambda: False))

    assert cli._supports_color(a_tty) is True
    assert cli._supports_color(not_a_tty) is False

    monkeypatch.setenv("NO_COLOR", "1")
    assert cli._supports_color(a_tty) is False

    monkeypatch.delenv("NO_COLOR")
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert cli._supports_color(not_a_tty) is True

    monkeypatch.delenv("FORCE_COLOR")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert cli._supports_color(not_a_tty) is True


def test_formatter_logs_utc_and_colors_only_when_enabled() -> None:
    record = logging.LogRecord("name", logging.ERROR, "path", 1, "boom", None, None)
    plain = cli._Formatter(color=False).format(record)
    colored = cli._Formatter(color=True).format(record)
    assert "+0000" in plain
    assert cli.RESET not in plain
    assert colored == f"{cli.LEVEL_COLORS[logging.ERROR]}{plain}{cli.RESET}"


def test_formatter_timestamp_has_microsecond_precision() -> None:
    record = logging.LogRecord("name", logging.INFO, "path", 1, "hi", None, None)
    stamp = cli._Formatter(color=False).formatTime(record)
    _, _, rest = stamp.partition(".")
    fraction, _, offset = rest.partition(" ")
    assert len(fraction) == 6
    assert fraction.isdigit()
    assert offset == "+0000"
