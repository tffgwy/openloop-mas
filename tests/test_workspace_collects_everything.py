# -*- coding: utf-8 -*-
"""v0.7.61 regression tests: nothing the loop produces is scattered.

Before v0.7.61 a run wrote its files into the caller's directory (or,
in an automated run, a temporary one that the operating system deletes
when the process ends) and its deliverable into a different place. So a
closed run could hand over a deliverable whose supporting files were
already gone.

Now every run owns a folder under the state directory, and both the
files and the deliverable live there.
"""

try:  # pragma: no cover
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # pragma: no cover
    import os    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401

import jsonimport pathlibimport tempfileimport unittestfrom mas.config import DEFAULT_CONFIGfrom mas.engine import LoopEnginefrom mas.report import run_summaryfrom mas.workspace import (    DELIVERABLE_NAME,    collect_files,    resolve_workspace,    safe_run_name,)class _WriterProvider:
    """Writes a file through the write protocol, then answers."""

    name = "provider"
    model = "provider-1"

    def complete(self, *, system, prompt, role="worker", expect_json=False,
                 max_tokens=4000):
        if role == "worker":
            return {
                "text": (
                    '<<<MAS:FILE path="notes/finding.txt">>>>\n'
                    "FINDING-BODY\n"
                    "<<<MAS:END FILE>>>\n"
                    "\u5199\u597d\u4e86\u3002"
                ),
                "usage": {"total_tokens": 100},
                "raw": {},
            }
        if role == "planner":
            return {"text": json.dumps({"summary": "s", "plan": ["p"],
                                        "risks": []}),
                    "usage": {"total_tokens": 10}, "raw": {}}
        if role == "reflector":
            return {"text": json.dumps({"summary": "r", "issues": [],
                                        "next_action": "revise"}),
                    "usage": {"total_tokens": 20}, "raw": {}}
        if role == "verifier":
            return {"text": json.dumps({"verdict": "pass", "scores": []}),
                    "usage": {"total_tokens": 30}, "raw": {}}
        return {"text": "{}", "usage": {"total_tokens": 5}, "raw": {}}

    def describe(self):
        return {"provider": self.name, "model": self.model, "chain": ["p/m"]}


def _config(state_dir, **overrides):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg["state_dir"] = state_dir
    cfg.update(overrides)
    return cfg


class RunWorkspaceTest(unittest.TestCase):
    """A run owns a folder, and everything it produced is in it."""

    def setUp(self):
        self.state = tempfile.mkdtemp(prefix="mas-ws-state-")
        self.engine = LoopEngine(_config(self.state), provider=_WriterProvider())

    def _run(self, criteria):
        return self.engine.run(
            "\u628a\u7ed3\u8bba\u5199\u8fdb notes/finding.txt",
            criteria,
        )

    def test_the_run_writes_the_file_into_its_own_folder(self):
        result = self._run([
            {"id": "c1", "desc": "\u6587\u4ef6\u5b58\u5728",
             "check": {"type": "file_exists", "arg": "notes/finding.txt"}},
        ])
        self.assertTrue(result.closed, result.status)
        run_dir = pathlib.Path(result.artifact_path).parent
        written = run_dir / "notes" / "finding.txt"
        self.assertTrue(written.exists(), "the file must be in the run folder")
        self.assertIn("FINDING-BODY", written.read_text(encoding="utf-8"))

    def test_the_deliverable_sits_next_to_the_files_it_produced(self):
        result = self._run([
            {"id": "c1", "desc": "\u6587\u4ef6\u5b58\u5728",
             "check": {"type": "file_exists", "arg": "notes/finding.txt"}},
        ])
        run_dir = pathlib.Path(result.artifact_path).parent
        self.assertEqual(run_dir.name, result.run_id)
        self.assertTrue((run_dir / DELIVERABLE_NAME).exists())
        self.assertTrue((run_dir / "notes" / "finding.txt").exists())

    def test_the_folder_is_under_the_state_directory(self):
        result = self._run([
            {"id": "c1", "desc": "\u6587\u4ef6\u5b58\u5728",
             "check": {"type": "file_exists", "arg": "notes/finding.txt"}},
        ])
        run_dir = pathlib.Path(result.artifact_path).parent
        state = pathlib.Path(self.state).resolve()
        self.assertTrue(
            str(run_dir.resolve()).startswith(str(state)),
            f"the run folder must live under the state dir: {run_dir}",
        )

    def test_the_ledger_books_the_workspace_and_the_files(self):
        result = self._run([
            {"id": "c1", "desc": "\u6587\u4ef6\u5b58\u5728",
             "check": {"type": "file_exists", "arg": "notes/finding.txt"}},
        ])
        rows = self.engine.ledger.entries()
        end = [r for r in rows if r.get("role") == "run_end"][0]
        self.assertTrue(end.get("workspace"))
        self.assertEqual(
            pathlib.Path(end["workspace"]).resolve(),
            pathlib.Path(result.artifact_path).parent.resolve(),
        )
        paths = [row.get("path") for row in (end.get("files") or [])]
        self.assertIn("notes/finding.txt", paths)

    def test_the_report_carries_the_workspace(self):
        result = self._run([
            {"id": "c1", "desc": "\u6587\u4ef6\u5b58\u5728",
             "check": {"type": "file_exists", "arg": "notes/finding.txt"}},
        ])
        rows = self.engine.ledger.entries()
        summary = run_summary(result.run_id, rows)
        self.assertTrue(summary.get("workspace"))
        self.assertTrue(summary.get("files"))

    def test_a_workspace_the_user_named_wins(self):
        workspace = tempfile.mkdtemp(prefix="mas-ws-user-")
        engine = LoopEngine(
            _config(self.state, workspace=workspace),
            provider=_WriterProvider(),
        )
        result = engine.run(
            "\u628a\u7ed3\u8bba\u5199\u8fdb notes/finding.txt",
            [{"id": "c1", "desc": "\u6587\u4ef6\u5b58\u5728",
              "check": {"type": "file_exists", "arg": "notes/finding.txt"}}],
        )
        self.assertTrue(result.closed, result.status)
        self.assertTrue(
            (pathlib.Path(workspace) / "notes" / "finding.txt").exists(),
            "the file must land in the workspace the user named",
        )
        self.assertEqual(
            pathlib.Path(result.artifact_path).parent.resolve(),
            pathlib.Path(workspace).resolve(),
        )



class WorkspaceHelperTest(unittest.TestCase):
    """The helpers themselves."""

    def test_a_run_id_cannot_escape_the_parent_folder(self):
        name = safe_run_name("../../evil")
        self.assertNotIn("/", name)
        self.assertNotIn("\\", name)
        self.assertNotIn("..", name)

    def test_an_empty_run_id_still_names_a_folder(self):
        self.assertTrue(safe_run_name(""))

    def test_a_named_workspace_wins_over_the_default(self):
        got = resolve_workspace("/state", "R-1", "/somewhere/else")
        self.assertEqual(str(got).replace("\\", "/"), "/somewhere/else")

    def test_without_a_named_workspace_the_run_gets_its_own_folder(self):
        got = resolve_workspace("/state", "R-1", "")
        self.assertTrue(
            str(got).replace("\\", "/").endswith("/state/artifacts/R-1")
        )

    def test_collect_files_lists_what_is_inside(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            (base / "notes").mkdir()
            (base / "notes" / "a.txt").write_text("x", encoding="utf-8")
            (base / DELIVERABLE_NAME).write_text("y", encoding="utf-8")
            rows = collect_files(base)
            paths = sorted(row["path"] for row in rows)
            self.assertEqual(paths, ["deliverable.md", "notes/a.txt"])

    def test_collect_files_of_a_missing_folder_is_empty(self):
        self.assertEqual(collect_files("/definitely/not/here"), [])


if __name__ == "__main__":
    unittest.main()
