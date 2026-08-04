"""Keep the dynamic sections of the profile README up to date.

The work is split across small modules: :mod:`github` makes the API
calls, :mod:`markup` builds the HTML and injects it between the README's
marker comments, :mod:`activity` renders the "Recent activity" log,
:mod:`languages` renders the language bars, :mod:`local` folds in local
private repositories, and :mod:`cli` ties them together behind
:func:`main`.
"""

from readme_updater.cli import main

__all__ = ["main"]
