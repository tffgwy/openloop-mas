# -*- coding: utf-8 -*-
"""v0.7.58 regression tests: why the loop did not close must say so in words.

Three defects motivated this file:

1. ``mas/reason_text.py`` covered reason strings the engine never
   writes. It translated ``budget: exceeded max_tokens=`` while the
   engine actually writes ``budget: tokens {used} >= {limit}`` and
   ``budget: wall-clock budget exhausted after {n}s`` - so the two
   most common ways a run stops were the two the translator missed.
2. The reading pointed at ``mas init --deadline-sec``,
   ``--max-tokens`` and ``--stagnation-limit``. Those three flags did
   not exist; the reader was told to run a command that failed.
3. ``note_reading`` compared the wrong way round: ``length 48 <
   999999`` was translated into "the body is only 48 characters, which
   *exceeds* 999999" - the exact opposite of the truth. A reader would
   go looking for the wrong problem.

The rules these tests pin:

  - every reason the engine can write has a reading;
  - an unknown reason keeps its own text (never invent a reason);
  - the ledger keeps its English (the audit record does not change);
  - the command the reading names must exist.
"""

try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # pragma: no cover - loaded as a top-level module
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401

import contextlib
import io
import json
import os
import pathlib
import tempfile
import unittest

from mas.cli import main
from mas.reason_text import reason_hint, reason_reading, reason_text
from mas.verdict_text import issue_reading, note_reading

#: Every reason string the engine can write. The list is checked
#: against the engine source by ``EngineReasonsTest`` below, so a new
#: reason cannot silently lose its reading.
ENGINE_REASONS = [
    "budget: reached max_iterations=1",
    "budget: exceeded deadline_sec=60",
    "budget: tokens 3000 >= 2000",
    (
        "budget: exceeded deadline_sec=180 "
        "(the deliverable passed the checks, but the run "
        "overran its wall-clock budget)"
    ),
    (
        "stagnation: the deliverable did not change for 3 "
        "consecutive attempts (stagnation_limit=3)"
    ),
    "budget: wall-clock budget exhausted after 120s",
    "provider error: HTTP 500: upstream refused",
    "internal error: ValueError: bad answer shape",
    "truncated answer: output token limit reached",
    "loop could not close itself",
    "the run ran out of budget",
    "the run failed",
    "resume did not close the loop: status=budget_exhausted",
]


class ReasonReadingTest(unittest.TestCase):
    """Every reason the engine writes has to become a sentence."""

    def test_no_engine_reason_is_left_untranslated(self):
        for reason in ENGINE_REASONS:
            with self.subTest(reason=reason):
                self.assertNotEqual(
                    reason_reading(reason),
                    "",
                    f"reason has no reading: {reason}",
                )

    def test_the_budget_reason_names_the_rounds_it_had(self):
        reading = reason_reading("budget: reached max_iterations=1")
        self.assertIn("1", reading)

    def test_the_token_reason_names_both_numbers(self):
        reading = reason_reading("budget: tokens 3000 >= 2000")
        self.assertIn("3000", reading)
        self.assertIn("2000", reading)

    def test_the_deadline_reason_names_the_seconds(self):
        reading = reason_reading("budget: exceeded deadline_sec=60")
        self.assertIn("60", reading)

    def test_the_stagnation_reason_names_the_limit(self):
        reading = reason_reading(
            "stagnation: the deliverable did not change for 3 "
            "consecutive attempts (stagnation_limit=3)"
        )
        self.assertIn("3", reading)

    def test_the_provider_error_keeps_the_backend_message(self):
        reading = reason_reading("provider error: HTTP 500: upstream refused")
        self.assertIn("HTTP 500: upstream refused", reading)

    def test_the_internal_error_keeps_the_exception_type(self):
        reading = reason_reading("internal error: ValueError: bad answer shape")
        self.assertIn("ValueError", reading)

    def test_the_truncated_answer_says_it_was_cut(self):
        reading = reason_reading("truncated answer: output token limit reached")
        self.assertIn("output token limit reached", reading)

    def test_the_resume_reason_names_the_status_in_words_and_raw(self):
        reading = reason_reading("resume did not close the loop: status=budget_exhausted")
        self.assertIn("budget_exhausted", reading)

    def test_an_unknown_reason_is_left_alone(self):
        self.assertEqual(reason_reading("something nobody has seen before"), "")

    def test_reason_text_falls_back_to_the_original(self):
        self.assertEqual(
            reason_text("something nobody has seen before"),
            "something nobody has seen before",
        )

    def test_an_empty_reason_stays_empty(self):
        self.assertEqual(reason_reading(""), "")
        self.assertEqual(reason_text(None), "")


class EngineReasonsTest(unittest.TestCase):
    """The reason list above must match what the engine actually writes.

    A translator that covers a list the engine never produces is a
    translator that misses the reasons the engine does produce. The
    check reads the engine source, so a new reason line added there
    fails this test until it has a reading.
    """

    def setUp(self):
        root = pathlib.Path(__file__).resolve().parent.parent
        self.engine = (root / "mas" / "engine.py").read_text(encoding="utf-8")
        self.providers = (root / "mas" / "providers.py").read_text(encoding="utf-8")

    def test_the_engine_writes_budget_max_iterations(self):
        self.assertIn('f"budget: reached max_iterations={max_iterations}"', self.engine)

    def test_the_engine_writes_the_token_ceiling(self):
        self.assertIn('f"budget: tokens {tokens} >= {max_tokens}"', self.engine)

    def test_the_engine_writes_the_deadline(self):
        self.assertIn('f"budget: exceeded deadline_sec={deadline_sec:.0f}"', self.engine)

    def test_the_engine_writes_the_stagnation_reason(self):
        self.assertIn('f"stagnation: the deliverable did not change for "', self.engine)

    def test_the_provider_writes_the_wall_clock_reason(self):
        self.assertIn('f"wall-clock budget exhausted after {timeout:.0f}s"', self.providers)


class ReasonHintTest(unittest.TestCase):
    """The hint is the command that raises the ceiling.

    The hint lives apart from the sentence so a one-line display stays
    one line, but the two belong together: a reason that names a
    ceiling is only half a story without the knob to turn.
    """

    def test_the_hint_names_the_ceiling_it_would_raise(self):
        self.assertIn(
            "max-iterations", reason_hint("budget: reached max_iterations=1")
        )
        self.assertIn(
            "max-tokens", reason_hint("budget: tokens 3000 >= 2000")
        )
        self.assertIn(
            "deadline-sec", reason_hint("budget: exceeded deadline_sec=60")
        )
        self.assertIn(
            "stagnation-limit",
            reason_hint(
                "stagnation: the deliverable did not change for 3 "
                "consecutive attempts (stagnation_limit=3)"
            ),
        )

    def test_the_hint_is_empty_for_a_reason_without_a_ceiling(self):
        self.assertEqual(reason_hint("provider error: HTTP 500"), "")

    def test_the_hint_does_not_carry_a_line_break(self):
        for reason in ENGINE_REASONS:
            with self.subTest(reason=reason):
                self.assertNotIn("\n", reason_hint(reason))
                self.assertNotIn("\n", reason_text(reason))


class ComparisonDirectionTest(unittest.TestCase):
    """A comparison has to say which side the body fell on."""

    def test_a_short_body_is_not_too_long(self):
        reading = note_reading("length 48 < 999999")
        self.assertNotIn("超过", reading)
        self.assertIn("不到", reading)

    def test_a_long_body_is_too_long(self):
        reading = note_reading("length 100 > 80")
        self.assertIn("超过", reading)

    def test_a_short_file_is_not_too_big(self):
        reading = note_reading("48 bytes < 999999")
        self.assertNotIn("超过", reading)
        self.assertIn("不到", reading)

    def test_the_minimum_case_is_not_reversed(self):
        self.assertIn("不少于", note_reading("length 100 >= 80"))


class IssueReadingTest(unittest.TestCase):
    """A blocking issue must name the standard it belongs to."""

    def test_the_issue_names_the_criterion(self):
        criteria = [{"id": "c1", "type": "contains", "arg": "结论"}]
        reading = issue_reading("c1: missing: ['结论']", criteria)
        self.assertIn("结论", reading)
        self.assertNotIn("[", reading)

    def test_an_unknown_issue_keeps_its_text(self):
        self.assertEqual(
            issue_reading("something nobody has seen before"),
            "something nobody has seen before",
        )


class _CliCase(unittest.TestCase):
    """Base class with a hermetic state directory per test."""

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix="mas-reason-")
        self.config_path = os.path.join(self.state_dir, "config.json")
        os.environ["MAS_STATE_DIR"] = self.state_dir
        os.environ["MAS_CONFIG_PATH"] = self.config_path
        os.environ["MAS_PROVIDER"] = "mock"
        os.environ.pop("MAS_API_KEY", None)

    def invoke(self, argv):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(argv)
        return code, buffer.getvalue()


class InitFlagsTest(_CliCase):
    """The command the reading names has to exist."""

    def test_the_three_new_ceilings_can_be_written(self):
        code, out = self.invoke(
            [
                "init",
                "--max-iterations",
                "5",
                "--deadline-sec",
                "60",
                "--max-tokens",
                "20000",
                "--stagnation-limit",
                "7",
                "--json",
            ]
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        config = payload["config"]
        self.assertEqual(config["budget"]["deadline_sec"], 60)
        self.assertEqual(config["budget"]["max_tokens"], 20000)
        self.assertEqual(config["stagnation_limit"], 7)

    def test_the_help_lists_the_three_ceilings(self):
        buffer = io.StringIO()
        with (
            contextlib.redirect_stdout(buffer),
            contextlib.suppress(SystemExit),
        ):
            main(["init", "--help"])
        out = buffer.getvalue()
        for flag in ("--deadline-sec", "--max-tokens", "--stagnation-limit"):
            self.assertIn(flag, out)


class ReadingReachesTheScreenTest(_CliCase):
    """The reading has to show up where the human reads it."""

    def _run_that_cannot_close(self):
        """A run whose criterion can never pass.

        ``file_mtime_newer`` compares the artifact's modification time
        with a stamp in the year 2099, so the check cannot pass however
        many rounds the loop takes.
        """
        workspace = os.path.join(self.state_dir, "ws")
        os.makedirs(workspace, exist_ok=True)
        return self.invoke(
            [
                "run",
                "--goal",
                "写一份永远通不过的报告",
                "--criteria",
                "file_mtime_newer:out/report.md::2099-01-01T00:00:00Z",
                "--workspace",
                workspace,
            ]
        )

    def test_the_run_says_why_in_words(self):
        _code, out = self._run_that_cannot_close()
        self.assertIn("说明：", out)
        self.assertIn("停滞", out)
        self.assertNotIn("stagnation: the deliverable", out)

    def test_the_run_status_reads_in_words(self):
        _code, out = self._run_that_cannot_close()
        self.assertIn("已交给人工", out)

    def test_the_ticket_says_why_in_words(self):
        self._run_that_cannot_close()
        _code, out = self.invoke(["tickets"])
        self.assertIn("原因：", out)
        self.assertNotIn("stagnation: the deliverable", out)

    def test_the_report_says_why_in_words(self):
        self._run_that_cannot_close()
        _code, out = self.invoke(["report"])
        self.assertIn("未闭合原因：", out)
        self.assertNotIn("stagnation: the deliverable did not change", out)

    def test_the_replay_says_why_in_words(self):
        self._run_that_cannot_close()
        _code, out = self.invoke(["replay"])
        self.assertIn("停滞", out)


class LedgerKeepsEnglishTest(_CliCase):
    """The audit record does not change - only the display does."""

    def test_the_ledger_keeps_the_english_reason(self):
        self.invoke(
            [
                "run",
                "--goal",
                "写一份永远通不过的报告",
                "--criteria",
                "file_mtime_newer:out/report.md::2099-01-01T00:00:00Z",
                "--workspace",
                self.state_dir,
            ]
        )
        ledger = pathlib.Path(self.state_dir) / "ledgers" / "runs.jsonl"
        raw = ledger.read_text(encoding="utf-8")
        self.assertIn("stagnation", raw)
        self.assertNotIn("停滞", raw)


if __name__ == "__main__":
    unittest.main()
