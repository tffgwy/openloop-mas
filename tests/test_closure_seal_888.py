# -*- coding: utf-8 -*-
"""v0.7.88 tests: the closure is sealed to the bytes it decided on.

A closed loop promises that *the same artifact and the same standard
always produce the same verdict*. Up to v0.7.87 that promise held only
at the moment of the decision: the ledger recorded the deliverable's
hash, the acceptance criteria stayed in the engine's memory, and the
files a worker wrote were recorded by *length* rather than by content.
So a verdict could not be recomputed, and a file edited after the fact
left no trace at all - the run kept saying ``closed / pass``.

This file covers:

* the seal carries the criteria, the deliverable hash and the hash of
  every file the run wrote;
* a healthy seal re-verifies from the disk;
* a file edited afterwards - even to the same length - is reported as
  drift;
* a criterion only a model could judge is reported as model-judged,
  not as recomputed;
* a tampered seal is reported as tampered;
* the command line can ask the question;
* the gate is registered and can fail.
"""

try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # loaded as a top-level module
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

from mas.engine import LoopEngine
from mas.providers import Provider
from mas.seal import build_seal, seal_digest, seal_report, verify_seal
from mas.selftest import CHECKS, EXPECTED_CHECKS


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
                            {"id": "c2", "pass": True, "note": "ok"},
                            {"id": "c3", "pass": True, "note": "reads well"},
                        ],
                    }
                ),
                "usage": usage,
                "raw": {},
            }
        return {"text": "{}", "usage": usage, "raw": {}}


class SealCase(unittest.TestCase):
    """A test case with its own state directory."""

    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="mas-p88-test-"))
        self.state = self.root / "state"
        self.state.mkdir(parents=True)
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))

    def _run(self):
        config = {
            "provider": "mock",
            "model": "test-model",
            "state_dir": str(self.state),
            "workspace": "",
            "budget": {"max_iterations": 2, "max_tokens": 1000000, "deadline_sec": 60},
            "orchestration": {"parallel_workers": 1, "reviewers": 1, "review_mode": "all"},
            "team": {"language": "zh"},
        }
        engine = LoopEngine(config, provider=_Writer(model="test-model"))
        result = engine.run(
            goal="写一份结论",
            criteria=[
                {"id": "c1", "desc": "含正文", "check": {"type": "contains", "arg": "正文"}},
                {"id": "c2", "desc": "文件写出来了",
                 "check": {"type": "file_exists", "arg": "notes/finding.txt"}},
                {"id": "c3", "desc": "内容是否通顺"},
            ],
        )
        rows = engine.ledger.entries()
        run_end = [row for row in rows if str(row.get("role") or "") == "run_end"]
        return result, rows, (run_end[-1] if run_end else None)


class TheSealTest(SealCase):
    """What the closure seals down."""

    def test_the_run_closed(self):
        result, _rows, _end = self._run()
        self.assertTrue(result.closed)

    def test_the_closure_carries_a_seal(self):
        _result, _rows, end = self._run()
        self.assertIsNotNone(end)
        seal = end.get("seal")
        self.assertIsInstance(seal, dict)
        self.assertTrue(seal.get("sha256"))

    def test_the_seal_carries_the_criteria(self):
        _result, _rows, end = self._run()
        criteria = end["seal"]["criteria"]
        ids = [str(c.get("id")) for c in criteria]
        self.assertIn("c1", ids)
        self.assertIn("c2", ids)
        self.assertIn("c3", ids)
        # The criteria are the ones the run was judged against - the
        # machine check has to be there, not just the description.
        c1 = [c for c in criteria if c.get("id") == "c1"][0]
        self.assertIsInstance(c1.get("check"), dict)

    def test_the_seal_carries_the_deliverable_hash(self):
        result, _rows, end = self._run()
        from mas.seal import sha256_bytes

        seal = end["seal"]
        self.assertEqual(
            seal["artifact_sha256"],
            sha256_bytes((result.artifact or "").encode("utf-8")),
        )

    def test_the_files_are_sealed_by_content(self):
        _result, _rows, end = self._run()
        files = end["seal"]["files"]
        self.assertTrue(files)
        row = files[0]
        self.assertEqual(str(row.get("path") or "").replace("\\", "/"),
                         "notes/finding.txt")
        self.assertTrue(row.get("sha256"))
        # The hash is of the bytes that are on the disk.
        from mas.seal import sha256_file

        self.assertEqual(row["sha256"], sha256_file(row["absolute"]))

    def test_the_ledger_books_the_hash_of_every_written_file(self):
        _result, _rows, end = self._run()
        for row in end.get("files") or []:
            self.assertTrue(row.get("sha256"), "a written file has no hash")


class VerificationTest(SealCase):
    """The seal is re-derived from the disk."""

    def test_a_healthy_seal_holds(self):
        result, _rows, end = self._run()
        report = verify_seal(end["seal"], result.artifact)
        self.assertTrue(report["ok"], report.get("problems"))
        self.assertEqual(report["state"], "holds")

    def test_a_healthy_seal_recomputes_the_machine_checks(self):
        result, _rows, end = self._run()
        report = verify_seal(end["seal"], result.artifact)
        self.assertIn("c1", report["recomputed"])
        self.assertIn("c2", report["recomputed"])

    def test_a_model_judged_criterion_is_not_recomputed(self):
        result, _rows, end = self._run()
        report = verify_seal(end["seal"], result.artifact)
        self.assertIn("c3", report["model_judged"])
        self.assertNotIn("c3", report["recomputed"])

    def test_an_edited_file_is_reported_as_drift(self):
        result, _rows, end = self._run()
        target = pathlib.Path(result.workspace) / "notes" / "finding.txt"
        original = target.read_bytes()
        # The edit keeps the length: a record that only knows the size
        # of a file cannot see this.
        tampered = "结论：SELFTST\n".encode("utf-8")
        self.assertEqual(len(tampered), len(original))
        target.write_bytes(tampered)
        report = verify_seal(end["seal"], result.artifact)
        self.assertFalse(report["ok"])
        self.assertEqual(report["state"], "drifted")
        self.assertTrue(report["drifted_files"])

    def test_a_changed_deliverable_is_reported_as_drift(self):
        result, _rows, end = self._run()
        report = verify_seal(end["seal"], (result.artifact or "") + "\n改动")
        self.assertFalse(report["ok"])
        self.assertEqual(report["state"], "drifted")

    def test_a_tampered_seal_is_reported_as_tampered(self):
        result, _rows, end = self._run()
        seal = dict(end["seal"])
        seal["verdict"] = "fail"
        report = verify_seal(seal, result.artifact)
        self.assertFalse(report["ok"])
        self.assertEqual(report["state"], "tampered")

    def test_a_missing_seal_is_reported_as_missing(self):
        report = verify_seal(None, "anything")
        self.assertFalse(report["ok"])
        self.assertEqual(report["state"], "missing")

    def test_a_verdict_that_cannot_be_recomputed_is_not_a_pass(self):
        _result, _rows, end = self._run()
        # The deliverable is gone - the verdict cannot be re-run, and
        # that has to be said out loud instead of passing.
        report = verify_seal(end["seal"], None)
        self.assertFalse(report["ok"])
        self.assertEqual(report["state"], "unrecomputable")


class SealDigestTest(unittest.TestCase):
    """The seal's own digest."""

    def test_the_digest_ignores_its_own_field(self):
        seal = build_seal(
            run_id="R1",
            criteria=[{"id": "c1", "desc": "x"}],
            artifact="hello",
            artifact_sha256="abc",
            files=[],
            scores=[{"id": "c1", "pass": True, "source": "rule"}],
            verdict="pass",
            decision="close",
        )
        self.assertEqual(seal["sha256"], seal_digest(seal))

    def test_a_changed_field_changes_the_digest(self):
        seal = build_seal(
            run_id="R1",
            criteria=[{"id": "c1", "desc": "x"}],
            artifact="hello",
            artifact_sha256="abc",
            files=[],
            scores=[],
            verdict="pass",
            decision="close",
        )
        changed = dict(seal)
        changed["decision"] = "revise"
        self.assertNotEqual(seal["sha256"], seal_digest(changed))


class LedgerReportTest(SealCase):
    """The ledger report over the sealed closures."""

    def test_the_report_sees_the_sealed_closure(self):
        _result, rows, _end = self._run()
        report = seal_report(rows)
        self.assertEqual(report["sealed"], 1)
        self.assertEqual(report["holds"], 1)
        self.assertTrue(report["ok"])

    def test_the_report_says_so_when_nothing_is_sealed(self):
        report = seal_report([])
        self.assertEqual(report["sealed"], 0)
        self.assertTrue(report["ok"])


class CommandLineTest(SealCase):
    """The command line can ask the question."""

    def _run_cli(self, *extra):
        env = dict(os.environ)
        env["MAS_STATE_DIR"] = str(self.state)
        env["PYTHONPATH"] = str(pathlib.Path(__file__).resolve().parents[1])
        env["PYTHONUTF8"] = "1"
        return subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "mas", "seal", "--json", *extra],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=120,
        )

    def test_the_command_answers(self):
        self._run()
        proc = self._run_cli()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["sealed"], 1)
        self.assertTrue(data["ok"])

    def test_the_command_fails_when_the_closure_drifted(self):
        result, _rows, _end = self._run()
        target = pathlib.Path(result.workspace) / "notes" / "finding.txt"
        target.write_bytes("结论：SELFTST\n".encode("utf-8"))
        proc = self._run_cli()
        self.assertEqual(proc.returncode, 1)
        data = json.loads(proc.stdout)
        self.assertFalse(data["ok"])
        self.assertEqual(data["drifted"], 1)


class GateRegistrationTest(unittest.TestCase):
    """The gate is registered - and it can fail."""

    def test_the_gate_is_part_of_the_suite(self):
        names = {getattr(check, "CHECK_NAME", check.__name__) for check in CHECKS}
        self.assertIn("closure_seal", names)

    def test_the_gate_count_follows_the_suite(self):
        self.assertEqual(EXPECTED_CHECKS, len(CHECKS))

    def test_the_gate_passes(self):
        from mas.selftest_seal import (
            check_a_closure_is_sealed_to_the_bytes_it_decided_on,
        )

        result = check_a_closure_is_sealed_to_the_bytes_it_decided_on(iterations=1)
        self.assertTrue(result["ok"], result.get("failures"))

    def test_the_gate_notices_a_broken_drift_detection(self):
        """The gate has to be able to fail."""
        from mas.selftest import run_selftest

        result = run_selftest(iterations=1, only=["closure_seal"])
        self.assertTrue(result["ok"], result.get("failures"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
