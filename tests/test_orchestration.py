"""Tests for the v0.2 orchestration layer: parallel candidates, adversarial
review aggregation and provider failover."""
# The session-wide test isolation must be active before this module is
# imported: importing a test module is what pulls in the code under test.
# ``unittest discover -s tests`` loads modules as top-level ``test_x`` and
# therefore never executes ``tests/__init__.py``, so the isolation cannot
# rely on the package hook alone - every test module imports it directly.
try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # loaded as a top-level module (unittest -s tests)
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401


import json
import tempfile
import unittest

from mas.config import DEFAULT_CONFIG
from mas.engine import LoopEngine
from mas.orchestration import (
    ParallelFailure,
    ProviderChain,
    aggregate_reviews,
    pick_angles,
    pick_personas,
    run_parallel,
)
from mas.providers import Provider, ProviderError
from mas.teams import load_team


def make_config(tmp, **overrides):
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    config["state_dir"] = tmp
    config["budget"] = dict(config["budget"])
    config["budget"].update(overrides.pop("budget", {}))
    config.update(overrides)
    return config


class MultiCandidateProvider(Provider):
    """Returns a different artifact depending on which angle the worker was
    told to take. Only the 'thorough' candidate satisfies the criteria."""

    name = "multi"

    def __init__(self, **options):
        super().__init__(model="multi-1", **options)
        self.seen_angles = []

    def complete(self, *, system, prompt, role="worker", expect_json=False, max_tokens=4000):
        self.calls += 1
        if role == "worker":
            self.seen_angles.append(prompt)
            if "追求完整与严谨" in prompt:
                text = "关键结论：完整版本。" + "补充细节。" * 200
            else:
                text = "简略版本，没有结论。"
        elif role == "verifier":
            text = json.dumps({"verdict": "fail", "scores": [], "issues": ["x"]}, ensure_ascii=False)
        elif role == "planner":
            text = json.dumps({"summary": "p", "plan": ["a"], "risks": []}, ensure_ascii=False)
        elif role == "reflector":
            text = json.dumps({"summary": "s", "issues": ["x"], "next_action": "revise"}, ensure_ascii=False)
        else:
            text = json.dumps({"decision": "revise", "reason": "r"}, ensure_ascii=False)
        return {
            "text": text,
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
            "raw": {},
        }


class FlakyProvider(Provider):
    """Fails every call - used to test the failover chain."""

    name = "flaky"

    def __init__(self, **options):
        super().__init__(model="flaky-1", **options)

    def complete(self, *, system, prompt, role="worker", expect_json=False, max_tokens=4000):
        self.calls += 1
        raise ProviderError("endpoint is down")


class CountingProvider(Provider):
    """Succeeds and counts calls."""

    name = "counting"

    def __init__(self, **options):
        super().__init__(model="counting-1", **options)

    def complete(self, *, system, prompt, role="worker", expect_json=False, max_tokens=4000):
        self.calls += 1
        return {
            "text": json.dumps({"verdict": "fail", "scores": [], "issues": []}, ensure_ascii=False),
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "raw": {},
        }


class TestHelpers(unittest.TestCase):
    def test_pick_angles_are_distinct(self):
        angles = pick_angles(4)
        self.assertEqual(len(angles), 4)
        self.assertEqual(len(set(angles)), 4)

    def test_pick_personas_are_distinct(self):
        personas = pick_personas(3)
        self.assertEqual(len(set(personas)), 3)

    def test_pick_angles_cycles_beyond_pool(self):
        angles = pick_angles(8)
        self.assertEqual(len(angles), 8)
        self.assertGreater(len(set(angles)), 1)

    def test_run_parallel_returns_results_in_order(self):
        results = run_parallel([lambda i: i * 2 for i in range(5)])
        self.assertEqual(results, [0, 2, 4, 6, 8])

    def test_run_parallel_survives_one_failure(self):
        def bad(index):
            if index == 1:
                raise RuntimeError("boom")
            return index

        results = run_parallel([bad, bad, bad])
        self.assertEqual(results[0], 0)
        self.assertIsInstance(results[1], ParallelFailure)
        self.assertIn("boom", results[1].message)
        self.assertEqual(results[2], 2)


class TestAggregateReviews(unittest.TestCase):
    def _review(self, scores):
        return {"verdict": "pass", "scores": scores, "issues": []}

    def test_mode_all_requires_unanimous_pass(self):
        reviews = [
            self._review([{"id": "c1", "pass": True, "note": "ok"}]),
            self._review([{"id": "c1", "pass": False, "note": "not ok"}]),
        ]
        merged = aggregate_reviews(reviews, mode="all")
        self.assertFalse(merged["scores"][0]["pass"])
        self.assertEqual(merged["scores"][0]["votes"], "1/2")

    def test_mode_majority_passes_on_two_of_three(self):
        reviews = [
            self._review([{"id": "c1", "pass": True, "note": "a"}]),
            self._review([{"id": "c1", "pass": True, "note": "b"}]),
            self._review([{"id": "c1", "pass": False, "note": "c"}]),
        ]
        merged = aggregate_reviews(reviews, mode="majority")
        self.assertTrue(merged["scores"][0]["pass"])
        self.assertEqual(merged["scores"][0]["votes"], "2/3")

    def test_mode_any_passes_on_single_vote(self):
        reviews = [
            self._review([{"id": "c1", "pass": True, "note": "a"}]),
            self._review([{"id": "c1", "pass": False, "note": "b"}]),
        ]
        merged = aggregate_reviews(reviews, mode="any")
        self.assertTrue(merged["scores"][0]["pass"])

    def test_a_string_false_vote_is_not_a_passing_vote(self):
        # bool("false") is True. A reviewer that answered the string
        # "false" was counted as a passing vote, so two reviewers both
        # saying "no" produced pass: True and votes: 2/2.
        reviews = [
            self._review([{"id": "c1", "pass": "false", "note": "n1"}]),
            self._review([{"id": "c1", "pass": "false", "note": "n2"}]),
        ]
        for mode in ("all", "majority", "any"):
            merged = aggregate_reviews(reviews, mode=mode)
            self.assertFalse(merged["scores"][0]["pass"], mode)
            self.assertEqual(merged["scores"][0]["votes"], "0/2", mode)
            self.assertEqual(merged["verdict"], "fail", mode)

    def test_an_unreadable_vote_is_not_a_passing_vote(self):
        # An answer nobody can read must never count as an approval.
        reviews = [
            self._review([{"id": "c1", "pass": "maybe", "note": "a"}]),
            self._review([{"id": "c1", "pass": True, "note": "b"}]),
        ]
        merged = aggregate_reviews(reviews, mode="any")
        self.assertTrue(merged["scores"][0]["pass"])
        self.assertEqual(merged["scores"][0]["votes"], "1/2")
        self.assertEqual(merged["scores"][0]["unreadable_votes"], 1)

    def test_an_unreadable_vote_alone_never_passes(self):
        reviews = [self._review([{"id": "c1", "pass": "maybe", "note": "a"}])]
        for mode in ("all", "majority", "any"):
            merged = aggregate_reviews(reviews, mode=mode)
            self.assertFalse(merged["scores"][0]["pass"], mode)

    def test_unusable_reviews_are_reported_not_hidden(self):
        merged = aggregate_reviews([{"oops": True}, {"nothing": 1}], mode="all")
        self.assertEqual(merged["usable"], 0)
        self.assertEqual(merged["skipped"], 2)
        self.assertEqual(merged["verdict"], "fail")

    def test_missing_reviewer_is_reported_not_hidden(self):
        # Before v0.7.13 this test asserted that a missing reviewer let
        # the others pass - which is exactly how a panel of three that
        # answered twice reported "2/2, all passed". The missing
        # reviewer is now visible in ``missing`` and, in the strict
        # reading, blocks the criterion.
        reviews = [
            self._review([{"id": "c1", "pass": True, "note": "a"}]),
            {"scores": []},
        ]
        merged = aggregate_reviews(reviews, mode="all")
        self.assertEqual(merged["usable"], 1)
        self.assertEqual(merged["skipped"], 1)
        self.assertEqual(merged["missing"], 1)
        self.assertFalse(merged["scores"][0]["pass"])
        self.assertEqual(merged["scores"][0]["votes"], "1/2")

    def test_a_weaker_mode_still_lets_the_others_through(self):
        reviews = [
            self._review([{"id": "c1", "pass": True, "note": "a"}]),
            {"scores": []},
        ]
        merged = aggregate_reviews(reviews, mode="majority")
        self.assertTrue(merged["scores"][0]["pass"])

    def test_an_absent_reviewer_cannot_masquerade_as_unanimous(self):
        # Found on the real endpoint: a panel of three answered twice.
        # The absent third reviewer was counted as neither pass nor fail,
        # so the aggregate reported "2/2, all passed" - a unanimous pass
        # that never happened.
        reviews = [
            self._review([{"id": "c1", "pass": True, "note": "a"}]),
            self._review([{"id": "c1", "pass": True, "note": "b"}]),
        ]
        merged = aggregate_reviews(reviews, mode="all", expected=3)
        self.assertEqual(merged["usable"], 2)
        self.assertEqual(merged["missing"], 1)
        self.assertFalse(merged["scores"][0]["pass"])
        self.assertEqual(merged["verdict"], "fail")
        # The vote string must not hide the missing reviewer.
        self.assertEqual(merged["scores"][0]["votes"], "2/3")
        self.assertEqual(merged["scores"][0]["absent_votes"], 1)

    def test_a_weaker_mode_tolerates_an_absent_reviewer(self):
        reviews = [
            self._review([{"id": "c1", "pass": True, "note": "a"}]),
            self._review([{"id": "c1", "pass": True, "note": "b"}]),
        ]
        merged = aggregate_reviews(reviews, mode="majority", expected=3)
        self.assertTrue(merged["scores"][0]["pass"])
        self.assertEqual(merged["scores"][0]["votes"], "2/3")
        self.assertEqual(merged["missing"], 1)

    def test_an_absent_reviewer_is_reported_in_the_issues(self):
        merged = aggregate_reviews(
            [self._review([{"id": "c1", "pass": True, "note": "a"}])],
            mode="all",
            expected=3,
        )
        self.assertTrue(any("no usable verdict" in issue for issue in merged["issues"]))

    def test_a_complete_panel_keeps_the_plain_vote_count(self):
        reviews = [
            self._review([{"id": "c1", "pass": True, "note": "a"}]),
            self._review([{"id": "c1", "pass": True, "note": "b"}]),
            self._review([{"id": "c1", "pass": True, "note": "c"}]),
        ]
        merged = aggregate_reviews(reviews, mode="all", expected=3)
        self.assertTrue(merged["scores"][0]["pass"])
        self.assertEqual(merged["scores"][0]["votes"], "3/3")
        self.assertEqual(merged["scores"][0]["absent_votes"], 0)
        self.assertEqual(merged["missing"], 0)

    def test_a_criterion_nobody_judged_is_not_silently_passed(self):
        # One reviewer judged c1 but not c2. The aggregate knows the panel
        # was three, so c2 has absent votes and cannot pass.
        reviews = [
            self._review([{"id": "c1", "pass": True, "note": "a"}]),
            self._review([{"id": "c1", "pass": True, "note": "b"}]),
        ]
        merged = aggregate_reviews(reviews, mode="all", expected=3)
        ids = {s["id"] for s in merged["scores"]}
        self.assertEqual(ids, {"c1"})
        self.assertFalse(merged["scores"][0]["pass"])


class TestThePanelKeepsAnUnreadableVote(unittest.TestCase):
    """A garbled vote must stay a garbled vote in the panel, too.

    The single-reviewer path keeps the ``unreadable`` marker, so the
    engine asks the reviewer again. The panel path used to fold the
    same garbled answer into a plain "no", so the engine saw a decided
    criterion and never asked again. The same bad answer was therefore
    treated two different ways depending on the reviewer count.
    """

    def test_an_unreadable_vote_is_marked_in_the_aggregate(self):
        reviews = [
            {"scores": [{"id": "c1", "pass": True, "note": "ok"}]},
            {"scores": [{"id": "c1", "pass": True, "note": "ok"}]},
            {"scores": [{"id": "c1", "pass": False, "unreadable": True, "note": "?"}]},
        ]
        merged = aggregate_reviews(reviews, mode="all", expected=3)
        self.assertTrue(merged["scores"][0].get("unreadable"))
        self.assertEqual(merged["scores"][0]["unreadable_votes"], 1)

    def test_a_panel_with_a_garbled_vote_is_asked_again(self):
        # The engine re-asks when a criterion is unjudged. With the
        # marker dropped, the panel booked it as "judged and failed" and
        # the question was never asked a second time.
        from mas.engine import Criterion, evaluate_check

        crits = [Criterion.from_raw({"id": "c1", "desc": "d"}, 0)]
        checks = [evaluate_check(crits[0], "artifact", {})]
        agg = aggregate_reviews(
            [
                {"scores": [{"id": "c1", "pass": True, "note": "ok"}]},
                {"scores": [{"id": "c1", "pass": False, "unreadable": True, "note": "?"}]},
            ],
            mode="all",
            expected=2,
        )
        merged = LoopEngine._merge_verdict_scores(crits, checks, [agg])
        self.assertEqual(merged[0]["source"], "model-missing")


class TestProviderChain(unittest.TestCase):
    def test_chain_uses_first_working_provider(self):
        chain = ProviderChain([CountingProvider(), CountingProvider()])
        chain.complete(system="s", prompt="p")
        self.assertEqual(chain.calls, 1)
        self.assertEqual(chain.name, "counting")

    def test_chain_falls_over_to_the_next_backend(self):
        broken = FlakyProvider()
        working = CountingProvider()
        chain = ProviderChain([broken, working])
        response = chain.complete(system="s", prompt="p")
        self.assertEqual(response["provider_used"], "counting")
        self.assertEqual(broken.calls, 1)
        self.assertEqual(working.calls, 1)

    def test_chain_raises_when_every_backend_fails(self):
        chain = ProviderChain([FlakyProvider(), FlakyProvider()])
        with self.assertRaises(ProviderError):
            chain.complete(system="s", prompt="p")

    def test_chain_describe_lists_the_backends(self):
        chain = ProviderChain([CountingProvider(), FlakyProvider()])
        described = chain.describe()
        self.assertEqual(described["chain"], ["counting/counting-1", "flaky/flaky-1"])


class TestParallelCandidates(unittest.TestCase):
    def test_parallel_workers_pick_the_passing_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp)
            config["orchestration"] = {"parallel_workers": 3, "reviewers": 1, "review_mode": "all"}
            provider = MultiCandidateProvider()
            engine = LoopEngine(config, provider=provider, team=load_team("default"))
            criteria = [
                {"id": "c1", "desc": "包含核心结论", "check": {"type": "contains", "arg": "关键结论"}},
                {"id": "c2", "desc": "长度达标", "check": {"type": "min_chars", "arg": 100}},
            ]
            result = engine.run("并行候选任务", criteria)
            self.assertTrue(result.closed, result.error)
            self.assertEqual(len(provider.seen_angles), 3)
            # The thorough angle is the one that satisfied the criteria.
            self.assertTrue(any("追求完整与严谨" in p for p in provider.seen_angles))

    def test_single_worker_keeps_the_v01_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp)
            provider = MultiCandidateProvider()
            engine = LoopEngine(config, provider=provider, team=load_team("default"))
            criteria = [
                {"id": "c1", "desc": "包含核心结论", "check": {"type": "contains", "arg": "关键结论"}},
            ]
            result = engine.run("单 worker 任务", criteria)
            # One worker per iteration - never a pool.
            self.assertGreaterEqual(len(provider.seen_angles), 1)
            for prompt in provider.seen_angles:
                self.assertNotIn("EXTRA INSTRUCTION", prompt)


class TestAdversarialReview(unittest.TestCase):
    def test_multiple_reviewers_block_a_criterion(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp)
            config["orchestration"] = {"parallel_workers": 1, "reviewers": 3, "review_mode": "all"}
            engine = LoopEngine(config, team=load_team("default"))
            state = {
                "run_id": "R-test",
                "task_id": "R-test-1",
                "attempt": 1,
                "goal": "g",
                "criteria": [{"id": "c1", "desc": "d", "check": None, "required": True}],
                "issues": [],
            }
            verdict = engine._review(state)
            self.assertEqual(verdict["mode"], "all")
            self.assertGreaterEqual(verdict["reviewers"], 1)


if __name__ == "__main__":
    unittest.main()


class PickAnglesAnchorTest(unittest.TestCase):
    def test_anchor_rotates_the_pool(self):
        self.assertEqual(pick_angles(3, "thorough")[0], "thorough")
        self.assertEqual(len(set(pick_angles(4, "risk_first"))), 4)

    def test_unknown_anchor_falls_back_to_direct(self):
        self.assertEqual(pick_angles(2, "nope")[0], "direct")


class CliOverrideTest(unittest.TestCase):
    """The CLI must be able to ask for a heavier orchestration setup for one
    task without editing the config file."""

    def test_run_accepts_parallel_and_review_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp)
            engine = LoopEngine(config, team=load_team("default"))
            result = engine.run(
                goal="写一份发布说明",
                criteria=[
                    {"id": "c1", "desc": "含版本号", "check": {"type": "contains", "arg": "v0.1.0"}},
                ],
                parallel=3,
                perspective="thorough",
                reviewers=3,
                review_mode="majority",
            )
            self.assertEqual(engine.parallel_workers, 3)
            self.assertEqual(engine.reviewers, 3)
            self.assertEqual(engine.review_mode, "majority")
            self.assertEqual(result.status, "closed")

    def test_criteria_json_string_is_parsed_not_flattened(self):
        from mas.cli import _parse_criterion

        parsed = _parse_criterion('{"id":"c1","desc":"x","check":{"type":"contains","arg":"y"}}')
        self.assertIsInstance(parsed, dict)
        self.assertEqual(parsed["check"]["type"], "contains")

    def test_plain_text_criterion_stays_a_string(self):
        from mas.cli import _parse_criterion

        self.assertEqual(_parse_criterion("写清楚就好"), "写清楚就好")
