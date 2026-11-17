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


class _FakeResponse:
    """A stand-in for the object ``urlopen`` returns."""

    def __init__(self, payload):
        self._payload = payload

    def read(self):
        import json

        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


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


class DoctorPypiPresenceTest(unittest.TestCase):
    """``mas doctor`` must say whether the package name works on PyPI.

    A beginner who is told "pip install openloop-mas" needs to know,
    before they type it, whether that name resolves. And when it does
    not, the answer has to include somewhere else to go - a dead end
    is not an answer.
    """

    def _run(self, monkey_payload):
        calls = {"count": 0}

        class _Response:
            def __init__(self, payload):
                self._payload = payload

            def read(self):
                import json

                return json.dumps(self._payload).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_urlopen(url, timeout=None):
            calls["count"] += 1
            return monkey_payload(calls["count"])

        import urllib.request

        real = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen
        try:
            return run_doctor()
        finally:
            urllib.request.urlopen = real

    def test_a_published_name_is_reported_as_installable(self):
        checks = self._run(
            lambda n: _FakeResponse({"info": {"version": "0.7.78"}})
        )
        by_name = {c["check"]: c for c in checks}
        self.assertIn("pypi", by_name)
        self.assertEqual(by_name["pypi"]["status"], "ok")
        self.assertIn("pip install openloop-mas", by_name["pypi"]["detail"])
        self.assertIn("0.7.78", by_name["pypi"]["detail"])

    def test_a_name_that_does_not_exist_points_somewhere_else(self):
        import urllib.error

        def missing(_n):
            raise urllib.error.HTTPError(
                "https://pypi.org/pypi/openloop-mas/json",
                404,
                "Not Found",
                None,
                None,
            )

        checks = self._run(missing)
        by_name = {c["check"]: c for c in checks}
        self.assertEqual(by_name["pypi"]["status"], "warn")
        self.assertIn("pip install openloop-mas", by_name["pypi"]["detail"])
        self.assertIn("releases/latest", by_name["pypi"]["detail"])

    def test_a_failed_lookup_is_not_reported_as_not_published(self):
        """A network failure says nothing about the package.

        Reporting "not published" because the lookup failed would be a
        lie with a confident voice: the name might be perfectly fine and
        the network might be down.
        """

        def broken(_n):
            raise OSError("the network said no")

        checks = self._run(broken)
        by_name = {c["check"]: c for c in checks}
        self.assertEqual(by_name["pypi"]["status"], "warn")
        detail = by_name["pypi"]["detail"]
        self.assertIn("失败", detail)
        self.assertNotIn("还没有", detail)

    def test_the_lookup_can_be_skipped(self):
        checks = run_doctor(allow_network=False)
        by_name = {c["check"]: c for c in checks}
        self.assertEqual(by_name["pypi"]["status"], "warn")
        self.assertIn("--no-network", by_name["pypi"]["detail"])


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


class DoctorSkillsSyncTest(unittest.TestCase):
    """``mas doctor`` must see skill drift, not just skill presence.

    Presence alone was not enough: the 0.6.0 copies survived a 0.7.0
    upgrade with every check green, because nothing compared the
    installed content with the packaged copy.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mas-doctor-sync-")
        self._saved = {
            name: os.environ.get(name)
            for name in ("MAS_CONFIG_PATH", "MAS_STATE_DIR")
        }
        os.environ["MAS_CONFIG_PATH"] = os.path.join(self.root, "config.json")
        os.environ["MAS_STATE_DIR"] = os.path.join(self.root, "state")
        from mas import installer

        self._orig_home = installer._home
        installer._home = lambda: pathlib.Path(self.root)
        self.addCleanup(lambda: setattr(installer, "_home", self._orig_home))

    def tearDown(self):
        for name, value in self._saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_the_sync_check_exists(self):
        checks = run_doctor()
        by_name = {c["check"]: c for c in checks}
        self.assertIn("skills_sync", by_name)

    def test_drift_is_reported(self):
        from mas import installer

        installer.install_skills()
        target = pathlib.Path(self.root) / ".codex" / "skills" / "mas-loop" / "SKILL.md"
        target.write_text("edited by hand\n", encoding="utf-8")
        checks = run_doctor()
        by_name = {c["check"]: c for c in checks}
        self.assertEqual(by_name["skills_sync"]["status"], "warn")
        self.assertIn("不一致", by_name["skills_sync"]["detail"])

    def test_a_fresh_install_reports_no_drift(self):
        from mas import installer

        installer.install_skills()
        checks = run_doctor()
        by_name = {c["check"]: c for c in checks}
        self.assertEqual(by_name["skills_sync"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
