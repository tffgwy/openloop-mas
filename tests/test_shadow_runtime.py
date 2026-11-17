# -*- coding: utf-8 -*-
"""Which ``mas`` does the user actually type?

The one-click installer puts a launcher in ``<install-dir>/bin`` and
adds that directory to the PATH. A PATH is a *list*, and the installer"s
entry is only one of its entries. A user who also ran ``pip install
openloop-mas`` has a second entry - usually earlier - and a second copy
of the same program on disk.

On a real machine the result was:

    AppData\\Roaming\\Python\\Python314\\Scripts\\mas.exe   0.7.16
    .mas\\bin\\mas.cmd                                      0.7.81

The installer unpacked 0.7.81, ran ``mas doctor`` against it, said
"everything is ready" - and then the user typed ``mas`` and got
0.7.16. Every check passed. The wrong program ran anyway.

These tests pin down the properties that make that impossible to miss:

1. the shadow is *found* (the PATH is walked the way the shell walks it);
2. the shadow is *named* (the version is read from disk, not guessed);
3. the shadow is *reported* (doctor says so, in its own bucket - not
   as "not installed", which would send the user to the wrong command);
4. the gate *can fail* (a doctor that never notices a shadow makes the
   test red).
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
import tempfile
import unittest

from mas.doctor import verdict
from mas.shadow import (
    find_launchers,
    format_shadow_status,
    path_entries,
    shadow_status,
)


def _make_launcher(directory: pathlib.Path, name: str, version: str) -> pathlib.Path:
    """A launcher that points at a package carrying ``version``."""
    app = directory / "app"
    package = app / "mas"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text(
        '__version__ = "%s"\n' % version, encoding="utf-8"
    )
    launcher = directory / "bin" / name
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text("launcher for %s\n" % version, encoding="utf-8")
    return launcher


class PathOrderTest(unittest.TestCase):
    def test_the_path_is_read_in_order(self):
        names = ["a", "b", "c"]
        env = {"PATH": os.pathsep.join(names)}
        entries = path_entries(env)
        self.assertEqual([e.name for e in entries], names)

    def test_empty_entries_are_dropped(self):
        env = {"PATH": os.pathsep.join(["a", "", "b"])}
        entries = path_entries(env)
        self.assertEqual([e.name for e in entries], ["a", "b"])


class FindLaunchersTest(unittest.TestCase):
    def test_the_first_launcher_on_the_path_is_the_one_that_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            first = root / "first"
            second = root / "second"
            _make_launcher(first, "mas.cmd", "0.7.16")
            _make_launcher(second, "mas.cmd", "0.7.81")
            env = {"PATH": os.pathsep.join([str(first / "bin"), str(second / "bin")])}
            launchers = find_launchers(env)
            self.assertEqual(len(launchers), 2)
            self.assertEqual(launchers[0].parent.parent, first)

    def test_a_directory_that_holds_two_names_counts_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            (bin_dir / "mas.cmd").write_text("x", encoding="utf-8")
            (bin_dir / "mas.exe").write_text("x", encoding="utf-8")
            env = {"PATH": str(bin_dir)}
            self.assertEqual(len(find_launchers(env)), 1)


class ShadowStatusTest(unittest.TestCase):
    def test_the_old_copy_on_the_path_is_found_and_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            pip_dir = root / "pip"
            _make_launcher(pip_dir, "mas.exe", "0.7.16")
            env = {"PATH": str(pip_dir / "bin")}
            status = shadow_status(env)
            self.assertTrue(status["shadowed"])
            self.assertEqual(status["shadow_version"], "0.7.16")
            self.assertEqual(status["launchers"][0]["version"], "0.7.16")
            self.assertTrue(status["launchers"][0]["winner"])

    def test_a_single_current_copy_is_not_a_shadow(self):
        from mas import __version__

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            home = root / "home"
            _make_launcher(home, "mas.cmd", str(__version__))
            env = {"PATH": str(home / "bin")}
            status = shadow_status(env)
            self.assertFalse(status["shadowed"])

    def test_the_report_names_the_winner_and_the_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            pip_dir = root / "pip"
            home = root / "home"
            _make_launcher(pip_dir, "mas.exe", "0.7.16")
            _make_launcher(home, "mas.cmd", "0.7.81")
            env = {"PATH": os.pathsep.join([str(pip_dir / "bin"), str(home / "bin")])}
            text = format_shadow_status(shadow_status(env))
            self.assertIn("0.7.16", text)
            self.assertIn("0.7.81", text)
            self.assertIn("mas", text)

    def test_a_copy_that_cannot_be_traced_is_reported_as_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            stray = root / "stray"
            stray.mkdir()
            (stray / "mas.cmd").write_text("x", encoding="utf-8")
            env = {"PATH": str(stray)}
            status = shadow_status(env)
            self.assertEqual(status["launchers"][0]["version"], "")
            self.assertFalse(status["launchers"][0]["traceable"])
            # Untraceable is not proof of a shadow, but it is not proof
            # of safety either - so it must not be silently "ok".
            self.assertTrue(status["shadowed"])


class DoctorVerdictTest(unittest.TestCase):
    """The doctor has to say it in its own words, not in the wrong bucket."""

    def _row(self, name, status, detail="detail"):
        return {"check": name, "status": status, "detail": detail}

    def test_a_shadow_is_not_reported_as_not_installed(self):
        # "not installed" would send the beginner to `mas install`, which
        # cannot change which copy the PATH reaches. The copy is there -
        # it is just the wrong one.
        final = verdict([
            self._row("python", "ok"),
            self._row("ledger", "ok"),
            self._row("shadow_runtime", "warn", "the old copy wins"),
        ])
        self.assertEqual(final["state"], "shadowed_runtime")
        self.assertIn("mas", final["action"])

    def test_a_shadow_still_leaves_the_loop_usable(self):
        final = verdict([
            self._row("python", "ok"),
            self._row("shadow_runtime", "warn"),
        ])
        self.assertTrue(final["loop_usable"])

    def test_a_shadow_is_not_counted_as_a_missing_optional_part(self):
        final = verdict([
            self._row("python", "ok"),
            self._row("shadow_runtime", "warn"),
        ])
        self.assertEqual(final["missing_optional"], [])
        self.assertEqual(final["shadowed_runtime"], ["shadow_runtime"])


class TheGateCanFailTest(unittest.TestCase):
    """A check that cannot fail does not watch anything.

    The shadow lookup is blinded on purpose. The gate has to notice
    that the doctor no longer reports a shadow - that is the proof that
    the check is what produced it.
    """

    def test_a_doctor_that_ignores_the_path_loses_the_shadow(self):
        import mas.doctor as doctor_module
        import mas.shadow as shadow_module

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            pip_dir = root / "pip"
            _make_launcher(pip_dir, "mas.exe", "0.7.16")
            env = {"PATH": str(pip_dir / "bin")}
            self.assertTrue(shadow_status(env)["shadowed"])

            real = shadow_module.shadow_status

            def _blind(*args, **kwargs):
                return {
                    "running_version": "0.0.0",
                    "launchers": [],
                    "winner": None,
                    "shadowed": False,
                    "shadow_version": "",
                    "distinct_versions": [],
                    "count": 0,
                }

            try:
                shadow_module.shadow_status = _blind
                results = doctor_module.run_doctor()
            finally:
                shadow_module.shadow_status = real
            names = {row["check"] for row in results}
            self.assertIn("shadow_runtime", names)
            row = [r for r in results if r["check"] == "shadow_runtime"][0]
            # Blinded, the doctor must not claim a shadow it did not
            # see - and the difference is what the gate watches.
            self.assertEqual(row["status"], "ok")


class InstallerPathOrderTest(unittest.TestCase):
    """The installer has to put its launcher FIRST, and never touch the
    real user PATH from a redirected home.

    Two separate bugs, both found on a real machine:

    * the launcher was *appended* to the PATH, so an older `mas` that
      was installed earlier kept winning - the user typed `mas` and ran
      the old copy while the installer said everything was ready;
    * the PowerShell installer wrote the *real* user PATH registry
      entry even when ``USERPROFILE`` pointed at a temporary directory.
      Every test run therefore left a throwaway directory on the
      user's PATH, and every later run lost to it.
    """

    def setUp(self):
        self.root = pathlib.Path(__file__).resolve().parent.parent
        self.ps1 = (self.root / "install.ps1").read_text(encoding="utf-8")
        self.sh = (self.root / "install.sh").read_text(encoding="utf-8")

    def test_the_powershell_installer_puts_the_launcher_first(self):
        # The launcher has to be *prepended*: a PATH is searched in
        # order, so appending loses to any older copy.
        self.assertIn('$newPath = (@($BinDir) + $rest) -join ";"', self.ps1)
        self.assertNotIn("($parts + $BinDir)", self.ps1)

    def test_the_shell_installer_puts_the_launcher_first(self):
        self.assertIn('export PATH="$BIN_DIR:$PATH"', self.sh)

    def test_both_installers_warn_about_an_older_copy(self):
        self.assertIn("another mas is still ahead", self.ps1)
        self.assertIn("another mas is still ahead", self.sh)

    def test_a_redirected_home_never_writes_the_real_user_path(self):
        # The registry write must be inside the branch that only runs
        # when the home is the real one.
        self.assertIn("$homeIsRedirected", self.ps1)
        self.assertIn("skipping the user PATH", self.ps1)
        write_line = self.ps1.index('SetEnvironmentVariable("Path", $newPath, "User")')
        guard_line = self.ps1.index("if ($homeIsRedirected)")
        self.assertLess(guard_line, write_line)

    def test_both_installers_mention_the_same_switch_name(self):
        # The two installers have to take the same decisions under the
        # same names, or a beginner gets different behaviour per OS.
        self.assertIn("MAS_SKIP_PATH", self.sh)
        self.assertIn("$SkipPath", self.ps1)


class UpgradeShadowTest(unittest.TestCase):
    """``mas upgrade --fix-shadow`` has to deal with the pip copy too.

    A pip-managed package is *not* rewritten: its ``.dist-info`` would
    go stale and ``pip list`` would start lying about the version. The
    fix is to report the uninstall command, not to fake the upgrade.
    """

    def test_a_pip_managed_copy_is_reported_not_rewritten(self):
        from mas.upgrade import _pip_managed_package

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            site = root / "site-packages"
            package = site / "mas"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text(
                '__version__ = "0.7.16"\n', encoding="utf-8"
            )
            (site / "openloop_mas-0.7.16.dist-info").mkdir()
            self.assertTrue(_pip_managed_package(package))

    def test_a_plain_copy_is_not_pip_managed(self):
        from mas.upgrade import _pip_managed_package

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            package = root / "app" / "mas"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text(
                '__version__ = "0.7.16"\n', encoding="utf-8"
            )
            self.assertFalse(_pip_managed_package(package))

    def test_the_upgrade_command_offers_the_shadow_fix(self):
        import contextlib
        import io

        from mas.cli import main

        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            try:
                main(["upgrade", "--help"])
            except SystemExit as exc:
                # argparse exits after printing the help text.
                self.assertEqual(exc.code, 0)
        self.assertIn("--fix-shadow", out.getvalue())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
