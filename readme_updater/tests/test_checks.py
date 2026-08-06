"""Tests for the single-command check runner."""

import subprocess

import pytest

from readme_updater import checks


def _fake_runs(
    monkeypatch: pytest.MonkeyPatch, *return_codes: int
) -> list[tuple[str, ...]]:
    commands: list[tuple[str, ...]] = []
    codes = iter(return_codes)

    def fake_run(
        command: tuple[str, ...], *, check: bool
    ) -> subprocess.CompletedProcess[bytes]:
        assert check is False
        commands.append(command)
        return subprocess.CompletedProcess(command, next(codes))

    monkeypatch.setattr(checks.subprocess, "run", fake_run)
    return commands


def test_check_runs_every_command_when_all_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = _fake_runs(monkeypatch, *([0] * len(checks.CHECK)))
    checks.check()
    assert commands == list(checks.CHECK)


def test_autofix_runs_every_command_when_all_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = _fake_runs(monkeypatch, *([0] * len(checks.AUTOFIX)))
    checks.autofix()
    assert commands == list(checks.AUTOFIX)


def test_exits_on_first_failing_command(monkeypatch: pytest.MonkeyPatch) -> None:
    commands = _fake_runs(monkeypatch, 0, 3)
    with pytest.raises(SystemExit) as exit_info:
        checks.check()
    assert exit_info.value.code == 3
    assert commands == list(checks.CHECK[:2])
