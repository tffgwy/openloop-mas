# -*- coding: utf-8 -*-
"""Rewrite the wheel-building helper so it needs no build backend.

``python -m build`` was the obvious way to make a wheel, but it drags in
an isolated build environment, a network fetch of the build backend and
a working compiler for some backends. On a CI matrix that means the test
fails for reasons that have nothing to do with the installer - which is
exactly what happened: the macOS and Linux jobs went red on
``Command 'python -m build --wheel' returned non-zero exit status 1``.

The installers do not care what is *inside* the wheel beyond the ``mas``
package: they unzip it and move that package into place. So the wheel is
built here with the standard library alone - no build backend, no
network, no compiler. The result is the same artifact the installers
care about, and the test now depends only on the thing it is actually
testing.
"""

import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest
import zipfile

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SENTINEL_NAME = "_wheel_sentinel.txt"
SENTINEL_TEXT = "openloop-wheel-sentinel"
WHEEL_VERSION = "0.0.0"
DIST_INFO = "openloop_mas-%s.dist-info" % WHEEL_VERSION


def _out(proc) -> str:
    """Decode a finished process' output.

    The installers print Chinese text, and the Windows console code page
    is not UTF-8, so decoding the captured bytes as UTF-8 fails. The
    errors are replaced rather than raised: the output is only used to
    explain a failure.
    """
    return (proc.stdout or b"").decode("utf-8", errors="replace") + (
        proc.stderr or b""
    ).decode("utf-8", errors="replace")


def _bash_can_run_scripts() -> bool:
    """Does the local ``bash`` actually work as a POSIX shell?

    On Windows the ``bash`` on PATH is often the WSL launcher stub, which
    prints an installation prompt and exits non-zero when no distribution
    is installed. A test that runs it would fail for a reason that has
    nothing to do with the installer.
    """
    if os.name == "nt":
        return False
    try:
        probe = subprocess.run(
            ["bash", "-c", "exit 0"], capture_output=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


def _build_wheel(dest: pathlib.Path) -> pathlib.Path:
    """Build a wheel out of this checkout, with the standard library only.

    The wheel carries the real ``mas`` package from the checkout plus a
    sentinel file that the checkout does not have. The sentinel is what
    tells the two copies apart once one of them is unpacked onto disk.
    """
    package = REPO_ROOT / "mas"
    if not package.is_dir():
        raise AssertionError("the checkout has no mas package to build from")

    out = dest / ("openloop_mas-%s-py3-none-any.whl" % WHEEL_VERSION)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in sorted(package.rglob("*")):
            if not item.is_file():
                continue
            if "__pycache__" in item.parts or item.suffix == ".pyc":
                continue
            zf.write(item, str(item.relative_to(REPO_ROOT)))
        zf.writestr("mas/" + SENTINEL_NAME, SENTINEL_TEXT)
        zf.writestr(DIST_INFO + "/METADATA", "Name: openloop-mas\nVersion: %s\n" % WHEEL_VERSION)
        zf.writestr(
            DIST_INFO + "/WHEEL",
            "Wheel-Version: 1.0\nGenerator: mas-test\nRoot-Is-Purelib: true\n"
            "Tag: py3-none-any\n",
        )
        zf.writestr(DIST_INFO + "/RECORD", "")
    return out


class WindowsWheelChoiceTest(unittest.TestCase):
    @unittest.skipIf(os.name != "nt", "the PowerShell installer is Windows-only")
    def test_an_explicit_wheel_beats_the_source_checkout(self):
        with tempfile.TemporaryDirectory(prefix="mas-wheel-choice-") as tmp:
            tmp = pathlib.Path(tmp)
            home = tmp / "home"
            home.mkdir()
            install_dir = tmp / "mas"
            wheel_dir = tmp / "wheel"
            wheel_dir.mkdir()
            wheel = _build_wheel(wheel_dir)

            env = dict(os.environ)
            env["USERPROFILE"] = str(home)
            env["HOME"] = str(home)
            env["MAS_HOME"] = str(install_dir)
            env.pop("MAS_STATE_DIR", None)
            env.pop("MAS_CONFIG_PATH", None)
            proc = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(REPO_ROOT / "install.ps1"),
                    "-InstallDir",
                    str(install_dir),
                    "-SkipPath",
                    "-SkipDemo",
                    "-SkipSkills",
                    "-SkipMcp",
                    "-Wheel",
                    str(wheel),
                ],
                cwd=str(REPO_ROOT),
                env=env,
                capture_output=True,
            )
            self.assertEqual(
                proc.returncode, 0, msg=_out(proc)
            )
            installed = install_dir / "app" / "mas" / SENTINEL_NAME
            self.assertTrue(
                installed.exists(),
                msg=(
                    "the source checkout was installed instead of the wheel\n"
                    + _out(proc)
                ),
            )
            self.assertFalse(
                (REPO_ROOT / "mas" / SENTINEL_NAME).exists(),
                msg="the sentinel leaked into the checkout",
            )


class ShellWheelChoiceTest(unittest.TestCase):
    """The same rule on the POSIX installer."""

    @unittest.skipUnless(
        shutil.which("bash") and _bash_can_run_scripts(),
        "a bash that can run POSIX scripts is required",
    )
    def test_an_explicit_wheel_beats_the_source_checkout(self):
        with tempfile.TemporaryDirectory(prefix="mas-wheel-choice-sh-") as tmp:
            tmp = pathlib.Path(tmp)
            home = tmp / "home"
            home.mkdir()
            install_dir = tmp / "mas"
            wheel_dir = tmp / "wheel"
            wheel_dir.mkdir()
            wheel = _build_wheel(wheel_dir)

            env = dict(os.environ)
            env["HOME"] = str(home)
            env["MAS_HOME"] = str(install_dir)
            env["MAS_WHEEL"] = str(wheel)
            env.pop("MAS_STATE_DIR", None)
            env.pop("MAS_CONFIG_PATH", None)
            proc = subprocess.run(
                ["bash", str(REPO_ROOT / "install.sh")],
                cwd=str(REPO_ROOT),
                env=env,
                capture_output=True,
            )
            self.assertEqual(
                proc.returncode, 0, msg=_out(proc)
            )
            installed = install_dir / "app" / "mas" / SENTINEL_NAME
            self.assertTrue(
                installed.exists(),
                msg=(
                    "the source checkout was installed instead of the wheel\n"
                    + _out(proc)
                ),
            )


if __name__ == "__main__":
    unittest.main()
