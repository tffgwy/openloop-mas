# -*- coding: utf-8 -*-
"""The fifty-sixth gate: the suite must converge, and say so (v0.11.4).

The gate this file watches is the one that watches the *suite* from
the outside. Two things about it matter, and they are tested here:

* it is **green** on a suite that cleans up after itself - a gate
  that is always red is a gate nobody reads;
* it is **red** on a suite that does not - and that is the part
  that cannot be taken on faith. The red is proved by breaking the
  MCP gate on purpose: the copy of the suite that leaves its server
  stranded must be reported as a leak, and the copy that closes the
  pipe must not.

The second test is the expensive one, and it is worth its price: a
gate that cannot fail is decoration, and this one is shown to fail.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

def _run_gate(package: Path) -> dict:
    """Run the convergence gate and return its row.

    The gate is run in a child process, with the given package on the
    import path, so a *copy* of the suite can be judged by the same
    gate that judges the real one.
    """
    script = (
        "import json, sys\n"
        "import mas.selftest_convergence as C\n"
        "C._REHEARSAL_PACKAGE = sys.argv[1]\n"
        "row = C.check_the_selftest_converges_and_says_so(iterations=1)\n"
        "sys.stdout.write(json.dumps(row, ensure_ascii=False))\n"
    )
    with tempfile.TemporaryDirectory(prefix="mas-converge-test-") as tmp:
        probe = Path(tmp) / "probe.py"
        probe.write_text(script, encoding="utf-8")
        env = dict(os.environ)
        env["PYTHONPATH"] = str(package)
        env["PYTHONUTF8"] = "1"
        env["MAS_STATE_DIR"] = str(Path(tmp) / "state")
        completed = subprocess.run(
            [sys.executable, "-X", "utf8", str(probe), str(package)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=600,
        )
    self_check = completed.stdout.strip().splitlines()
    assert self_check, completed.stderr[-2000:]
    return json.loads(self_check[-1])


def _sabotage_the_mcp_cleanup(source: str) -> str:
    """Remove the MCP gate's shutdown block, leaving the server stranded.

    The block is located *by structure*, not by an exact quote: the
    quote would rot the first time the shutdown code was reworded,
    and a rotten quote fails the test for the wrong reason - it says
    "the copy does not look like the original" instead of "the copy
    leaks".

    What is removed is the whole tail of the gate: the moment the
    pipe is closed, the wait, and the kill that follows it. Leaving
    the kill in place would *hide* the leak (the child would be
    killed and the suite would look clean), so all three go.
    """
    lines = source.splitlines()
    close_at = None
    for index, line in enumerate(lines):
        if "proc.stdin.close()" in line:
            close_at = index
            break
    assert close_at is not None, "the MCP gate no longer closes its pipe"

    start = close_at
    while start > 0 and "contextlib.suppress" not in lines[start]:
        start -= 1
    assert "contextlib.suppress" in lines[start], lines[start]

    end = close_at
    while end < len(lines) and not (
        lines[end].startswith("    return {") or lines[end].startswith("        return {")
    ):
        end += 1
    assert end < len(lines), "the MCP gate has no return statement"
    while end > close_at and not lines[end - 1].strip():
        end -= 1

    sabotaged = [
        *lines[:start],
        "        # SABOTAGE: the server is never told to leave",
        "        pass",
        *lines[end:],
    ]
    return "\n".join(sabotaged) + "\n"


class TheSuiteConvergesTest(unittest.TestCase):
    def test_the_gate_is_green_on_a_clean_suite(self):
        row = _run_gate(REPO)
        self.assertTrue(row["ok"], row)
        self.assertEqual(row["failures"], [])
        self.assertEqual(row["name"], "selftest_converges")
        self.assertTrue(row["title"])
        self.assertTrue(row["invariant"])

    def test_the_gate_reports_no_children_left_behind(self):
        row = _run_gate(REPO)
        # The suite that cleans up after itself answers "none".
        self.assertEqual(row["details"]["children_left"], 0)

    def test_the_gate_is_red_on_a_suite_that_leaks(self):
        # The proof that the gate can fail: a copy of the suite whose
        # MCP gate never closes the pipe. The copy is judged by the
        # same gate that judges the real suite.
        with tempfile.TemporaryDirectory(prefix="mas-leaky-suite-") as tmp:
            leaky = Path(tmp) / "leaky"
            shutil.copytree(REPO / "mas", leaky / "mas")
            target = leaky / "mas" / "selftest.py"
            source = target.read_text(encoding="utf-8")
            # Sabotage: the server is never told to leave.
            sabotaged = _sabotage_the_mcp_cleanup(source)
            self.assertNotEqual(sabotaged, source)
            self.assertNotIn("proc.stdin.close()", sabotaged)
            target.write_text(sabotaged, encoding="utf-8")
            row = _run_gate(leaky)

        self.assertFalse(row["ok"], row)
        self.assertTrue(row["failures"], row)
        self.assertEqual(row["details"]["children_left"], 1)
        self.assertIn("no_leaked_children", " ".join(row["failures"]))


class TheBudgetIsHonestTest(unittest.TestCase):
    def test_a_cut_suite_says_so_and_exits_differently(self):
        """A suite that ran out of time must not look like a green one."""
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO)
        env["PYTHONUTF8"] = "1"
        with tempfile.TemporaryDirectory(prefix="mas-budget-test-") as tmp:
            env["MAS_STATE_DIR"] = str(Path(tmp) / "state")
            completed = subprocess.run(
                [
                    sys.executable, "-X", "utf8", "-m", "mas", "selftest",
                    "--iterations", "1", "--time-budget", "5", "--json",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                timeout=600,
            )
        payload = json.loads(completed.stdout)
        self.assertFalse(payload["complete"])
        self.assertTrue(payload["cut"])
        self.assertEqual(completed.returncode, 3)

    def test_a_green_suite_exits_zero_and_is_complete(self):
        from mas.selftest import run_selftest

        result = run_selftest(iterations=1, only=["relay_refusal"])
        self.assertTrue(result["complete"])
        self.assertEqual(result["cut"], [])


class TheCatalogueIsFreeTest(unittest.TestCase):
    def test_the_catalogue_lists_every_gate_without_running_them(self):
        from mas.selftest import CHECKS, list_checks

        rows = list_checks()
        self.assertEqual(len(rows), len(CHECKS))
        self.assertEqual(rows[-1]["name"], "selftest_converges")
        for row in rows:
            self.assertTrue(row["name"])
            self.assertTrue(row["function"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
