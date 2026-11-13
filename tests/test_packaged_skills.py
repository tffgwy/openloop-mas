"""The packaged skills must stay identical to the repository skills.

``mas/skills/`` is a copy of ``skills/`` that travels inside the wheel, so
that ``pip install openloop-mas`` can install the skills without the
repository being present. A copy is a duplication risk: if somebody edits
``skills/mas-loop/SKILL.md`` and forgets the copy, the installed skill
silently differs from the documented one.

This test fails loudly when the two drift apart.
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

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_SOURCE = _ROOT / "skills"
_PACKAGED = _ROOT / "mas" / "skills"


def _normalise(text: str) -> str:
    # The repository stores CRLF, the wheel stores LF. Compare the content,
    # not the line endings.
    return text.replace("\r\n", "\n")


class PackagedSkillsTest(unittest.TestCase):
    def test_the_same_skill_folders_exist_in_both_places(self):
        self.assertTrue(_SOURCE.is_dir(), "skills/ is missing")
        self.assertTrue(_PACKAGED.is_dir(), "mas/skills/ is missing")
        source_names = {p.name for p in _SOURCE.iterdir() if p.is_dir()}
        packaged_names = {p.name for p in _PACKAGED.iterdir() if p.is_dir()}
        self.assertEqual(source_names, packaged_names)

    def test_every_skill_file_is_identical(self):
        for skill_dir in sorted(p for p in _SOURCE.iterdir() if p.is_dir()):
            for source_file in sorted(skill_dir.rglob("*")):
                if not source_file.is_file():
                    continue
                relative = source_file.relative_to(_SOURCE)
                packaged_file = _PACKAGED / relative
                self.assertTrue(
                    packaged_file.exists(),
                    f"mas/skills/{relative} is missing - re-run the packaging step",
                )
                self.assertEqual(
                    _normalise(source_file.read_text(encoding="utf-8")),
                    _normalise(packaged_file.read_text(encoding="utf-8")),
                    f"mas/skills/{relative} differs from skills/{relative}",
                )

    def test_the_packaged_skills_are_declared_in_the_wheel(self):
        pp = ( _ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("skills/*/SKILL.md", pp)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
