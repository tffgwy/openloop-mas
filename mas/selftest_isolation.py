# -*- coding: utf-8 -*-
"""The thirty-second gate: the self-test must not touch the machine.

The gap
-------
The self-test proves things by *doing* them: it approves a draft and
takes it back, it stages a change, it writes a file. Doing a decision
means recording it - and a decision is written into the main ledger
of the machine.

So every run of ``mas selftest`` used to leave rows in
``~/.mas/ledgers/runs.jsonl`` that pointed at a temporary folder which
was deleted a moment later. The rows stayed. The chains they pointed
at did not.

The consequence was a report full of ghosts: ``mas report`` reported
hundreds of audit problems that had no cause, because the ledger said
"the human approved something" and the chain said "there is no such
chain". A user who ran the self-test once was left with a machine
that reported hundreds of problems for ever, and no way to tell a
real problem from a leftover.

A test that changes the state of the machine it runs on is not a
test. It is a side effect with an exit code.

What this gate watches
----------------------
* the self-test leaves the **machine's** ledger exactly as it found
  it - not "roughly the same", but the same number of rows and the
  same bytes;
* the self-test leaves the **environment** exactly as it found it - a
  gate that re-points ``MAS_STATE_DIR`` and never puts it back
  silently re-points every gate that runs after it, which hides the
  next leak;
* the decisions the self-test makes are still *recorded* - in a state
  directory of the test's own. Isolation that threw the records away
  would be isolation by amnesia, and the gates that prove the audit
  trail works would be proving nothing.

And the gate proves it can fail: the isolation is broken on purpose -
a decision is made without it, straight into the ledger of the
machine - and the gate has to notice that it can no longer tell a
clean run from a dirty one.

The rehearsal never touches the real ledger of the machine. The
"machine" it watches is a directory of the test's own, standing in for
``~/.mas``, and the broken run only ever writes into *that*.
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import shutil
import tempfile
from typing import Any, Dict, List

from mas.isolation import (
    diff_snapshots,
    ledger_path,
    snapshot_state,
)

#: The observations the gate makes, and the two readings of them.
ISOLATION_OBSERVATIONS = (
    "the machine's ledger is exactly as it was found",
    "the environment is exactly as it was found",
    "the decisions the self-test makes are still recorded",
)


def _ledger_rows(state_dir: Any) -> int:
    """How many rows the main ledger of a state directory holds."""
    path = ledger_path(state_dir)
    if not path.exists():
        return 0
    rows = 0
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    rows += 1
    except OSError:  # pragma: no cover - defensive
        return 0
    return rows


def _approve_one_draft(workspace: pathlib.Path) -> None:
    """Make one human decision, the way the draft layer makes it."""
    from mas.drafts import approve_draft, stage_draft

    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "notes.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    staged = stage_draft(
        workspace,
        "notes.txt",
        "alpha\nbeta-agent\n".encode("utf-8"),
        reason="the agent proposes this",
    )
    approve_draft(workspace, str(staged.get("id") or ""))


def _isolation_observations(root: Any) -> Dict[str, Any]:
    """Do what a gate does, and look at what it left behind.

    Two things are watched. The first is the *machine*: a state
    directory that stands in for ``~/.mas``. The environment points at
    it, so anything that writes a decision without isolating itself
    writes into it. It must not change.

    The second is the state directory the isolation hands out. The
    decision has to land *there* - in a ledger of the test's own - so
    the gates that prove the audit trail works still prove something.
    """
    import mas.isolation as isolation_module

    machine_state = pathlib.Path(root) / "machine-state"
    machine_state.mkdir(parents=True, exist_ok=True)
    # The isolation hands out ``<root>/state``.
    isolated_root = pathlib.Path(root) / "isolated"
    isolated_root.mkdir(parents=True, exist_ok=True)
    isolated_state = isolated_root / "state"
    isolated_state.mkdir(parents=True, exist_ok=True)
    # A workspace of its own for this observation. The same folder
    # cannot be used twice: the draft of the first observation would
    # still be there, and the second one would approve a draft that
    # was approved already - so nothing would be written, and the
    # rehearsal would never see a leak.
    workspace = pathlib.Path(tempfile.mkdtemp(prefix="ws-", dir=str(root)))

    original_env = os.environ.get("MAS_STATE_DIR")
    os.environ["MAS_STATE_DIR"] = str(machine_state)
    try:
        before = snapshot_state(machine_state)
        env_before = os.environ.get("MAS_STATE_DIR")

        # A failure here is the subject of the observation, not an
        # error to raise: the point is what was left behind.
        with isolation_module.state_isolation(isolated_root), \
                contextlib.suppress(Exception):
            _approve_one_draft(workspace)

        after = snapshot_state(machine_state)
        env_after = os.environ.get("MAS_STATE_DIR")
    finally:
        if original_env is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = original_env

    return {
        "machine_before": before,
        "machine_after": after,
        "machine_problems": diff_snapshots(before, after),
        "isolated_rows": _ledger_rows(isolated_state),
        "env_before": env_before,
        "env_after": env_after,
    }


def _isolation_verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """What the gate concludes from what it saw."""
    verdicts: Dict[str, bool] = {}
    problems = list(facts.get("machine_problems") or [])
    verdicts["the machine's ledger is exactly as it was found"] = not problems
    verdicts["the environment is exactly as it was found"] = (
        facts.get("env_before") == facts.get("env_after")
    )
    # The decision has to be recorded - somewhere. If the isolation
    # threw the records away, the gates that prove the audit trail
    # works would be proving nothing.
    verdicts["the decisions the self-test makes are still recorded"] = (
        int(facts.get("isolated_rows") or 0) > 0
    )
    return verdicts


def _isolation_verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """A second reading of the same facts, written differently.

    The first reading asks the snapshot helper what changed. This one
    counts the rows of the ledger itself and compares the two
    environments directly - so a tampered verdict cannot survive both.
    """
    verdicts: Dict[str, bool] = {}
    before = dict(facts.get("machine_before") or {})
    after = dict(facts.get("machine_after") or {})
    verdicts["the machine's ledger is exactly as it was found"] = (
        int(before.get("ledger_rows") or 0) == int(after.get("ledger_rows") or 0)
        and dict(before.get("files") or {}) == dict(after.get("files") or {})
    )
    verdicts["the environment is exactly as it was found"] = (
        str(facts.get("env_before") or "") == str(facts.get("env_after") or "")
    )
    verdicts["the decisions the self-test makes are still recorded"] = (
        int(facts.get("isolated_rows") or 0) > 0
    )
    return verdicts


def check_the_selftest_leaves_the_machine_alone(
    iterations: int = 3,
) -> Dict[str, Any]:
    """The self-test must not touch the machine - and the gate can fail."""
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        root = pathlib.Path(tempfile.mkdtemp(prefix="mas-isolation-"))
        rounds += 1
        try:
            try:
                facts = _isolation_observations(root)
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the isolation check broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                facts = {}

            verdicts = _isolation_verdicts(facts)
            independent = _isolation_verdicts_independent(facts)
            for label in ISOLATION_OBSERVATIONS:
                if label not in verdicts:
                    failures.append(
                        "round %d: the gate did not observe %s"
                        % (round_index + 1, label)
                    )
                    continue
                if not verdicts[label]:
                    failures.append("round %d: %s" % (round_index + 1, label))
                if independent.get(label) != verdicts.get(label):
                    failures.append(
                        "round %d: %s - the two readings disagree (%s vs %s)"
                        % (
                            round_index + 1,
                            label,
                            verdicts.get(label),
                            independent.get(label),
                        )
                    )

            # The gate proves it can fail: the isolation is broken on
            # purpose - the decision is made without it, straight into
            # the ledger of the machine - and the gate has to notice.
            import mas.isolation as isolation_module

            real_isolation = isolation_module.state_isolation

            class _NoIsolation:
                """A stand-in that isolates nothing at all."""

                def __init__(self, target: pathlib.Path) -> None:
                    self._target = target

                def __enter__(self):
                    return self._target

                def __exit__(self, *exc_info):
                    return False

            def _no_isolation(_root, target=root):
                return _NoIsolation(pathlib.Path(target))

            isolation_module.state_isolation = _no_isolation
            broken: Dict[str, bool] = {}
            try:
                broken = _isolation_verdicts(_isolation_observations(root))
            except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                broken = {}
            finally:
                isolation_module.state_isolation = real_isolation
            if broken.get("the machine's ledger is exactly as it was found"):
                failures.append(
                    "round %d: the gate did not notice that the isolation is "
                    "broken - it cannot fail, so it does not watch"
                    % (round_index + 1)
                )
        finally:
            shutil.rmtree(root, ignore_errors=True)

    return {
        "name": "selftest_isolation",
        "title": "自检不往用户账本里写东西",
        "skipped": False,
        "invariant": (
            "自检做完之后用户账本一行不多、一行不少；"
            "环境变量原样还原；"
            "自检自己的决定仍然记在它自己的账本里"
        ),
        "detail": "自检不污染机器状态：mas selftest",
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_the_selftest_leaves_the_machine_alone.CHECK_NAME = "selftest_isolation"  # type: ignore[attr-defined]
