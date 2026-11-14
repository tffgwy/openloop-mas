# -*- coding: utf-8 -*-
"""v0.7.45 regression tests: the user must be able to FIND the deliverable.

v0.7.44 made the deliverable survive the process. v0.7.45 makes it
reachable: ``mas run`` and ``mas resume`` say where the file landed, and
``mas artifacts`` lists every deliverable the machine has produced.

The gap was measured, not guessed: before this change the CLI printed a
1500-character *preview* of the artifact and the ledger path - never the
path of the file the user could actually open. And nothing at all could
list the deliverables of previous runs.
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


def _run_cli(argv, state_dir):
    """Run the CLI with an isolated state directory and capture stdout."""
    os.environ["MAS_STATE_DIR"] = state_dir
    os.environ["MAS_PROVIDER"] = "mock"
    os.environ.pop("MAS_API_KEY", None)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main(argv)
    return code, buffer.getvalue()


class ArtifactsCommandTest(unittest.TestCase):
    """``mas artifacts`` must list what this machine actually produced."""

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix="mas-artifacts-")
        self._saved_state = os.environ.get("MAS_STATE_DIR")
        self._saved_provider = os.environ.get("MAS_PROVIDER")

    def tearDown(self):
        if self._saved_state is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = self._saved_state
        if self._saved_provider is None:
            os.environ.pop("MAS_PROVIDER", None)
        else:
            os.environ["MAS_PROVIDER"] = self._saved_provider

    def _run_once(self, goal="写一份说明"):
        code, out = _run_cli(
            ["run", "--goal", goal, "--criteria", "contains:交付物"],
            self.state_dir,
        )
        self.assertEqual(code, 0, out)
        return out

    def test_empty_ledger_says_so_instead_of_printing_nothing(self):
        code, out = _run_cli(["artifacts"], self.state_dir)
        self.assertEqual(code, 0)
        self.assertIn("还没有任何交付物", out)
        self.assertIn("mas run", out)

    def test_empty_ledger_json_has_zero_count(self):
        code, out = _run_cli(["artifacts", "--json"], self.state_dir)
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["artifacts"], [])

    def test_listing_shows_the_deliverable_after_a_run(self):
        self._run_once()
        code, out = _run_cli(["artifacts"], self.state_dir)
        self.assertEqual(code, 0)
        self.assertIn("共 1 份交付物", out)
        self.assertIn("artifacts", out)
        self.assertIn("closed", out)

    def test_listing_json_carries_the_full_row(self):
        self._run_once()
        code, out = _run_cli(["artifacts", "--json"], self.state_dir)
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["count"], 1)
        row = payload["artifacts"][0]
        self.assertTrue(row["exists"])
        self.assertTrue(row["path"])
        self.assertTrue(row["chars"] > 0)
        self.assertEqual(row["status"], "closed")
        self.assertTrue(row["run_id"].startswith("R-"))
        self.assertTrue(row["finished_at"])

    def test_two_runs_are_listed_as_two_deliverables(self):
        self._run_once(goal="第一份")
        self._run_once(goal="第二份")
        _code, out = _run_cli(["artifacts", "--json"], self.state_dir)
        payload = json.loads(out)
        self.assertEqual(payload["count"], 2)
        paths = {row["path"] for row in payload["artifacts"]}
        self.assertEqual(len(paths), 2)

    def test_missing_file_is_reported_not_silently_dropped(self):
        self._run_once()
        _code, out = _run_cli(["artifacts", "--json"], self.state_dir)
        row = json.loads(out)["artifacts"][0]
        pathlib.Path(row["path"]).unlink()
        code, out = _run_cli(["artifacts", "--json"], self.state_dir)
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["count"], 1)
        self.assertFalse(payload["artifacts"][0]["exists"])

    def test_missing_file_is_called_out_in_the_text_view(self):
        self._run_once()
        _code, out = _run_cli(["artifacts", "--json"], self.state_dir)
        row = json.loads(out)["artifacts"][0]
        pathlib.Path(row["path"]).unlink()
        _code, out = _run_cli(["artifacts"], self.state_dir)
        self.assertIn("缺失", out)
        self.assertIn("已不在原处", out)

    def test_run_id_filter_narrows_the_listing(self):
        self._run_once(goal="第一份")
        self._run_once(goal="第二份")
        _code, out = _run_cli(["artifacts", "--json"], self.state_dir)
        payload = json.loads(out)
        first = payload["artifacts"][0]["run_id"]
        _code, out = _run_cli(["artifacts", "--run-id", first, "--json"], self.state_dir)
        filtered = json.loads(out)
        self.assertEqual(filtered["count"], 1)
        self.assertEqual(filtered["artifacts"][0]["run_id"], first)

    def test_unknown_run_id_lists_nothing(self):
        self._run_once()
        _code, out = _run_cli(["artifacts", "--run-id", "R-nope", "--json"], self.state_dir)
        payload = json.loads(out)
        self.assertEqual(payload["count"], 0)


class DeliverablePathIsAnnouncedTest(unittest.TestCase):
    """``mas run`` and ``mas resume`` must name the file, not just preview it."""

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix="mas-announce-")
        self._saved_state = os.environ.get("MAS_STATE_DIR")
        self._saved_provider = os.environ.get("MAS_PROVIDER")

    def tearDown(self):
        if self._saved_state is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = self._saved_state
        if self._saved_provider is None:
            os.environ.pop("MAS_PROVIDER", None)
        else:
            os.environ["MAS_PROVIDER"] = self._saved_provider

    def test_run_prints_the_artifact_path(self):
        code, out = _run_cli(
            ["run", "--goal", "写一份说明", "--criteria", "contains:交付物"],
            self.state_dir,
        )
        self.assertEqual(code, 0, out)
        self.assertIn("交付物：", out)
        line = [ln for ln in out.splitlines() if ln.startswith("交付物：")][0]
        path = line.split("交付物：", 1)[1].strip()
        self.assertTrue(path.endswith(".md"), path)
        self.assertTrue(pathlib.Path(path).exists(), path)

    def test_the_announced_path_is_the_file_the_report_names(self):
        _code, out = _run_cli(
            ["run", "--goal", "写一份说明", "--criteria", "contains:交付物"],
            self.state_dir,
        )
        announced = [
            ln.split("交付物：", 1)[1].strip()
            for ln in out.splitlines()
            if ln.startswith("交付物：")
        ][0]
        code, out = _run_cli(["report"], self.state_dir)
        self.assertEqual(code, 0)
        self.assertIn(announced, out)

    def test_run_json_carries_the_artifact_path(self):
        code, out = _run_cli(
            ["run", "--goal", "写一份说明", "--criteria", "contains:交付物", "--json"],
            self.state_dir,
        )
        self.assertEqual(code, 0, out)
        payload = json.loads(out)
        self.assertTrue(payload["artifact_path"])
        self.assertTrue(pathlib.Path(payload["artifact_path"]).exists())

    def test_resume_prints_the_artifact_path(self):
        # A resumed loop is the expected path for an escalated run, so it
        # is the run whose result is most likely to be lost. Escalate
        # first, then resume the ticket.
        code, out = _run_cli(
            ["run", "--goal", "不可能达成",
             "--criteria", "regex:^IMPOSSIBLE_NEVER$"],
            self.state_dir,
        )
        self.assertNotEqual(code, 0, out)
        _code, out = _run_cli(["tickets", "--json"], self.state_dir)
        tickets = json.loads(out)["tickets"]
        ticket_id = tickets[0]["id"]
        _code, out = _run_cli(
            ["resume", ticket_id, "--note", "请再试一次"], self.state_dir
        )
        self.assertIn("交付物：", out)
        line = [ln for ln in out.splitlines() if ln.startswith("交付物：")][0]
        path = line.split("交付物：", 1)[1].strip()
        self.assertTrue(path.endswith(".md"), path)
        self.assertTrue(pathlib.Path(path).exists(), path)

    def test_resume_json_carries_the_artifact_path(self):
        _code, out = _run_cli(
            ["run", "--goal", "不可能达成",
             "--criteria", "regex:^IMPOSSIBLE_NEVER$"],
            self.state_dir,
        )
        _code, out = _run_cli(["tickets", "--json"], self.state_dir)
        ticket_id = json.loads(out)["tickets"][0]["id"]
        _code, out = _run_cli(
            ["resume", ticket_id, "--note", "再试一次", "--json"], self.state_dir
        )
        payload = json.loads(out)
        self.assertTrue(payload["artifact_path"])
        self.assertTrue(pathlib.Path(payload["artifact_path"]).exists())

    def test_help_lists_the_artifacts_command(self):
        # ``--help`` makes argparse exit, so the call has to be caught.
        os.environ["MAS_STATE_DIR"] = self.state_dir
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), self.assertRaises(SystemExit):
            main(["--help"])
        self.assertIn("artifacts", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
