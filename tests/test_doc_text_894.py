# -*- coding: utf-8 -*-
"""The thirty-fifth gate needs its own tests.

The gate watches the documents of the project - the READMEs, the
changelog and the ``docs/`` folder - and it checks that they are still
text: no control characters, no stray tabs in the prose, and code
fences that open and close where they should.

These tests check the *checker*, because a checker that cannot tell a
clean document from a mangled one is worse than no checker at all.
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mas import selftest_doc_text as doc_text  # noqa: E402


class DocumentTextReadingTest(unittest.TestCase):
    def test_a_clean_document_reads_as_clean(self):
        text = "# Title\n\nA sentence with `code` in it.\n\n```bash\nmas --version\n```\n"
        facts = {
            "control": doc_text._control_problems(text),
            "tabs": doc_text._tab_problems(text),
            "fences": doc_text._fence_problems(text),
        }
        verdicts = doc_text._document_verdicts(facts)
        for label in doc_text.DOC_TEXT_OBSERVATIONS:
            self.assertTrue(verdicts[label], label)

    def test_a_backspace_is_caught(self):
        text = "the engine prints `budget` in the log\n"
        broken = text.replace("budget", "\x08udget")
        self.assertTrue(doc_text._control_problems(broken))
        self.assertFalse(doc_text._control_problems(text))

    def test_a_carriage_return_inside_a_line_is_caught(self):
        text = "one line\rwith a stray carriage return\n"
        self.assertTrue(doc_text._control_problems(text))

    def test_a_tab_in_the_prose_is_caught(self):
        self.assertTrue(doc_text._tab_problems("a line\twith a tab\n"))
        self.assertFalse(doc_text._tab_problems("a line with no tab\n"))

    def test_a_tab_inside_a_fence_is_allowed(self):
        text = "```\n\tindented with a tab\n```\n"
        self.assertFalse(doc_text._tab_problems(text))

    def test_a_broken_fence_is_caught(self):
        text = "```bash\nmas --version\n"
        problems = doc_text._fence_problems(text)
        self.assertTrue(problems)
        self.assertTrue(any("never closed" in why for _, why in problems))

    def test_a_fence_with_a_bogus_language_is_caught(self):
        text = "```bash bash\nx\n```\n"
        self.assertTrue(doc_text._fence_problems(text))

    def test_a_closing_fence_with_text_is_caught(self):
        text = "```json\n{}\n```json\n"
        problems = doc_text._fence_problems(text)
        self.assertTrue(any("closing code fence" in why for _, why in problems))


class DocumentTextGateTest(unittest.TestCase):
    def test_the_gate_passes_on_this_repository(self):
        result = doc_text.check_the_documents_are_still_text(iterations=1)
        self.assertTrue(result["ok"], result["failures"])

    def test_the_gate_has_a_name_and_a_subject(self):
        self.assertEqual(
            doc_text.check_the_documents_are_still_text.CHECK_NAME,
            "doc_text_integrity",
        )
        self.assertTrue(doc_text._document_paths())

    def test_the_gate_names_its_observations(self):
        self.assertEqual(len(doc_text.DOC_TEXT_OBSERVATIONS), 3)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
