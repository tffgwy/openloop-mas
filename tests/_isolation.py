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
# Force the offline mock backend so no test can reach a real endpoint, no
# matter what the developer's own configuration says.
os.environ["MAS_PROVIDER"] = "mock"
for name in ("MAS_MODEL", "MAS_BASE_URL", "MAS_API_KEY"):
    os.environ.pop(name, None)
pathlib.Path(_STATE_DIR).mkdir(parents=True, exist_ok=True)
os.environ["MAS_TEST_ISOLATION"] = "1"


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


#: True once the isolation has been installed. Tests assert this so a
#: regression that stops the isolation from loading is caught loudly
#: instead of silently making the suite hit the developer's real
#: endpoint.
ISOLATION_ACTIVE = True

atexit.register(restore)
