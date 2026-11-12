"""Engine level tests: closure, stagnation, budget, deterministic checks."""
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
from mas.demo import StaticProvider
from mas.engine import Criterion, LoopEngine, evaluate_check
from mas.teams import load_team


def make_config(tmp, **overrides):
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    config["state_dir"] = tmp
    config["budget"] = dict(config["budget"])
    config["budget"].update(overrides.pop("budget", {}))
    config.update(overrides)
    return config


class TestCriterionChecks(unittest.TestCase):
    def test_contains_and_missing(self):
        criterion = Criterion("c1", "包含", {"type": "contains", "arg": "关键结论"})
        self.assertTrue(evaluate_check(criterion, "这里有关键结论。")["pass"])
        result = evaluate_check(criterion, "没有。")
        self.assertFalse(result["pass"])
        self.assertIn("关键结论", result["note"])

    def test_min_max_chars(self):
        criterion = Criterion("c1", "长度", {"type": "min_chars", "arg": 10})
        self.assertFalse(evaluate_check(criterion, "短")["pass"])
        self.assertTrue(evaluate_check(criterion, "0123456789abc")["pass"])
        criterion = Criterion("c2", "长度", {"type": "max_chars", "arg": 5})
        self.assertFalse(evaluate_check(criterion, "0123456789")["pass"])

    def test_regex_and_json_keys(self):
        criterion = Criterion("c1", "正则", {"type": "regex", "arg": r"^#\s"})
        self.assertTrue(evaluate_check(criterion, "# 标题")["pass"])
        criterion = Criterion("c2", "JSON", {"type": "json_keys", "arg": ["a", "b"]})
        self.assertFalse(evaluate_check(criterion, "not json")["pass"])
        self.assertTrue(evaluate_check(criterion, json.dumps({"a": 1, "b": 2}))["pass"])

    def test_unknown_check_is_not_a_silent_pass(self):
        criterion = Criterion("c1", "未知", {"type": "nope", "arg": 1})
        result = evaluate_check(criterion, "anything")
        self.assertIsNone(result["pass"])


class TestLoopClosure(unittest.TestCase):
    def test_loop_closes_and_ledger_has_one_entry_per_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp)
            engine = LoopEngine(config, team=load_team("default"))
            criteria = [
                {"id": "c1", "desc": "包含核心结论", "check": {"type": "contains", "arg": "关键结论"}},
                {"id": "c2", "desc": "长度达标", "check": {"type": "min_chars", "arg": 220}},
            ]
            result = engine.run("写一段短文", criteria)
            self.assertEqual(result.status, "closed")
            self.assertTrue(result.closed)
            self.assertGreaterEqual(result.iterations, 2)
            entries = engine.ledger.entries()
            roles = [row["role"] for row in entries]
            self.assertIn("planner", roles)
            self.assertEqual(roles.count("closure"), result.iterations)
            for row in entries:
                if row["role"] == "closure":
                    self.assertIn("artifact_sha", row)
                    self.assertIn("verdict", row)
                    self.assertIn("decision", row)

    def test_rule_result_overrides_model_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp)
            engine = LoopEngine(config, team=load_team("default"))
            merged = LoopEngine._merge_scores(
                [Criterion("c1", "d", {"type": "contains", "arg": "x"})],
                [{"id": "c1", "pass": False, "note": "rule says no"}],
                {"scores": [{"id": "c1", "pass": True, "note": "model says yes"}]},
            )
            self.assertFalse(merged[0]["pass"])
            self.assertEqual(merged[0]["source"], "rule")

    def test_unjudged_criterion_cannot_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp)
            engine = LoopEngine(config, team=load_team("default"))
            merged = LoopEngine._merge_scores(
                [Criterion("c1", "no rule")],
                [{"id": "c1", "pass": None, "note": "no machine check"}],
                {"scores": []},
            )
            self.assertFalse(merged[0]["pass"])

    def test_stagnation_escalates_with_ticket(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp, stagnation_limit=3)
            provider = StaticProvider("同样的输出")
            engine = LoopEngine(config, provider=provider, team=load_team("default"))
            result = engine.run("停滞任务", [{"id": "c1", "desc": "d", "check": {"type": "min_chars", "arg": 9999}}])
            self.assertEqual(result.status, "escalated")
            self.assertTrue(result.ticket_id)
            ticket = engine.tickets.get(result.ticket_id)
            self.assertEqual(ticket["state"], "waiting_human")
            self.assertIn("last_artifact", ticket["payload"])

    def test_budget_stops_the_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp, budget={"max_iterations": 2})
            engine = LoopEngine(config, team=load_team("default"))
            result = engine.run("预算任务", [{"id": "c1", "desc": "d", "check": {"type": "min_chars", "arg": 999999}}])
            self.assertEqual(result.status, "budget_exhausted")
            self.assertEqual(result.iterations, 2)

    def test_resume_uses_human_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp, stagnation_limit=2)
            provider = StaticProvider("同样的输出")
            engine = LoopEngine(config, provider=provider, team=load_team("default"))
            result = engine.run("停滞任务", [{"id": "c1", "desc": "d", "check": {"type": "min_chars", "arg": 9999}}])
            self.assertEqual(result.status, "escalated")
            engine.tickets.resolve(result.ticket_id, "approved", "请补充数据")
            resumed = engine.resume(result.ticket_id, "请补充数据")
            self.assertIn(resumed.status, ("closed", "escalated", "budget_exhausted"))
            self.assertEqual(engine.tickets.get(result.ticket_id)["state"], "resolved")

    def test_optional_criterion_never_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp)
            engine = LoopEngine(config, team=load_team("default"))
            merged = LoopEngine._merge_scores(
                [Criterion("c1", "optional", None, required=False)],
                [{"id": "c1", "pass": None, "note": "no machine check"}],
                {"scores": []},
            )
            self.assertTrue(merged[0]["pass"])


if __name__ == "__main__":
    unittest.main()
