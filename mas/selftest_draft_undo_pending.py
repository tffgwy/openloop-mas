# -*- coding: utf-8 -*-
"""The forty-eighth gate: a taken-back draft still waits for a human.

The gap
-------
A draft has four states. Three of them are obvious: ``pending``
(nobody decided), ``approved`` (the change is on the disk),
``rejected`` (the change is dead). The fourth one - ``undone`` - is
the one that broke.

``undo_draft`` writes the **baseline** back to the disk and puts the
draft into ``undone``. And ``approve_draft`` accepts a draft in that
state again: the whole point of the undo is that the change can be
approved a second time once the reason for taking it back is
understood.

So a draft in ``undone`` is a **live change waiting for a person**.
It is not handled. It is not dead. It is a proposal that is standing
in front of a human who has not said yes.

Three surfaces read the queue, and all three read only ``pending``:

* the **machine-check overlay** (``mas.drafts.draft_overlay``) handed
  the checks an empty overlay for an undone draft - so a check read
  the disk, and the disk held the *baseline* the undo had just put
  back. The check judged the old bytes while the loop was proposing
  the new ones, so the loop could never close on its own proposal -
  and nothing said why;
* the **draft reading** (``mas.drafts_reading.draft_reading``) put
  undone drafts into ``other`` - so the queue said "nothing is
  waiting" while a change was waiting;
* the **closure verdict** counts ``drafts_pending``, so a run that
  closed while its change still waited was reported as **closed**.

The fix
-------
The reading of "does this draft still wait for a human" lives in one
function - ``mas.drafts_reading.is_waiting_for_human()`` - and every
surface calls it: the overlay, the draft reading, the report, the
closure verdict, the status page and the MCP surface.

This gate watches:

* the machine-check overlay carries the bytes of an undone draft;
* the draft reading counts an undone draft as waiting;
* the closure verdict does not call a loop closed while its change
  still waits;
* the report says the undone draft still waits, and offers the
  approve command for it;
* the status page counts it as waiting;
* the MCP surface says it is waiting;
* and the gate proves it can fail: the **wiring** is broken on
  purpose - the overlay stops reading the undone drafts - and the
  gate has to notice that the machine now reads the wrong bytes.

The break is on the wiring, not on the shared function. A shared
function that is broken makes every surface wrong *in the same way*,
so the surfaces agree and the gate sees nothing. The break has to
make one surface read something different.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import shutil
import tempfile
from typing import Any, Dict, List, Optional

#: The observations the gate makes.
DRAFT_UNDO_PENDING_OBSERVATIONS = (
    "the machine-check overlay carries the bytes of an undone draft",
    "the draft reading counts an undone draft as waiting",
    "the closure verdict does not call a loop closed while its change waits",
    "the report says the undone draft still waits",
    "the status page counts the undone draft as waiting",
    "the MCP surface says the undone draft is waiting",
)


class _Env:
    """The environment variables the gate has to borrow.

    The machine has to look at *this* case and at nothing else: a
    gate that reads the reader's own state directory is a gate that
    fails on a machine that has real work in it, and passes on a
    machine that has none.
    """

    def __init__(self, state_dir: str, config_path: str) -> None:
        self._values = {
            "MAS_STATE_DIR": state_dir,
            "MAS_CONFIG_PATH": config_path,
        }
        self._saved: Dict[str, Optional[str]] = {}

    def __enter__(self) -> "_Env":
        for name, value in self._values.items():
            self._saved[name] = os.environ.get(name)
            os.environ[name] = value
        return self

    def __exit__(self, *exc: Any) -> None:
        for name, saved in self._saved.items():
            if saved is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = saved


def _make_case(root: str) -> Dict[str, Any]:
    """A workspace with one draft whose approval was taken back.

    The draft is approved and then taken back, so its state is
    ``undone`` - the change is live again, the disk holds the
    baseline, and the change is waiting for a person.

    The layout is the real one: a run writes into
    ``<state_dir>/artifacts/<run_id>/``, so the workspace lives one
    folder below the artifacts root. The report page and the status
    page both scan that root, so a workspace that does not sit there
    would be invisible to two of the surfaces - and the gate would be
    measuring the wrong thing.
    """
    from mas.drafts import approve_draft, stage_draft, undo_draft

    state = pathlib.Path(root) / "state"
    artifacts = state / "artifacts"
    workspace = artifacts / "R-1"
    workspace.mkdir(parents=True, exist_ok=True)
    config_path = pathlib.Path(root) / "config.json"
    target = workspace / "notes.txt"
    target.write_text("alpha\nbeta\n", encoding="utf-8")

    # The ledger copies of the decisions are written into the state
    # directory, so the environment has to point there *before* the
    # decisions are made - otherwise the copies land in the reader's
    # own ledger and this gate dirties the machine it runs on.
    with _Env(str(state), str(config_path)):
        staged = stage_draft(
            workspace,
            "notes.txt",
            "alpha\nbeta-agent\n".encode("utf-8"),
            reason="the agent proposes this",
        )
        draft_id = str(staged.get("id") or "")
        approve_draft(workspace, draft_id, note="looks fine")
        undo_draft(workspace, draft_id, note="approved the wrong thing")

    return {
        "state_dir": str(state),
        "artifacts": str(artifacts),
        "workspace": str(workspace),
        "config_path": str(config_path),
        "draft_id": draft_id,
        "proposed": "alpha\nbeta-agent\n",
        "baseline": "alpha\nbeta\n",
    }


def _run_cli(args: List[str], state_dir: str, config_path: str) -> str:
    """Run one command of the command line and keep what it printed."""
    from mas.cli import main

    buffer = io.StringIO()
    with _Env(state_dir, config_path):
        try:
            with contextlib.redirect_stdout(buffer):
                main(args)
        except SystemExit:
            pass
    return buffer.getvalue()


def _json_cli(args: List[str], state_dir: str, config_path: str) -> Dict[str, Any]:
    """Run a command that prints JSON and read it back."""
    text = _run_cli(args, state_dir, config_path)
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001 - a broken page is the subject
        return {}


def _status_payload(case: Dict[str, Any]) -> Dict[str, Any]:
    """What ``mas status --json`` says."""
    return _json_cli(
        ["status", "--json"],
        str(case.get("state_dir") or ""),
        str(case.get("config_path") or ""),
    )


def _report_payload(case: Dict[str, Any]) -> Dict[str, Any]:
    """What ``mas report --json`` says."""
    return _json_cli(
        ["report", "--json"],
        str(case.get("state_dir") or ""),
        str(case.get("config_path") or ""),
    )


def _report_markdown(case: Dict[str, Any]) -> str:
    """The report page, as a person reads it."""
    return _run_cli(
        ["report"],
        str(case.get("state_dir") or ""),
        str(case.get("config_path") or ""),
    )


def _mcp_drafts(artifacts: str) -> Dict[str, Any]:
    """What the MCP surface says about the drafts."""
    from mas.mcp_server import handle_tool

    return handle_tool("mas_drafts", {"workspace": artifacts, "action": "list"})


def _surfaces() -> Dict[str, Any]:
    """Everything the gate looks at, gathered in one place."""
    root = tempfile.mkdtemp(prefix="mas-selftest-draft-undo-pending-")
    try:
        case = _make_case(root)
        artifacts = str(case.get("artifacts") or "")
        workspace = str(case.get("workspace") or "")
        with _Env(str(case["state_dir"]), str(case["config_path"])):
            mcp = _mcp_drafts(artifacts)
            overlay = _overlay_of(workspace)
        status = _status_payload(case)
        report = _report_payload(case)
        markdown = _report_markdown(case)
        reading = _reading_of(case)
        verdict = _verdict_of(case)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    return {
        "draft_id": case["draft_id"],
        "workspace": case["workspace"],
        "proposed": case["proposed"],
        "baseline": case["baseline"],
        "overlay": overlay,
        "reading": reading,
        "verdict": verdict,
        "mcp": mcp,
        "status": status,
        "report": report,
        "markdown": markdown,
    }


def _overlay_of(workspace: str) -> Dict[str, bytes]:
    """The overlay the machine checks would read."""
    from mas.drafts import draft_overlay

    return draft_overlay(workspace)


def _reading_of(case: Dict[str, Any]) -> Dict[str, Any]:
    """What the draft reading says about the queue."""
    from mas.drafts_reading import draft_reading

    workspace = pathlib.Path(str(case.get("workspace") or ""))
    drafts = []
    folder = workspace / ".mas-drafts"
    if folder.exists():
        for path in sorted(folder.glob("D-*.json")):
            try:
                drafts.append(json.loads(path.read_text(encoding="utf-8")))
            except ValueError:
                continue
    return draft_reading([], drafts)


def _verdict_of(case: Dict[str, Any]) -> Dict[str, Any]:
    """What the closure verdict says."""
    from mas.report import closure_verdict

    report = _report_payload(case)
    try:
        return closure_verdict(report)
    except Exception:  # noqa: BLE001 - a broken verdict is the subject
        return {}


def _mcp_row(payload: Dict[str, Any], draft_id: str) -> Dict[str, Any]:
    """The one row about this draft, on the MCP surface."""
    for row in payload.get("drafts") or []:
        if isinstance(row, dict) and str(row.get("id") or "") == draft_id:
            return row
    return {}


def _report_row_of(report: Dict[str, Any], draft_id: str) -> Dict[str, Any]:
    """The one row about this draft, in the report."""
    for entry in (report.get("drafts") or {}).get("drafts") or []:
        if isinstance(entry, dict) and str(entry.get("draft_id") or "") == draft_id:
            return entry
    return {}


def _verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """What the gate believes, from the gathered facts."""
    draft_id = str(facts.get("draft_id") or "")
    overlay = facts.get("overlay") or {}
    reading = facts.get("reading") or {}
    verdict = facts.get("verdict") or {}
    mcp_row = _mcp_row(facts.get("mcp") or {}, draft_id)
    status = facts.get("status") or {}
    report = facts.get("report") or {}
    markdown = str(facts.get("markdown") or "")

    drafts_section = status.get("drafts") or {}
    report_row = _report_row_of(report, draft_id)
    counts = verdict.get("counts") or {}

    # The overlay must carry the bytes the loop proposes - not the
    # bytes the undo put back.
    proposed = str(facts.get("proposed") or "").encode("utf-8")
    overlay_bytes = b"".join(
        bytes(data) for data in overlay.values() if data
    )

    return {
        "the machine-check overlay carries the bytes of an undone draft": (
            bool(overlay_bytes)
            and proposed in overlay_bytes
            # The baseline must NOT be what the machine reads - that
            # is the wrong-bytes failure this gate exists for.
            and str(facts.get("baseline") or "").encode("utf-8") not in overlay_bytes
        ),
        "the draft reading counts an undone draft as waiting": (
            draft_id in (reading.get("pending") or [])
            and int(reading.get("pending_count") or 0) >= 1
            and draft_id in (reading.get("undone_waiting") or [])
        ),
        "the closure verdict does not call a loop closed while its change waits": (
            int(counts.get("drafts_pending") or 0) >= 1
            and not bool(verdict.get("ok"))
        ),
        "the report says the undone draft still waits": (
            str(report_row.get("state") or "") == "undone"
            and draft_id in markdown
            and "approve" in markdown
        ),
        "the status page counts the undone draft as waiting": (
            draft_id in (drafts_section.get("pending") or [])
        ),
        "the MCP surface says the undone draft is waiting": (
            bool(mcp_row.get("waiting_for_human"))
            and draft_id in ((facts.get("mcp") or {}).get("waiting_draft_ids") or [])
            and int((facts.get("mcp") or {}).get("undone_waiting_count") or 0) >= 1
        ),
    }


def _verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """The same reading, made from the raw facts, not from the pages.

    This pass does not look at what the pages printed. It reads the
    draft's own state and the disk, works out what the truth is, and
    then says what every surface ought to report. If the two
    readings disagree, the gate says so - and the gate cannot be
    satisfied by a surface that merely looks right.
    """
    draft_id = str(facts.get("draft_id") or "")
    reading = facts.get("reading") or {}
    proposed = str(facts.get("proposed") or "")

    # The raw truth: the draft waits (its state is ``undone``, and an
    # undone draft can be approved again), and the disk holds the
    # baseline - so the machine must be handed the proposal.
    the_draft_waits = draft_id in (reading.get("pending") or [])
    the_overlay_has_the_proposal = proposed.encode("utf-8") in b"".join(
        bytes(data) for data in (facts.get("overlay") or {}).values() if data
    )
    return {
        "the machine-check overlay carries the bytes of an undone draft": (
            the_overlay_has_the_proposal
        ),
        "the draft reading counts an undone draft as waiting": the_draft_waits,
        "the closure verdict does not call a loop closed while its change waits": (
            the_draft_waits
        ),
        "the report says the undone draft still waits": the_draft_waits,
        "the status page counts the undone draft as waiting": the_draft_waits,
        "the MCP surface says the undone draft is waiting": the_draft_waits,
    }


def check_an_undone_draft_is_still_waiting_everywhere(
    iterations: int = 3,
) -> Dict[str, Any]:
    """The forty-eighth gate."""
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        rounds += 1
        try:
            try:
                facts = _surfaces()
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the check broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                facts = {}

            verdicts = _verdicts(facts)
            independent = _verdicts_independent(facts)
            for label in DRAFT_UNDO_PENDING_OBSERVATIONS:
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

            # The gate proves it can fail.
            #
            # The break is on the **wiring**: the overlay stops
            # reading the undone drafts. It is not the same as
            # breaking the shared function - a broken shared function
            # makes every surface wrong in the same way, so the
            # surfaces agree and the gate sees nothing. The break has
            # to make one surface read something different.
            import mas.drafts as drafts_module

            real_list_drafts = drafts_module.list_drafts

            def _no_undone(workspace, state=None, _real=real_list_drafts):
                # The undone drafts are not read at all - exactly
                # what the overlay did before this release.
                if str(state or "") == "undone":
                    return []
                return _real(workspace, state)

            try:
                drafts_module.list_drafts = _no_undone
                broken: Dict[str, bool] = {}
                try:
                    broken = _verdicts(_surfaces())
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                drafts_module.list_drafts = real_list_drafts

            if broken.get(
                "the machine-check overlay carries the bytes of an undone draft"
            ):
                failures.append(
                    "round %d: the gate did not notice that the overlay stops "
                    "reading the undone drafts - it cannot fail, so it does "
                    "not watch" % (round_index + 1)
                )
        finally:
            pass

    return {
        "name": "an_undone_draft_is_still_waiting_everywhere",
        "title": "\u64a4\u9500\u8fc7\u7684\u8349\u7a3f\u4ecd\u7136\u7b49\u4eba\u6279",
        "skipped": False,
        "invariant": (
            "\u4e00\u5f20\u88ab\u64a4\u9500\u8fc7\u7684\u8349\u7a3f\u4ecd\u7136\u662f\u4e00\u4e2a"
            "\u7b49\u4eba\u7684\u6539\u52a8\uff1a\u64a4\u9500\u628a\u57fa\u7ebf\u5199\u56de\u4e86"
            "\u78c1\u76d8\uff0c\u5b83\u4ecd\u7136\u53ef\u4ee5\u518d\u6279\u51c6\uff0c\u6240\u4ee5"
            "\u5b83\u5fc5\u987b\u5728\u8986\u76d6\u5c42\u3001\u961f\u5217\u3001\u62a5\u544a\u3001"
            "\u72b6\u6001\u9875\u548c MCP \u9762\u4e0a\u90fd\u7b49\u7740\u4eba"
        ),
        "detail": (
            "\u673a\u68c0\u8986\u76d6\u5c42\u662f\u5426\u62ff\u5230\u4e86\u64a4\u9500\u540e"
            "\u8349\u7a3f\u7684\u63d0\u8bae\u5b57\u8282\uff0c\u5f85\u6279\u53e3\u5f84\u662f\u5426"
            "\u5728\u6240\u6709\u9762\u4e0a\u90fd\u4e00\u81f4"
        ),
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_an_undone_draft_is_still_waiting_everywhere.CHECK_NAME = (  # type: ignore[attr-defined]
    "an_undone_draft_is_still_waiting_everywhere"
)
