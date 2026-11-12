"""Regression tests for ``mas doctor``.

v0.4.0 shipped a build in which ``mas/doctor.py`` referenced the helper
``config_path()`` without importing it. The crash only surfaced when the
configuration file was *absent*, because the legacy-fallback branch is the
only place that calls the helper -- which is exactly the situation of a
clean first-time install. These tests pin that branch down.
"""

import os
import pathlib
import tempfile
import unittest

from mas.doctor import run_doctor


class DoctorCleanInstallTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mas-doctor-")
        self._saved = {
            name: os.environ.get(name)
            for name in ("MAS_CONFIG_PATH", "MAS_STATE_DIR")
        }
        os.environ["MAS_CONFIG_PATH"] = os.path.join(self.root, "nested", "config.json")
        os.environ["MAS_STATE_DIR"] = os.path.join(self.root, "state")

    def tearDown(self):
        for name, value in self._saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_doctor_runs_when_no_config_file_exists(self):
        """The missing-config branch must not raise NameError."""
        checks = run_doctor()
        by_name = {c["check"]: c for c in checks}
        self.assertIn("config", by_name)
        self.assertEqual(by_name["config"]["status"], "warn")
        self.assertTrue(any(c["check"] == "package" for c in checks))

    def test_doctor_reports_config_when_present(self):
        cfg = pathlib.Path(os.environ["MAS_CONFIG_PATH"])
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text("{}", encoding="utf-8")
        checks = run_doctor()
        by_name = {c["check"]: c for c in checks}
        self.assertEqual(by_name["config"]["status"], "ok")

    def test_doctor_never_raises_for_any_branch(self):
        """Every check must resolve to a status, never an exception."""
        statuses = {c["status"] for c in run_doctor()}
        self.assertTrue(statuses <= {"ok", "warn", "fail"})


if __name__ == "__main__":
    unittest.main()
