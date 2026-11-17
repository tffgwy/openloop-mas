"""The installer must install the wheel it was handed.

``install.ps1`` and ``install.sh`` both accept an explicit wheel (the
release pipeline and the CI install matrix use it). The runtime is then
also available as a source tree next to the script - the CI copies the
scripts into a standalone directory *and* builds a wheel, but a developer
running the script from the checkout hit the other order.

Before this was fixed, the source tree won: the wheel parameter was
parsed, the file was checked, and then the script copied ``mas/`` from
the checkout instead. The install then silently shipped a different
build than the one the caller asked for - and a release pipeline that
asserts on the *installed* version would have compared the wheel
against itself and passed without noticing.

These tests run the real scripts against a real wheel.
"""

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SENTINEL_NAME = "_wheel_sentinel.txt"
SENTINEL_TEXT = "openloop-wheel-sentinel"


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
    """Build a wheel from this checkout into ``dest``."""
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(dest)],
        cwd=str(REPO_ROOT),
        check=True,
        capture_output=True,
    )
    wheels = sorted(dest.glob("openloop_mas-*.whl"))
    if not wheels:
        raise AssertionError("no wheel was built")
    return wheels[-1]


def _wheel_with_sentinel(wheel: pathlib.Path, dest: pathlib.Path) -> pathlib.Path:
    """Repack the wheel with a file the checkout does not have.

    The checkout and the wheel carry the same source, so comparing their
    contents cannot tell them apart. A file that only exists in the wheel
    proves which copy landed on disk.
    """
    unpacked = dest / "unpacked"
    if unpacked.exists():
        shutil.rmtree(unpacked)
    unpacked.mkdir(parents=True)
    with zipfile.ZipFile(wheel) as zf:
        zf.extractall(unpacked)
    (unpacked / "mas" / SENTINEL_NAME).write_text(SENTINEL_TEXT, encoding="utf-8")
    out = dest / "sentinel-wheel.whl"
    with zipfile.ZipFile(out, "w") as zf:
        for item in sorted(unpacked.rglob("*")):
            if item.is_file():
                zf.write(item, str(item.relative_to(unpacked)))
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
            wheel = _wheel_with_sentinel(_build_wheel(wheel_dir), wheel_dir)

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
            wheel = _wheel_with_sentinel(_build_wheel(wheel_dir), wheel_dir)

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
