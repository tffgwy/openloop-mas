# -*- coding: utf-8 -*-
"""The twenty-first gate: a human decision leaves a trace that cannot be
silently changed.

The ledger is append-only, and that is a promise about behaviour. The
bytes on the disk are another matter: an editor can change a line and
the file stays readable. So the approval and the undo each leave a
hash-linked entry in a chain next to the workspace, and a copy in the
main ledger. The gate watches that:

* every decision - the proposal, the approval, the undo - leaves an
  entry, in order, and a draft that was approved and taken back twice
  leaves *five* entries, not one;
* every entry links to the one before it by hash, and hashes to the
  hash it carries;
* a change to an entry, a deleted entry, a changed ledger copy and a
  missing ledger copy are each noticed and named;
* the chain and the ledger agree.

And the gate proves it can fail: the chain's verification is broken on
purpose, and the gate has to notice that it can no longer tell a real
chain from a broken one.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import tempfile
from typing import Any, Dict, List

#: The observations the gate makes, and the two readings of them.
AUDIT_OBSERVATIONS = (
    "every decision leaves an entry, in order",
    "the same draft keeps every decision",
    "the chain links entry to entry",
    "the chain and the ledger agree",
    "a changed entry is noticed",
    "a deleted entry is noticed",
    "a deleted last entry is caught by the ledger",
    "a changed ledger copy is noticed",
    "a missing ledger copy is noticed",
    "the command line can check the chain",
)


def _audit_observations(root: Any) -> Dict[str, Any]:
    """Watch what a chain of human decisions records - and what it catches."""
    from mas.audit import AuditChain, audit_report
    from mas.drafts import approve_draft, stage_draft, undo_draft
    from mas.ledger import Ledger

    seen: Dict[str, Any] = {}
    state = pathlib.Path(root) / "state"
    state.mkdir(parents=True, exist_ok=True)
    workspace = pathlib.Path(root) / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / "notes.txt"
    target.write_text("alpha\nbeta\n", encoding="utf-8")

    staged = stage_draft(
        workspace,
        "notes.txt",
        "alpha\nbeta-agent\n".encode("utf-8"),
        reason="the agent proposes this",
    )
    draft_id = str(staged.get("id") or "")
    approve_draft(workspace, draft_id)
    undo_draft(workspace, draft_id, note="approved the wrong thing")
    approve_draft(workspace, draft_id)
    undo_draft(workspace, draft_id, note="still wrong")

    chain = AuditChain(workspace / ".mas-drafts" / "audit.jsonl")
    entries = chain.entries()
    seen["events"] = [str(entry.get("event") or "") for entry in entries]
    seen["entry_count"] = len(entries)
    seen["draft_ids"] = [str(entry.get("draft_id") or "") for entry in entries]
    seen["verify"] = chain.verify()
    seen["links_ok"] = all(
        str(entry.get("prev_sha256") or "") == (
            str(entries[index - 1].get("sha256") or "") if index else "0" * 64
        )
        for index, entry in enumerate(entries)
    )

    ledger_path = state / "ledgers" / "runs.jsonl"
    ledger = Ledger(ledger_path)
    ledger_rows = ledger.entries()
    seen["ledger_roles"] = [str(row.get("role") or "") for row in ledger_rows]

    report = audit_report(workspace, ledger_rows)
    seen["audit_ok"] = bool(report.get("ok"))
    seen["audit_problems"] = list(report.get("problems") or [])

    # A changed entry must be noticed.
    tampered = pathlib.Path(root) / "tampered"
    tampered.mkdir(parents=True, exist_ok=True)
    tampered_file = tampered / "notes.txt"
    tampered_file.write_text("alpha\nbeta\n", encoding="utf-8")
    tampered_draft = stage_draft(
        tampered, "notes.txt", "alpha\nbeta-agent\n".encode("utf-8")
    )
    tampered_id = str(tampered_draft.get("id") or "")
    approve_draft(tampered, tampered_id)
    undo_draft(tampered, tampered_id, note="wrong")
    chain_file = tampered / ".mas-drafts" / "audit.jsonl"
    lines = chain_file.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[1])
    row["note"] = "changed behind the back of the reader"
    lines[1] = json.dumps(row, ensure_ascii=False)
    chain_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    broken = AuditChain(chain_file).verify()
    seen["tamper_noticed"] = not bool(broken.get("ok"))
    seen["tamper_problems"] = list(broken.get("problems") or [])

    # A deleted entry must be noticed. A gap in the middle breaks the
    # chain: the sequence numbers no longer line up.
    deleted = pathlib.Path(root) / "deleted"
    deleted.mkdir(parents=True, exist_ok=True)
    deleted_file = deleted / "notes.txt"
    deleted_file.write_text("alpha\nbeta\n", encoding="utf-8")
    deleted_draft = stage_draft(
        deleted, "notes.txt", "alpha\nbeta-agent\n".encode("utf-8")
    )
    deleted_id = str(deleted_draft.get("id") or "")
    approve_draft(deleted, deleted_id)
    undo_draft(deleted, deleted_id, note="wrong")
    deleted_chain = deleted / ".mas-drafts" / "audit.jsonl"
    deleted_lines = deleted_chain.read_text(encoding="utf-8").splitlines()
    deleted_chain.write_text(
        "\n".join(deleted_lines[:1] + deleted_lines[2:]) + "\n", encoding="utf-8"
    )
    gone = AuditChain(deleted_chain).verify()
    seen["delete_noticed"] = not bool(gone.get("ok"))
    seen["delete_problems"] = list(gone.get("problems") or [])

    # A deleted *last* entry leaves the chain itself self-consistent -
    # there is nothing after it to point at what was removed. This is
    # the honest limit of a single chain, and the second copy is what
    # catches it: the ledger still holds the decision.
    tail = pathlib.Path(root) / "tail"
    tail.mkdir(parents=True, exist_ok=True)
    tail_file = tail / "notes.txt"
    tail_file.write_text("alpha\nbeta\n", encoding="utf-8")
    tail_draft = stage_draft(tail, "notes.txt", "alpha\nbeta-agent\n".encode("utf-8"))
    tail_id = str(tail_draft.get("id") or "")
    approve_draft(tail, tail_id)
    undo_draft(tail, tail_id, note="wrong")
    tail_chain = tail / ".mas-drafts" / "audit.jsonl"
    tail_lines = tail_chain.read_text(encoding="utf-8").splitlines()
    tail_chain.write_text("\n".join(tail_lines[:-1]) + "\n", encoding="utf-8")
    tail_rows = [row for row in ledger_rows if str(row.get("workspace") or "") == str(tail)]
    tail_report = audit_report(tail, tail_rows)
    seen["tail_noticed"] = not bool(tail_report.get("ok"))
    seen["tail_problems"] = list(tail_report.get("problems") or [])

    # A changed ledger copy must be noticed.
    changed = pathlib.Path(root) / "changed-copy"
    changed.mkdir(parents=True, exist_ok=True)
    changed_file = changed / "notes.txt"
    changed_file.write_text("alpha\nbeta\n", encoding="utf-8")
    changed_draft = stage_draft(
        changed, "notes.txt", "alpha\nbeta-agent\n".encode("utf-8")
    )
    changed_id = str(changed_draft.get("id") or "")
    approve_draft(changed, changed_id)
    undo_draft(changed, changed_id, note="wrong")
    copy = Ledger(state / "copy.jsonl")
    for source in ledger_rows:
        row = dict(source)
        if str(row.get("role") or "") == "draft_undo":
            row["note"] = "the ledger was edited on its own"
        copy.append(row)
    copy_report = audit_report(changed, copy.entries())
    seen["copy_change_noticed"] = not bool(copy_report.get("ok"))

    # A missing ledger copy must be noticed.
    missing = pathlib.Path(root) / "missing-copy"
    missing.mkdir(parents=True, exist_ok=True)
    missing_file = missing / "notes.txt"
    missing_file.write_text("alpha\nbeta\n", encoding="utf-8")
    missing_draft = stage_draft(
        missing, "notes.txt", "alpha\nbeta-agent\n".encode("utf-8")
    )
    missing_id = str(missing_draft.get("id") or "")
    approve_draft(missing, missing_id)
    undo_draft(missing, missing_id, note="wrong")
    dropped = Ledger(state / "dropped.jsonl")
    for source in ledger_rows:
        if str(source.get("role") or "") == "draft_undo":
            continue
        dropped.append(source)
    drop_report = audit_report(missing, dropped.entries())
    seen["missing_copy_noticed"] = not bool(drop_report.get("ok"))

    # The command line can check the chain.
    import argparse
    import contextlib
    import io

    from mas.cli import cmd_audit

    cli = argparse.Namespace(
        workspace=str(workspace),
        json=False,
    )
    plain = io.StringIO()
    noise = io.StringIO()
    with contextlib.redirect_stdout(plain), contextlib.redirect_stderr(noise):
        cli_code = cmd_audit(cli)
    seen["cli_exit_code"] = int(cli_code)
    seen["cli_text"] = plain.getvalue()
    return seen


def _exit_code(facts: Dict[str, Any]) -> int:
    """The exit code the command line returned.

    ``0`` is falsy in Python, so ``facts.get("cli_exit_code") or 1``
    turns a *successful* run into a failure. The code is read
    explicitly instead.
    """
    try:
        return int(facts.get("cli_exit_code"))
    except (TypeError, ValueError):
        return -1


def _audit_verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """Turn the collected facts into the verdicts of the gate."""
    events = [str(event) for event in (facts.get("events") or [])]
    draft_ids = [str(value) for value in (facts.get("draft_ids") or [])]
    roles = [str(role) for role in (facts.get("ledger_roles") or [])]
    return {
        "every decision leaves an entry, in order": (
            events[:5] == [
                "draft_stage",
                "draft_approve",
                "draft_undo",
                "draft_approve",
                "draft_undo",
            ]
            and int(facts.get("entry_count") or 0) == 5
        ),
        "the same draft keeps every decision": (
            len(draft_ids) == 5
            and len(set(draft_ids)) == 1
            and draft_ids[0] != ""
        ),
        "the chain links entry to entry": (
            bool(facts.get("links_ok")) is True
            and bool((facts.get("verify") or {}).get("ok")) is True
        ),
        "the chain and the ledger agree": (
            bool(facts.get("audit_ok")) is True
            and not list(facts.get("audit_problems") or [])
            and roles.count("draft_undo") == 2
            and roles.count("draft_approve") == 2
        ),
        "a changed entry is noticed": (
            facts.get("tamper_noticed") is True
            and len(list(facts.get("tamper_problems") or [])) >= 1
        ),
        "a deleted entry is noticed": (
            facts.get("delete_noticed") is True
            and len(list(facts.get("delete_problems") or [])) >= 1
        ),
        "a deleted last entry is caught by the ledger": (
            facts.get("tail_noticed") is True
            and len(list(facts.get("tail_problems") or [])) >= 1
        ),
        "a changed ledger copy is noticed": (
            facts.get("copy_change_noticed") is True
        ),
        "a missing ledger copy is noticed": (
            facts.get("missing_copy_noticed") is True
        ),
        "the command line can check the chain": (
            _exit_code(facts) == 0
            and "\u5ba1\u8ba1\u94fe" in str(facts.get("cli_text") or "")
            and "\u5b8c\u597d" in str(facts.get("cli_text") or "")
        ),
    }


def _audit_verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """A second, independent reading of the same facts."""
    verdicts: Dict[str, bool] = {}
    events = [str(event) for event in (facts.get("events") or [])]
    verify = facts.get("verify") or {}
    problems = list(facts.get("audit_problems") or [])
    verdicts["every decision leaves an entry, in order"] = (
        len(events) == 5
        and events[0] == "draft_stage"
        and events.count("draft_approve") == 2
        and events.count("draft_undo") == 2
        and events.index("draft_stage") == 0
    )
    verdicts["the same draft keeps every decision"] = (
        len({str(v) for v in (facts.get("draft_ids") or [])}) == 1
        and int(facts.get("entry_count") or 0) == len(facts.get("draft_ids") or [])
    )
    verdicts["the chain links entry to entry"] = (
        bool(facts.get("links_ok")) and verify.get("ok") is True
        and int(verify.get("entries") or 0) == int(facts.get("entry_count") or 0)
    )
    verdicts["the chain and the ledger agree"] = (
        facts.get("audit_ok") is True and not problems
    )
    verdicts["a changed entry is noticed"] = (
        facts.get("tamper_noticed") is True
        and bool(facts.get("tamper_problems"))
    )
    verdicts["a deleted entry is noticed"] = (
        facts.get("delete_noticed") is True
        and bool(facts.get("delete_problems"))
    )
    verdicts["a deleted last entry is caught by the ledger"] = (
        facts.get("tail_noticed") is True and bool(facts.get("tail_problems"))
    )
    verdicts["a changed ledger copy is noticed"] = (
        facts.get("copy_change_noticed") is True
    )
    verdicts["a missing ledger copy is noticed"] = (
        facts.get("missing_copy_noticed") is True
    )
    verdicts["the command line can check the chain"] = (
        _exit_code(facts) == 0
        and "\u5b8c\u597d" in str(facts.get("cli_text") or "")
    )
    return verdicts


def check_a_human_decision_leaves_a_trace_that_cannot_be_changed(
    iterations: int = 10,
) -> Dict[str, Any]:
    """A human decision leaves a trace that cannot be silently changed.

    The gate proves it can fail: the chain's verification is broken on
    purpose, and the gate has to notice.
    """
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        root = pathlib.Path(tempfile.mkdtemp(prefix="mas-audit-"))
        rounds += 1
        # The ledger copy lives in the state directory, so the gate
        # points the state at the temporary folder for the duration of
        # the round. Without this the gate would read - and write - the
        # real ledger of the machine, and the decisions of a test round
        # would end up in the user's own audit trail.
        previous_state = os.environ.get("MAS_STATE_DIR")
        os.environ["MAS_STATE_DIR"] = str(pathlib.Path(root) / "state")
        try:
            try:
                facts = _audit_observations(root)
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the audit chain broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                facts = {}
            verdicts = _audit_verdicts(facts)
            independent = _audit_verdicts_independent(facts)
            for label in AUDIT_OBSERVATIONS:
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

            # The gate proves it can fail: the chain's verification is
            # broken on purpose, and the gate has to notice.
            import mas.audit as audit_module

            real_verify = audit_module.AuditChain.verify

            def _verify_that_always_passes(self):
                return {"ok": True, "entries": 999, "head": "", "problems": []}

            try:
                audit_module.AuditChain.verify = _verify_that_always_passes
                broken = {}
                try:
                    broken = _audit_verdicts(_audit_observations(root))
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                audit_module.AuditChain.verify = real_verify
            if broken.get("a changed entry is noticed"):
                failures.append(
                    "round %d: the gate did not notice that the chain no "
                    "longer verifies - it cannot fail, so it does not watch"
                    % (round_index + 1)
                )
        finally:
            if previous_state is None:
                os.environ.pop("MAS_STATE_DIR", None)
            else:
                os.environ["MAS_STATE_DIR"] = previous_state
            shutil.rmtree(root, ignore_errors=True)

    return {
        "name": "audit_chain",
        "title": "批准和撤销都留下不能篡改的痕迹",
        "invariant": (
            "每一次批准和撤销都写进链式审计链（修改、删除都能被发现）；"
            "账本里的副本与链不一致时也能被发现"
        ),
        "detail": "检查人工决定的审计链：mas audit",
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_a_human_decision_leaves_a_trace_that_cannot_be_changed.CHECK_NAME = "audit_chain"  # type: ignore[attr-defined]
