# -*- coding: utf-8 -*-
"""v0.7.70 tests for the binary write protocol.

v0.7.70 made the content checks able to read the words inside a PDF.
That only helps if the loop can actually *produce* a PDF - and the
write protocol of v0.7.15 could only carry text. These tests pin the
base64 protocol down: what lands on the disk, what is refused, and
what the engine plans.
"""

try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # pragma: no cover - loaded as a top-level module
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401

import base64
import pathlib
import tempfile
import unittest

from mas.filewrite import (
    apply_write_blocks,
    parse_write_blocks,
    plan_writes,
    write_instruction,
)


def _pdf_bytes() -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"trailer<</Root 1 0 R>>\n"
        b"%%EOF\n"
    )


def _block(path: str, payload: str, encoding: str = "") -> str:
    attrs = ' encoding="%s"' % encoding if encoding else ""
    return (
        '<<<MAS:FILE path="%s"%s>>>\n%s\n<<<MAS:END FILE>>>'
        % (path, attrs, payload)
    )


class Base64WriteTest(unittest.TestCase):
    def test_a_base64_block_lands_as_the_decoded_bytes(self):
        pdf = _pdf_bytes()
        encoded = base64.b64encode(pdf).decode("ascii")
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            applied = apply_write_blocks(_block("out/report.pdf", encoded, "base64"), root)
            self.assertEqual(applied["errors"], [])
            target = root / "out" / "report.pdf"
            self.assertTrue(target.exists())
            self.assertEqual(target.read_bytes(), pdf)

    def test_the_base64_text_itself_is_not_written(self):
        pdf = _pdf_bytes()
        encoded = base64.b64encode(pdf).decode("ascii")
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            apply_write_blocks(_block("out/report.pdf", encoded, "base64"), root)
            data = (root / "out" / "report.pdf").read_bytes()
            self.assertNotEqual(data, encoded.encode("utf-8"))
            self.assertTrue(data.startswith(b"%PDF"))

    def test_a_block_without_an_encoding_is_still_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            applied = apply_write_blocks(_block("out/plain.txt", "hello"), root)
            self.assertEqual(applied["errors"], [])
            self.assertEqual((root / "out" / "plain.txt").read_bytes(), b"hello")

    def test_an_unknown_encoding_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            artifact, writes = parse_write_blocks(_block("out/x.bin", "hi", "rot13"))
            self.assertEqual(writes, [])
            self.assertIn("<<<MAS:FILE", artifact)
            applied = apply_write_blocks(_block("out/x.bin", "hi", "rot13"), root)
            self.assertTrue(applied["errors"])
            self.assertFalse((root / "out" / "x.bin").exists())

    def test_broken_base64_is_refused_and_leaves_no_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            applied = apply_write_blocks(
                _block("out/y.bin", "!!!not base64!!!", "base64"), root
            )
            self.assertTrue(applied["errors"])
            self.assertFalse((root / "out" / "y.bin").exists())

    def test_broken_base64_raises_when_the_bytes_are_asked_for(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            planned = plan_writes(_block("out/y.bin", "!!!!", "base64"), root)
            self.assertEqual(planned["planned"], [])
            self.assertTrue(planned["errors"])

    def test_the_plan_carries_the_decoded_bytes(self):
        pdf = _pdf_bytes()
        encoded = base64.b64encode(pdf).decode("ascii")
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            planned = plan_writes(_block("out/report.pdf", encoded, "base64"), root)
            self.assertEqual(len(planned["planned"]), 1)
            row = planned["planned"][0]
            self.assertEqual(row["data"], pdf)
            self.assertEqual(row["bytes"], len(pdf))
            self.assertEqual(row["encoding"], "base64")
            self.assertIsInstance(row["data"], bytes)

    def test_the_plan_of_a_text_block_carries_utf8_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            planned = plan_writes(_block("out/plain.txt", "结论"), root)
            row = planned["planned"][0]
            self.assertEqual(row["data"], "结论".encode("utf-8"))
            self.assertEqual(row["bytes"], len("结论".encode("utf-8")))

    def test_the_instruction_mentions_the_base64_encoding(self):
        text = write_instruction(["out/report.pdf"])
        self.assertIn("base64", text)
        self.assertIn('encoding="base64"', text)


class EngineOverlayTest(unittest.TestCase):
    def test_the_engine_overlay_carries_the_bytes_and_not_their_number(self):
        from mas.engine import LoopEngine

        pdf = _pdf_bytes()
        encoded = base64.b64encode(pdf).decode("ascii")
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            engine = LoopEngine(
                {
                    "provider": "mock",
                    "model": "test-model",
                    "state_dir": str(root / "state"),
                    "workspace": "",
                    "budget": {"max_iterations": 2, "max_tokens": 10000, "deadline_sec": 30},
                    "orchestration": {"parallel_workers": 1, "reviewers": 1, "review_mode": "all"},
                    "team": {"language": "zh"},
                }
            )
            engine._run_id = "test"
            overlay = engine._planned_overlay(
                _block("out/ghost.pdf", encoded, "base64")
            )
            self.assertEqual(len(overlay), 1)
            for value in overlay.values():
                self.assertIsInstance(value, bytes)
                self.assertEqual(value, pdf)

    def test_a_content_check_creates_a_write_obligation(self):
        from mas.engine import Criterion, LoopEngine

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            engine = LoopEngine(
                {
                    "provider": "mock",
                    "model": "test-model",
                    "state_dir": str(root / "state"),
                    "workspace": "",
                    "budget": {"max_iterations": 2, "max_tokens": 10000, "deadline_sec": 30},
                    "orchestration": {"parallel_workers": 1, "reviewers": 1, "review_mode": "all"},
                    "team": {"language": "zh"},
                }
            )
            criteria = [
                Criterion.from_raw(
                    {
                        "id": "c1",
                        "desc": "the report has to say Conclusion",
                        "check": {"type": "content_contains", "arg": "out/report.pdf::Conclusion"},
                    },
                    0,
                )
            ]
            self.assertIn("out/report.pdf", engine._required_write_paths(criteria))


class BinaryWriteGateTest(unittest.TestCase):
    def test_the_gate_is_registered(self):
        from mas.selftest import CHECKS

        names = {getattr(c, "CHECK_NAME", c.__name__) for c in CHECKS}
        self.assertIn("binary_write", names)
        self.assertEqual(len(CHECKS), 16)

    def test_the_gate_passes_offline(self):
        from mas.selftest import check_a_binary_write_lands_as_bytes

        row = check_a_binary_write_lands_as_bytes(iterations=1)
        self.assertTrue(row["ok"], row["failures"])
        self.assertEqual(row["name"], "binary_write")

    def test_the_gate_states_what_it_guarantees(self):
        from mas.selftest import check_a_binary_write_lands_as_bytes

        row = check_a_binary_write_lands_as_bytes(iterations=1)
        self.assertTrue(row.get("title"))
        self.assertTrue(row.get("invariant"))

    def test_the_gate_can_be_selected_by_name(self):
        from mas.selftest import run_selftest

        result = run_selftest(iterations=1, only=["binary_write"])
        self.assertEqual(len(result["checks"]), 1)
        self.assertTrue(result["ok"], result)

    def test_the_gate_notices_a_broken_write_path(self):
        """A gate that cannot fail is not a gate."""
        import mas.filewrite as filewrite
        from mas.selftest import check_a_binary_write_lands_as_bytes

        real = filewrite._request_bytes

        def _blind(content, encoding=None):
            return str(content).encode("utf-8")

        filewrite._request_bytes = _blind
        try:
            row = check_a_binary_write_lands_as_bytes(iterations=1)
        finally:
            filewrite._request_bytes = real
        self.assertFalse(row["ok"])
        self.assertTrue(row["failures"])


if __name__ == "__main__":
    unittest.main()
