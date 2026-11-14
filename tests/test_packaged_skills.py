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
