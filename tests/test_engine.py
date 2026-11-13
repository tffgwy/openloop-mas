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
from mas.engine import Criterion, LoopEngine, evaluate_check, validate_criteria
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

    def test_a_failed_resume_keeps_the_ticket_in_the_human_queue(self):
        # A resumed loop that still cannot close must NOT look finished.
        # Marking the ticket resolved anyway emptied the human queue: the
        # escalation disappeared from `mas tickets` while the very problem
        # it was opened for was still open.
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp, stagnation_limit=2)
            provider = StaticProvider("同样的输出")
            engine = LoopEngine(config, provider=provider, team=load_team("default"))
            result = engine.run(
                "停滞任务",
                [{"id": "c1", "desc": "d", "check": {"type": "min_chars", "arg": 9999}}],
            )
            self.assertEqual(result.status, "escalated")
            resumed = engine.resume(result.ticket_id, "再试一次")
            self.assertFalse(resumed.closed)
            ticket = engine.tickets.get(result.ticket_id)
            self.assertEqual(ticket["state"], "waiting_human")
            self.assertIsNone(ticket["decision"])
            self.assertEqual(ticket["note"], "再试一次")
            attempts = (ticket.get("payload") or {}).get("resume_attempts") or []
            self.assertEqual(len(attempts), 1)
            self.assertIn("resume did not close the loop", attempts[0]["reason"])

    def test_a_successful_resume_resolves_the_ticket(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp, stagnation_limit=2)
            provider = StaticProvider("同样的输出")
            engine = LoopEngine(config, provider=provider, team=load_team("default"))
            result = engine.run(
                "停滞任务",
                [{"id": "c1", "desc": "d", "check": {"type": "min_chars", "arg": 9999}}],
            )
            self.assertEqual(result.status, "escalated")
            engine.tickets.resolve(result.ticket_id, "approved", "请补充数据")
            # A criterion the artifact actually satisfies: the resumed
            # loop closes, so the ticket may be closed as well.
            ticket = engine.tickets.get(result.ticket_id)
            payload = ticket["payload"]
            payload["criteria"] = [{"id": "c1", "desc": "d", "check": {"type": "min_chars", "arg": 1}}]
            engine.tickets._file(result.ticket_id).write_text(
                json.dumps(ticket, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            resumed = engine.resume(result.ticket_id, "标准已改")
            self.assertTrue(resumed.closed)
            self.assertEqual(engine.tickets.get(result.ticket_id)["state"], "resolved")

    def test_a_failed_resume_does_not_open_a_second_ticket(self):
        # One problem must be one ticket. A resumed loop that escalates
        # again used to open a brand new ticket, so the human queue listed
        # the same escalation twice.
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp, stagnation_limit=2)
            provider = StaticProvider("同样的输出")
            engine = LoopEngine(config, provider=provider, team=load_team("default"))
            result = engine.run(
                "停滞任务",
                [{"id": "c1", "desc": "d", "check": {"type": "min_chars", "arg": 9999}}],
            )
            self.assertEqual(result.status, "escalated")
            before = len(engine.tickets.list())
            resumed = engine.resume(result.ticket_id, "再试一次")
            self.assertFalse(resumed.closed)
            after = engine.tickets.list()
            self.assertEqual(len(after), before)
            waiting = [t for t in after if t["state"] == "waiting_human"]
            self.assertEqual(len(waiting), 1)
            self.assertEqual(waiting[0]["id"], result.ticket_id)
            # The fresh evidence lands on the ticket that was already open.
            payload = waiting[0]["payload"]
            self.assertEqual(payload.get("attempts_total"), 1)

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
            self.assertFalse(merged[0]["blocking"])

    def test_optional_criterion_keeps_the_real_rule_result(self):
        # An optional criterion used to be reported as "pass" no matter
        # what the rule said. The rule result is now recorded truthfully -
        # it just does not block the closure.
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp)
            engine = LoopEngine(config, team=load_team("default"))
            merged = LoopEngine._merge_scores(
                [Criterion("c1", "optional", {"type": "min_chars", "arg": 10},
                           required=False)],
                [{"id": "c1", "pass": False, "note": "length 3 < 10", "source": "rule"}],
                {"scores": []},
            )
            self.assertFalse(merged[0]["pass"])
            self.assertEqual(merged[0]["source"], "rule")
            self.assertTrue(merged[0]["optional"])
            self.assertFalse(merged[0]["blocking"])

    def test_optional_criterion_records_the_reviewers_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp)
            engine = LoopEngine(config, team=load_team("default"))
            merged = LoopEngine._merge_scores(
                [Criterion("c1", "optional", None, required=False)],
                [{"id": "c1", "pass": None, "note": "no machine check"}],
                {"scores": [{"id": "c1", "pass": False, "note": "太单薄"}]},
            )
            self.assertFalse(merged[0]["pass"])
            self.assertFalse(merged[0]["blocking"])
            self.assertIn("太单薄", merged[0]["note"])

    def test_required_criterion_is_blocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp)
            engine = LoopEngine(config, team=load_team("default"))
            merged = LoopEngine._merge_scores(
                [Criterion("c1", "required", {"type": "contains", "arg": "x"})],
                [{"id": "c1", "pass": True, "note": "ok", "source": "rule"}],
                {"scores": []},
            )
            self.assertTrue(merged[0]["blocking"])

    def test_failing_optional_criterion_does_not_block_closure(self):
        # The regression guard: once the optional rule result became real,
        # a failing optional standard must still not keep the loop open.
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp)
            engine = LoopEngine(config, team=load_team("default"))
            result = engine.run(
                "带可选标准的任务",
                [
                    {"id": "c1", "desc": "必选", "check": {"type": "contains", "arg": "关键结论"}},
                    {"id": "c2", "desc": "可选", "required": False,
                     "check": {"type": "min_chars", "arg": 999999}},
                ],
            )
            self.assertEqual(result.status, "closed")
            self.assertTrue(result.closed)
            by_id = {s["id"]: s for s in result.scores}
            self.assertTrue(by_id["c1"]["pass"])
            self.assertFalse(by_id["c2"]["pass"])
            self.assertFalse(by_id["c2"]["blocking"])
            # The optional failure is visible, but it is not a reason to
            # revise: the loop closed without a second attempt.
            self.assertEqual(result.issues, [])
            self.assertEqual(result.iterations, 1)

    def test_failing_required_criterion_still_blocks_closure(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp)
            engine = LoopEngine(config, team=load_team("default"))
            result = engine.run(
                "必选不达标的任务",
                [
                    {"id": "c1", "desc": "必选", "check": {"type": "min_chars", "arg": 999999}},
                    {"id": "c2", "desc": "可选", "required": False,
                     "check": {"type": "contains", "arg": "关键结论"}},
                ],
            )
            self.assertNotEqual(result.status, "closed")
            by_id = {s["id"]: s for s in result.scores}
            self.assertFalse(by_id["c1"]["pass"])
            self.assertTrue(by_id["c1"]["blocking"])



class RequiredFlagTest(unittest.TestCase):
    """The ``required`` flag must be read by meaning, not by truthiness."""

    def test_string_false_is_not_truthy(self):
        # bool("false") is True. A criterion that travels through JSON,
        # an MCP client or a shell script often arrives with the flag as a
        # string - and the user asked for a soft standard, not a hard one.
        criterion = Criterion.from_raw(
            {"id": "c1", "desc": "d", "required": "false"}, 0
        )
        self.assertFalse(criterion.required)

    def test_string_spellings_of_false(self):
        for raw in ("false", "no", "0", "False", "NO"):
            with self.subTest(raw=raw):
                criterion = Criterion.from_raw(
                    {"id": "c1", "desc": "d", "required": raw}, 0
                )
                self.assertFalse(criterion.required, raw)

    def test_string_spellings_of_true(self):
        for raw in ("true", "yes", "1", "True", "YES"):
            with self.subTest(raw=raw):
                criterion = Criterion.from_raw(
                    {"id": "c1", "desc": "d", "required": raw}, 0
                )
                self.assertTrue(criterion.required, raw)

    def test_real_booleans_and_numbers(self):
        self.assertFalse(Criterion.from_raw({"required": False}, 0).required)
        self.assertFalse(Criterion.from_raw({"required": 0}, 0).required)
        self.assertTrue(Criterion.from_raw({"required": True}, 0).required)
        self.assertTrue(Criterion.from_raw({"required": 1}, 0).required)

    def test_missing_and_unreadable_values_stay_required(self):
        # The strict reading is the safe one: an unreadable flag must
        # never silently relax a standard.
        self.assertTrue(Criterion.from_raw({"desc": "d"}, 0).required)
        self.assertTrue(Criterion.from_raw({"desc": "d", "required": None}, 0).required)
        self.assertTrue(Criterion.from_raw({"desc": "d", "required": "maybe"}, 0).required)
        self.assertTrue(Criterion.from_raw({"desc": "d", "required": []}, 0).required)

    def test_a_string_false_optional_criterion_does_not_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp)
            engine = LoopEngine(config, team=load_team("default"))
            result = engine.run(
                "字符串 required 的任务",
                [
                    {"id": "c1", "desc": "必选",
                     "check": {"type": "contains", "arg": "关键结论"}},
                    {"id": "c2", "desc": "可选", "required": "false",
                     "check": {"type": "min_chars", "arg": 999999}},
                ],
            )
            self.assertEqual(result.status, "closed")
            by_id = {s["id"]: s for s in result.scores}
            self.assertFalse(by_id["c2"]["blocking"])


class CheckSemanticsTest(unittest.TestCase):
    """Pin the exact semantics of every supported machine check type."""

    def check(self, kind, arg, artifact):
        criterion = Criterion("c1", "d", {"type": kind, "arg": arg})
        return evaluate_check(criterion, artifact)

    def test_contains_accepts_array_and_requires_all(self):
        self.assertTrue(self.check("contains", ["a", "b"], "a and b")["pass"])
        result = self.check("contains", ["a", "b"], "only a")
        self.assertFalse(result["pass"])
        self.assertIn("b", result["note"])

    def test_not_contains_fails_if_any_forbidden_present(self):
        self.assertTrue(self.check("not_contains", ["x", "y"], "clean output")["pass"])
        result = self.check("not_contains", ["x", "y"], "contains x")
        self.assertFalse(result["pass"])

    def test_char_limits_count_characters_including_whitespace(self):
        self.assertTrue(self.check("min_chars", 5, "abcd\n")["pass"])
        self.assertFalse(self.check("min_chars", 6, "abcd\n")["pass"])
        self.assertTrue(self.check("max_chars", 5, "abcd\n")["pass"])
        self.assertFalse(self.check("max_chars", 4, "abcd\n")["pass"])

    def test_regex_is_dotall_by_default(self):
        self.assertTrue(self.check("regex", "a.b", "a\nb")["pass"])
        self.assertFalse(self.check("regex", "a.b", "ab")["pass"])

    def test_lines_min_counts_only_non_empty_lines(self):
        self.assertTrue(self.check("lines_min", 2, "one\n\n\ntwo")["pass"])
        self.assertFalse(self.check("lines_min", 3, "one\n\n\ntwo")["pass"])

    def test_section_requires_second_level_heading(self):
        self.assertTrue(self.check("section", "结论", "## 结论\n内容")["pass"])
        self.assertFalse(self.check("section", "结论", "# 结论\n内容")["pass"])
        self.assertFalse(self.check("section", "结论", "### 结论\n内容")["pass"])

    def test_section_matches_a_heading_line_not_any_substring(self):
        # A body sentence that merely mentions the heading is not a section.
        self.assertFalse(self.check("section", "结论", "正文里提到 ## 结论这个词")["pass"])
        # A longer heading must not satisfy a shorter one.
        self.assertFalse(self.check("section", "结论", "## 结论分析\n内容")["pass"])
        # Trailing whitespace on the heading line is tolerated.
        self.assertTrue(self.check("section", "结论", "## 结论   \n内容")["pass"])

    def test_json_keys_requires_json_object(self):
        payload = json.dumps({"a": 1, "b": 2}, ensure_ascii=False)
        self.assertTrue(self.check("json_keys", ["a", "b"], payload)["pass"])
        self.assertFalse(self.check("json_keys", ["a", "c"], payload)["pass"])
        self.assertFalse(self.check("json_keys", "a", "[1, 2]")["pass"])
        self.assertFalse(self.check("json_keys", "a", "not json")["pass"])

    def test_unknown_type_is_unjudged_not_passed(self):
        result = self.check("mystery", "x", "anything")
        self.assertIsNone(result["pass"])
        self.assertIn("unknown check type", result["note"])

    def test_invalid_argument_is_reported_not_raised(self):
        # A malformed argument is caught by the pre-check, so the checker
        # reports it instead of raising or silently coercing it.
        result = self.check("min_chars", "not-a-number", "abc")
        self.assertIsNone(result["pass"])
        self.assertIn("invalid argument", result["note"])


class ModelVerdictTruthinessTest(unittest.TestCase):
    """A model's answer must be read, not coerced with ``bool()``.

    ``bool("false")`` is ``True``. A verifier that answered
    ``{"id": "c1", "pass": "false"}`` therefore closed a criterion that it
    had just failed. The merge layer is the last line of defence, so it
    has to read the meaning of the value instead of trusting its
    truthiness.
    """

    def _merge(self, value):
        from mas.engine import Criterion, LoopEngine, evaluate_check

        criterion = Criterion(id="c1", desc="说清楚就好", check=None)
        check = evaluate_check(criterion, "x")
        verdict = {"scores": [{"id": "c1", "pass": value, "note": "t"}]}
        return LoopEngine._merge_scores([criterion], [check], verdict)[0]

    def test_the_string_false_is_read_as_false(self):
        score = self._merge("false")
        self.assertFalse(score["pass"])
        self.assertEqual(score["source"], "model")

    def test_the_string_true_is_read_as_true(self):
        score = self._merge("true")
        self.assertTrue(score["pass"])

    def test_digit_strings_are_read(self):
        self.assertFalse(self._merge("0")["pass"])
        self.assertTrue(self._merge("1")["pass"])

    def test_an_unreadable_answer_stays_unjudged(self):
        for value in ("maybe", "", None):
            with self.subTest(value=value):
                score = self._merge(value)
                self.assertFalse(score["pass"])
                self.assertEqual(score["source"], "model-missing")

    def test_real_booleans_are_untouched(self):
        self.assertFalse(self._merge(False)["pass"])
        self.assertTrue(self._merge(True)["pass"])

    def test_the_multi_reviewer_path_agrees(self):
        from mas.engine import Criterion, LoopEngine, evaluate_check

        criterion = Criterion(id="c1", desc="说清楚就好", check=None)
        check = evaluate_check(criterion, "x")
        verdict = {"scores": [{"id": "c1", "pass": "false", "note": "t"}]}
        score = LoopEngine._merge_review_scores([criterion], [check], verdict)[0]
        self.assertFalse(score["pass"])


class UnknownCheckTypeCannotFakeClosureTest(unittest.TestCase):
    """An unknown check type must never be closed by the model.

    ``evaluate_check`` reported an unknown type as undecided
    (``pass=None``). The merge step used "the check did not decide" as
    "there is no check", so the criterion fell back to the model verifier:
    a model that said "looks fine" closed a criterion whose rule had never
    been written. The documented contract says an unknown type is
    *unjudged*, which means it never passes.
    """

    def _criterion(self, kind="mystery", arg="x"):
        from mas.engine import Criterion

        return Criterion(id="c1", desc="产物满足某条标准", check={"type": kind, "arg": arg})

    def test_the_check_reports_the_type_as_invalid(self):
        from mas.engine import evaluate_check

        result = evaluate_check(self._criterion(), "anything")
        self.assertIsNone(result["pass"])
        self.assertEqual(result["source"], "invalid")

    def test_the_model_cannot_close_it(self):
        from mas.engine import LoopEngine, evaluate_check

        criterion = self._criterion()
        check = evaluate_check(criterion, "anything")
        verdict = {"scores": [{"id": "c1", "pass": True, "note": "模型说没问题"}]}
        scores = LoopEngine._merge_verdict_scores([criterion], [check], [verdict])
        self.assertFalse(scores[0]["pass"])
        self.assertEqual(scores[0]["source"], "invalid")

    def test_an_invalid_argument_cannot_be_closed_by_the_model_either(self):
        from mas.engine import LoopEngine, evaluate_check

        criterion = self._criterion("min_chars", "not-a-number")
        check = evaluate_check(criterion, "anything")
        verdict = {"scores": [{"id": "c1", "pass": True, "note": "模型说没问题"}]}
        scores = LoopEngine._merge_verdict_scores([criterion], [check], [verdict])
        self.assertFalse(scores[0]["pass"])
        self.assertEqual(scores[0]["source"], "invalid")

    def test_the_multi_reviewer_path_cannot_close_it_either(self):
        """The adversarial-review path shares the merge, so it must behave
        the same way - otherwise a run with several reviewers would reopen
        the fake closure that the single-reviewer path closes."""
        from mas.engine import LoopEngine, evaluate_check

        criterion = self._criterion()
        check = evaluate_check(criterion, "anything")
        verdict = {"scores": [{"id": "c1", "pass": True, "note": "评审说没问题"}]}
        scores = LoopEngine._merge_review_scores([criterion], [check], verdict)
        self.assertFalse(scores[0]["pass"])
        self.assertEqual(scores[0]["source"], "invalid")

    def test_a_criterion_without_a_check_is_still_decided_by_the_model(self):
        from mas.engine import Criterion, LoopEngine, evaluate_check

        criterion = Criterion(id="c1", desc="说清楚就好", check=None)
        check = evaluate_check(criterion, "anything")
        verdict = {"scores": [{"id": "c1", "pass": True, "note": "模型说没问题"}]}
        scores = LoopEngine._merge_verdict_scores([criterion], [check], [verdict])
        self.assertTrue(scores[0]["pass"])
        self.assertEqual(scores[0]["source"], "model")


class DegenerateArgumentTest(unittest.TestCase):
    """A machine check with no usable argument must never look like a pass.

    The old checker degraded a missing argument into a constant: an empty
    regex matched everything, an empty ``contains`` list required nothing,
    and a missing ``min_chars`` became "at least 0 characters". The loop
    then reported a green light for a criterion nobody had specified.
    """

    def check(self, kind, arg, artifact="some artifact text"):
        criterion = Criterion("c1", "d", {"type": kind, "arg": arg})
        return evaluate_check(criterion, artifact)

    def test_missing_argument_is_unjudged_for_every_type(self):
        for kind in ("contains", "not_contains", "min_chars", "max_chars",
                     "regex", "lines_min", "section", "json_keys"):
            with self.subTest(kind=kind):
                result = self.check(kind, None)
                self.assertIsNone(result["pass"], kind)
                self.assertIn("missing argument", result["note"])
                self.assertEqual(result["source"], "invalid")

    def test_empty_argument_is_unjudged_for_every_type(self):
        for kind in ("contains", "not_contains", "min_chars", "max_chars",
                     "regex", "lines_min", "section", "json_keys"):
            with self.subTest(kind=kind):
                result = self.check(kind, "")
                self.assertIsNone(result["pass"], kind)
                self.assertEqual(result["source"], "invalid")

    def test_empty_list_argument_is_unjudged(self):
        for kind in ("contains", "not_contains", "json_keys"):
            with self.subTest(kind=kind):
                result = self.check(kind, [])
                self.assertIsNone(result["pass"], kind)

    def test_empty_regex_never_matches_everything(self):
        result = self.check("regex", "")
        self.assertIsNone(result["pass"])
        self.assertNotEqual(result["source"], "rule")

    def test_negative_count_is_rejected(self):
        for kind in ("min_chars", "max_chars", "lines_min"):
            with self.subTest(kind=kind):
                result = self.check(kind, -1)
                self.assertIsNone(result["pass"], kind)
                self.assertIn("non-negative", result["note"])

    def test_uncompilable_regex_is_rejected_before_matching(self):
        result = self.check("regex", "([unclosed")
        self.assertIsNone(result["pass"])
        self.assertIn("cannot compile", result["note"])

    def test_zero_count_stays_legal(self):
        # 0 is a degenerate but *well formed* bound; rejecting it would
        # break configurations that were always valid.
        self.assertTrue(self.check("min_chars", 0)["pass"])
        self.assertTrue(self.check("lines_min", 0)["pass"])

    def test_validate_criteria_reports_only_broken_checks(self):
        crits = [
            Criterion("c1", "d", {"type": "regex", "arg": ""}),
            Criterion("c2", "d", {"type": "min_chars", "arg": None}),
            Criterion("c3", "d", {"type": "contains", "arg": ["a"]}),
            Criterion("c4", "d", None),
        ]
        problems = validate_criteria(crits)
        self.assertEqual(len(problems), 2)
        self.assertTrue(any(p.startswith("c1:") for p in problems))
        self.assertTrue(any(p.startswith("c2:") for p in problems))

    def test_run_fails_fast_on_a_broken_machine_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp)
            engine = LoopEngine(config, team=load_team("default"))
            with self.assertRaises(ValueError) as ctx:
                engine.run("任务", [{"id": "c1", "desc": "d",
                                      "check": {"type": "regex", "arg": ""}}])
            self.assertIn("无法执行", str(ctx.exception))
            # A rejected standard must not leave a half-finished run behind.
            self.assertEqual(len(engine.ledger.entries()), 0)



if __name__ == "__main__":
    unittest.main()
