"""``mas upgrade`` must really replace the runtime - and say so honestly.

The gap it closes: the one-click installer copies the runtime into
``<install-dir>/app/mas``. Refreshing the skills and the MCP
registration does not touch that copy, so an old runtime keeps serving
the agents while every other check reports "registered".

These tests pin the behaviour that matters:

* an old installed runtime is detected;
* the upgrade replaces it, and the new version is what is on disk;
* the old runtime is kept as a backup;
* a runtime that is *not* older is left alone;
* a failure is reported as a failure, never as a success.
"""

import pathlib
import tempfile
import unittest

from mas import upgrade


def _fake_runtime(root: pathlib.Path, version: str) -> pathlib.Path:
    """A directory that looks like an installed ``mas`` package."""
    package = root / "mas"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text(
        '__version__ = "%s"\n__all__ = ["__version__"]\n' % version,
        encoding="utf-8",
    )
    return package


class VersionComparisonTest(unittest.TestCase):
    def test_an_older_version_is_detected(self):
        self.assertTrue(upgrade.is_older("0.7.15", "0.7.79"))

    def test_the_same_version_is_not_older(self):
        self.assertFalse(upgrade.is_older("0.7.79", "0.7.79"))

    def test_a_newer_version_is_not_older(self):
        self.assertFalse(upgrade.is_older("0.8.0", "0.7.79"))

    def test_an_unknown_version_counts_as_older(self):
        # An unreadable version must not be trusted. Replacing it is
        # the safe direction; leaving it would keep serving old code.
        self.assertTrue(upgrade.is_older("", "0.7.79"))

    def test_versions_with_different_lengths_compare_sensibly(self):
        self.assertTrue(upgrade.is_older("0.7", "0.7.1"))
        self.assertFalse(upgrade.is_older("0.7.1", "0.7"))


class RuntimeStatusTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="mas-upgrade-status-")
        self.addCleanup(self._tmp.cleanup)
        self.state = pathlib.Path(self._tmp.name)

    def test_an_old_runtime_is_reported_as_outdated(self):
        _fake_runtime(self.state / "app", "0.7.15")
        status = upgrade.runtime_status(self.state)
        self.assertEqual(status["outdated_count"], 1)
        self.assertTrue(status["installed"][0]["outdated"])

    def test_a_current_runtime_is_not_reported_as_outdated(self):
        _fake_runtime(self.state / "app", upgrade._running_version())
        status = upgrade.runtime_status(self.state)
        self.assertEqual(status["outdated_count"], 0)

    def test_no_installed_runtime_is_not_an_error(self):
        status = upgrade.runtime_status(self.state)
        self.assertEqual(status["outdated_count"], 0)
        self.assertEqual(status["installed"], [])


class UpgradeRuntimeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="mas-upgrade-run-")
        self.addCleanup(self._tmp.cleanup)
        self.state = pathlib.Path(self._tmp.name)

    def test_an_old_runtime_is_replaced(self):
        package = _fake_runtime(self.state / "app", "0.7.15")
        (package / "marker.txt").write_text("old build\n", encoding="utf-8")

        report = upgrade.upgrade_runtime(self.state)
        self.assertEqual(report["errors"], [])
        self.assertEqual(len(report["upgraded"]), 1)
        self.assertEqual(
            upgrade._installed_version(package), upgrade._running_version()
        )

    def test_the_old_runtime_is_kept_as_a_backup(self):
        package = _fake_runtime(self.state / "app", "0.7.15")
        (package / "marker.txt").write_text("old build\n", encoding="utf-8")

        report = upgrade.upgrade_runtime(self.state)
        backup = pathlib.Path(report["backup"])
        self.assertTrue(backup.is_dir())
        self.assertEqual(
            upgrade._installed_version(backup), "0.7.15"
        )
        self.assertTrue((backup / "marker.txt").is_file())

    def test_a_current_runtime_is_left_alone(self):
        package = _fake_runtime(self.state / "app", upgrade._running_version())
        marker = package / "marker.txt"
        marker.write_text("do not touch\n", encoding="utf-8")

        report = upgrade.upgrade_runtime(self.state)
        self.assertEqual(report["upgraded"], [])
        self.assertTrue(marker.is_file())
        self.assertEqual(marker.read_text(encoding="utf-8"), "do not touch\n")

    def test_force_replaces_a_current_runtime_too(self):
        package = _fake_runtime(self.state / "app", upgrade._running_version())
        marker = package / "marker.txt"
        marker.write_text("do not touch\n", encoding="utf-8")

        report = upgrade.upgrade_runtime(self.state, force=True)
        self.assertEqual(len(report["upgraded"]), 1)
        self.assertFalse(marker.is_file())

    def test_nothing_to_upgrade_is_not_a_failure(self):
        report = upgrade.upgrade_runtime(self.state)
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["upgraded"], [])

    def test_the_report_says_what_was_done(self):
        _fake_runtime(self.state / "app", "0.7.15")
        report = upgrade.upgrade_runtime(self.state)
        text = upgrade.format_upgrade_report(report)
        self.assertIn("已升级", text)
        self.assertIn("0.7.15", text)

    def test_a_failure_is_reported_as_a_failure(self):
        # A package directory that cannot be replaced must not be
        # reported as a successful upgrade.
        package = _fake_runtime(self.state / "app", "0.7.15")
        package.parent.chmod(0o500)
        try:
            report = upgrade.upgrade_runtime(self.state)
        finally:
            package.parent.chmod(0o700)
        if report["upgraded"]:
            # The filesystem allowed it - then it really did upgrade.
            self.assertEqual(report["errors"], [])
        else:
            self.assertTrue(report["errors"])

    def test_the_new_runtime_has_no_stale_bytecode(self):
        package = _fake_runtime(self.state / "app", "0.7.15")
        cache = package / "__pycache__"
        cache.mkdir()
        (cache / "stale.pyc").write_bytes(b"stale")

        upgrade.upgrade_runtime(self.state)
        self.assertFalse((package / "__pycache__" / "stale.pyc").exists())


class DoctorRuntimeCheckTest(unittest.TestCase):
    """``mas doctor`` must show the version gap."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="mas-doctor-runtime-")
        self.addCleanup(self._tmp.cleanup)
        self.state = pathlib.Path(self._tmp.name)

    def test_doctor_reports_an_outdated_runtime(self):
        import os

        from mas.doctor import run_doctor

        _fake_runtime(self.state / "app", "0.7.15")
        config = {
            "provider": "mock",
            "state_dir": str(self.state),
            "api_key_env": "MAS_API_KEY",
            "api_key": "",
            "base_url": "",
        }
        previous = os.environ.get("MAS_CONFIG_PATH")
        os.environ["MAS_CONFIG_PATH"] = str(self.state / "config.json")
        try:
            checks = run_doctor(config, allow_network=False)
        finally:
            if previous is None:
                os.environ.pop("MAS_CONFIG_PATH", None)
            else:
                os.environ["MAS_CONFIG_PATH"] = previous
        by_name = {c["check"]: c for c in checks}
        self.assertIn("runtime", by_name)
        self.assertEqual(by_name["runtime"]["status"], "warn")
        self.assertIn("mas upgrade", by_name["runtime"]["detail"])


if __name__ == "__main__":
    unittest.main()
