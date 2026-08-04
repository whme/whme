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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    result = CliRunner().invoke(
        cli.main, ["--readme-path", str(readme), "--github-username", "whme"]
    )
    assert result.exit_code == 0
    text = readme.read_text()
    assert "recent-bar" in text
    assert "all-bar" in text


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
        lambda _self, _repo, _head: ([commit], False),
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
