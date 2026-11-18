# -*- coding: utf-8 -*-
"""The folder handoff must be sealed too.

v0.7.88 sealed every closure. The folder handoff - a human editing
the files where they are and handing the folder back - is the third
closure path, and it carries the same seal as the other two. This
pins it, because the seal of a folder handoff is the one that covers
the *human's own edits* on disk: if those files are changed after the
handoff, the seal has to say so.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from mas.engine import LoopEngine  # noqa: E402
from mas.providers import Provider  # noqa: E402
from mas.seal import verify_seal  # noqa: E402


class _AlwaysFails(Provider):
    """The verifier always says no, so the human has to close the loop."""

    name = "handoff-seal-test"

    def __init__(self, model: str = "test-model", **options):
        super().__init__(model=model, **options)

    def complete(self, *, system, prompt, role="worker", expect_json=False, max_tokens=4000):
        self.calls += 1
        usage = {"total_tokens": 10}
        if role == "planner":
            text = json.dumps({"summary": "s", "plan": ["p"], "risks": []})
        elif role == "verifier":
            text = json.dumps(
                {"verdict": "fail", "scores": [{"id": "c1", "pass": False, "note": "no"}]}
            )
        elif role == "reflector":
            text = json.dumps({"summary": "s", "issues": [], "next_action": "revise"})
        elif role == "worker":
            text = "the answer is 41."
        else:
            text = "{}"
        return {"text": text, "usage": usage, "raw": {}}


FILE_CRITERIA = [
    {
        "id": "c1",
        "desc": "the finding file holds the answer",
        "check": {"type": "file_contains", "arg": "finding.txt::42"},
    }
]


def _config(tmp, **budget):
    base = {"max_iterations": 1, "max_tokens": 1000000, "deadline_sec": 60}
    base.update(budget)
    return {
        "provider": "mock",
        "model": "test-model",
        "state_dir": os.path.join(tmp, "state"),
        "workspace": "",
        "budget": base,
        "orchestration": {"parallel_workers": 1, "reviewers": 1, "review_mode": "all"},
        "team": {"language": "zh"},
        "stagnation_limit": 2,
    }


class FolderHandoffSealTest(unittest.TestCase):
    def _escalated(self, tmp):
        engine = LoopEngine(_config(tmp), provider=_AlwaysFails(model="test-model"))
        result = engine.run(goal="write the finding", criteria=FILE_CRITERIA)
        self.assertFalse(result.closed)
        return engine, str(result.ticket_id)

    def _folder_of(self, engine, ticket_id):
        ticket = engine.tickets.get(ticket_id)
        return Path(str(ticket["payload"].get("workspace") or ""))

    def test_a_folder_handoff_is_sealed(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, ticket_id = self._escalated(tmp)
            folder = self._folder_of(engine, ticket_id)
            (folder / "finding.txt").write_text("the answer is 42.", encoding="utf-8")
            result = engine.handback_workspace(ticket_id, note="by hand")
            self.assertTrue(result.closed, result.error)
            ends = [
                r
                for r in engine.ledger.entries()
                if r.get("role") == "run_end" and r.get("handback")
            ]
            self.assertTrue(ends)
            seal = ends[-1].get("seal")
            self.assertIsInstance(seal, dict)
            self.assertTrue(seal)
            self.assertEqual(seal.get("verdict"), "pass")

    def test_the_folder_handoff_seal_holds(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, ticket_id = self._escalated(tmp)
            folder = self._folder_of(engine, ticket_id)
            (folder / "finding.txt").write_text("the answer is 42.", encoding="utf-8")
            engine.handback_workspace(ticket_id)
            ends = [
                r
                for r in engine.ledger.entries()
                if r.get("role") == "run_end" and r.get("handback")
            ]
            artifact = None
            path = ends[-1].get("artifact_path")
            if path:
                artifact = Path(path).read_text(encoding="utf-8")
            report = verify_seal(ends[-1]["seal"], artifact)
            self.assertTrue(report["ok"], report)
            self.assertEqual(report["state"], "holds")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
