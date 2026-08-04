"""Count lines added in local, private repositories.

Some past work lives in repositories that will never be on GitHub. When
their paths are given in ``README_UPDATER_LOCAL_REPOS`` (an ``os.pathsep``
separated list), each is read with local ``git`` and folded into the
**all-time** totals only: being finished work, it yields no new commits
and so never reaches the rolling recent window. Only aggregated line
counts enter the cache, behind an opaque key, so nothing about a private
repository is published.
"""
