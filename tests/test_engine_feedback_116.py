"""The feedback has to reach the worker (v0.11.6).

Before this release the loop iterated without learning. Measured with
a probe provider that recorded every prompt:

* the worker's second attempt in the same run never saw the
  deliverable of its first attempt - the ``PREVIOUS DELIVERABLE``
  block existed, but the state that fills it was only ever set for a
  *resumed* run;
* the reflector's own words never reached anybody - it returned
  ``{"issues": ["必须用中文重写整篇"], "reflection": "先把结论写在
  第一段"}`` and both strings were absent from the next worker
  prompt;
* the reflector was asked what the next attempt must change without
  being shown the deliverable that failed.

These tests pin the fixed behaviour, and they pin it against the
prompts themselves - not against the engine's internals - because the
prompts are what a real model reads.
"""
try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # loaded as a top-level module
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401

import json
import tempfile
import unittest
from typing import List, Tuple

from mas.config import DEFAULT_CONFIG
from mas.engine import LoopEngine
from mas.providers import Provider
from mas.roles import Roles
from mas.teams import load_team

FIRST_DELIVERABLE = "第一版交付物：正文内容在此。"
REFLECTOR_ISSUE = "必须用中文重写整篇"
REFLECTOR_SUMMARY = "先把结论写在第一段"
MACHINE_NOTE = "missing: ['关键词']"


def _make_config(tmp, **overrides):
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    config["state_dir"] = tmp
    config["budget"] = dict(config["budget"])
    config["budget"].update(overrides.pop("budget", {}))
    config.update(overrides)
    return config


class _Probe(Provider):
    """A provider that keeps every prompt it is given."""

    name = "probe"

    def __init__(self) -> None:
        super().__init__(model="probe-model")
        self.calls: List[Tuple[str, str]] = []

    def complete(self, *, system="", prompt="", role="worker",
                 expect_json=False, max_tokens=4000):
        self.calls.append((role, prompt))
        usage = {"total_tokens": 10}
        if role == "planner":
            text = json.dumps({"summary": "s", "plan": ["a"], "risks": []})
        elif role == "reflector":
            text = json.dumps({
                "summary": REFLECTOR_SUMMARY,
                "issues": [REFLECTOR_ISSUE],
                "next_action": "revise",
            })
        elif role == "verifier":
            text = json.dumps({
                "verdict": "fail",
                "scores": [{"id": "c1", "pass": False, "note": MACHINE_NOTE}],
                "issues": ["c1"],
            })
        else:
            worker_calls = len([r for r, _ in self.calls if r == "worker"])
            text = (
                FIRST_DELIVERABLE
                if worker_calls <= 1
                else "第二版交付物：已按复盘改写。"
            )
        return {"text": text, "usage": usage, "raw": {}}


def _criteria():
    """One criterion the deliverable cannot satisfy."""
    return [{
        "id": "c1",
        "desc": "必须包含关键词",
        "check": {"type": "contains", "arg": "关键词"},
    }]


def _run(probe, previous_artifact="", max_iterations=2):
    config = _make_config(
        tempfile.mkdtemp(), budget={"max_iterations": max_iterations}
    )
    engine = LoopEngine(config, team=load_team("default"))
    engine.provider = probe
    engine.run(
        goal="做一个东西",
        criteria=_criteria(),
        previous_artifact=previous_artifact,
    )
    return probe.calls


def _prompts(calls, role):
    return [prompt for name, prompt in calls if name == role]


def _state_of(prompt):
    marker = "<<MAS_STATE>>"
    if not prompt or marker not in prompt:
        return {}
    tail = prompt.split(marker, 1)[1]
    end = tail.find("<<END>>")
    if end == -1:
        return {}
    try:
        data = json.loads(tail[:end].strip())
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


class TestTheFeedbackReachesTheWorker(unittest.TestCase):
    def test_the_second_attempt_sees_the_first_attempts_deliverable(self):
        calls = _run(_Probe())
        workers = _prompts(calls, "worker")
        self.assertGreaterEqual(len(workers), 2)
        self.assertIn(FIRST_DELIVERABLE, workers[1])

    def test_the_second_attempt_sees_the_reflectors_summary(self):
        calls = _run(_Probe())
        workers = _prompts(calls, "worker")
        self.assertGreaterEqual(len(workers), 2)
        self.assertIn(REFLECTOR_SUMMARY, workers[1])

    def test_the_second_attempt_sees_the_reflectors_issues(self):
        calls = _run(_Probe())
        workers = _prompts(calls, "worker")
        self.assertGreaterEqual(len(workers), 2)
        self.assertIn(REFLECTOR_ISSUE, workers[1])

    def test_the_issues_the_worker_sees_are_the_reflectors_own_words(self):
        # The machine's note is a different thing, and it must not be
        # mistaken for the reflector's advice.
        calls = _run(_Probe())
        workers = _prompts(calls, "worker")
        state = _state_of(workers[1])
        reflection = state.get("reflection") or {}
        self.assertIsInstance(reflection, dict)
        issues = [str(item) for item in (reflection.get("issues") or [])]
        self.assertIn(REFLECTOR_ISSUE, issues)
        self.assertNotIn(MACHINE_NOTE, issues)

    def test_the_reflector_is_shown_the_deliverable_it_reflects_on(self):
        calls = _run(_Probe())
        reflectors = _prompts(calls, "reflector")
        self.assertGreaterEqual(len(reflectors), 1)
        state = _state_of(reflectors[0])
        self.assertIn(
            FIRST_DELIVERABLE, str(state.get("candidate_artifact") or "")
        )

    def test_the_first_attempt_is_shown_no_previous_deliverable(self):
        calls = _run(_Probe())
        workers = _prompts(calls, "worker")
        self.assertGreaterEqual(len(workers), 1)
        self.assertNotIn("<<PREVIOUS>>", workers[0])
        self.assertIsNone(_state_of(workers[0]).get("previous_artifact"))

    def test_a_resumed_run_still_sees_the_deliverable_it_was_handed(self):
        calls = _run(
            _Probe(), previous_artifact="外部交接过来的交付物：正文在此。"
        )
        workers = _prompts(calls, "worker")
        self.assertGreaterEqual(len(workers), 1)
        self.assertIn("外部交接过来的交付物", workers[0])

    def test_a_long_deliverable_is_clipped_and_the_omission_is_announced(self):
        long_text = (
            "HEAD-MARKER-UNIQUE " + ("开头的旧内容。" * 4000)
            + "TAIL-MARKER-UNIQUE 尾部的最新结论必须保留。"
        )
        calls = _run(_Probe(), previous_artifact=long_text)
        workers = _prompts(calls, "worker")
        self.assertGreaterEqual(len(workers), 1)
        prompt = workers[0]
        self.assertIn("尾部的最新结论必须保留。", prompt)
        self.assertIn("字已省略", prompt)

    def test_the_clipped_deliverable_keeps_its_tail_and_not_its_head(self):
        long_text = (
            "HEAD-MARKER-UNIQUE " + ("开头的旧内容。" * 4000)
            + "TAIL-MARKER-UNIQUE 尾部的最新结论必须保留。"
        )
        calls = _run(_Probe(), previous_artifact=long_text)
        workers = _prompts(calls, "worker")
        carried = str(
            _state_of(workers[0]).get("previous_artifact") or ""
        )
        self.assertIn("尾部的最新结论必须保留。", carried)
        self.assertIn("TAIL-MARKER-UNIQUE", carried)
        # The head is what got dropped. The tail is what survived.
        self.assertNotIn("HEAD-MARKER-UNIQUE", carried)
        self.assertLess(len(carried), len(long_text))

    def test_a_short_deliverable_is_not_truncated(self):
        calls = _run(_Probe(), previous_artifact="短交付物。")
        workers = _prompts(calls, "worker")
        carried = str(
            _state_of(workers[0]).get("previous_artifact") or ""
        )
        self.assertIn("短交付物。", carried)
        self.assertNotIn("字已省略", carried)


class TestTheRolesRenderTheFeedback(unittest.TestCase):
    """The prompt layer on its own, without an engine."""

    def setUp(self):
        self.roles = Roles({"language": "zh"})

    def test_the_reflection_block_is_rendered(self):
        state = {
            "goal": "做一个东西",
            "attempt": 2,
            "max_iterations": 3,
            "criteria": [{"id": "c1", "desc": "含关键词"}],
            "issues": ["c1: missing: ['关键词']"],
            "reflection": {
                "summary": REFLECTOR_SUMMARY,
                "issues": [REFLECTOR_ISSUE],
            },
        }
        text = self.roles.prompt("worker", state)
        self.assertIn("REFLECTION ON THE LAST FAILURE", text)
        self.assertIn(REFLECTOR_SUMMARY, text)
        self.assertIn(REFLECTOR_ISSUE, text)

    def test_the_reflection_reaches_the_reflector_too(self):
        # The reflector of the second round has to know what the
        # first round's reflection already said, or it repeats itself.
        state = {
            "goal": "做一个东西",
            "attempt": 2,
            "max_iterations": 3,
            "criteria": [{"id": "c1", "desc": "含关键词"}],
            "reflection": {"summary": REFLECTOR_SUMMARY},
        }
        text = self.roles.prompt("reflector", state)
        self.assertIn(REFLECTOR_SUMMARY, text)

    def test_a_state_without_a_reflection_renders_no_reflection_block(self):
        state = {
            "goal": "做一个东西",
            "attempt": 1,
            "max_iterations": 3,
            "criteria": [{"id": "c1", "desc": "含关键词"}],
        }
        text = self.roles.prompt("worker", state)
        self.assertNotIn("REFLECTION ON THE LAST FAILURE", text)

    def test_a_previous_deliverable_alone_triggers_the_rewrite_instruction(self):
        # A resumed run starts its attempt counter at one again and
        # comes with a deliverable but no issue list. It used to be
        # told to write the deliverable from scratch.
        state = {
            "goal": "做一个东西",
            "attempt": 1,
            "max_iterations": 3,
            "criteria": [{"id": "c1", "desc": "含关键词"}],
            "previous_artifact": "外部交接过来的交付物。",
        }
        text = self.roles.prompt("worker", state)
        self.assertIn("<<PREVIOUS>>", text)
        self.assertIn("外部交接过来的交付物。", text)
        self.assertIn("不要从零重写", text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
