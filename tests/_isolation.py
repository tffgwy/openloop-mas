"""Session-wide test isolation.

Without this module the test-suite reads the *real* user configuration in
``~/.mas/config.json``. That worked while the user configuration happened to
be the offline mock, but as soon as it pointed at a live endpoint the HTTP
tests started making real network calls and timing out - intermittently,
because the timeout depends on how slow the endpoint is.

Every test module imports this module directly, before it imports
anything under test. That matters because ``python -m unittest discover -s
tests`` loads the modules as *top-level* ``test_x`` names and therefore
never executes ``tests/__init__.py`` - a package-level hook alone silently
does nothing, and the suite ends up talking to the developer's real
endpoint. The ``try: from ._isolation import ...`` / ``except ImportError:
from _isolation import ...`` pair in each test module covers both layouts
(package discovery and top-level discovery) as well as pytest.

Isolating both the configuration file and the state directory for the whole
session keeps the suite hermetic: no test can reach the developer's real
ledger, and no test can pick up the developer's real provider settings.
"""

import atexit
import os
import pathlib
import shutil
import tempfile
import unittest

#: Guards against a second copy of this module being imported under a
#: different name (``tests._isolation`` vs. a top-level ``_isolation``).
#: A second import would snapshot the *already isolated* environment and its
#: ``restore()`` would then wipe the isolation on the way out.
_ALREADY_INSTALLED = os.environ.get("MAS_TEST_ISOLATION") == "1"

_SESSION_ROOT = tempfile.mkdtemp(prefix="mas-test-session-")
_CONFIG_PATH = os.path.join(_SESSION_ROOT, "config.json")
_STATE_DIR = os.path.join(_SESSION_ROOT, "state")

_SAVED = {
    name: os.environ.get(name)
    for name in (
        "MAS_CONFIG_PATH",
        "MAS_STATE_DIR",
        "MAS_PROVIDER",
        "MAS_MODEL",
        "MAS_BASE_URL",
        "MAS_API_KEY",
    )
}

os.environ["MAS_CONFIG_PATH"] = _CONFIG_PATH
os.environ["MAS_STATE_DIR"] = _STATE_DIR
# The session's own state directory is the baseline every test must be
# restored to. Several test modules point MAS_STATE_DIR at a directory of
# their own and used to leave it there, so a later test read somebody
# else's throw-away directory as if it were the user's configuration -
# and a test that checks "the configuration never names a disposable
# directory" saw exactly that.
SESSION_STATE_DIR = _STATE_DIR
# Force the offline mock backend so no test can reach a real endpoint, no
# matter what the developer's own configuration says.
os.environ["MAS_PROVIDER"] = "mock"
for name in ("MAS_MODEL", "MAS_BASE_URL", "MAS_API_KEY"):
    os.environ.pop(name, None)
pathlib.Path(_STATE_DIR).mkdir(parents=True, exist_ok=True)
os.environ["MAS_TEST_ISOLATION"] = "1"


def restore_environment() -> None:
    """Put the session's own environment values back.

    Several test modules point ``MAS_STATE_DIR`` at a directory of their
    own so the run writes its ledger somewhere disposable. When one of
    them forgets to put it back, the next test reads that throw-away
    directory as if it were the user's configuration - and a check like
    "the configuration never names a disposable directory" fails for a
    reason that has nothing to do with the code under test.

    This helper is the session-wide safety net; each module's own
    ``tearDown`` is the specific fix. Either alone would do, and both
    together mean a module that forgets cannot leak into the next one.
    """
    os.environ["MAS_CONFIG_PATH"] = _CONFIG_PATH
    os.environ["MAS_STATE_DIR"] = _STATE_DIR
    os.environ["MAS_PROVIDER"] = "mock"
    for name in ("MAS_MODEL", "MAS_BASE_URL", "MAS_API_KEY"):
        os.environ.pop(name, None)


def restore() -> None:
    if not _ALREADY_INSTALLED:
        for name, value in _SAVED.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        os.environ.pop("MAS_TEST_ISOLATION", None)
    shutil.rmtree(_SESSION_ROOT, ignore_errors=True)


def pytest_sessionfinish(session, exitstatus):  # pragma: no cover - pytest hook
    restore()


def pytest_runtest_teardown(item, nextitem):  # pragma: no cover - pytest hook
    """Restore the environment after every single test.

    A class-level ``setUpClass`` that points ``MAS_STATE_DIR`` somewhere
    disposable and forgets to undo it in ``tearDownClass`` leaks past
    the module-scoped fixture, because the class teardown runs after
    the last function-scoped fixture has already restored things.
    """
    restore_environment()


#: True once the isolation has been installed. Tests assert this so a
#: regression that stops the isolation from loading is caught loudly
#: instead of silently making the suite hit the developer's real
#: endpoint.
def _install_unittest_environment_guard() -> None:
    """Make every ``unittest`` test leave the environment as it found it.

    ``python -m unittest discover -s tests`` does not load
    ``tests/conftest.py``, so the pytest fixtures cannot help there. The
    obvious fix - a base class every test module inherits from - only
    works for the modules that remember to inherit from it, and a
    subclass that overrides ``setUp`` without calling ``super()`` skips
    it entirely.

    ``TestCase.run()`` is the one entry point every single test goes
    through, whatever it does in ``setUp``. Wrapping it closes the gap
    for good: a test that points ``MAS_STATE_DIR`` at a throw-away
    directory and forgets to undo it can no longer leak into the next
    test, whether the suite runs under pytest or under the
    standard-library runner.
    """
    if getattr(unittest.TestCase.run, "_mas_guarded", False):
        return

    original_run = unittest.TestCase.run
    saved_names = ("MAS_STATE_DIR", "MAS_CONFIG_PATH")

    def guarded_run(self, result=None):
        saved = {name: os.environ.get(name) for name in saved_names}
        try:
            return original_run(self, result)
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    guarded_run._mas_guarded = True
    unittest.TestCase.run = guarded_run


_install_unittest_environment_guard()


ISOLATION_ACTIVE = True

atexit.register(restore)


#: The result of a full self-test run, keyed by the iteration count.
#:
#: Ten test modules each want to assert "the whole gate still passes".
#: Each of them ran the *entire* suite to say so, so the suite was run
#: ten times over - about ten minutes of the CI job spent proving the
#: same thing ten times, and every minute of it grew with the number
#: of checks. Adding a fifty-sixth check made every CI job slower for
#: no new information.
#:
#: The run is the same run: the suite does not look at the clock or
#: at the random seed, and it is hermetic (it runs against the mock
#: backend in a temporary state directory). So one run per iteration
#: count is enough, and the rest read the same result.
_SELFTEST_CACHE: dict = {}


def full_selftest(iterations: int = 3) -> dict:
    """The whole gate, run once per session for each iteration count.

    The caller gets the real result of a real run - the first caller
    triggers it, everybody else reads the same outcome. The result is
    a copy, so a test that pokes at it cannot change what the next
    test sees.

    A request for *fewer* iterations is served by a run that did
    *more*. A gate that passed two hundred rounds did not fail in any
    of them, so it did not fail in the first one either: the weaker
    request is answered by the stronger evidence, and the suite is
    run once instead of once per iteration count.

    The default is three rounds, which is what the whole-suite
    assertions actually want: the strongest run the suite offers.
    """
    key = int(iterations)
    # The strongest run this session has already made.
    if _SELFTEST_CACHE:
        best = max(_SELFTEST_CACHE)
        if best >= key:
            key = best
    if key not in _SELFTEST_CACHE:
        from mas.selftest import run_selftest

        _SELFTEST_CACHE[key] = run_selftest(iterations=key)
    result = _SELFTEST_CACHE[key]
    return {
        "ok": result.get("ok"),
        "failed": list(result.get("failed") or []),
        "skipped": list(result.get("skipped") or []),
        "cut": list(result.get("cut") or []),
        "complete": result.get("complete", True),
        "iterations": result.get("iterations"),
        "checks": list(result.get("checks") or []),
        "passed": list(result.get("passed") or []),
        "machine_state": dict(result.get("machine_state") or {}),
    }
