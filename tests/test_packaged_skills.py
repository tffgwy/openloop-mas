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
        delegate = re.compile(r'"-m",\s*"mas",\s*"install"|-m mas install')
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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
