# -*- coding: utf-8 -*-
"""Keeping the self-test out of the ledger of the machine it runs on.

The gap
-------
A self-test that proves "the undo leaves a trace" has to actually
approve a draft and take it back - and an approval is a *human
decision*, so it is written into the main ledger of the machine. That
is right when a person approves something. It is wrong when the
approval came from a test.

The consequence was not subtle. Every run of ``mas selftest`` put
rows into ``~/.mas/ledgers/runs.jsonl`` whose workspace pointed at a
temporary folder that was deleted a moment later. The rows stayed.
So ``mas report`` afterwards reported hundreds of audit problems that
had no cause: the decisions in those rows were real records of *test*
decisions, and the chains they pointed at were gone with the temporary
folder.

A test that changes the state of the machine it runs on is not a
test - it is a side effect with an exit code.

What this module provides
-------------------------
* :func:`state_isolation` - a context manager that points
  ``MAS_STATE_DIR`` at a directory of the caller's choosing for the
  duration of a block, and puts the environment back afterwards.
  Every gate that writes a decision uses it, so a gate cannot write
  into the ledger of the machine by accident.
* :func:`snapshot_state` and :func:`diff_snapshots` - a way to *look*
  at the state directory and see whether anything changed, so the
  isolation can be verified instead of assumed.

The environment variable is the right lever because the configuration
merges the environment over the file: a temporary ``MAS_STATE_DIR``
wins over the state directory the user configured, and it is never
written back to the configuration file.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import pathlib
from typing import Any, Dict, Iterator, List


@contextlib.contextmanager
def state_isolation(root: Any) -> Iterator[pathlib.Path]:
    """Point the machine at a state directory of its own, for a while.

    The directory is ``<root>/state``. The previous value of
    ``MAS_STATE_DIR`` is restored on the way out - including the case
    where there was none, which is restored by *removing* the
    variable again, so a later reader cannot tell the difference
    between "never set" and "set to the default".
    """
    previous = os.environ.get("MAS_STATE_DIR")
    state = pathlib.Path(root) / "state"
    with contextlib.suppress(OSError):  # pragma: no cover - defensive
        state.mkdir(parents=True, exist_ok=True)
    os.environ["MAS_STATE_DIR"] = str(state)
    try:
        yield state
    finally:
        if previous is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = previous


def ledger_path(state_dir: Any) -> pathlib.Path:
    """Where the main ledger of a state directory lives."""
    return pathlib.Path(state_dir) / "ledgers" / "runs.jsonl"


def _digest(path: pathlib.Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:  # pragma: no cover - defensive
        return ""


def snapshot_state(state_dir: Any) -> Dict[str, Any]:
    """A picture of a state directory: what is in it, and what it says.

    The picture holds every file under the directory with its digest,
    and the number of rows in the main ledger. Two pictures can be
    compared, so "the self-test changed nothing" is a statement that
    can be checked instead of one that has to be believed.
    """
    base = pathlib.Path(state_dir)
    files: Dict[str, str] = {}
    if base.exists():
        try:
            for path in sorted(base.rglob("*")):
                if path.is_file():
                    try:
                        name = str(path.relative_to(base))
                    except ValueError:  # pragma: no cover - defensive
                        name = str(path)
                    files[name] = _digest(path)
        except OSError:  # pragma: no cover - defensive
            files = {}
    ledger = ledger_path(base)
    rows = 0
    if ledger.exists():
        try:
            with ledger.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        rows += 1
        except OSError:  # pragma: no cover - defensive
            rows = 0
    return {
        "state_dir": str(base),
        "files": files,
        "ledger_rows": rows,
    }


def diff_snapshots(before: Dict[str, Any], after: Dict[str, Any]) -> List[str]:
    """What changed between two pictures of a state directory.

    An empty list means nothing changed. Every entry names one thing
    that did - so the message can say *what* the self-test wrote into
    the machine, not merely that it wrote something.
    """
    problems: List[str] = []
    old_rows = int(before.get("ledger_rows") or 0)
    new_rows = int(after.get("ledger_rows") or 0)
    if old_rows != new_rows:
        problems.append(
            "the main ledger grew from %d rows to %d rows" % (old_rows, new_rows)
        )
    old_files = dict(before.get("files") or {})
    new_files = dict(after.get("files") or {})
    added = sorted(set(new_files) - set(old_files))
    if added:
        problems.append(
            "new files appeared: %s" % ", ".join(added[:8])
        )
    changed = sorted(
        name
        for name in set(old_files) & set(new_files)
        if old_files.get(name) != new_files.get(name)
    )
    if changed:
        problems.append("files changed: %s" % ", ".join(changed[:8]))
    return problems
