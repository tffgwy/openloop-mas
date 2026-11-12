"""Session-wide test isolation.

Without this module the test-suite reads the *real* user configuration in
``~/.mas/config.json``. That worked while the user configuration happened to
be the offline mock, but as soon as it pointed at a live endpoint the HTTP
tests started making real network calls and timing out - intermittently,
because the timeout depends on how slow the endpoint is.

This module is imported by ``tests/__init__.py`` so the isolation applies
to *every* runner: ``python -m unittest discover -s tests`` as well as
``pytest``. Previously the isolation lived in ``tests/conftest.py``, which
pytest loads automatically and unittest does not - so the unittest run (the
one CI uses) silently talked to the developer's real endpoint.

Isolating both the configuration file and the state directory for the whole
session keeps the suite hermetic: no test can reach the developer's real
ledger, and no test can pick up the developer's real provider settings.
"""

import atexit
import os
import pathlib
import shutil
import tempfile

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
# Force the offline mock backend so no test can reach a real endpoint, no
# matter what the developer's own configuration says.
os.environ["MAS_PROVIDER"] = "mock"
for name in ("MAS_MODEL", "MAS_BASE_URL", "MAS_API_KEY"):
    os.environ.pop(name, None)
pathlib.Path(_STATE_DIR).mkdir(parents=True, exist_ok=True)


def restore() -> None:
    for name, value in _SAVED.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    shutil.rmtree(_SESSION_ROOT, ignore_errors=True)


def pytest_sessionfinish(session, exitstatus):  # pragma: no cover - pytest hook
    restore()


atexit.register(restore)
