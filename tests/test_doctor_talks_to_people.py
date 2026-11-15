# -*- coding: utf-8 -*-
"""``mas doctor`` must speak to a person, not to a machine.

The self-check used to print one line per check - a machine name, a
status code, and a path - and then exit zero. A beginner who had just
installed the tool saw six WARN lines, a zero exit code and no idea
whether the thing worked. These tests pin down the three properties
that make the output actionable:

1. every check says *what it is* in words;
2. the report ends with one conclusion and one next command;
3. the conclusion distinguishes "the loop cannot run" from "the loop
   runs but no agent can reach it".
"""
try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # loaded as a top-level module (unittest -s tests)
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401


import unittest

from mas.doctor import (
    CHECK_PLAIN_LANGUAGE,
    ESSENTIAL_CHECKS,
    OPTIONAL_CHECKS,
    RECOMMENDED_CHECKS,
    explain,
    format_doctor,
    is_optional,
    is_recommended,
    run_doctor,
    verdict,
)


def _row(name, status, detail="detail"):
    return {"check": name, "status": status, "detail": detail}


class PlainLanguageTest(unittest.TestCase):
    def test_every_check_name_has_an_explanation(self):
        names = set(CHECK_PLAIN_LANGUAGE)
        for name in names:
            self.assertTrue(explain(name).strip(), name)

    def test_an_unknown_check_has_no_explanation(self):
        self.assertEqual(explain("no_such_check"), "")

    def test_the_tiers_do_not_overlap(self):
        self.assertFalse(OPTIONAL_CHECKS & ESSENTIAL_CHECKS)
        self.assertFalse(OPTIONAL_CHECKS & RECOMMENDED_CHECKS)
        self.assertFalse(RECOMMENDED_CHECKS & ESSENTIAL_CHECKS)

    def test_optional_checks_are_the_agent_reachability_ones(self):
        for name in ("skill_codex", "skill_claude", "skills_sync", "mcp_codex", "mcp_claude"):
            self.assertTrue(is_optional(name), name)
            self.assertFalse(is_recommended(name), name)

    def test_the_config_file_is_recommended_not_required(self):
        # The runtime falls back to defaults, so a missing config file
        # must not be labelled "必需" while also being reported as a
        # warning - that contradiction is what confused beginners.
        self.assertTrue(is_recommended("config"))
        self.assertFalse(is_optional("config"))
        self.assertNotIn("config", ESSENTIAL_CHECKS)


class VerdictTest(unittest.TestCase):
    def test_a_broken_essential_check_says_the_loop_cannot_run(self):
        final = verdict([_row("python", "ok"), _row("ledger", "fail", "不可写")])
        self.assertEqual(final["state"], "broken")
        self.assertFalse(final["loop_usable"])
        self.assertIn("ledger", final["headline"])

    def test_missing_optional_parts_mean_the_agent_cannot_reach_the_loop(self):
        final = verdict(
            [
                _row("python", "ok"),
                _row("ledger", "ok"),
                _row("skill_codex", "warn"),
                _row("mcp_codex", "warn"),
            ]
        )
        self.assertEqual(final["state"], "agent_not_reachable")
        self.assertTrue(final["loop_usable"])
        self.assertFalse(final["agent_reachable"])
        self.assertEqual(sorted(final["missing_optional"]), ["mcp_codex", "skill_codex"])

    def test_a_failed_optional_part_is_degraded_not_broken(self):
        final = verdict([_row("python", "ok"), _row("skill_codex", "fail")])
        self.assertEqual(final["state"], "degraded")
        self.assertTrue(final["loop_usable"])
        self.assertFalse(final["agent_reachable"])

    def test_everything_present_means_ready(self):
        final = verdict([_row("python", "ok"), _row("ledger", "ok"), _row("skill_codex", "ok")])
        self.assertEqual(final["state"], "ready")
        self.assertTrue(final["loop_usable"])
        self.assertTrue(final["agent_reachable"])

    def test_the_action_always_names_a_command(self):
        broken = verdict([_row("ledger", "fail")])
        self.assertIn("mas", broken["action"])
        reachable = verdict([_row("python", "ok"), _row("skill_codex", "warn")])
        self.assertIn("mas install", reachable["action"])
        ready = verdict([_row("python", "ok")])
        self.assertIn("mas run", ready["action"])

    def test_a_missing_config_is_mentioned_in_the_action(self):
        final = verdict([_row("config", "warn"), _row("python", "ok")])
        self.assertIn("config", final["missing_recommended"])
        self.assertIn("mas init", final["action"])

    def test_the_verdict_carries_the_check_details(self):
        final = verdict([_row("python", "ok", "3.14.0")])
        self.assertEqual(final["checks"]["python"]["status"], "ok")
        self.assertEqual(final["checks"]["python"]["detail"], "3.14.0")
        self.assertTrue(final["checks"]["python"]["plain"])


class RenderTest(unittest.TestCase):
    def test_the_report_explains_every_row(self):
        text = format_doctor([_row("python", "ok", "3.14.0"), _row("ledger", "fail", "不可写")])
        self.assertIn("是什么：", text)
        self.assertIn("小结：", text)

    def test_the_report_marks_the_tiers(self):
        text = format_doctor(
            [
                _row("python", "ok"),
                _row("config", "warn"),
                _row("skill_codex", "warn"),
            ]
        )
        self.assertIn("python（必需）", text)
        self.assertIn("config（建议）", text)
        self.assertIn("skill_codex（可选）", text)

    def test_the_report_counts_the_outcomes(self):
        text = format_doctor([_row("a", "ok"), _row("b", "warn"), _row("c", "fail")])
        self.assertIn("1 项正常，1 项注意，1 项坏了", text)


class CliDoctorTest(unittest.TestCase):
    """The CLI is where a beginner actually meets the report."""

    def _run(self, argv):
        import contextlib
        import io

        from mas.cli import main

        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_the_text_mode_prints_a_conclusion_and_a_next_step(self):
        code, out, _err = self._run(["doctor", "--no-network"])
        self.assertEqual(code, 0)
        self.assertIn("结论：", out)
        self.assertIn("下一步：", out)
        self.assertIn("是什么：", out)

    def test_the_json_mode_carries_the_verdict(self):
        import json

        code, out, _err = self._run(["doctor", "--json", "--no-network"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertIn("verdict", payload)
        self.assertIn("state", payload["verdict"])
        self.assertTrue(payload["verdict"]["headline"])
        self.assertTrue(payload["verdict"]["action"])
        self.assertIn("counts", payload)

    def test_the_exit_code_stays_zero_on_a_machine_without_skills(self):
        # CI calls `python -m mas doctor` as a smoke test on a clean
        # runner: the optional parts are missing there by definition, so
        # a non-zero exit would break the build for the wrong reason.
        code, _out, _err = self._run(["doctor", "--no-network"])
        self.assertEqual(code, 0)


class DoctorEndToEndTest(unittest.TestCase):
    def test_the_live_report_contains_a_conclusion_and_an_action(self):
        results = run_doctor()
        text = format_doctor(results)
        self.assertIn("是什么：", text)
        final = verdict(results)
        self.assertTrue(final["headline"])
        self.assertTrue(final["action"])
        # A real machine always has a python and a package.
        self.assertNotEqual(final["state"], "broken")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
