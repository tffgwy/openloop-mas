"""Regression tests for the session-wide test isolation.

The isolation used to be installed by ``tests/__init__.py`` only. Under
``python -m unittest discover -s tests`` the modules are loaded as
top-level ``test_x`` names, the package ``__init__`` never runs, and the
whole suite silently fell back to the developer's real ``~/.mas``
configuration - which made the suite talk to a live endpoint and fail
intermittently. These tests assert the invariants so that a future
refactor cannot quietly turn the isolation off again.
"""

try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # loaded as a top-level module (unittest -s tests)
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401


import os
import pathlib
import unittest

from mas.config import load_config, user_config_path


class TestIsolationActive(unittest.TestCase):
    def test_isolation_module_is_loaded(self):
        self.assertTrue(ISOLATION_ACTIVE)

    def test_provider_is_forced_to_mock(self):
        self.assertEqual(os.environ.get("MAS_PROVIDER"), "mock")

    def test_config_path_is_isolated(self):
        config_path = os.environ.get("MAS_CONFIG_PATH")
        self.assertTrue(config_path)
        resolved = pathlib.Path(config_path).resolve()
        real = pathlib.Path(os.path.expanduser("~/.mas")).resolve()
        self.assertNotEqual(resolved, real / "config.json")
        self.assertEqual(resolved, user_config_path())

    def test_state_dir_is_isolated(self):
        state_dir = os.environ.get("MAS_STATE_DIR")
        self.assertTrue(state_dir)
        resolved = pathlib.Path(state_dir).resolve()
        real = pathlib.Path(os.path.expanduser("~/.mas")).resolve()
        self.assertNotEqual(resolved, real)
        self.assertTrue(resolved.exists())

    def test_load_config_does_not_read_the_real_user_config(self):
        config = load_config()
        self.assertEqual(config["provider"], "mock")
        self.assertEqual(config["model"], "mock-1")

    def test_real_user_config_is_not_the_isolated_one(self):
        real_config = pathlib.Path(os.path.expanduser("~/.mas/config.json"))
        if real_config.exists():
            self.assertNotEqual(real_config.resolve(), user_config_path())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
