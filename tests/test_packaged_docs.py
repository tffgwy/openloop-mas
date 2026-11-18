# -*- coding: utf-8 -*-
"""The onboarding documents must travel with the wheel.

Two things used to be true at the same time, and they cannot both be:

* the wheel carries the package, not the repository's prose;
* the onboarding gate watches the README and the quickstart.

So in a plain ``pip install`` tree the gate had no subject, and the
installation reported a failure that said nothing about the beginner.

These tests hold the fix in place:

* the documents are copied into ``mas/docs/`` and are byte-identical
  to the repository's copies - a drift fails CI instead of shipping;
* the packaging declaration actually ships them;
* the gate finds the packaged copy, and reports "skipped" (not
  "failed") when there is genuinely nothing to read.
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

from mas.selftest_onboarding import (
    DOC_CANDIDATES,
    DOC_FILES,
    _roots,
    check_a_beginner_can_walk_the_docs_and_close_a_loop,
    doc_path,
    docs_present,
    missing_docs,
)

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PACKAGED = _ROOT / "mas" / "docs"


class PackagedDocsTest(unittest.TestCase):
    def test_the_documents_are_packaged(self):
        self.assertTrue(_PACKAGED.is_dir(), "mas/docs/ is missing")
        for name in DOC_FILES:
            self.assertTrue(
                (_PACKAGED / name).exists(),
                "mas/docs/%s is missing - the wheel would not carry it" % name,
            )

    def test_the_packaged_copy_is_identical_to_the_repository(self):
        # A drift here means the wheel ships a document the repository
        # does not have - and the gate would then check the wrong prose.
        for source, name in (
            ("README.md", "README.md"),
            ("README.en.md", "README.en.md"),
            ("docs/quickstart.md", "quickstart.md"),
        ):
            origin = _ROOT / source
            copy = _PACKAGED / name
            self.assertTrue(origin.exists(), origin)
            self.assertTrue(copy.exists(), copy)
            self.assertEqual(
                origin.read_bytes(),
                copy.read_bytes(),
                "mas/docs/%s has drifted from %s" % (name, source),
            )

    def test_the_packaging_declaration_ships_the_documents(self):
        text = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('"mas" = [', text)
        # The package-data pattern has to cover the documents.
        self.assertIn("docs/", text, "the wheel would not carry mas/docs/")

    def test_the_gate_reads_the_packaged_copy_first(self):
        path = doc_path("README.md")
        self.assertIsNotNone(path)
        self.assertTrue(
            str(path).replace("\\", "/").endswith("mas/docs/README.md"),
            "the gate read %s, not the packaged copy" % path,
        )

    def test_every_document_is_found(self):
        present = docs_present()
        for name in DOC_FILES:
            self.assertTrue(present.get(name), "%s is not on disk" % name)
        self.assertEqual(missing_docs(), [])

    def test_the_gate_passes_in_a_packaged_tree(self):
        result = check_a_beginner_can_walk_the_docs_and_close_a_loop(iterations=1)
        self.assertTrue(result["ok"], result.get("failures"))
        self.assertFalse(result.get("skipped"))

    def test_the_gate_reports_skipped_when_there_is_nothing_to_read(self):
        """No documents is not a broken beginner - it is a blind gate."""
        saved = {}
        for root in _roots():
            for candidates in DOC_CANDIDATES.values():
                for candidate in candidates:
                    path = root / candidate
                    if path.exists():
                        saved[path] = path.read_bytes()
                        path.unlink()
        try:
            self.assertEqual(len(missing_docs()), len(DOC_FILES))
            result = check_a_beginner_can_walk_the_docs_and_close_a_loop(iterations=1)
            self.assertTrue(result["skipped"])
            self.assertTrue(result["ok"], "a skipped gate must not be a failure")
            self.assertEqual(result["failures"], [])
        finally:
            for path, data in saved.items():
                path.write_bytes(data)

    def test_the_summary_knows_a_skipped_gate(self):
        from mas.selftest import format_selftest

        text = format_selftest(
            {
                "ok": True,
                "checks": [
                    {
                        "name": "onboarding_path",
                        "title": "x",
                        "invariant": "y",
                        "ok": True,
                        "skipped": True,
                        "failures": [],
                    }
                ],
                "failed": [],
                "skipped": ["onboarding_path"],
            }
        )
        self.assertIn("[跳过]", text)
        self.assertNotIn("[OK  ]", text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
