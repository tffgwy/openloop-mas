# -*- coding: utf-8 -*-
"""v0.11.3 tests: the take-over is visible on every face.

v0.11.2 gave the seal a third state - ``taken-over``: the conclusion
still holds, but a person approved a change to the deliverable after
the run closed. The report and ``mas seal`` said so.

The status page and the replay said **nothing at all** - not about a
take-over, not about a drift, not about a seal at all. A person who
opened the status page - the page a beginner opens first - learned
nothing about a person having taken the deliverable over, and had to
assume the file had never been touched.

This file pins the fix down:

* the report, the status page, the replay, the MCP status tool and the
  MCP replay tool all say the take-over;
* all five count the **same** take-over - one fact, five faces;
* an unapproved change is still a drift on every face - the lock is
  not weakened by any of this;
* the reading lives in one place, so the faces cannot drift apart.

The bytes are written with ``write_bytes`` on purpose. A file written
with ``write_text`` can hold ``\\r\\n`` where the text holds ``\\n``,
and a comparison across two bases is not a comparison at all - that
trap cost v0.7.77 a CI failure that only showed up on Windows.
"""

try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # loaded as a top-level module
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401

import contextlib
import io
import json
import os
import pathlib
import shutil
import tempfile
import unittest

from mas.drafts import approve_draft, stage_draft
from mas.engine import LoopEngine
from mas.providers import Provider


class _Writer(Provider):
    """A provider that writes a file and then passes the review."""

    name = "test-writer"

    def complete(self, *, system: str = "", prompt: str = "", role: str = "worker",
                 expect_json: bool = False, max_tokens: int = 4000):
        usage = {"total_tokens": 10}
        if role == "worker":
            text = (
                "交付物正文\n"
                '<<<MAS:FILE path="notes/finding.txt">>>\n'
                "结论：SELFTEST\n"
                "<<<MAS:END FILE>>>\n"
            )
            return {"text": text, "usage": usage, "raw": {}}
        if role == "verifier":
            return {
                "text": json.dumps(
                    {
                        "verdict": "pass",
                        "scores": [
                            {"id": "c1", "pass": True, "note": "ok"},
                            {"id": "c2", "pass": True, "note": "file there"},
                        ],
                    }
                ),
                "usage": usage,
                "raw": {},
            }
        return {"text": "{}", "usage": usage, "raw": {}}


class _Env:
    """The two environment variables the surfaces read."""

    def __init__(self, state_dir, config_path):
        self._values = {
            "MAS_STATE_DIR": state_dir,
            "MAS_CONFIG_PATH": config_path,
        }
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


class TheTakeOverIsVisibleEverywhereTest(unittest.TestCase):
    """One closure, one approved change, five faces."""

    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="mas-p9113-test-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.state = self.root / "state"
        self.state.mkdir(parents=True)
        self.config_path = self.root / "config.json"

        config = {
            "provider": "mock",
            "model": "test-model",
            "state_dir": str(self.state),
            "workspace": "",
            "budget": {"max_iterations": 2, "max_tokens": 1000000, "deadline_sec": 60},
            "orchestration": {"parallel_workers": 1, "reviewers": 1, "review_mode": "all"},
            "team": {"language": "zh"},
        }
        with _Env(str(self.state), str(self.config_path)):
            engine = LoopEngine(config, provider=_Writer(model="test-model"))
            result = engine.run(
                goal="写一份结论",
                criteria=[
                    {"id": "c1", "desc": "含正文",
                     "check": {"type": "contains", "arg": "正文"}},
                    {"id": "c2", "desc": "文件写出来了",
                     "check": {"type": "file_exists", "arg": "notes/finding.txt"}},
                ],
            ).to_dict()
        self.run_id = str(result.get("run_id") or "")
        self.workspace = pathlib.Path(str(result.get("workspace") or ""))
        self.target = self.workspace / "notes" / "finding.txt"
        self.original = self.target.read_bytes()

    # -- helpers ----------------------------------------------------

    def _cli(self, args):
        """Run one command of the command line and keep what it printed."""
        from mas.cli import main

        buffer = io.StringIO()
        with _Env(str(self.state), str(self.config_path)):
            try:
                with contextlib.redirect_stdout(buffer):
                    main(args)
            except SystemExit:
                pass
        return buffer.getvalue()

    def _cli_json(self, args):
        text = self._cli(args)
        try:
            return json.loads(text)
        except Exception:  # noqa: BLE001 - a broken page is the subject
            return {}

    def _mcp(self, name, arguments=None):
        from mas.mcp_server import handle_tool

        with _Env(str(self.state), str(self.config_path)):
            try:
                outcome = handle_tool(name, arguments or {})
            except Exception:  # noqa: BLE001 - a broken tool is the subject
                return {}
        if isinstance(outcome, dict) and isinstance(outcome.get("result"), str):
            try:
                inner = json.loads(outcome["result"])
                if isinstance(inner, dict):
                    return inner
            except Exception:  # noqa: BLE001
                pass
        return outcome if isinstance(outcome, dict) else {}

    def _seal_item(self, payload, run_id=None):
        seal = (payload or {}).get("seal") or {}
        wanted = run_id or self.run_id
        for item in seal.get("items") or []:
            if str(item.get("run_id") or "") == wanted:
                return item
        return {}

    def _approve(self):
        """A person reviews the deliverable and approves a change to it."""
        with _Env(str(self.state), str(self.config_path)):
            draft = stage_draft(
                self.workspace,
                "notes/finding.txt",
                self.original + b"REVIEWED\n",
                reason="test",
            )
            approve_draft(
                self.workspace, str(draft.get("id") or ""), note="a person reviewed it"
            )

    # -- the five faces ---------------------------------------------

    def test_the_report_says_the_take_over(self):
        self._approve()
        payload = self._cli_json(["report", "--json"])
        item = self._seal_item(payload)
        self.assertEqual(item.get("state"), "taken-over")
        self.assertIn("人工接手", self._cli(["report"]))

    def test_the_status_page_says_the_take_over(self):
        self._approve()
        payload = self._cli_json(["status", "--json"])
        item = self._seal_item(payload)
        self.assertEqual(item.get("state"), "taken-over")
        self.assertIn("人工接手", self._cli(["status"]))

    def test_the_replay_says_the_take_over(self):
        self._approve()
        text = self._cli(["replay"])
        self.assertTrue(
            "人工接手" in text or "被人审过并批准了改动" in text,
            "the replay said nothing about the take-over:\n%s" % text,
        )

    def test_the_mcp_status_tool_says_the_take_over(self):
        self._approve()
        payload = self._mcp("mas_status")
        item = self._seal_item(payload)
        self.assertEqual(item.get("state"), "taken-over")
        self.assertEqual(int((payload.get("seal") or {}).get("taken_over") or 0), 1)

    def test_the_mcp_replay_tool_says_the_take_over(self):
        self._approve()
        payload = self._mcp("mas_replay")
        block = {}
        for entry in payload.get("runs") or []:
            if str(entry.get("run_id") or "") == self.run_id:
                block = entry
        self.assertEqual(block.get("seal_state"), "taken-over")
        self.assertEqual(
            int((payload.get("seal") or {}).get("taken_over") or 0), 1
        )

    # -- the faces agree --------------------------------------------

    def test_every_face_counts_the_same_take_over(self):
        self._approve()
        counts = [
            int(((self._cli_json(["report", "--json"]).get("seal") or {})
                 .get("taken_over") or 0)),
            int(((self._cli_json(["status", "--json"]).get("seal") or {})
                 .get("taken_over") or 0)),
            int(((self._mcp("mas_status").get("seal") or {})
                 .get("taken_over") or 0)),
            int(((self._mcp("mas_replay").get("seal") or {})
                 .get("taken_over") or 0)),
        ]
        self.assertEqual(counts, [1, 1, 1, 1])

    def test_the_faces_do_not_call_a_take_over_a_drift(self):
        self._approve()
        for payload in (
            self._cli_json(["report", "--json"]),
            self._cli_json(["status", "--json"]),
            self._mcp("mas_status"),
        ):
            seal = payload.get("seal") or {}
            self.assertEqual(int(seal.get("drifted") or 0), 0)
            self.assertTrue(seal.get("ok"))

    # -- the lock is not weakened -----------------------------------

    def test_an_unapproved_change_is_still_a_drift_everywhere(self):
        self.target.write_bytes(self.original + b"SNEAKY\n")
        for payload in (
            self._cli_json(["report", "--json"]),
            self._cli_json(["status", "--json"]),
            self._mcp("mas_status"),
            self._mcp("mas_replay"),
        ):
            seal = payload.get("seal") or {}
            self.assertEqual(int(seal.get("drifted") or 0), 1)
            self.assertEqual(int(seal.get("taken_over") or 0), 0)
            self.assertFalse(seal.get("ok"))

    def test_the_status_page_knows_a_drift_too(self):
        self.target.write_bytes(self.original + b"SNEAKY\n")
        self.assertIn("已漂移", self._cli(["status"]))


class TheSealOverviewTest(unittest.TestCase):
    """The reading lives in one place, so the faces cannot drift apart."""

    def test_the_overview_counts_the_three_states_apart(self):
        from mas.report import seal_overview

        runs = [
            {"run_id": "R1", "seal_state": "holds", "seal_ok": True},
            {"run_id": "R2", "seal_state": "taken-over", "seal_ok": True},
            {"run_id": "R3", "seal_state": "no-verdict", "seal_ok": True},
            {"run_id": "R4", "seal_state": "drifted", "seal_ok": False},
            {"run_id": "R5", "seal_state": None, "seal_ok": None},
        ]
        view = seal_overview(runs)
        self.assertEqual(view.get("sealed"), 4)
        self.assertEqual(view.get("holds"), 1)
        self.assertEqual(view.get("taken_over"), 1)
        self.assertEqual(view.get("no_verdict"), 1)
        self.assertEqual(view.get("drifted"), 1)
        # A take-over is not a drift: only a drift breaks the seal.
        self.assertFalse(view.get("ok"))

    def test_the_overview_keeps_the_take_over_out_of_the_problems(self):
        from mas.report import seal_overview

        runs = [
            {
                "run_id": "R1",
                "seal_state": "taken-over",
                "seal_ok": True,
                "seal_notes": ["a person approved the change"],
                "seal_taken_over_files": [{"path": "a.txt", "draft_id": "D1"}],
            }
        ]
        view = seal_overview(runs)
        item = (view.get("items") or [{}])[0]
        self.assertEqual(item.get("problems"), [])
        self.assertEqual(len(item.get("notes") or []), 1)
        self.assertEqual(len(item.get("taken_over_files") or []), 1)
        self.assertEqual(item.get("reading"), "人工接手")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
