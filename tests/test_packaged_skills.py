"""The packaged skills must be the only copy of the skills.

``mas/skills/`` is the single source of truth: it is the copy that travels
inside the wheel, so ``pip install openloop-mas`` and a source checkout
install byte-identical skills. A second copy used to live at the repository
root, and the two could silently drift - the installed skill was then not
the skill the repository documented.

These tests fail loudly when a duplicate reappears or when the packaging
declaration stops covering the skill files.
"""

try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # loaded as a top-level module (unittest -s tests)
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401


import pathlib
import re
import unittest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PACKAGED = _ROOT / "mas" / "skills"


class PackagedSkillsTest(unittest.TestCase):
    def test_the_packaged_skills_exist(self):
        self.assertTrue(_PACKAGED.is_dir(), "mas/skills/ is missing")

    def test_the_three_known_skills_are_present(self):
        names = {p.name for p in _PACKAGED.iterdir() if p.is_dir()}
        for expected in ("mas-loop", "mas-review", "mas-scaffold"):
            self.assertIn(expected, names)

    def test_every_skill_has_a_manifest(self):
        for skill_dir in sorted(p for p in _PACKAGED.iterdir() if p.is_dir()):
            manifest = skill_dir / "SKILL.md"
            self.assertTrue(
                manifest.exists(),
                f"mas/skills/{skill_dir.name}/SKILL.md is missing",
            )
            self.assertIn("---", manifest.read_text(encoding="utf-8"))

    def test_there_is_no_duplicate_copy_at_the_repository_root(self):
        # The duplicate used to live at <repo>/skills. If it comes back, the
        # two copies can drift again, so this test has to keep failing.
        self.assertFalse(
            (_ROOT / "skills").exists(),
            "the repository root must not hold a second copy of the skills",
        )

    def test_the_installers_delegate_to_the_single_implementation(self):
        # Both installers must call `mas install` instead of implementing
        # their own copy loop. Two implementations are two behaviours, and
        # they drift - which is exactly the bug this layout removes.
        # The runtime is started through its package path rather than
        # ``-m mas``: ``python -m`` puts the current working directory
        # first on ``sys.path``, so a source checkout would shadow the
        # freshly installed runtime and the download would be pointless.
        delegate = re.compile(
            r'"-m",\s*"mas",\s*"install"|-m mas install'
            r'|__main__\.py[^\n]*install'
        )
        second_source = re.compile(r'skills/\*|/skills"|"skills"')
        for script in ("install.sh", "install.ps1"):
            text = (_ROOT / script).read_text(encoding="utf-8")
            self.assertRegex(
                text,
                delegate,
                f"{script} must delegate the skills+MCP work to `mas install`",
            )
            self.assertIsNone(
                second_source.search(text),
                f"{script} must not install from a second skills directory",
            )

    def test_the_packaged_skills_are_declared_in_the_wheel(self):
        pp = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("skills/*/SKILL.md", pp)


class CiWorkflowTest(unittest.TestCase):
    """A platform-capacity event must not be able to redden the build.

    GitHub cancels macOS runners *while they are still queued* when macOS
    capacity runs out. Such a job reports "cancelled" with zero steps
    executed - it never ran a single line of the project's code. When
    macOS shared the matrix with Linux and Windows, that cancellation
    reddened the whole run and told a reader "the project is broken" when
    nothing had been tested at all.

    Linux and Windows stay the hard gate; macOS is reported separately
    and cannot fail the build.
    """

    def setUp(self):
        self.text = (_ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.macos = (_ROOT / ".github" / "workflows" / "ci-macos.yml").read_text(
            encoding="utf-8"
        )

    def test_the_hard_gate_matrix_covers_linux_and_windows(self):
        self.assertIn("os: [ubuntu-latest, windows-latest]", self.text)

    def test_macos_is_not_in_the_hard_gate_workflow(self):
        self.assertNotIn(
            "os: [ubuntu-latest, windows-latest, macos-latest]",
            self.text,
        )
        self.assertNotIn("runs-on: macos-latest", self.text)

    def test_macos_lives_in_its_own_workflow_file(self):
        # A separate workflow file is the only thing that actually works:
        # a job that GitHub *cancels* is not a job that *fails*, and
        # continue-on-error does not cover the cancelled case. A separate
        # file cannot affect the other workflow's conclusion at all.
        self.assertIn("runs-on: macos-latest", self.macos)
        self.assertIn("name: ci-macos", self.macos)

    def test_the_macos_workflow_runs_the_same_checks(self):
        for step in (
            "python -m unittest discover -s tests -v",
            "python -m mas demo",
            "python -m mas bench --repeats 1",
            "python -m mas verify",
            "python -m mas selftest --iterations 120",
            "python -m mas doctor",
        ):
            self.assertIn(step, self.macos, step)

    def test_the_macos_workflow_asks_for_one_runner(self):
        # The GitHub-hosted macOS pool is the smallest of the three, and
        # GitHub cancels queued runners when it runs out. Asking for four
        # runners is asking to be cancelled three times. The full version
        # matrix is covered by the Linux and Windows legs.
        self.assertIn('python-version: ["3.12"]', self.macos)
        for version in ("3.10", "3.11", "3.13"):
            self.assertNotIn('"%s"' % version, self.macos, version)

    def test_the_install_workflow_keeps_macos_out_of_the_hard_gate(self):
        install = (_ROOT / ".github" / "workflows" / "install.yml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("os: [ubuntu-latest, windows-latest, macos-latest]", install)
        self.assertNotIn("runs-on: macos-latest", install)

    def test_the_macos_install_lives_in_its_own_workflow_file(self):
        install_macos = (
            _ROOT / ".github" / "workflows" / "install-macos.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("name: install-macos", install_macos)
        self.assertIn("runs-on: macos-latest", install_macos)
        self.assertIn("bash ./install.sh", install_macos)
        self.assertIn("tools/verify_install.py", install_macos)

    def test_no_workflow_guards_macos_with_continue_on_error(self):
        # A cancelled job is not a failed job, so continue-on-error does
        # not protect the build. Leaving it behind would look like the
        # problem is handled when it is not.
        for name in ("ci.yml", "install.yml"):
            text = (_ROOT / ".github" / "workflows" / name).read_text(
                encoding="utf-8"
            )
            self.assertNotIn("macos-latest' }}", text, name)


class ReleaseWorkflowTest(unittest.TestCase):
    """The release workflow must be able to publish without a stored token.

    The repository carries no secrets, so the only path that can work
    out of the box is PyPI Trusted Publishing: GitHub mints an OIDC
    token for the job and PyPI exchanges it for a short-lived upload
    token. The classic token path stays as the fallback.
    """

    def setUp(self):
        self.text = (_ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )

    def test_the_workflow_requests_an_oidc_token(self):
        self.assertIn("id-token: write", self.text)

    def test_the_trusted_publishing_path_exists(self):
        self.assertIn("pypa/gh-action-pypi-publish", self.text)

    def test_the_token_path_still_exists(self):
        self.assertIn("PYPI_API_TOKEN", self.text)
        self.assertIn("twine upload", self.text)

    def test_the_two_paths_are_mutually_exclusive(self):
        # Exactly one of the two publish steps may run per job.
        self.assertIn("if: ${{ env.TWINE_PASSWORD != '' }}", self.text)
        self.assertIn("if: ${{ env.TWINE_PASSWORD == '' }}", self.text)

    def test_a_failed_pypi_upload_does_not_fail_the_release(self):
        # The GitHub release is the primary artifact; a missing PyPI
        # registration must not turn a good release into a red run.
        self.assertIn("continue-on-error: true", self.text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
