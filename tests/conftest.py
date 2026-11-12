"""Session-wide test isolation.

Without this module the test-suite reads the *real* user configuration in
``~/.mas/config.json``. That worked while the user configuration happened to
be the offline mock, but as soon as it pointed at a live endpoint the HTTP
tests started making real network calls and timing out.

Isolating both the configuration file and the state directory for the whole
session keeps the suite hermetic: no test can reach the developer's real
ledger, and no test can pick up the developer's real provider settings.
"""

import os
import pathlib
import shutil
import tempfile

_SESSION_ROOT = tempfile.mkdtemp(prefix="mas-test-session-")
_CONFIG_PATH = os.path.join(_SESSION_ROOT, "config.json")
_STATE_DIR = os.path.join(_SESSION_ROOT, "state")

_SAVED = {
    name: os.environ.get(name)
    for name in ("MAS_CONFIG_PATH", "MAS_STATE_DIR", "MAS_PROVIDER", "MAS_MODEL", "MAS_API_KEY")
}

os.environ["MAS_CONFIG_PATH"] = _CONFIG_PATH
os.environ["MAS_STATE_DIR"] = _STATE_DIR
for name in ("MAS_PROVIDER", "MAS_MODEL", "MAS_API_KEY"):
    os.environ.pop(name, None)
pathlib.Path(_STATE_DIR).mkdir(parents=True, exist_ok=True)


def pytest_sessionfinish(session, exitstatus):
    for name, value in _SAVED.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    shutil.rmtree(_SESSION_ROOT, ignore_errors=True)
