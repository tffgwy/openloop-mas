"""Pytest entry point for the session-wide test isolation.

The isolation itself lives in :mod:`tests._isolation` so that the
standard-library ``unittest`` runner picks it up as well (via
``tests/__init__.py``). Pytest imports this file automatically.
"""

from ._isolation import pytest_sessionfinish  # noqa: F401
