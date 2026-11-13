"""Regression tests for ``mas doctor``.

v0.4.0 shipped a build in which ``mas/doctor.py`` referenced the helper
``config_path()`` without importing it. The crash only surfaced when the
configuration file was *absent*, because the legacy-fallback branch is the
only place that calls the helper -- which is exactly the situation of a
clean first-time install. These tests pin that branch down.
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


class DoctorSkillsSourceTest(unittest.TestCase):
    """The packaged skills must be present in the installed runtime.

    If they are not, ``mas install`` cannot do its job - the user gets a
    successful-looking install with no skills at all.
    """

    def test_the_packaged_skills_are_reported_as_present(self):
        checks = run_doctor()
        by_name = {c["check"]: c for c in checks}
        self.assertIn("skills_source", by_name)
        self.assertEqual(by_name["skills_source"]["status"], "ok")

    def test_a_missing_skill_is_a_failure_not_a_warning(self):
        from mas import installer

        original = installer.PACKAGED_SKILLS
        try:
            installer.PACKAGED_SKILLS = ("mas-loop", "not-a-real-skill")
            checks = run_doctor()
        finally:
            installer.PACKAGED_SKILLS = original
        by_name = {c["check"]: c for c in checks}
        self.assertEqual(by_name["skills_source"]["status"], "fail")
        self.assertIn("not-a-real-skill", by_name["skills_source"]["detail"])


if __name__ == "__main__":
    unittest.main()
