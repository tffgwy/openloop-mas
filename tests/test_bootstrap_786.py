# -*- coding: utf-8 -*-
"""The twenty-sixth gate: the one-click install survives a machine without Python.

The defect this version fixes
-----------------------------
The README promised the first install path as *"one script, cross-platform,
no Python environment preparation"*. Both installers began by looking for
Python 3.10+, and when they did not find it they printed a sentence that
sent the user to a browser to install an interpreter by hand.

That sentence was the whole beginner's first step. Everything after it
really is zero-dependency - the runtime is pure standard library, the
wheel is unpacked by hand, no pip, no git, no compiler - so the bootstrap
was the only part that still asked for human labour.

These tests hold three things:

* the module's rules are right (versions, plans, refusal to guess);
* both installers *have* a bootstrap path and an opt-out, and neither
  can still dead-end on a missing Python;
* the twenty-sixth gate notices when the copies drift apart.
"""
try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # loaded as a top-level module (unittest -s tests)
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401


import pathlib
import unittest

from mas import bootstrap
from mas.selftest import CHECKS
from mas.selftest_bootstrap import (
    BOOTSTRAP_OBSERVATIONS,
    INSTALLERS,
    check_the_one_click_install_survives_a_machine_without_python,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


class BootstrapRulesTest(unittest.TestCase):
    """The rules the installers have to follow."""

    def test_the_minimum_version_is_readable(self):
        self.assertEqual(bootstrap.MINIMUM_PYTHON, (3, 10))

    def test_a_version_string_reads_into_a_tuple(self):
        self.assertEqual(bootstrap.version_tuple("3.12.8"), (3, 12, 8))
        self.assertEqual(bootstrap.version_tuple("Python 3.11.2"), (3, 11, 2))
        self.assertEqual(bootstrap.version_tuple("3.10"), (3, 10, 0))

    def test_an_unreadable_version_is_not_waved_through(self):
        # A version that cannot be read is not "probably fine" - it is
        # below every supported version, so it is refused.
        self.assertEqual(bootstrap.version_tuple("not a version"), (0, 0, 0))
        self.assertEqual(bootstrap.version_tuple(""), (0, 0, 0))
        self.assertEqual(bootstrap.version_tuple(None), (0, 0, 0))
        self.assertFalse(bootstrap.is_supported("not a version"))
        self.assertFalse(bootstrap.is_supported(""))

    def test_the_supported_range_is_right(self):
        self.assertFalse(bootstrap.is_supported("3.9.18"))
        self.assertTrue(bootstrap.is_supported("3.10.0"))
        self.assertTrue(bootstrap.is_supported("3.12.8"))
        self.assertTrue(bootstrap.is_supported("3.13.0"))

    def test_the_interpreter_is_asked_not_guessed(self):
        # A file that is not an interpreter at all must not be mistaken
        # for one, whatever its name says.
        self.assertEqual(bootstrap.interpreter_version(""), (0, 0, 0))
        self.assertEqual(
            bootstrap.interpreter_version(str(REPO_ROOT / "README.md")), (0, 0, 0)
        )

    def test_a_real_interpreter_reports_its_version(self):
        import sys

        version = bootstrap.interpreter_version(sys.executable)
        self.assertGreaterEqual(version, (3, 8))
        self.assertTrue(bootstrap.is_supported(version))

    def test_an_explicit_interpreter_wins(self):
        import sys

        found = bootstrap.find_interpreter([sys.executable])
        self.assertEqual(found, sys.executable)
        self.assertIsNone(bootstrap.find_interpreter([]))
        self.assertIsNone(bootstrap.find_interpreter(["definitely-not-python"]))

    def test_the_bootstrap_version_is_pinned_not_latest(self):
        # "latest" would be a bootstrap whose behaviour changes without
        # anybody changing the code.
        self.assertEqual(bootstrap.BOOTSTRAP_PYTHON_VERSION, "3.12.8")
        self.assertNotIn("latest", bootstrap.BOOTSTRAP_PYTHON_VERSION.lower())
        self.assertTrue(
            bootstrap.is_supported(bootstrap.BOOTSTRAP_PYTHON_VERSION)
        )

    def test_the_series_matches_the_version(self):
        self.assertEqual(
            bootstrap.BOOTSTRAP_PYTHON_SERIES,
            ".".join(bootstrap.BOOTSTRAP_PYTHON_VERSION.split(".")[:2]),
        )

    def test_the_download_url_names_the_pinned_version(self):
        url = bootstrap.windows_bootstrap_url()
        self.assertIn(bootstrap.BOOTSTRAP_PYTHON_VERSION, url)
        self.assertTrue(url.startswith("https://www.python.org/ftp/python/"))
        self.assertTrue(url.endswith("amd64.exe"))

    def test_the_posix_hint_gives_exact_commands(self):
        hint = bootstrap.posix_bootstrap_hint()
        self.assertIn("brew install python@3.12", hint)
        self.assertIn("apt install python3.12", hint)
        self.assertIn("dnf install python3.12", hint)


class BootstrapPlanTest(unittest.TestCase):
    """The three outcomes a plan can have."""

    def test_a_found_interpreter_is_ready(self):
        plan = bootstrap.bootstrap_plan("windows", "C:/Python312/python.exe")
        self.assertEqual(plan["action"], "ready")
        self.assertEqual(plan["interpreter"], "C:/Python312/python.exe")

    def test_no_interpreter_is_needed(self):
        plan = bootstrap.bootstrap_plan("windows", None)
        self.assertEqual(plan["action"], "needed")
        self.assertEqual(plan["url"], bootstrap.windows_bootstrap_url())

    def test_a_refused_bootstrap_is_skipped(self):
        plan = bootstrap.bootstrap_plan("windows", None, True)
        self.assertEqual(plan["action"], "skipped")
        self.assertEqual(plan["url"], "")

    def test_the_posix_plan_carries_the_hint(self):
        plan = bootstrap.bootstrap_plan("linux", None)
        self.assertEqual(plan["action"], "needed")
        self.assertIn("brew install python@3.12", plan["hint"])

    def test_the_plan_says_what_it_is_doing(self):
        self.assertIn("找到可用的 Python", bootstrap.describe(bootstrap.bootstrap_plan("windows", "x")))
        self.assertIn("自动装一个", bootstrap.describe(bootstrap.bootstrap_plan("windows", None)))
        self.assertIn("按你的要求", bootstrap.describe(bootstrap.bootstrap_plan("windows", None, True)))

    def test_the_status_carries_the_text(self):
        status = bootstrap.bootstrap_status("windows", None)
        self.assertIn("text", status)
        self.assertEqual(status["action"], "needed")


class BootstrapLookupTest(unittest.TestCase):
    """Where a freshly installed interpreter is looked for."""

    def test_an_empty_place_finds_nothing(self):
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(bootstrap.installed_python_dirs(folder), [])
            self.assertIsNone(bootstrap.find_just_installed(folder))

    def test_the_windows_place_is_the_users_own_programs_folder(self):
        self.assertIn("Programs", str(bootstrap.WINDOWS_PER_USER_PYTHON))
        self.assertIn("Python", str(bootstrap.WINDOWS_PER_USER_PYTHON))


class InstallerBehaviourTest(unittest.TestCase):
    """What the two installers actually do when Python is missing."""

    def _installer(self, name):
        path = REPO_ROOT / name
        self.assertTrue(path.exists(), "%s is missing" % name)
        return path.read_text(encoding="utf-8")

    def test_both_installers_exist(self):
        for name, _flag in INSTALLERS:
            self.assertTrue((REPO_ROOT / name).exists(), name)

    def test_neither_installer_dead_ends_on_a_missing_python(self):
        # The old defect, verbatim: the installer found no Python, told
        # the user to install it, and stopped. That sentence has to be
        # gone - or at least gone as the *only* outcome.
        for name, _flag in INSTALLERS:
            text = self._installer(name)
            self.assertNotIn(
                "Python 3.10+ not found. Install Python from",
                text,
                "%s still dead-ends on a missing Python" % name,
            )
            self.assertNotIn(
                "Python 3.10+ not found. Install it, then re-run this script.\"",
                text,
                "%s still dead-ends on a missing Python" % name,
            )

    def test_both_installers_have_a_bootstrap_path(self):
        ps1 = self._installer("install.ps1")
        sh = self._installer("install.sh")
        self.assertIn("python.org/ftp/python", ps1)
        self.assertIn("MAS_BOOTSTRAP_URL", ps1)
        for token in ("brew install python", "apt-get install", "dnf install"):
            self.assertIn(token, sh)

    def test_both_installers_can_be_told_not_to_bootstrap(self):
        ps1 = self._installer("install.ps1")
        sh = self._installer("install.sh")
        self.assertIn("-NoBootstrap", ps1)
        self.assertIn("MAS_NO_BOOTSTRAP", sh)

    def test_the_windows_installer_installs_for_the_current_user(self):
        ps1 = self._installer("install.ps1")
        # No administrator rights, no PATH change, no launcher: the
        # installer changes as little of the machine as it can.
        self.assertIn("InstallAllUsers=0", ps1)
        self.assertIn("PrependPath=0", ps1)
        self.assertIn("Include_launcher=0", ps1)
        self.assertIn("/quiet", ps1)

    def test_the_windows_installer_finds_the_python_it_just_installed(self):
        # The per-user install is not on PATH, so the installer has to
        # look where it put it - otherwise it would report "Python not
        # found" right after having installed one.
        ps1 = self._installer("install.ps1")
        self.assertIn("Find-MasBootstrappedPython", ps1)
        self.assertIn("Programs\\Python", ps1)

    def test_the_installers_pin_the_same_python_the_module_names(self):
        ps1 = self._installer("install.ps1")
        self.assertIn('$MAS_BOOTSTRAP_PYTHON = "%s"' % bootstrap.BOOTSTRAP_PYTHON_VERSION, ps1)
        sh = self._installer("install.sh")
        self.assertIn("python@%s" % bootstrap.BOOTSTRAP_PYTHON_SERIES, sh)
        self.assertIn("python%s" % bootstrap.BOOTSTRAP_PYTHON_SERIES, sh)

    def test_both_installers_ask_for_the_same_minimum_version(self):
        for name, _flag in INSTALLERS:
            text = self._installer(name)
            self.assertIn(
                "(3,10)",
                text.replace(" ", ""),
                "%s does not ask for Python 3.10+" % name,
            )


class TheGateTest(unittest.TestCase):
    """The twenty-sixth gate itself."""

    def test_the_gate_is_registered(self):
        names = [getattr(c, "CHECK_NAME", "") for c in CHECKS]
        self.assertIn("bootstrap_path", names)

    def test_the_gate_passes_on_this_tree(self):
        result = check_the_one_click_install_survives_a_machine_without_python(iterations=2)
        self.assertTrue(result["ok"], result["failures"])
        self.assertEqual(result["name"], "bootstrap_path")
        self.assertFalse(result["skipped"])

    def test_the_gate_reports_every_observation(self):
        self.assertEqual(len(BOOTSTRAP_OBSERVATIONS), 8)

    def test_the_gate_notices_a_drift(self):
        # The gate has to be able to fail. A drift in the Windows
        # installer is fed to the comparison, and the gate has to say so.
        import mas.selftest_bootstrap as gate_module

        real_root = gate_module.REPO_ROOT
        facts = gate_module._bootstrap_observations(real_root)
        drifted = dict(facts)
        installers = dict(drifted.get("installers") or {})
        drifted["installers"] = {
            name: text.replace("3.12.8", "3.11.9").replace("python3.12", "python3.9")
            for name, text in installers.items()
        }
        drifted["windows_url"] = gate_module._expanded_windows_url(
            drifted["installers"].get("install.ps1", "")
        )
        drifted["installer_bootstrap_versions"] = {
            name: sorted(set(__import__("re").findall(r"(\d+\.\d+\.\d+)", text)))
            for name, text in drifted["installers"].items()
        }
        drifted["installer_series"] = {
            name: sorted(set(__import__("re").findall(r"python@?(\d+\.\d+)", text)))
            for name, text in drifted["installers"].items()
        }
        broken = gate_module._bootstrap_verdicts(drifted)
        self.assertFalse(
            broken["the installers and the module agree on the bootstrap version"]
        )
        self.assertFalse(
            broken["the Windows installer and the module agree on the download URL"]
        )

    def test_the_gate_notices_a_missing_rehearsal_switch(self):
        # The twenty-seventh gate can only rehearse a platform whose
        # installer offers the rehearsal switches. If one of them goes
        # missing, that platform silently stops being tested - and a
        # path that is no longer tested is a path that no longer works.
        import mas.selftest_bootstrap as gate_module

        facts = gate_module._bootstrap_observations(gate_module.REPO_ROOT)
        broken = dict(facts)
        broken["rehearsal_switches"] = {
            name: [
                switch
                for switch in found
                if switch != "MAS_FORCE_BOOTSTRAP"
            ]
            for name, found in (facts.get("rehearsal_switches") or {}).items()
        }
        verdicts = gate_module._bootstrap_verdicts(broken)
        self.assertFalse(
            verdicts["both installers offer the same rehearsal switches"]
        )

    def test_both_installers_offer_the_same_rehearsal_switches(self):
        import mas.selftest_bootstrap as gate_module

        facts = gate_module._bootstrap_observations(gate_module.REPO_ROOT)
        for name in ("install.ps1", "install.sh"):
            found = set((facts.get("rehearsal_switches") or {}).get(name) or [])
            self.assertEqual(
                found, set(gate_module.REHEARSAL_ENV_SWITCHES), name
            )
        # The command line is an extra on the PowerShell side.
        self.assertEqual(
            set((facts.get("rehearsal_cli_switches") or {}).get("install.ps1") or []),
            set(gate_module.REHEARSAL_CLI_SWITCHES),
        )

    def test_the_gate_notices_a_dead_end(self):
        import mas.selftest_bootstrap as gate_module

        facts = gate_module._bootstrap_observations(gate_module.REPO_ROOT)
        facts = dict(facts)
        facts["dead_end"] = {"install.ps1": True, "install.sh": False}
        broken = gate_module._bootstrap_verdicts(facts)
        self.assertFalse(broken["neither installer dead-ends on a missing Python"])

    def test_an_installed_tree_is_not_a_drift(self):
        # A wheel carries the package, not the entry point: the
        # installers are absent in an installed tree *by design*. A gate
        # that reported their absence as a defect would report a
        # perfectly good install as broken - and it did, once.
        import mas.selftest_bootstrap as gate_module

        real_root = gate_module.REPO_ROOT
        try:
            gate_module.REPO_ROOT = real_root / "no-such-tree"
            facts = gate_module._bootstrap_observations(gate_module.REPO_ROOT)
            self.assertFalse(facts["source_tree"])
            self.assertEqual(facts["present"], {"install.ps1": False, "install.sh": False})
            verdicts = gate_module._bootstrap_verdicts(facts)
            self.assertTrue(
                verdicts["the installers and the module agree on the minimum version"]
            )
            self.assertTrue(
                verdicts["the installers and the module agree on the bootstrap version"]
            )
            self.assertTrue(
                verdicts["both installers can bootstrap a Python"]
            )
            self.assertTrue(
                verdicts["both installers can be told not to bootstrap a Python"]
            )
            self.assertTrue(
                verdicts["both installers offer the same rehearsal switches"]
            )
            # The module's own rules still have to hold there: the
            # installed runtime is what a beginner ends up running.
            self.assertTrue(verdicts["the module refuses what it should refuse"])
        finally:
            gate_module.REPO_ROOT = real_root

    def test_a_drift_in_a_source_tree_is_still_a_drift(self):
        import mas.selftest_bootstrap as gate_module

        facts = gate_module._bootstrap_observations(gate_module.REPO_ROOT)
        self.assertTrue(facts["source_tree"])
        drifted = dict(facts)
        drifted["source_tree"] = True
        drifted["installer_bootstrap_versions"] = dict(
            facts.get("installer_bootstrap_versions") or {}
        )
        for name in drifted["installer_bootstrap_versions"]:
            drifted["installer_bootstrap_versions"][name] = ["3.11.9"]
        broken = gate_module._bootstrap_verdicts(drifted)
        self.assertFalse(
            broken["the installers and the module agree on the bootstrap version"]
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

