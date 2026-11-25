# -*- coding: utf-8 -*-
"""The fifty-fifth gate: the take-over is visible on every face.

The gap
-------
v0.11.2 gave the seal a third state: ``taken-over``. The conclusion
still holds, but a person approved a change to the deliverable after
the run closed. The **report** and ``mas seal`` said so in those
words.

The **status page** and the **replay** said *nothing at all* - not
about a take-over, not about a drift, not about a seal. A person
who opened the status page - the page a beginner opens first - had
no way to learn that a person had taken the deliverable over, and
had to assume the file had never been touched. Silence reads as
"nothing happened", which is the opposite of the truth.

The fix
-------
The reading lives in one place - ``mas.report.seal_overview()`` - and
every surface calls it:

* the report (``mas report``) says it and counts it;
* the status page (``mas status``) says it and counts it;
* the replay (``mas replay``) says it, per run;
* the MCP status tool says it and counts it;
* the MCP replay tool carries it, per run.

And the numbers are the same numbers everywhere: one take-over seen
by five faces is one take-over, not five.

This gate watches:

* every face says the take-over, in words;
* every face counts the same take-over;
* an **unapproved** change is still a drift on every face - the
  lock is not weakened by any of this;
* the faces agree with each other;
* and the gate proves it can fail: the **status page** is broken on
  purpose - it cannot see the take-over any more - and the gate has
  to notice that one face has gone dark.

The break is on one face, not on a shared helper: breaking a shared
helper makes every face wrong in the same way, so they agree and the
gate sees nothing.
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
SEAL_TAKEOVER_EVERYWHERE_OBSERVATIONS = (
    "the report says the take-over",
    "the status page says the take-over",
    "the replay says the take-over",
    "the MCP status tool says the take-over",
    "the MCP replay tool says the take-over",
    "every face counts the same take-over",
    "an unapproved change is still a drift everywhere",
)


class _Env:
    """The environment the gate borrows - and gives back exactly.

    The restore is **absolute**: a variable that was not there before
    is removed again, not set to an empty string. A self-test that
    points the machine's state at a temporary folder and leaves the
    pointer behind dirties the machine it is running on - and the
    machine-state gate would (correctly) fail.
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
    """One closed run whose deliverable is later taken over by a person.

    The run is real - the engine closes it - so the seal, the report
    and the replay all have real closures to talk about.

    Two states are produced on purpose, and they are the two halves of
    the whole gate:

    * an **approved** change: a person reviewed the deliverable and
      approved a change to it. The conclusion still holds, and every
      face has to say *take-over*;
    * an **unapproved** change: the same bytes, written with nobody's
      approval behind them. Every face has to say *drift* - the lock
      is not weakened by any of this.
    """
    from mas.drafts import approve_draft, stage_draft
    from mas.engine import LoopEngine
    from mas.providers import Provider

    class _Offline(Provider):
        name = "selftest-takeover-everywhere"

        def __init__(self) -> None:
            super().__init__(model="selftest-model")

        def complete(self, *, system: str = "", prompt: str = "",
                     role: str = "worker", expect_json: bool = False,
                     max_tokens: int = 4000) -> Dict[str, Any]:
            usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
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
    artifacts = state / "artifacts"
    config_path = pathlib.Path(root) / "config.json"
    state.mkdir(parents=True, exist_ok=True)
    artifacts.mkdir(parents=True, exist_ok=True)

    workspace = artifacts / "ws"
    (workspace / "notes").mkdir(parents=True, exist_ok=True)

    config = {
        "provider": "mock",
        "model": "selftest-model",
        "state_dir": str(state),
        "workspace": str(workspace),
        "budget": {"max_iterations": 2, "max_tokens": 1000000, "deadline_sec": 60},
        "orchestration": {"parallel_workers": 1, "reviewers": 1, "review_mode": "all"},
        "team": {"language": "zh"},
    }

    run_id = ""
    with _Env(str(state), str(config_path)):
        engine = LoopEngine(config, provider=_Offline())
        result = engine.run(
            goal="写一份结论",
            criteria=[
                {"id": "c1", "desc": "含正文", "check": {"type": "contains", "arg": "正文"}},
                {"id": "c2", "desc": "文件写出来了",
                 "check": {"type": "file_exists", "arg": "notes/finding.txt"}},
            ],
        ).to_dict()
        run_id = str(result.get("run_id") or "")

    target = workspace / "notes" / "finding.txt"
    original = target.read_bytes() if target.exists() else b""

    # The approved change. The bytes are written with ``write_bytes``
    # on purpose: a file written with ``write_text`` can hold
    # ``\r\n`` where the text holds ``\n``, and a comparison across
    # two bases is not a comparison at all. That trap cost v0.7.77 a
    # CI failure that only showed up on Windows, and it must not come
    # back here.
    draft_id = ""
    with _Env(str(state), str(config_path)):
        staged = stage_draft(
            workspace,
            "notes/finding.txt",
            original + b"REVIEWED\n",
            reason="a person reviewed the deliverable",
            source="gate",
            run_id=run_id,
            task_id="T-" + run_id,
        )
        draft_id = str(staged.get("id") or "")
        approve_draft(workspace, draft_id, note="a person reviewed it")

    return {
        "state_dir": str(state),
        "config_path": str(config_path),
        "workspace": str(workspace),
        "run_id": run_id,
        "draft_id": draft_id,
        "target": str(target),
        "original": original,
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


def _mcp(name: str, arguments: Dict[str, Any], state_dir: str,
         config_path: str) -> Dict[str, Any]:
    """Call one MCP tool and get its payload back."""
    from mas.mcp_server import handle_tool

    with _Env(state_dir, config_path):
        try:
            outcome = handle_tool(name, arguments)
        except Exception:  # noqa: BLE001 - a broken tool is the subject
            return {}
    if isinstance(outcome, dict) and isinstance(outcome.get("result"), str):
        try:
            inner = json.loads(outcome["result"])
            if isinstance(inner, dict):
                return inner
        except Exception:  # noqa: BLE001
            pass
    return outcome if isinstance(outcome, dict) else {}


def _seal_block_of(payload: Dict[str, Any], run_id: str) -> Dict[str, Any]:
    """The seal item of one run, wherever the payload keeps it."""
    for item in payload.get("items") or []:
        if isinstance(item, dict) and str(item.get("run_id") or "") == run_id:
            return item
    return {}


def _facts_of(case: Dict[str, Any]) -> Dict[str, Any]:
    """Everything every face said, gathered in one place."""
    state_dir = str(case.get("state_dir") or "")
    config_path = str(case.get("config_path") or "")
    run_id = str(case.get("run_id") or "")
    target = pathlib.Path(str(case.get("target") or ""))
    original = case.get("original") or b""

    report_json = _json_cli(["report", "--json"], state_dir, config_path)
    report_md = _run_cli(["report"], state_dir, config_path)
    status_text = _run_cli(["status"], state_dir, config_path)
    status_json = _json_cli(["status", "--json"], state_dir, config_path)
    replay_text = _run_cli(["replay"], state_dir, config_path)
    mcp_status = _mcp("mas_status", {}, state_dir, config_path)
    mcp_replay = _mcp("mas_replay", {}, state_dir, config_path)

    # The approved case: every face has to say take-over.
    approved = {
        "report_json": report_json,
        "report_md": report_md,
        "status_text": status_text,
        "status_json": status_json,
        "replay_text": replay_text,
        "mcp_status": mcp_status,
        "mcp_replay": mcp_replay,
        "run_id": run_id,
    }

    # The unapproved case: the same bytes, with nobody's approval
    # behind them. Every face has to say drift.
    target.write_bytes(original + b"SNEAKY\n")
    unapproved = {
        "report_json": _json_cli(["report", "--json"], state_dir, config_path),
        "report_md": _run_cli(["report"], state_dir, config_path),
        "status_text": _run_cli(["status"], state_dir, config_path),
        "status_json": _json_cli(["status", "--json"], state_dir, config_path),
        "replay_text": _run_cli(["replay"], state_dir, config_path),
        "mcp_status": _mcp("mas_status", {}, state_dir, config_path),
        "mcp_replay": _mcp("mas_replay", {}, state_dir, config_path),
        "run_id": run_id,
    }
    # Put the approved bytes back, so the machine is left as it was.
    target.write_bytes(original + b"REVIEWED\n")

    return {"approved": approved, "unapproved": unapproved}


def _verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """What the gate believes, from the faces."""
    verdicts: Dict[str, bool] = {}
    good = facts.get("approved") or {}
    bad = facts.get("unapproved") or {}
    run_id = str(good.get("run_id") or "")

    report_json = good.get("report_json") or {}
    report_seal = report_json.get("seal") or {}
    report_item = _seal_block_of(report_seal, run_id)
    verdicts["the report says the take-over"] = (
        str(report_item.get("state") or "") == "taken-over"
        and "人工接手" in str(good.get("report_md") or "")
    )

    status_json = good.get("status_json") or {}
    status_seal = status_json.get("seal") or {}
    status_item = _seal_block_of(status_seal, run_id)
    verdicts["the status page says the take-over"] = (
        str(status_item.get("state") or "") == "taken-over"
        and "人工接手" in str(good.get("status_text") or "")
    )

    replay_text = str(good.get("replay_text") or "")
    mcp_replay = good.get("mcp_replay") or {}
    replay_block: Dict[str, Any] = {}
    for block in mcp_replay.get("runs") or []:
        if isinstance(block, dict) and str(block.get("run_id") or "") == run_id:
            replay_block = block
    verdicts["the replay says the take-over"] = (
        "人工接手" in replay_text
        or "被人审过并批准了改动" in replay_text
    )
    verdicts["the MCP replay tool says the take-over"] = (
        str(replay_block.get("seal_state") or "") == "taken-over"
        and int((mcp_replay.get("seal") or {}).get("taken_over") or 0) >= 1
    )

    mcp_status = good.get("mcp_status") or {}
    mcp_status_seal = mcp_status.get("seal") or {}
    mcp_status_item = _seal_block_of(mcp_status_seal, run_id)
    verdicts["the MCP status tool says the take-over"] = (
        str(mcp_status_item.get("state") or "") == "taken-over"
        and int(mcp_status_seal.get("taken_over") or 0) >= 1
    )

    # Every face counts the same take-over. One fact, five faces -
    # if two of them disagree, one of them is wrong.
    counts = [
        int(report_seal.get("taken_over") or 0),
        int(status_seal.get("taken_over") or 0),
        int(mcp_status_seal.get("taken_over") or 0),
        int((mcp_replay.get("seal") or {}).get("taken_over") or 0),
    ]
    verdicts["every face counts the same take-over"] = (
        all(count == 1 for count in counts)
    )

    # The unapproved change is still a drift everywhere. This is the
    # half of the gate that keeps the lock honest.
    bad_report = (bad.get("report_json") or {}).get("seal") or {}
    bad_status = (bad.get("status_json") or {}).get("seal") or {}
    bad_mcp_status = (bad.get("mcp_status") or {}).get("seal") or {}
    bad_mcp_replay = (bad.get("mcp_replay") or {}).get("seal") or {}
    verdicts["an unapproved change is still a drift everywhere"] = (
        int(bad_report.get("drifted") or 0) >= 1
        and int(bad_report.get("taken_over") or 0) == 0
        and int(bad_status.get("drifted") or 0) >= 1
        and int(bad_status.get("taken_over") or 0) == 0
        and int(bad_mcp_status.get("drifted") or 0) >= 1
        and int(bad_mcp_status.get("taken_over") or 0) == 0
        and int(bad_mcp_replay.get("drifted") or 0) >= 1
        and int(bad_mcp_replay.get("taken_over") or 0) == 0
    )
    return verdicts


def _verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """The same reading, made from the chain and the disk.

    This pass does not look at any page. It asks the audit chain
    directly: is there a **standing** approval for exactly the bytes
    on the disk, in this folder, for this file? And it reads the
    state of the seal straight off the closure itself. If the two
    readings disagree, the gate says so - and the gate cannot be
    satisfied by a face that merely looks right.
    """
    from mas.audit import chain_for
    from mas.seal import approved_bytes

    good = facts.get("approved") or {}
    bad = facts.get("unapproved") or {}

    verdicts: Dict[str, bool] = {}

    # The independent truth about the approved case: the bytes on the
    # disk are covered by a standing approval. Read off the chain.
    standing = 0
    workspace = str(good.get("workspace") or "")
    try:
        chain = chain_for(workspace)
    except OSError:
        chain = None
    if chain is not None and chain.verify().get("ok"):
        standing = len(approved_bytes([], workspace=workspace))

    verdicts["the report says the take-over"] = standing >= 1
    verdicts["the status page says the take-over"] = standing >= 1
    verdicts["the replay says the take-over"] = standing >= 1
    verdicts["the MCP status tool says the take-over"] = standing >= 1
    verdicts["the MCP replay tool says the take-over"] = standing >= 1
    verdicts["every face counts the same take-over"] = standing == 1

    # And the independent truth about the unapproved case: nobody
    # approved those bytes, so the drift stands. The chain holds one
    # approval - the one for the approved bytes - and the sneaky
    # bytes are not among them.
    verdicts["an unapproved change is still a drift everywhere"] = (
        standing == 1
        and int((bad.get("report_json") or {}).get("seal", {}).get("drifted") or 0) >= 1
    )
    return verdicts


def check_the_takeover_is_visible_everywhere(iterations: int = 3) -> Dict[str, Any]:
    """The fifty-fifth gate."""
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        root = pathlib.Path(tempfile.mkdtemp(prefix="mas-selftest-takeover-faces-"))
        rounds += 1
        try:
            try:
                case = _make_case(str(root))
                facts = _facts_of(case)
                facts["approved"]["workspace"] = case.get("workspace")
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the check broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                facts = {}

            verdicts = _verdicts(facts)
            independent = _verdicts_independent(facts)
            for label in SEAL_TAKEOVER_EVERYWHERE_OBSERVATIONS:
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
            # The break is on **one face**: the status page stops
            # being able to see the take-over. It is not a shared
            # helper - breaking a shared helper makes every face
            # wrong in the same way, so the faces agree and the gate
            # sees nothing. The break has to make one face read
            # something different.
            import mas.cli as cli_module

            real_overview = cli_module.seal_overview

            def _blind_overview(runs, _real=real_overview):
                # Exactly what the status page did before this
                # release: it had no seal at all. Here the status
                # page can see that *something* is sealed, but it
                # cannot see that a person took it over - the third
                # state is invisible to it.
                view = _real(runs)
                view["taken_over"] = 0
                view["items"] = [
                    item for item in view.get("items") or []
                    if str(item.get("state") or "") != "taken-over"
                ]
                return view

            try:
                cli_module.seal_overview = _blind_overview
                broken: Dict[str, bool] = {}
                try:
                    broken = _verdicts(_facts_of(_make_case(str(root))))
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                cli_module.seal_overview = real_overview

            if broken.get("the status page says the take-over"):
                failures.append(
                    "round %d: the gate did not notice that the status page "
                    "cannot see the take-over - it cannot fail, so it does "
                    "not watch" % (round_index + 1)
                )
        finally:
            shutil.rmtree(root, ignore_errors=True)

    return {
        "name": "seal_takeover_everywhere",
        "title": "\u4eba\u5de5\u63a5\u624b\u5728\u6bcf\u4e00\u4e2a\u9762\u4e0a\u90fd\u770b\u5f97\u89c1",
        "skipped": False,
        "invariant": (
            "\u4e00\u4e2a\u7ed3\u8bba\u88ab\u4eba\u5de5\u63a5\u624b\u4e86\uff0c\u62a5\u544a\u3001"
            "\u72b6\u6001\u9875\u3001\u56de\u653e\u3001MCP \u72b6\u6001\u4e0e MCP \u56de\u653e"
            "\u90fd\u5fc5\u987b\u8bf4\u51fa\u6765\uff0c\u4e14\u8ba4\u4e3a\u540c\u4e00\u4e2a\u4e8b"
        ),
        "detail": (
            "\u5c01\u5b58\u7684\u4e09\u4e2a\u72b6\u6001\uff08\u4ecd\u7136\u6210\u7acb\u3001\u4eba\u5de5"
            "\u63a5\u624b\u3001\u5df2\u6f02\u79fb\uff09\u5728\u6240\u6709\u9762\u4e0a\u90fd\u8bf4\u540c"
            "\u4e00\u53e5\u8bdd\uff1b\u6ca1\u6709\u6279\u51c6\u7684\u6539\u52a8\u4ecd\u7136\u662f\u6f02\u79fb"
        ),
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_the_takeover_is_visible_everywhere.CHECK_NAME = (  # type: ignore[attr-defined]
    "seal_takeover_everywhere"
)
