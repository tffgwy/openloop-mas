# -*- coding: utf-8 -*-
"""v0.9.0: a run knows which backend really delivered it.

The regression this file locks down is the one v0.9.0 fixed. The
failover machinery was there and worked: ``ProviderChain`` tried the
primary, the primary answered 502, and the backup delivered the run.

But nobody could see it. The ledger wrote ``provider`` from
``self.provider.name``, and for a chain that name is the **primary** -
the endpoint that had just failed. So every row of a run delivered by
the backup named the dead endpoint, and the report - which had no
notion of a backend at all - repeated it.

A reader of the report could not tell that the machine had survived
an outage on its own, and could not tell that it had *not*: the same
words appeared for a run served by the primary and for a run served by
a backup.

What has to hold after this release:

* the row keeps the *configured* backend and gains the backend that
  actually answered;
* the run summary knows which backend delivered it, and whether the
  run had to fall over;
* the report's run table shows it;
* the run's own detail section says it in words, and says the primary
  was down when it fell over;
* a run that never fell over does not claim it did;
* the command line and the MCP surface agree with the report;
* an old ledger row carries no backend, and nothing is guessed for it.
"""

import contextlib
import io
import json
import os
import pathlib
import tempfile
import unittest

from mas.report import (
    backend_of_row,
    backends_of_run,
    build_report,
    render_markdown,
    run_summaries,
)


class _Env:
    def __init__(self, state_dir, config_path):
        self._values = {"MAS_STATE_DIR": state_dir, "MAS_CONFIG_PATH": config_path}
        self._saved = {}

    def __enter__(self):
        for name, value in self._values.items():
            self._saved[name] = os.environ.get(name)
            os.environ[name] = value
        return self

    def __exit__(self, *exc):
        for name, saved in self._saved.items():
            if saved is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = saved


def _row(role, run_id, provider, provider_used=None, model_used=None):
    row = {"role": role, "run_id": run_id, "provider": provider}
    if provider_used is not None:
        row["provider_used"] = provider_used
    if model_used is not None:
        row["model_used"] = model_used
    return row


class TheRowTest(unittest.TestCase):
    """A row carries the backend that answered."""

    def test_a_row_names_the_backend_that_answered(self):
        fields = backend_of_row(
            _row("planner", "R-a", "dead-primary", "backup-kimi", "kimi-k2")
        )
        self.assertEqual(fields.get("provider_used"), "backup-kimi")
        self.assertEqual(fields.get("model_used"), "kimi-k2")

    def test_an_old_row_says_nothing(self):
        # A row written before this release has no backend. It must
        # not be guessed: claiming the configured backend answered it
        # would be the very lie this release removes.
        self.assertEqual(backend_of_row(_row("planner", "R-a", "openai_compat")), {})

    def test_a_row_with_only_a_model_still_says_something(self):
        fields = backend_of_row(
            _row("planner", "R-a", "openai_compat", None, "gpt-4o")
        )
        self.assertEqual(fields.get("model_used"), "gpt-4o")
        # The provider is empty, not filled in with a guess.
        self.assertEqual(fields.get("provider_used"), "")


class TheRunReadingTest(unittest.TestCase):
    """The run reads the backends of its own rows."""

    def test_a_run_that_fell_over_is_told(self):
        rows = [
            _row("planner", "R-a", "dead-primary", "backup-kimi", "kimi-k2"),
            _row("candidates", "R-a", "dead-primary", "backup-kimi", "kimi-k2"),
            _row("run_end", "R-a", "dead-primary"),
        ]
        facts = backends_of_run(rows)
        self.assertTrue(facts.get("failed_over"))
        self.assertEqual(facts.get("configured_provider"), "dead-primary")
        self.assertEqual(
            (facts.get("primary_backend") or {}).get("provider_used"),
            "backup-kimi",
        )

    def test_a_run_that_did_not_fall_over_is_not_told(self):
        rows = [
            _row("planner", "R-a", "openai_compat", "openai_compat", "gpt-4o"),
            _row("run_end", "R-a", "openai_compat"),
        ]
        facts = backends_of_run(rows)
        self.assertFalse(facts.get("failed_over"))
        self.assertEqual(facts.get("configured_provider"), "openai_compat")

    def test_an_old_run_says_nothing(self):
        rows = [_row("planner", "R-a", "openai_compat")]
        self.assertEqual(backends_of_run(rows), {})


def _make_backend_class(name, model, alive=True):
    from mas.providers import Provider, ProviderError

    class _P(Provider):
        def __init__(self):
            super().__init__(model=model)

        def complete(self, *, system="", prompt="", role="worker",
                     expect_json=False, max_tokens=4000):
            if not alive:
                raise ProviderError(
                    "502 %s is down" % name, usage={"total_tokens": 5}
                )
            usage = {"total_tokens": 3}
            if role == "worker":
                return {
                    "text": "final answer line\n",
                    "usage": usage,
                    "raw": {},
                }
            return {
                "text": json.dumps({
                    "verdict": "pass",
                    "scores": [{"id": "c1", "pass": True, "note": "ok"}],
                    "issues": [], "summary": "s", "plan": ["p"],
                    "risks": [], "decision": "approve", "reason": "ok",
                }),
                "usage": usage, "raw": {},
            }

    _P.name = name
    return _P


class _RealLoopWithAFailover(unittest.TestCase):
    """One healthy run and one run that had to fall over."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="mas-test-backend-")
        root = pathlib.Path(self._tmp.name)
        self.state = root / "state"
        self.artifacts = self.state / "artifacts"
        self.artifacts.mkdir(parents=True)
        self.config_path = root / "config.json"
        self.workspace = self.artifacts / "ws"
        self.workspace.mkdir(parents=True, exist_ok=True)
        (self.workspace / "note.txt").write_text(
            "final answer line\n", encoding="utf-8"
        )

        from mas.engine import LoopEngine
        from mas.orchestration import ProviderChain

        config = {
            "provider": "mock", "model": "test-model",
            "state_dir": str(self.state), "workspace": str(self.workspace),
            "budget": {"max_iterations": 2, "max_tokens": 1000000, "deadline_sec": 60},
            "orchestration": {"parallel_workers": 1, "reviewers": 1, "review_mode": "all"},
            "team": {"language": "zh"},
        }
        criteria = [{
            "id": "c1", "desc": "final",
            "check": {"type": "contains", "arg": "final answer"},
        }]
        self.run_ids = []
        with _Env(str(self.state), str(self.config_path)):
            engine = LoopEngine(
                config, provider=_make_backend_class("healthy-primary", "healthy-model")()
            )
            result = engine.run(goal="写一份说明", criteria=criteria).to_dict()
            self.run_ids.append(str(result.get("run_id") or ""))

            engine = LoopEngine(
                config,
                provider=ProviderChain(
                    [
                        _make_backend_class("dead-primary", "dead-model", alive=False)(),
                        _make_backend_class("backup-kimi", "kimi-k2")(),
                    ]
                ),
            )
            result = engine.run(goal="再写一份说明", criteria=criteria).to_dict()
            self.run_ids.append(str(result.get("run_id") or ""))

        self.healthy = self.run_ids[0]
        self.failover = self.run_ids[1]

    def tearDown(self):
        self._tmp.cleanup()

    def _rows(self):
        from mas.ledger import Ledger

        path = self.state / "ledgers" / "runs.jsonl"
        if not path.exists():
            return []
        return list(Ledger(path).entries())

    def _run(self, args):
        from mas.cli import main

        buffer = io.StringIO()
        with _Env(str(self.state), str(self.config_path)):
            try:
                with contextlib.redirect_stdout(buffer):
                    main(args)
            except SystemExit:
                pass
        return buffer.getvalue()

    def _json(self, args):
        text = self._run(args)
        try:
            return json.loads(text)
        except Exception:  # noqa: BLE001 - a broken page is the subject
            return {}

    def _summary(self, run_id):
        for summary in run_summaries(self._rows()):
            if summary.get("run_id") == run_id:
                return summary
        return {}

    def _row_of(self, report, run_id):
        for entry in report.get("runs") or []:
            if entry.get("run_id") == run_id:
                return entry
        return {}

    def _section(self, markdown, run_id):
        kept = []
        inside = False
        for line in (markdown or "").split("\n"):
            if line.startswith("## "):
                inside = ("`%s`" % run_id) in line
                if inside:
                    kept.append(line)
                continue
            if inside:
                kept.append(line)
        return "\n".join(kept)


class TheFailoverRunTest(_RealLoopWithAFailover):
    def test_the_ledger_row_names_the_backend_that_answered(self):
        used = [
            row.get("provider_used")
            for row in self._rows()
            if row.get("run_id") == self.failover and row.get("provider_used")
        ]
        self.assertTrue(used)
        self.assertEqual(set(used), {"backup-kimi"})

    def test_the_row_still_names_the_configured_backend(self):
        for row in self._rows():
            if row.get("run_id") == self.failover and row.get("provider_used"):
                self.assertEqual(row.get("provider"), "dead-primary")

    def test_the_run_summary_knows_its_backend(self):
        summary = self._summary(self.failover)
        self.assertEqual(summary.get("backend_used"), "backup-kimi")
        self.assertEqual(summary.get("model_used"), "kimi-k2")
        self.assertEqual(summary.get("configured_provider"), "dead-primary")
        self.assertTrue(summary.get("failed_over"))

    def test_the_report_shows_the_backend_in_the_table(self):
        report = build_report(self._rows())
        markdown = render_markdown(report)
        row = self._row_of(report, self.failover)
        self.assertEqual(row.get("backend_used"), "backup-kimi")
        self.assertTrue(row.get("failed_over"))
        self.assertIn("后端", markdown)

    def test_the_run_detail_says_it_in_words(self):
        markdown = render_markdown(build_report(self._rows()))
        section = self._section(markdown, self.failover)
        self.assertIn("backup-kimi", section)
        self.assertIn("实际交付后端", section)
        self.assertIn("不可用", section)

    def test_the_command_line_agrees_with_the_report(self):
        status = self._json(["status", "--json"])
        items = [
            item
            for item in ((status.get("backends") or {}).get("items") or [])
            if item.get("run_id") == self.failover
        ]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].get("backend_used"), "backup-kimi")
        self.assertTrue(items[0].get("failed_over"))
        self.assertIn("backup-kimi", self._run(["status"]))

    def test_the_mcp_surface_agrees_with_the_report(self):
        from mas.mcp_server import handle_tool

        with _Env(str(self.state), str(self.config_path)):
            mcp_status = handle_tool("mas_status", {})
            mcp_report = handle_tool("mas_report", {"as_markdown": False})
            mcp_replay = handle_tool("mas_replay", {})

        items = [
            item
            for item in ((mcp_status.get("backends") or {}).get("items") or [])
            if item.get("run_id") == self.failover
        ]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].get("backend_used"), "backup-kimi")
        self.assertTrue(items[0].get("failed_over"))

        row = self._row_of(mcp_report, self.failover)
        self.assertEqual(row.get("backend_used"), "backup-kimi")
        self.assertTrue(row.get("failed_over"))

        block = {}
        for entry in mcp_replay.get("runs") or []:
            if entry.get("run_id") == self.failover:
                block = entry
        self.assertEqual(block.get("backend_used"), "backup-kimi")
        self.assertTrue(block.get("failed_over"))


class TheHealthyRunTest(_RealLoopWithAFailover):
    def test_it_names_its_own_backend(self):
        summary = self._summary(self.healthy)
        self.assertEqual(summary.get("backend_used"), "healthy-primary")

    def test_it_does_not_claim_a_failover(self):
        summary = self._summary(self.healthy)
        self.assertFalse(summary.get("failed_over"))
        report = build_report(self._rows())
        self.assertFalse(self._row_of(report, self.healthy).get("failed_over"))

    def test_its_own_section_does_not_mention_a_failover(self):
        markdown = render_markdown(build_report(self._rows()))
        section = self._section(markdown, self.healthy)
        self.assertNotIn("实际交付后端", section)
        self.assertNotIn("backup-kimi", section)

    def test_it_does_not_claim_the_other_runs_backend(self):
        # A filter that returns every backend for every run would
        # satisfy a gate that only ever looks at the failing run.
        summary = self._summary(self.healthy)
        backends = [
            item.get("provider_used") for item in (summary.get("backends") or [])
        ]
        self.assertNotIn("backup-kimi", backends)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
