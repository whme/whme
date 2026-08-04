"""Run the whole check suite behind a single ``check`` command."""

import subprocess
import sys

CHECKS: tuple[tuple[str, ...], ...] = (
    ("ruff", "format", "--check"),
    ("ruff", "check"),
    ("ty", "check"),
    ("pytest",),
)


def main() -> None:
    """Run each check in order and exit on the first that fails.

    Raises:
      SystemExit: With the return code of the first failing check.
    """
    for command in CHECKS:
        completed = subprocess.run(command, check=False)  # noqa: S603
        if completed.returncode:
            sys.exit(completed.returncode)
