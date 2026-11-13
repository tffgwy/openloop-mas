"""Tests for the append-only ledger.

The ledger assigns a monotonically increasing ``seq`` to every entry. The
count is cached for performance (re-reading the whole file on every append
made a long run O(n^2)), so these tests pin the invariants that the cache
must not break: the sequence stays gapless, and two writers that share the
same file still get distinct sequence numbers.
"""

try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # loaded as a top-level module (unittest -s tests)
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401


import pathlib
import tempfile
import unittest

from mas.ledger import Ledger


class LedgerSequenceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="mas-ledger-seq-")
        self.addCleanup(self._tmp.cleanup)
        self.path = pathlib.Path(self._tmp.name) / "runs.jsonl"

    def test_seq_is_gapless_and_starts_at_one(self):
        ledger = Ledger(self.path)
        for expected in range(1, 21):
            entry = ledger.append({"role": "worker", "n": expected})
            self.assertEqual(entry["seq"], expected)
        rows = ledger.entries()
        self.assertEqual(len(rows), 20)
        self.assertEqual([row["seq"] for row in rows], list(range(1, 21)))

    def test_cache_does_not_break_when_the_file_is_truncated(self):
        ledger = Ledger(self.path)
        ledger.append({"role": "worker"})
        ledger.append({"role": "worker"})
        # Something else rewrote the ledger behind our back.
        self.path.write_text("", encoding="utf-8")
        entry = ledger.append({"role": "worker"})
        self.assertEqual(entry["seq"], 1)

    def test_two_writers_on_the_same_file_get_distinct_sequences(self):
        first = Ledger(self.path)
        second = Ledger(self.path)
        first.append({"role": "a"})
        second.append({"role": "b"})
        first.append({"role": "c"})
        sequences = sorted(row["seq"] for row in Ledger(self.path).entries())
        self.assertEqual(sequences, [1, 2, 3])

    def test_entries_are_still_appended_never_rewritten(self):
        ledger = Ledger(self.path)
        ledger.append({"role": "worker", "value": "one"})
        ledger.append({"role": "worker", "value": "two"})
        lines = self.path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn('"one"', lines[0])
        self.assertIn('"two"', lines[1])

    def test_ts_is_filled_in(self):
        ledger = Ledger(self.path)
        entry = ledger.append({"role": "worker"})
        self.assertTrue(entry.get("ts"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
