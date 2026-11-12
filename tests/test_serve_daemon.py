"""Tests for the persistent-service helper (``tools/serve_daemon.py``).

The helper is what turns "run `mas http` in a terminal" into "the service
survives a reboot", so the parts that matter are:

* the token is generated once and then *reused* (a rotating token would break
  every client the moment the service restarts);
* the token never ends up inside the launcher file;
* the launcher refuses to be produced with an empty token;
* the launcher always binds loopback, never a LAN address.
"""
# The session-wide test isolation must be active before this module is
# imported: importing a test module is what pulls in the code under test.
# ``unittest discover -s tests`` loads modules as top-level ``test_x`` and
# therefore never executes ``tests/__init__.py``, so the isolation cannot
# rely on the package hook alone - every test module imports it directly.
try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # loaded as a top-level module (unittest -s tests)
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401


import importlib.util
import os
import pathlib
import sys
import tempfile
import unittest

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "serve_daemon", _ROOT / "tools" / "serve_daemon.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ServeDaemonTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="mas-daemon-test-")
        self.addCleanup(self._tmp.cleanup)
        self.module = _load_module()
        # Redirect the token file into the temp dir.
        self._orig_token_file = self.module.TOKEN_FILE
        self.module.TOKEN_FILE = pathlib.Path(self._tmp.name) / "http_token.txt"
        self.addCleanup(self._restore)

    def _restore(self):
        self.module.TOKEN_FILE = self._orig_token_file

    def test_token_is_created_once_and_reused(self):
        first = self.module.read_or_create_token()
        self.assertTrue(first)
        self.assertEqual(len(first), 43)  # token_urlsafe(32)
        second = self.module.read_or_create_token()
        self.assertEqual(first, second)

    def test_token_file_contains_exactly_the_token(self):
        token = self.module.read_or_create_token()
        self.assertEqual(self.module.TOKEN_FILE.read_text(encoding="utf-8").strip(), token)

    def test_existing_token_is_preserved(self):
        self.module.TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.module.TOKEN_FILE.write_text("preexisting-token", encoding="utf-8")
        self.assertEqual(self.module.read_or_create_token(), "preexisting-token")

    def test_empty_token_file_is_regenerated(self):
        self.module.TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.module.TOKEN_FILE.write_text("   \n", encoding="utf-8")
        token = self.module.read_or_create_token()
        self.assertTrue(token.strip())
        self.assertNotEqual(token.strip(), "")

    def test_launcher_never_contains_the_token_on_windows(self):
        if os.name != "nt":
            self.skipTest("Windows launcher body only")
        token = self.module.read_or_create_token()
        body = self.module.launcher_body(8768)
        self.assertNotIn(token, body)
        self.assertIn("MAS_HTTP_TOKEN_FILE", body)
        self.assertIn("--host 127.0.0.1", body)

    def test_launcher_never_contains_the_token_on_posix(self):
        if os.name == "nt":
            self.skipTest("POSIX launcher body only")
        token = self.module.read_or_create_token()
        unit = self.module.launcher_body(8768)
        wrapper = self.module._shell_wrapper(8768)
        # Neither the unit file nor the wrapper may embed the token: both
        # read it from the 0600 token file at start-up.
        self.assertNotIn(token, unit)
        self.assertNotIn(token, wrapper)
        self.assertIn("127.0.0.1", wrapper)

    def test_posix_wrapper_reads_token_from_file(self):
        if os.name == "nt":
            self.skipTest("POSIX wrapper only")
        token = self.module.read_or_create_token()
        wrapper = self.module._shell_wrapper(8768)
        self.assertIn("http_token.txt", wrapper)
        self.assertIn("MAS_HTTP_TOKEN", wrapper)
        self.assertNotIn(token, wrapper)

    def test_launcher_binds_loopback_only(self):
        body = self.module.launcher_body(8768)
        self.assertIn("127.0.0.1", body)
        self.assertNotIn("0.0.0.0", body)

    def test_install_writes_launcher_and_token(self):
        original = self.module.launcher_path
        original_wrapper = self.module.wrapper_path
        target = pathlib.Path(self._tmp.name) / "launcher"
        self.module.launcher_path = lambda: target
        self.module.wrapper_path = lambda: pathlib.Path(self._tmp.name) / "run-http.sh"
        self.addCleanup(lambda: setattr(self.module, "launcher_path", original))
        self.addCleanup(lambda: setattr(self.module, "wrapper_path", original_wrapper))
        code = self.module.main(["install", "--port", "8768"])
        self.assertEqual(code, 0)
        self.assertTrue(target.exists())
        self.assertTrue(self.module.TOKEN_FILE.exists())
        if os.name != "nt":
            wrapper = pathlib.Path(self._tmp.name) / "run-http.sh"
            self.assertTrue(wrapper.exists())
            self.assertNotIn(self.module.read_or_create_token(), wrapper.read_text(encoding="utf-8"))

    def test_uninstall_removes_launcher(self):
        original = self.module.launcher_path
        original_wrapper = self.module.wrapper_path
        target = pathlib.Path(self._tmp.name) / "launcher"
        target.write_text("x", encoding="utf-8")
        self.module.launcher_path = lambda: target
        self.module.wrapper_path = lambda: pathlib.Path(self._tmp.name) / "run-http.sh"
        self.addCleanup(lambda: setattr(self.module, "launcher_path", original))
        self.addCleanup(lambda: setattr(self.module, "wrapper_path", original_wrapper))
        code = self.module.main(["uninstall"])
        self.assertEqual(code, 0)
        self.assertFalse(target.exists())

    def test_status_reports_state(self):
        original = self.module.launcher_path
        target = pathlib.Path(self._tmp.name) / "launcher"
        self.module.launcher_path = lambda: target
        self.addCleanup(lambda: setattr(self.module, "launcher_path", original))
        self.assertEqual(self.module.main(["status", "--port", "8768"]), 0)

    def test_port_is_open_detects_closed_port(self):
        # Port 1 is never bound on a normal machine.
        self.assertFalse(self.module.port_is_open("127.0.0.1", 1, timeout=0.3))


if __name__ == "__main__":
    unittest.main()
