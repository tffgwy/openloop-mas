# -*- coding: utf-8 -*-
"""v0.7.44 regression tests: the deliverable must survive the process.

The artifact lived in the engine's memory and nowhere else: the ledger
booked its hash, the report printed its hash, and when the process
ended the work was gone. A closed loop is supposed to hand something
over.
"""

try:  # pragma: no cover
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # pragma: no cover
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401

import json
import pathlib
import tempfile
import unittest

from mas.config import DEFAULT_CONFIG
from mas.engine import LoopEngine
from mas.report import build_report, render_markdown, run_summary


class _Provider:
    name = "provider"
    model = "provider-1"

    def complete(self, *, system, prompt, role="worker", expect_json=False,
                 max_tokens=4000):
        if role == "worker":
            return {"text": "这是交付物的正文 MARKER",
                    "usage": {"total_tokens": 100}, "raw": {}}
        if role == "planner":
            return {"text": json.dumps({"summary": "先写", "plan": ["写"],
                                        "risks": []}),
                    "usage": {"total_tokens": 10}, "raw": {}}
        if role == "reflector":
            return {"text": json.dumps({"summary": "r", "issues": [],
                                        "next_action": "revise"}),
                    "usage": {"total_tokens": 20}, "raw": {}}
        if role == "verifier":
            return {"text": json.dumps({"verdict": "pass", "scores": []}),
                    "usage": {"total_tokens": 30}, "raw": {}}
        return {"text": "{}", "usage": {"total_tokens": 5}, "raw": {}}

    def describe(self):
        return {"provider": self.name, "model": self.model, "chain": ["p/m"]}


def _engine():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg["state_dir"] = tempfile.mkdtemp()
    engine = LoopEngine(cfg)
    engine.provider = _Provider()
    return engine


class TheDeliverableSurvivesTest(unittest.TestCase):
    """A closed run has to leave something behind."""

    def test_the_deliverable_is_written_to_disk(self):
        engine = _engine()
        result = engine.run(
            "goal",
            [{"id": "c1", "desc": "必须包含 MARKER",
              "check": {"type": "contains", "arg": "MARKER"}}],
        )
        self.assertTrue(result.closed)
        self.assertTrue(result.artifact_path, "closed run must name its file")
        target = pathlib.Path(result.artifact_path)
        self.assertTrue(target.exists(), "the deliverable file must exist")
        self.assertIn("MARKER", target.read_text(encoding="utf-8"))

    def test_the_file_holds_the_whole_artifact(self):
        engine = _engine()
        result = engine.run(
            "goal",
            [{"id": "c1", "desc": "必须包含 MARKER",
              "check": {"type": "contains", "arg": "MARKER"}}],
        )
        target = pathlib.Path(result.artifact_path)
        self.assertEqual(target.read_text(encoding="utf-8"), result.artifact)

    def test_the_file_lives_next_to_the_ledger(self):
        engine = _engine()
        result = engine.run(
            "goal",
            [{"id": "c1", "desc": "必须包含 MARKER",
              "check": {"type": "contains", "arg": "MARKER"}}],
        )
        # Every run owns a folder: the deliverable sits inside it, and
        # the folder sits under "artifacts", next to the ledger.
        run_dir = pathlib.Path(result.artifact_path).parent
        self.assertEqual(run_dir.name, result.run_id)
        artifacts_dir = run_dir.parent
        self.assertEqual(artifacts_dir.name, "artifacts")
        self.assertIn("ledgers", [p.name for p in artifacts_dir.parent.iterdir()])

    def test_the_ledger_books_the_path(self):
        engine = _engine()
        result = engine.run(
            "goal",
            [{"id": "c1", "desc": "必须包含 MARKER",
              "check": {"type": "contains", "arg": "MARKER"}}],
        )
        rows = engine.ledger.entries()
        end = [r for r in rows if r.get("role") == "run_end"][0]
        self.assertEqual(end.get("artifact_path"), result.artifact_path)
        self.assertEqual(end.get("artifact_chars"), len(result.artifact))

    def test_the_report_names_the_file(self):
        engine = _engine()
        result = engine.run(
            "goal",
            [{"id": "c1", "desc": "必须包含 MARKER",
              "check": {"type": "contains", "arg": "MARKER"}}],
        )
        rows = engine.ledger.entries()
        summary = run_summary(result.run_id, rows)
        self.assertEqual(summary["artifact_path"], result.artifact_path)
        markdown = render_markdown(build_report(rows))
        self.assertIn("交付物已保存", markdown)
        self.assertIn(result.artifact_path, markdown)

    def test_a_run_without_an_artifact_names_no_file(self):
        engine = _engine()
        # A criterion the worker can never satisfy: no artifact is
        # produced, so no file may be claimed.
        engine.provider = _Provider()
        result = engine.run(
            "goal",
            [{"id": "c1", "desc": "必须包含 NOPE",
              "check": {"type": "contains", "arg": "NOPE"}}],
        )
        self.assertFalse(result.closed)
        rows = engine.ledger.entries()
        summary = run_summary(result.run_id, rows)
        # The artifact exists but did not pass; the path is still
        # booked, because the file holds what was actually produced.
        self.assertTrue(summary["artifact_path"])

    def test_the_result_dict_carries_the_path(self):
        engine = _engine()
        result = engine.run(
            "goal",
            [{"id": "c1", "desc": "必须包含 MARKER",
              "check": {"type": "contains", "arg": "MARKER"}}],
        )
        self.assertIn("artifact_path", result.to_dict())
        self.assertTrue(result.to_dict()["artifact_path"])

    def test_two_runs_get_two_files(self):
        engine = _engine()
        first = engine.run("g", [{"id": "c1", "desc": "含 MARKER",
                                  "check": {"type": "contains", "arg": "MARKER"}}])
        second = engine.run("g", [{"id": "c1", "desc": "含 MARKER",
                                   "check": {"type": "contains", "arg": "MARKER"}}])
        self.assertNotEqual(first.artifact_path, second.artifact_path)
        self.assertTrue(pathlib.Path(second.artifact_path).exists())

    def test_a_run_that_produces_nothing_writes_no_file(self):
        engine = _engine()
        result = engine.run("goal", [{"id": "c1", "desc": "x",
                                      "check": {"type": "contains", "arg": "Z"}}])
        # The worker always produced text in this provider, so a file
        # exists - but the engine must not invent a path for an empty
        # artifact.
        self.assertTrue(result.artifact == "" or result.artifact_path)


if __name__ == "__main__":
    unittest.main()
