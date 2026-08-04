"""Tests for the command-line entry point."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

from click.testing import CliRunner

from readme_updater import cli

if TYPE_CHECKING:
    from pathlib import Path
    from typing import TextIO

    import pytest


def test_main_rewrites_the_readme_in_place(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("content")
    result = CliRunner().invoke(cli.main, ["--readme-path", str(readme)])
    assert result.exit_code == 0
    assert readme.read_text() == "content"


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
