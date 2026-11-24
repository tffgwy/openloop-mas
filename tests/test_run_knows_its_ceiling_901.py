# -*- coding: utf-8 -*-
"""A run that never judged anything is not a drifted run.

v0.10.0 sealed *every* closure. A run that died on the budget
ceiling before it ever scored anything was sealed too, with an empty
score list - and the report read that seal as a conclusion that had
drifted. That accuses a run of lying about something it never said.

This file pins the fix:

* a run with no verdict carries no seal;
* a run with no verdict is not counted as drifted;
* an old ledger that already carries a verdict-less seal is read as
  "no verdict", not as drift;
* a closure that *did* decide and was edited afterwards is still
  reported as drifted - the fix does not weaken into "never report
  anything";
* the run's own end entry names the ceiling it ran under and the
  ceiling that stopped it;
* and the gate that watches all of this is registered.
"""
import json
import pathlib
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from mas.engine import LoopEngine  # noqa: E402
from mas.providers import Provider, ProviderError  # noqa: E402
from mas.report import build_report, render_markdown, run_summary  # noqa: E402
from mas.seal import build_seal, seal_has_verdict, sha256_bytes, verify_seal  # noqa: E402


class _Worker(Provider):
    """A worker that answers, and a verifier that says yes."""

    name = "ceiling-test"

    def __init__(self, model="test-model", **options):
        super().__init__(model=model, **options)

    def complete(self, *, system, prompt, role="worker", expect_json=False, max_tokens=4000):
        usage = {"total_tokens": 10}
        if role == "verifier":
            text = json.dumps(
                {
                    "verdict": "pass",
                    "scores": [{"id": "c1", "pass": True, "note": "ok"}],
                }
            )
        elif role == "worker":
            text = "the answer is 42.\n"
        else:
            text = "{}"
        return {"text": text, "usage": usage, "raw": {}}


class _Broken(Provider):
    """A backend that is down."""

    name = "ceiling-broken"

    def __init__(self, model="test-model", **options):
        super().__init__(model=model, **options)

    def complete(self, *, system, prompt, role="worker", expect_json=False, max_tokens=4000):
        raise ProviderError("502 the backend is down")


CRITERIA = [
    {"id": "c1", "desc": "the answer", "check": {"type": "contains", "arg": "the answer"}},
]


def _config(root, **budget):
    state = pathlib.Path(root) / "state"
    state.mkdir(parents=True, exist_ok=True)
    base = {"max_iterations": 2, "max_tokens": 1000000, "deadline_sec": 60}
    base.update(budget)
    return {
        "provider": "mock",
        "model": "test-model",
        "state_dir": str(state),
        "workspace": "",
        "budget": base,
        "orchestration": {"parallel_workers": 1, "reviewers": 1, "review_mode": "all"},
        "team": {"language": "zh"},
    }


def _run_end(rows, run_id):
    for row in rows:
        if (
            isinstance(row, dict)
            and str(row.get("role") or "") == "run_end"
            and str(row.get("run_id") or "") == run_id
        ):
            return row
    return {}


class VerdictLessRunsCarryNoSeal(unittest.TestCase):
    """The core of the fix."""

    def _clock_run(self, root):
        engine = LoopEngine(_config(root, deadline_sec=0), provider=_Worker())
        result = engine.run(goal="write", criteria=CRITERIA)
        return str(result.to_dict().get("run_id") or ""), engine.ledger.entries()

    def _provider_run(self, root):
        engine = LoopEngine(_config(root), provider=_Broken())
        result = engine.run(goal="write again", criteria=CRITERIA)
        return str(result.to_dict().get("run_id") or ""), engine.ledger.entries()

    def test_a_run_that_never_judged_carries_no_seal(self):
        with tempfile.TemporaryDirectory() as root:
            clock_id, clock_rows = self._clock_run(root)
            provider_id, provider_rows = self._provider_run(root)
            clock = _run_end(clock_rows, clock_id)
            provider = _run_end(provider_rows, provider_id)
            self.assertEqual(clock.get("seal"), {})
            self.assertEqual(provider.get("seal"), {})

    def test_a_run_that_never_judged_is_not_counted_as_drifted(self):
        with tempfile.TemporaryDirectory() as root:
            _, clock_rows = self._clock_run(root)
            _, provider_rows = self._provider_run(root)
            rows = list(clock_rows) + list(provider_rows)
            report = build_report(rows)
            self.assertEqual(int(report["totals"].get("seal_drifted") or 0), 0)
            self.assertEqual(int(report["totals"].get("seal_no_verdict") or 0), 0)
            self.assertNotIn("\u7ed3\u8bba\u5df2\u6f02\u79fb", render_markdown(report))

    def test_an_old_verdict_less_seal_is_read_as_no_verdict(self):
        seal = build_seal(
            run_id="R-legacy",
            criteria=[{"id": "c1", "desc": "x"}],
            artifact="the answer is 42.\n",
            artifact_sha256=sha256_bytes(b"the answer is 42.\n"),
            files=[],
            scores=[],
            verdict="fail",
            decision="",
        )
        self.assertFalse(seal_has_verdict(seal))
        outcome = verify_seal(seal, "the answer is 42.\n")
        self.assertEqual(outcome.get("state"), "no-verdict")
        self.assertTrue(outcome.get("ok"))


class RealDriftStillReported(unittest.TestCase):
    """The other half: the lock must not be weakened."""

    def test_a_closure_that_decided_and_was_edited_is_still_drifted(self):
        with tempfile.TemporaryDirectory() as root:
            engine = LoopEngine(_config(root), provider=_Worker())
            result = engine.run(goal="write", criteria=CRITERIA)
            rows = engine.ledger.entries()
            run_id = str(result.to_dict().get("run_id") or "")
            end = _run_end(rows, run_id)
            artifact_path = pathlib.Path(str(end.get("artifact_path") or ""))
            self.assertTrue(artifact_path.exists())
            original = artifact_path.read_bytes()
            artifact_path.write_bytes(b"the answer is 43.\n")
            try:
                report = build_report(rows)
            finally:
                artifact_path.write_bytes(original)
            self.assertGreaterEqual(int(report["totals"].get("seal_drifted") or 0), 1)
            self.assertIn("\u7ed3\u8bba\u5df2\u6f02\u79fb", render_markdown(report))


class TheRunNamesItsCeiling(unittest.TestCase):
    """The run has to be able to say what stopped it."""

    def test_the_end_entry_carries_the_budget_and_the_stop_reason(self):
        with tempfile.TemporaryDirectory() as root:
            engine = LoopEngine(_config(root, deadline_sec=0), provider=_Worker())
            result = engine.run(goal="write", criteria=CRITERIA)
            end = _run_end(engine.ledger.entries(), str(result.to_dict().get("run_id") or ""))
            self.assertIsInstance(end.get("budget"), dict)
            self.assertTrue(end.get("budget"))
            self.assertEqual(end.get("stopped_by"), "deadline_sec")

    def test_a_provider_failure_names_the_provider(self):
        with tempfile.TemporaryDirectory() as root:
            engine = LoopEngine(_config(root), provider=_Broken())
            result = engine.run(goal="write", criteria=CRITERIA)
            end = _run_end(engine.ledger.entries(), str(result.to_dict().get("run_id") or ""))
            self.assertEqual(end.get("stopped_by"), "provider_error")

    def test_the_summary_and_the_markdown_say_the_same_thing(self):
        with tempfile.TemporaryDirectory() as root:
            engine = LoopEngine(_config(root, deadline_sec=0), provider=_Worker())
            result = engine.run(goal="write", criteria=CRITERIA)
            rows = engine.ledger.entries()
            run_id = str(result.to_dict().get("run_id") or "")
            summary = run_summary(run_id, [r for r in rows if r.get("run_id") == run_id])
            self.assertTrue(summary.get("budget"))
            self.assertEqual(summary.get("stopped_by"), "deadline_sec")
            markdown = render_markdown(build_report(rows))
            self.assertIn("\u5929\u82b1\u677f", markdown)
            self.assertIn("\u505c\u5728", markdown)


class TheGateIsRegistered(unittest.TestCase):
    """A gate nobody runs is not a gate."""

    def test_the_fifty_third_gate_is_in_the_suite(self):
        from mas import selftest

        names = [getattr(c, "CHECK_NAME", "") for c in selftest.CHECKS]
        self.assertIn("a_run_knows_its_ceiling", names)

    def test_the_fifty_third_gate_passes(self):
        from mas.selftest_run_knows_its_ceiling import (
            check_a_run_knows_its_ceiling,
        )

        outcome = check_a_run_knows_its_ceiling(iterations=1)
        self.assertTrue(outcome.get("ok"), outcome.get("failures"))


if __name__ == "__main__":
    unittest.main()
