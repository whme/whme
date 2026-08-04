"""Keep the dynamic sections of my profile README fresh.

The work is split across small modules: :mod:`github` makes the API
calls, :mod:`markup` builds the HTML and injects it between the README's
marker comments, :mod:`activity` renders the "Currently working on" log,
:mod:`languages` renders the language bars, and :mod:`app` ties them
together behind :func:`main`.
"""

from readme_updater.app import main

__all__ = ["main"]
