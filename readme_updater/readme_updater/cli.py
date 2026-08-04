"""Command-line entry point: rewrite the README's dynamic sections."""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from typing import TextIO

logger = logging.getLogger(__name__)

LOG_FORMAT = "%(asctime)s %(threadName)s %(name)s %(levelname)s %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S %z"
LEVEL_COLORS = {
    logging.WARNING: "\033[33m",
    logging.ERROR: "\033[31m",
    logging.CRITICAL: "\033[1;31m",
}
RESET = "\033[0m"


class _Formatter(logging.Formatter):
    """Formats log records in UTC, coloring warning and error levels."""

    def __init__(self, *, color: bool) -> None:
        """Configure UTC timestamps and record whether to colorize.

        Args:
          color:  Whether to wrap warning and error lines in ANSI color codes.
        """
        super().__init__(LOG_FORMAT, datefmt=LOG_DATEFMT)
        self.converter = time.gmtime  # log in UTC, so the offset is always +0000
        self._color = color

    def format(self, record: logging.LogRecord) -> str:
        """Render a record, wrapping it in color when its level has one.

        Args:
          record:  Log record to render.

        Returns:
          The formatted line, wrapped in ANSI color codes for colored levels.
        """
        line = super().format(record)
        color = self._color and LEVEL_COLORS.get(record.levelno)
        return f"{color}{line}{RESET}" if color else line


def _supports_color(stream: TextIO) -> bool:
    """Decide whether ANSI colors should be written to a stream.

    Honors NO_COLOR, then FORCE_COLOR and GITHUB_ACTIONS, before falling
    back to whether the stream is attached to a terminal.

    Args:
      stream:  Output stream that log lines are written to.

    Returns:
      Whether colored output is appropriate for the stream.
    """
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR") or os.environ.get("GITHUB_ACTIONS") == "true":
        return True
    return stream.isatty()


def _configure_logging(*, verbose: bool) -> None:
    """Route colored, UTC log lines for the whole process to stderr.

    Args:
      verbose:  Whether to log at DEBUG instead of INFO.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_Formatter(color=_supports_color(sys.stderr)))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        handlers=[handler],
    )


@click.command()
@click.option(
    "--readme-path",
    type=click.Path(dir_okay=False, path_type=Path),
    default="README.md",
    show_default=True,
    help="Path of the profile README to rewrite.",
)
@click.option("-v", "--verbose", is_flag=True, help="Log at the DEBUG level.")
def main(readme_path: Path, *, verbose: bool) -> None:
    """Rewrite the dynamic sections of the profile README in place.

    Args:
      readme_path:  Profile README to rewrite.
      verbose:      Whether to lower the log threshold to DEBUG.
    """
    _configure_logging(verbose=verbose)
    logger.info("refreshing %s", readme_path)
    readme_path.write_text(readme_path.read_text())
    logger.info("wrote %s", readme_path)
