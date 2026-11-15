"""Pytest entry point for the session-wide test isolation.

The isolation itself lives in :mod:`tests._isolation` so that the
standard-library ``unittest`` runner picks it up as well (via
``tests/__init__.py``). Pytest imports this file automatically.
"""

import os

import pytest

from ._isolation import pytest_sessionfinish  # noqa: F401


@pytest.fixture(autouse=True, scope="module")
def _restore_the_environment_between_modules():
    """Restore the session environment between *modules* as well.

    A test class may point ``MAS_STATE_DIR`` at a directory of its own
    in ``setUpClass`` and forget to put it back in ``tearDownClass``.
    The function-scoped fixture below cannot see that leak, because the
    class-level teardown happens after the last function fixture has
    already restored the environment. The module-scoped fixture closes
    the gap.
    """
    saved = {
        name: os.environ.get(name)
        for name in ("MAS_STATE_DIR", "MAS_CONFIG_PATH")
    }
    yield
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


@pytest.fixture(autouse=True)
def _restore_the_session_environment():
    """Put the session's own environment back after every test.

    Several test modules point ``MAS_STATE_DIR`` at a directory of their
    own so the run writes its ledger somewhere disposable. When one of
    them forgot to restore it, the next test read that throw-away
    directory as if it were the user's configuration - and a check like
    "the configuration never names a disposable directory" failed for a
    reason that had nothing to do with the code under test.

    The fixture is the belt; the ``tearDown`` in each module is the
    braces. Either alone would do, and both together mean a module that
    forgets cannot leak into the next one.
    """
    saved = {
        name: os.environ.get(name)
        for name in ("MAS_STATE_DIR", "MAS_CONFIG_PATH")
    }
    yield
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
