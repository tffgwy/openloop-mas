# -*- coding: utf-8 -*-
"""The wall-clock ceiling has to hold even on a coarse clock.

The gap
-------
The wall-clock ceiling was measured with ``time.time``. On Windows
that clock had a resolution of about 15.6ms on CPython 3.12 and
older - CPython 3.13 moved it to a high-resolution timer - so two
reads taken microseconds apart came back *equal*. A run with a
zero-second budget therefore compared ``0 > 0``, found it false, went
on, judged something and closed: a machine with no budget at all
produced a verdict.

That failure was invisible on a developer machine running 3.13 or
newer, and it broke the whole suite on 3.10, 3.11 and 3.12 in CI.

The fix
-------
* The engine measures its ceiling on a **monotonic** clock, whose
  resolution does not depend on the platform's wall-clock timer.
* The comparison is ``>=``, so a budget of zero means zero - a
  ceiling of zero cannot be talked out of by the clock's grain.

These tests pin both halves. The first one injects a deliberately
coarse clock - one that returns the same value for many reads, the
way a 15.6ms timer does - and insists the ceiling still stops the
run. The second one checks the default instrument: the engine must
not measure a budget on a clock the operating system can move.
"""
import pathlib
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from mas.engine import LoopEngine  # noqa: E402
from mas.providers import Provider  # noqa: E402


class _Worker(Provider):
    """A worker that answers, and a verifier that says yes."""

    name = "coarse-clock-test"

    def __init__(self, model="test-model", **options):
        super().__init__(model=model, **options)

    def complete(self, *, system, prompt, role="worker", expect_json=False, max_tokens=4000):
        usage = {"total_tokens": 10}
        if role == "verifier":
            text = (
                '{"verdict": "pass", '
                '"scores": [{"id": "c1", "pass": true, "note": "ok"}]}'
            )
        elif role == "worker":
            text = "the answer is 42.\n"
        else:
            text = "{}"
        return {"text": text, "usage": usage, "raw": {}}


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


class _CoarseClock:
    """A clock with a 15.6ms tick - the old Windows wall clock.

    It only advances every ``tick`` reads, which is exactly what the
    coarse timer did: two reads inside one tick returned the same
    number, so a difference of zero meant "no time has passed *as far
    as this clock can tell*" - and the old code read that as "the
    budget is not exhausted".
    """

    def __init__(self, tick=64):
        self._tick = max(1, int(tick))
        self._reads = 0
        self._value = 1000.0

    def __call__(self):
        self._reads += 1
        if self._reads % self._tick == 0:
            self._value += 0.0156
        return self._value


class TheCeilingSurvivesACoarseClock(unittest.TestCase):
    """A ceiling that depends on the clock's grain is not a ceiling."""

    def test_a_zero_budget_stops_the_run_even_on_a_coarse_clock(self):
        with tempfile.TemporaryDirectory() as root:
            engine = LoopEngine(
                _config(root, deadline_sec=0),
                provider=_Worker(),
                clock=_CoarseClock(),
            )
            result = engine.run(goal="write", criteria=CRITERIA)
            self.assertEqual(result.status, "budget_exhausted", result.error)
            self.assertEqual(result.scores, [])
            self.assertFalse(result.deliverable_ok)

    def test_a_run_that_stopped_on_the_clock_judged_nothing(self):
        """The whole point: no verdict, so no seal, so no false drift."""
        with tempfile.TemporaryDirectory() as root:
            engine = LoopEngine(
                _config(root, deadline_sec=0),
                provider=_Worker(),
                clock=_CoarseClock(),
            )
            result = engine.run(goal="write", criteria=CRITERIA)
            rows = engine.ledger.entries()
            end = None
            for row in rows:
                if row.get("role") == "run_end" and row.get("run_id") == result.run_id:
                    end = row
            self.assertIsNotNone(end)
            self.assertEqual(end.get("seal"), {})
            self.assertEqual(end.get("stopped_by"), "deadline_sec")


class TheDefaultClockIsMonotonic(unittest.TestCase):
    """A budget must not be measured on a clock the machine can move."""

    def test_the_engine_measures_its_budget_on_a_monotonic_clock(self):
        with tempfile.TemporaryDirectory() as root:
            engine = LoopEngine(_config(root), provider=_Worker())
            self.assertIs(engine.clock, __import__("time").monotonic)

    def test_the_ceiling_is_compared_with_inclusive_equality(self):
        """A budget of zero means zero - the comparison cannot be ``>``."""
        source = (REPO / "mas" / "engine.py").read_text(encoding="utf-8")
        self.assertIn("(self.clock() - start) >= deadline_sec", source)
        self.assertNotIn("(self.clock() - start) > deadline_sec", source)


if __name__ == "__main__":
    unittest.main()
