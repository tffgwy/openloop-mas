# -*- coding: utf-8 -*-
"""The fifty-fourth gate: an approved change is not a drift.

The gap
------
The seal compares the bytes on the disk with the bytes the closure
was decided on. When they differ, the seal had one word for it:
*drifted* - and a drifted conclusion is an **accusation**. It says
"the run lied about what it decided on".

But a human who reviews a deliverable and approves a change is
*doing the loop's job*, not attacking it. Before v0.11.2 a
legitimate, approved edit made the seal accuse the run of lying -
and the report, the command line and the MCP surface all repeated
the accusation, because they all read the same seal.

So this gate watches the difference between the two:

* a change a person **approved** is a *take-over*: the conclusion
  still holds, and the report says so in those words;
* a change **nobody** approved is still a drift, and the lock is not
  weakened by any of this;
* an approval that was **taken back** does not excuse anything -
  the bytes it wrote are not standing any more;
* an approval of another **folder** does not excuse a change here,
  even when the bytes are identical;
* an approval whose **chain is broken** does not excuse anything
  either - an approval nobody can verify is not evidence.

And the gate proves it can fail: the take-over is broken on purpose -
every approved change is reported as a drift again - and the gate has
to notice that the two cases can no longer be told apart.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import tempfile
from typing import Any, Dict, List

#: The observations the gate makes, and the two readings of them.
SEAL_TAKEOVER_OBSERVATIONS = (
    "an approved change is reported as a take-over, not as a drift",
    "an unapproved change is still reported as a drift",
    "an approval that was taken back does not excuse the change",
    "an approval of another folder does not excuse the change",
    "an approval whose chain is broken does not excuse the change",
    "the report and the command line say the same thing",
)


def _run_a_closure(root: Any) -> Dict[str, Any]:
    """Run one real closure offline, and return what it left behind."""
    from mas.engine import LoopEngine
    from mas.providers import Provider

    class _Writer(Provider):
        name = "selftest-seal-takeover"

        def complete(self, *, system: str = "", prompt: str = "", role: str = "worker",
                     expect_json: bool = False, max_tokens: int = 4000) -> Dict[str, Any]:
            usage = {"total_tokens": 10}
            if role == "worker":
                text = (
                    "交付物正文\n"
                    '<<<MAS:FILE path="notes/finding.txt">>>>\n'
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

    state = pathlib.Path(root) / "state"
    state.mkdir(parents=True, exist_ok=True)
    config = {
        "provider": "mock",
        "model": "selftest-model",
        "state_dir": str(state),
        "workspace": "",
        "budget": {"max_iterations": 2, "max_tokens": 1000000, "deadline_sec": 60},
        "orchestration": {"parallel_workers": 1, "reviewers": 1, "review_mode": "all"},
        "team": {"language": "zh"},
    }
    engine = LoopEngine(config, provider=_Writer(model="selftest-model"))
    result = engine.run(
        goal="写一份结论",
        criteria=[
            {"id": "c1", "desc": "含正文", "check": {"type": "contains", "arg": "正文"}},
            {"id": "c2", "desc": "文件写出来了", "check": {"type": "file_exists", "arg": "notes/finding.txt"}},
        ],
    )
    workspace = pathlib.Path(str(result.workspace or ""))
    return {
        "closed": bool(result.closed),
        "rows": engine.ledger.entries(),
        "workspace": str(workspace),
        "ledger_path": str(state / "ledgers" / "runs.jsonl"),
    }


def _seal_state_of(workspace: Any, rows: Any) -> Dict[str, Any]:
    """The state of the closure, read the way the command line reads it."""
    from mas.ledger import Ledger
    from mas.seal import seal_report

    ledger = Ledger(pathlib.Path(str(workspace)))
    report = seal_report(ledger.entries())
    results = report.get("results") or []
    return results[0] if results else {}


def _observe(root: Any) -> Dict[str, Any]:
    """Run the whole story and record what every face said about it."""
    from mas.drafts import approve_draft, stage_draft, undo_draft
    from mas.report import build_report, render_markdown

    seen: Dict[str, Any] = {}
    facts = _run_a_closure(root)
    rows = facts.get("rows") or []
    workspace = pathlib.Path(str(facts.get("workspace") or ""))
    seen["closed"] = bool(facts.get("closed"))
    seen["rows"] = rows
    seen["workspace"] = str(workspace)
    if not workspace.exists():
        return seen

    target = workspace / "notes" / "finding.txt"
    seen["target_exists"] = target.exists()
    if not target.exists():
        return seen

    # The seal before anything is touched.
    from mas.ledger import Ledger
    from mas.seal import seal_report

    ledger = Ledger(pathlib.Path(str(facts.get("ledger_path") or "")))
    seen["baseline_state"] = str(
        (seal_report(ledger.entries()).get("results") or [{}])[0].get("state") or ""
    )

    # 1. An approved change. The bytes are written with ``write_bytes``
    # on purpose: a file written with ``write_text`` can hold
    # ``\r\n`` where the text holds ``\n``, and a comparison across two
    # bases is not a comparison at all. That trap cost v0.7.77 a CI
    # failure that only showed up on Windows, and it must not come
    # back here.
    original = target.read_bytes()
    draft = stage_draft(
        workspace,
        "notes/finding.txt",
        original + b"REVIEWED\n",
        reason="selftest",
    )
    approve_draft(workspace, str(draft.get("id") or ""), note="a person reviewed it")
    seen["approved_state"] = str(
        (seal_report(ledger.entries()).get("results") or [{}])[0].get("state") or ""
    )
    # The standing approvals, read straight off the chain. This is
    # the evidence the independent reading below reasons about, and
    # it is read *now* - while the chain is intact. A later reading
    # would find nothing, because the chain is broken on purpose at
    # the end of this story.
    seen["standing_before_tamper"] = _standing_approvals(workspace)
    approved_report = build_report(rows)
    seen["approved_markdown"] = render_markdown(approved_report)
    seen["approved_json"] = approved_report

    # 2. An unapproved change. This is the one that must stay a
    # drift - the lock is not weakened by any of this.
    target.write_bytes(target.read_bytes() + b"SNEAKY\n")
    seen["unapproved_state"] = str(
        (seal_report(ledger.entries()).get("results") or [{}])[0].get("state") or ""
    )
    seen["unapproved_markdown"] = render_markdown(build_report(rows))

    # Put the approved bytes back, so the next case starts from a
    # clean place.
    target.write_bytes(original + b"REVIEWED\n")

    # 3. An approval that was taken back. The bytes it wrote are not
    # standing any more, so the same bytes on the disk are a drift.
    undo_draft(workspace, str(draft.get("id") or ""), note="took it back")
    seen["undone_state"] = str(
        (seal_report(ledger.entries()).get("results") or [{}])[0].get("state") or ""
    )
    # Write exactly the approved bytes again - by hand, with no
    # approval standing.
    target.write_bytes(original + b"REVIEWED\n")
    seen["rewritten_state"] = str(
        (seal_report(ledger.entries()).get("results") or [{}])[0].get("state") or ""
    )

    # 4. An approval of another folder. The same bytes, approved in a
    # different workspace, must not excuse a change here.
    other = pathlib.Path(str(root)) / "other-workspace"
    (other / "notes").mkdir(parents=True, exist_ok=True)
    other_target = other / "notes" / "finding.txt"
    other_target.write_bytes(original)
    other_draft = stage_draft(
        other,
        "notes/finding.txt",
        original + b"REVIEWED\n",
        reason="selftest",
    )
    approve_draft(other, str(other_draft.get("id") or ""), note="approved elsewhere")
    seen["other_state"] = str(
        (seal_report(ledger.entries()).get("results") or [{}])[0].get("state") or ""
    )

    # 5. An approval whose chain is broken. The chain is the
    # authority, so a chain that does not verify cannot excuse
    # anything.
    chain_path = workspace / ".mas-drafts" / "audit.jsonl"
    seen["chain_exists"] = chain_path.exists()
    if chain_path.exists():
        lines = chain_path.read_text(encoding="utf-8").splitlines()
        seen["chain_lines"] = len(lines)
        # Rewrite the approve entry so its own hash no longer matches.
        tampered: List[str] = []
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                tampered.append(line)
                continue
            if str(entry.get("event") or "") == "draft_approve":
                entry["approved_sha256"] = "0" * 64
            tampered.append(json.dumps(entry, ensure_ascii=False))
        chain_path.write_text("\n".join(tampered) + "\n", encoding="utf-8")
        seen["broken_chain_state"] = str(
            (seal_report(ledger.entries()).get("results") or [{}])[0].get("state") or ""
        )
        # And the standing approvals again - read the same way. The
        # chain is broken now, so there are none: an approval nobody
        # can verify is not evidence.
        seen["standing_after_tamper"] = _standing_approvals(workspace)
    return seen


def _standing_approvals(workspace: Any) -> int:
    """How many approvals stand in one workspace, read off its chain.

    Read straight from the chain - the seal is not consulted at all -
    so this is a second, independent account of the evidence.
    """
    from mas.audit import chain_for
    from mas.seal import approved_bytes

    try:
        chain = chain_for(str(workspace or ""))
    except OSError:
        return 0
    if chain is None:
        return 0
    if not chain.verify().get("ok"):
        # A broken chain is not a source of truth.
        return 0
    return len(approved_bytes([], workspace=str(workspace or "")))


def _verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """What the gate concludes from what it saw."""
    verdicts: Dict[str, bool] = {}

    verdicts["an approved change is reported as a take-over, not as a drift"] = (
        str(facts.get("approved_state") or "") == "taken-over"
    )
    verdicts["an unapproved change is still reported as a drift"] = (
        str(facts.get("unapproved_state") or "") == "drifted"
    )
    verdicts["an approval that was taken back does not excuse the change"] = (
        str(facts.get("rewritten_state") or "") == "drifted"
    )
    verdicts["an approval of another folder does not excuse the change"] = (
        str(facts.get("other_state") or "") == "drifted"
    )
    verdicts["an approval whose chain is broken does not excuse the change"] = (
        str(facts.get("broken_chain_state") or "") == "drifted"
    )

    # The report and the command line have to say the same thing.
    # The report is the screen a human reads; the command is what a
    # script asks. If one of them calls an approved change a drift,
    # the reader cannot tell which one to believe.
    markdown = str(facts.get("approved_markdown") or "")
    payload = facts.get("approved_json") or {}
    totals = payload.get("totals") if isinstance(payload, dict) else None
    seal = payload.get("seal") if isinstance(payload, dict) else None
    verdicts["the report and the command line say the same thing"] = (
        str(facts.get("approved_state") or "") == "taken-over"
        and isinstance(totals, dict)
        and int(totals.get("seal_taken_over") or 0) >= 1
        and int(totals.get("seal_drifted") or 0) == 0
        and isinstance(seal, dict)
        and int(seal.get("taken_over") or 0) >= 1
        and seal.get("ok") is True
        and "\u7ed3\u8bba\u5df2\u6f02\u79fb" not in markdown
    )
    return verdicts


def _verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """A second, independent reading of the same facts.

    This reading does not consult the report's own seal field and
    does not look at the rendered text. It asks the chain and the
    disk directly: *is there a standing approval for exactly these
    bytes, in this folder, that has not been taken back?*
    """
    verdicts: Dict[str, bool] = {}
    # The evidence, read off the chain while the chain was still
    # intact. The seal is not consulted for this.
    verdicts["an approved change is reported as a take-over, not as a drift"] = (
        int(facts.get("standing_before_tamper") or 0) >= 1
    )
    verdicts["an unapproved change is still reported as a drift"] = (
        str(facts.get("unapproved_state") or "") == "drifted"
    )
    verdicts["an approval that was taken back does not excuse the change"] = (
        str(facts.get("rewritten_state") or "") == "drifted"
    )
    verdicts["an approval of another folder does not excuse the change"] = (
        str(facts.get("other_state") or "") == "drifted"
    )
    verdicts["an approval whose chain is broken does not excuse the change"] = (
        str(facts.get("broken_chain_state") or "") == "drifted"
        and int(facts.get("standing_after_tamper") or 0) == 0
    )

    # Independently: the report's own numbers have to agree with the
    # chain's word.
    payload = facts.get("approved_json") or {}
    totals = payload.get("totals") if isinstance(payload, dict) else None
    verdicts["the report and the command line say the same thing"] = (
        isinstance(totals, dict)
        and int(totals.get("seal_taken_over") or 0) >= 1
        and int(totals.get("seal_drifted") or 0) == 0
    )
    return verdicts


def check_an_approved_change_is_not_a_drift(iterations: int = 5) -> Dict[str, Any]:
    """An approved change is a take-over - and the gate can fail."""
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        root = pathlib.Path(tempfile.mkdtemp(prefix="mas-seal-takeover-"))
        rounds += 1
        # A human decision is copied into the main ledger, and the
        # ledger lives in the state directory. The gate points the
        # state at the temporary folder for the duration of the round,
        # so the decisions of a test round do not end up in the user's
        # own audit trail. Without this the gate would dirty the very
        # machine it is running on - and the isolation gate would
        # (rightly) report it.
        previous_state = os.environ.get("MAS_STATE_DIR")
        os.environ["MAS_STATE_DIR"] = str(pathlib.Path(root) / "state")
        try:
            try:
                facts = _observe(root)
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the take-over check broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                facts = {}

            verdicts = _verdicts(facts)
            independent = _verdicts_independent(facts)
            for label in SEAL_TAKEOVER_OBSERVATIONS:
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

            # The gate proves it can fail: the take-over is broken on
            # purpose - every approved change is reported as a drift
            # again - and the gate has to notice that the two cases
            # can no longer be told apart.
            import mas.seal as seal_module

            real_verify = seal_module.verify_seal

            def _verify_without_takeover(seal, artifact=None, approvals=None,
                                         artifact_bytes=None, _real=real_verify):
                outcome = _real(seal, artifact, approvals, artifact_bytes)
                if outcome.get("taken_over") or outcome.get("taken_over_files"):
                    outcome["state"] = "drifted"
                    outcome["ok"] = False
                    outcome["problems"] = [
                        *list(outcome.get("problems") or []),
                        "the deliverable changed after the run closed",
                    ]
                return outcome

            try:
                seal_module.verify_seal = _verify_without_takeover
                broken: Dict[str, bool] = {}
                try:
                    broken = _verdicts(_observe(root))
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                seal_module.verify_seal = real_verify
            if broken.get("an approved change is reported as a take-over, not as a drift"):
                failures.append(
                    "round %d: the gate did not notice that the take-over "
                    "is broken - it cannot fail, so it does not watch"
                    % (round_index + 1)
                )
        finally:
            if previous_state is None:
                os.environ.pop("MAS_STATE_DIR", None)
            else:
                os.environ["MAS_STATE_DIR"] = previous_state
            shutil.rmtree(root, ignore_errors=True)

    return {
        "name": "seal_takeover",
        "title": "\u4eba\u5de5\u6279\u51c6\u7684\u6539\u52a8\u4e0d\u662f\u6f02\u79fb",
        "skipped": False,
        "invariant": (
            "\u88ab\u4eba\u5ba1\u8fc7\u5e76\u6279\u51c6\u7684\u6539\u52a8\u62a5\u201c\u4eba\u5de5\u63a5\u624b\u201d\uff0c"
            "\u800c\u4e0d\u662f\u201c\u7ed3\u8bba\u5df2\u6f02\u79fb\u201d\uff1b"
            "\u6ca1\u6709\u6279\u51c6\u7684\u6539\u52a8\u4ecd\u7136\u662f\u6f02\u79fb"
        ),
        "detail": "\u6279\u51c6\u8fc7\u7684\u6539\u52a8\u662f\u5426\u88ab\u8bef\u62a5\u4e3a\u6f02\u79fb\uff1amas seal",
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_an_approved_change_is_not_a_drift.CHECK_NAME = "seal_takeover"  # type: ignore[attr-defined]

