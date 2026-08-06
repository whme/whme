"""Run the check suite in verify (``check``) or autofix (``autofix``) mode."""

import subprocess
import sys

CHECK: tuple[tuple[str, ...], ...] = (
    ("ruff", "format", "--check"),
    ("ruff", "check"),
    ("ty", "check"),
    ("pytest",),
)
AUTOFIX: tuple[tuple[str, ...], ...] = (
    ("ruff", "format"),
    ("ruff", "check", "--fix"),
    ("ty", "check"),
    ("pytest",),
)


def _run(commands: tuple[tuple[str, ...], ...]) -> None:
    """Run each command in order and exit on the first that fails.

    Args:
      commands: Commands to run, each as an argv tuple.

    Raises:
      SystemExit: With the return code of the first failing command.
    """
    for command in commands:
        completed = subprocess.run(command, check=False)  # noqa: S603
        if completed.returncode:
            sys.exit(completed.returncode)


def check() -> None:
    """Verify formatting, lint, types and tests without changing files."""
    _run(CHECK)


def autofix() -> None:
    """Apply formatting and lint fixes, then verify types and tests."""
    _run(AUTOFIX)
