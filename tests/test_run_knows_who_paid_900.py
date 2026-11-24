# -*- coding: utf-8 -*-
"""v0.10.0: a run knows which backend paid for it.

The regression this file locks down is the one v0.10.0 fixed. The
failover chain folds the spend of every backend that failed into the
total the run is charged - and that total is right, because the run
really did pay for the failed calls.

But the *name* was wrong. The total used to be booked under the name
of the backend that **answered**, so a row said "backup-kimi spent 8
tokens" when backup-kimi had spent 3 and the dead primary had spent
5.

That is a lie about money, and it is a lie in the direction that
costs the reader the most: the endpoint that was burning the budget
is exactly the one that failed, so a reader asking "which backend is
draining my budget?" got the healthy one instead of the sick one.

What has to hold after this release:

* the chain splits the spend of a failed-over call by backend;
* the total the run is charged does **not** change;
* the dead backend's spend is booked under the dead backend;
* the ledger row carries the split;
* the run summary says who paid;
* the report's run detail says it in words;
* a run that never failed over claims no split;
* the command line and the MCP surface agree with the report;
* an old ledger row carries no split, and nothing is guessed for it.
"""

import contextlib
import io
import json
import os
import pathlib
import tempfile
import unittest

from mas.report import (
    CHARGE_ROLES,
    build_report,
    render_markdown,
    run_summaries,
    spend_by_backend_of_run,
    spend_of_row,
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


def _row(role, run_id, provider, provider_used=None, tokens=0, split=None):
    row = {
        "role": role,
        "run_id": run_id,
        "provider": provider,
        "tokens": tokens,
    }
    if provider_used is not None:
        row["provider_used"] = provider_used
    if split is not None:
        row["backend_tokens"] = split
    return row


class TheChainTest(unittest.TestCase):
    """The chain keeps the spend of each backend apart."""

    def _dead_and_live(self):
        from mas.providers import Provider, ProviderError

        class _Dead(Provider):
            name = "dead-primary"

            def complete(self, **kwargs):
                raise ProviderError("502 boom", usage={"total_tokens": 5})

        class _Live(Provider):
            name = "backup-kimi"

            def complete(self, **kwargs):
                return {
                    "text": "ok",
                    "usage": {"total_tokens": 3},
                    "raw": {},
                }

        return _Dead, _Live

    def test_the_split_names_both_backends(self):
        from mas.orchestration import ProviderChain

        dead, live = self._dead_and_live()
        chain = ProviderChain([dead(), live()])
        response = chain.complete(system="s", prompt="p")
        usage = response.get("usage") or {}
        split = usage.get("backend_tokens") or {}
        self.assertEqual(split.get("dead-primary"), 5)
        self.assertEqual(split.get("backup-kimi"), 3)

    def test_the_total_does_not_change(self):
        # The run really did pay for the failed call. The fix changes
        # the *attribution*, never the bill.
        from mas.orchestration import ProviderChain

        dead, live = self._dead_and_live()
        chain = ProviderChain([dead(), live()])
        response = chain.complete(system="s", prompt="p")
        usage = response.get("usage") or {}
        self.assertEqual(int(usage.get("total_tokens") or 0), 8)
        self.assertEqual(int(usage.get("failover_tokens") or 0), 5)

    def test_a_chain_that_never_failed_over_has_no_split(self):
        from mas.orchestration import ProviderChain
        from mas.providers import Provider

        class _Live(Provider):
            name = "only-one"

            def complete(self, **kwargs):
                return {
                    "text": "ok",
                    "usage": {"total_tokens": 3},
                    "raw": {},
                }

        chain = ProviderChain([_Live()])
        response = chain.complete(system="s", prompt="p")
        usage = response.get("usage") or {}
        self.assertNotIn("backend_tokens", usage)

    def test_every_backend_failing_still_names_them(self):
        from mas.orchestration import ProviderChain
        from mas.providers import Provider, ProviderError

        class _Dead(Provider):
            def __init__(self, name):
                super().__init__(model="m")
                self.name = name

            def complete(self, **kwargs):
                raise ProviderError("502 boom", usage={"total_tokens": 5})

        chain = ProviderChain([_Dead("one"), _Dead("two")])
        with self.assertRaises(ProviderError) as caught:
            chain.complete(system="s", prompt="p")
        split = (caught.exception.usage or {}).get("backend_tokens") or {}
        self.assertEqual(split.get("one"), 5)
        self.assertEqual(split.get("two"), 5)


class TheRowReadingTest(unittest.TestCase):
    """The ledger row carries the split."""

    def test_a_row_carries_its_split(self):
        spent = spend_of_row(
            _row("planner", "R-a", "dead-primary", "backup-kimi", 8,
                 {"dead-primary": 5, "backup-kimi": 3})
        )
        self.assertEqual(spent.get("dead-primary"), 5)
        self.assertEqual(spent.get("backup-kimi"), 3)

    def test_an_old_row_says_nothing(self):
        # A row written before this release carries no split. Nothing
        # is guessed for it - claiming the answering backend paid for
        # everything would be the very lie this release removes.
        old = _row("planner", "R-a", "openai_compat", "openai_compat", 8)
        self.assertEqual(spend_of_row(old), {})

    def test_a_run_sums_its_rows(self):
        rows = [
            _row("planner", "R-a", "dead-primary", "backup-kimi", 8,
                 {"dead-primary": 5, "backup-kimi": 3}),
            _row("review", "R-a", "dead-primary", "backup-kimi", 8,
                 {"dead-primary": 5, "backup-kimi": 3}),
            _row("run_end", "R-a", "dead-primary"),
        ]
        spent = spend_by_backend_of_run(rows)
        self.assertEqual(spent.get("dead-primary"), 10)
        self.assertEqual(spent.get("backup-kimi"), 6)

    def test_a_row_that_is_not_charged_is_not_counted(self):
        rows = [
            _row("planner", "R-a", "dead-primary", "backup-kimi", 8,
                 {"dead-primary": 5, "backup-kimi": 3}),
            _row("draft", "R-a", "dead-primary", "backup-kimi", 0,
                 {"dead-primary": 99}),
        ]
        spent = spend_by_backend_of_run(rows)
        self.assertEqual(spent.get("dead-primary"), 5)


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
        self._tmp = tempfile.TemporaryDirectory(prefix="mas-test-paid-")
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
                config,
                provider=_make_backend_class("healthy-primary", "healthy-model")(),
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

    def _summary(self, run_id):
        for summary in run_summaries(self._rows()):
            if summary.get("run_id") == run_id:
                return summary
        return {}

    def _section(self, markdown, run_id):
        lines = markdown.split("\n")
        kept = []
        inside = False
        for line in lines:
            if line.startswith("## "):
                inside = ("`%s`" % run_id) in line
                if inside:
                    kept.append(line)
                continue
            if inside:
                kept.append(line)
        return "\n".join(kept)


class TheLedgerTest(_RealLoopWithAFailover):
    """The book keeps the spend of each backend apart."""

    def test_the_row_splits_the_spend(self):
        rows = [
            row
            for row in self._rows()
            if row.get("run_id") == self.failover
            and row.get("backend_tokens")
        ]
        self.assertTrue(rows, "a failed-over call must carry its split")
        for row in rows:
            split = row["backend_tokens"]
            self.assertEqual(split.get("dead-primary"), 5)
            self.assertEqual(split.get("backup-kimi"), 3)
            # The split has to add up to the charge of the row.
            self.assertEqual(
                sum(int(v or 0) for v in split.values()),
                int(row.get("tokens") or 0),
            )

    def test_the_dead_backend_is_not_charged_to_the_living_one(self):
        # The old behaviour booked the whole 8 tokens under
        # "backup-kimi". That is the lie this release removes.
        for row in self._rows():
            if row.get("run_id") != self.failover:
                continue
            split = row.get("backend_tokens") or {}
            if not split:
                continue
            self.assertLess(
                int(split.get("backup-kimi") or 0),
                int(row.get("tokens") or 0),
                "the living backend must not carry the dead one's money",
            )

    def test_the_healthy_run_carries_no_split(self):
        for row in self._rows():
            if row.get("run_id") == self.healthy:
                self.assertFalse(row.get("backend_tokens"))


class TheSummaryTest(_RealLoopWithAFailover):
    """The run's own summary says who paid."""

    def test_the_summary_says_who_paid(self):
        summary = self._summary(self.failover)
        spent = summary.get("spend_by_backend") or {}
        self.assertGreater(int(spent.get("dead-primary") or 0), 0)
        self.assertGreater(int(spent.get("backup-kimi") or 0), 0)

    def test_the_summary_of_a_healthy_run_says_nothing(self):
        summary = self._summary(self.healthy)
        self.assertFalse(summary.get("spend_by_backend"))

    def test_the_report_says_it_in_words(self):
        markdown = render_markdown(build_report(self._rows()))
        section = self._section(markdown, self.failover)
        self.assertIn("dead-primary", section)
        self.assertIn("backup-kimi", section)
        self.assertIn("花费", section)

    def test_the_healthy_run_is_not_asked_to_pay_somewhere(self):
        markdown = render_markdown(build_report(self._rows()))
        section = self._section(markdown, self.healthy)
        self.assertNotIn("dead-primary", section)
        self.assertNotIn("💸", section)


class TheSurfacesTest(_RealLoopWithAFailover):
    """The command line and the MCP surface agree with the report."""

    def test_the_command_line_agrees(self):
        payload = json.loads(self._run(["status", "--json"]) or "{}")
        backends = payload.get("backends") or {}
        items = [
            item
            for item in (backends.get("items") or [])
            if item.get("run_id") == self.failover
        ]
        self.assertTrue(items)
        spent = items[0].get("spend_by_backend") or {}
        self.assertGreater(int(spent.get("dead-primary") or 0), 0)
        self.assertGreater(int(spent.get("backup-kimi") or 0), 0)
        # And the text page says it too.
        text = self._run(["status"])
        self.assertIn("dead-primary", text)

    def test_the_mcp_surface_agrees(self):
        from mas.mcp_server import handle_tool

        with _Env(str(self.state), str(self.config_path)):
            status = handle_tool("mas_status", {})
            report = handle_tool("mas_report", {"as_markdown": False})
            replay = handle_tool("mas_replay", {})

        items = [
            item
            for item in ((status.get("backends") or {}).get("items") or [])
            if item.get("run_id") == self.failover
        ]
        self.assertTrue(items)
        self.assertGreater(
            int((items[0].get("spend_by_backend") or {}).get("dead-primary") or 0),
            0,
        )

        rows = [
            row
            for row in (report.get("runs") or [])
            if row.get("run_id") == self.failover
        ]
        self.assertTrue(rows)
        self.assertGreater(
            int((rows[0].get("spend_by_backend") or {}).get("dead-primary") or 0),
            0,
        )

        blocks = [
            block
            for block in (replay.get("runs") or [])
            if block.get("run_id") == self.failover
        ]
        self.assertTrue(blocks)
        self.assertGreater(
            int((blocks[0].get("spend_by_backend") or {}).get("dead-primary") or 0),
            0,
        )


class TheTotalTest(unittest.TestCase):
    """The report's totals name who the whole bill went to."""

    def test_the_totals_sum_every_backend(self):
        rows = [
            _row("planner", "R-a", "dead-primary", "backup-kimi", 8,
                 {"dead-primary": 5, "backup-kimi": 3}),
            _row("review", "R-b", "openai_compat", "openai_compat", 7,
                 {"openai_compat": 7}),
            _row("run_end", "R-a", "dead-primary"),
        ]
        report = build_report(rows)
        totals = report.get("totals") or {}
        spent = totals.get("spend_by_backend") or {}
        self.assertEqual(spent.get("dead-primary"), 5)
        self.assertEqual(spent.get("backup-kimi"), 3)
        self.assertEqual(spent.get("openai_compat"), 7)

    def test_the_charged_rows_are_the_ones_that_count(self):
        self.assertIn("planner", CHARGE_ROLES)
        self.assertIn("review", CHARGE_ROLES)
        self.assertNotIn("run_end", CHARGE_ROLES)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
