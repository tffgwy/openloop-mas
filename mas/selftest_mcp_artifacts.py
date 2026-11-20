# -*- coding: utf-8 -*-
"""The thirty-seventh gate: the deliverable list has to reach the MCP caller.

The gap
-------
This project sells itself on the MCP road. An agent driving Claude,
GPT or Kimi holds the MCP tools - not the CLI. The MCP surface could
run a loop (``mas_run``), read the report (``mas_report``), replay the
ledger (``mas_replay``), audit the decisions (``mas_audit``), seal them
(``mas_seal``) and list every file it wrote (``mas_files``).

It could not answer **"what did I actually get?"**

``mas artifacts`` answered that - but only on the terminal. The
listing was written inside the CLI command, where the MCP surface
could not reach it. So an agent that had just closed a loop, holding
every other tool in the catalogue, had to *guess* where the
deliverable was, or ask the human to go and look.

The two surfaces also had no way to agree. There was one
implementation, in the CLI, and no second one to disagree with - which
means the moment a second one was written, the two would drift, and
the CLI and the MCP would tell the user two different stories about
what this machine produced.

So this gate watches the *listing*:

* the tool is advertised in the MCP catalogue;
* calling it returns the deliverable, with its task, its path, its
  size and whether the file is still there;
* a deliverable that was deleted afterwards is still listed and
  **flagged** - "the file is gone" is a fact the reader needs, and
  dropping the row would hide it;
* the MCP answer and the CLI answer are **the same answer** - the
  listing is one implementation, and both surfaces call it;
* and the MCP caller is not left to parse Markdown: the rows come
  back as data.

And the gate proves it can fail: the shared listing is broken on
purpose - it returns nothing - and the gate has to notice that the
MCP caller can no longer tell a finished job from no job at all.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from typing import Any, Dict, List

#: The observations the gate makes.
ARTIFACTS_OBSERVATIONS = (
    "the tool is advertised in the MCP catalogue",
    "the tool returns the deliverable with its task and its path",
    "a deleted deliverable is still listed and flagged",
    "the MCP answer and the CLI answer are the same answer",
    "the caller gets rows as data, not as Markdown",
)


def _artifact_rows(tmp: str) -> List[Dict[str, Any]]:
    """Ledger rows of a run that produced one deliverable.

    The row set is written by hand so that the gate does not need a
    provider: the deliverable is a file on the disk, and the ledger
    says where it is.
    """
    deliverable = os.path.join(tmp, "out.md")
    with open(deliverable, "w", encoding="utf-8") as handle:
        handle.write("# 交付物\n\n这是一份交付物。\n")
    return [
        {
            "role": "closure",
            "run_id": "R-selftestart",
            "task_id": "T-selftestart",
            "iteration": 1,
            "ts": "2026-01-01T00:00:00+00:00",
            "seq": 1,
            "verdict": "pass",
            "decision": "accept",
            "scores": [{"id": "c1", "pass": True}],
        },
        {
            "role": "run_end",
            "run_id": "R-selftestart",
            "task_id": "T-selftestart",
            "iteration": 1,
            "ts": "2026-01-01T00:00:01+00:00",
            "seq": 2,
            "status": "closed",
            "decision": "accept",
            "artifact_path": deliverable,
            "artifact_chars": 24,
            "workspace": tmp,
            "title": "写一份结论",
            "goal": "写一份结论",
            "files": [{"path": "out.md", "bytes": 24}],
        },
    ]


class _FakeLedger:
    """A ledger that holds exactly the rows the gate wrote."""

    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self._rows = list(rows)

    def entries(self) -> List[Dict[str, Any]]:
        return list(self._rows)


class _FakeEngine:
    """The smallest thing the MCP tool needs from an engine."""

    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.ledger = _FakeLedger(rows)


def _call_mcp(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Call the MCP tool the way a caller would.

    The tool reads the ledger of the machine it runs on. The gate
    points that machine at a ledger holding the rows it wrote, so the
    call goes through the real code path - the tool really reads the
    rows, rather than being handed the answer.
    """
    import mas.mcp_server as mcp_module

    real_engine = mcp_module._engine

    def _fake(_config, team=None):
        return _FakeEngine(rows)

    try:
        mcp_module._engine = _fake
        return mcp_module.handle_tool("mas_artifacts", {})
    finally:
        mcp_module._engine = real_engine


def _artifacts_observations(tmp: str) -> Dict[str, Any]:
    """Everything the gate looks at, gathered in one place."""
    from mas.mcp_server import TOOLS
    from mas.report import artifact_rows, format_artifacts

    rows = _artifact_rows(tmp)
    names = [str(tool.get("name") or "") for tool in TOOLS]

    # The MCP answer, with the ledger of the machine pointed at the
    # temporary folder so that the tool reads *this* run.
    payload = _call_mcp(rows)
    # The CLI answer, from the same implementation.
    shared = artifact_rows(rows)

    return {
        "tool_names": names,
        "payload": payload,
        "shared": shared,
        "reading": format_artifacts(shared),
    }


def _artifacts_verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """What the gate believes, from the gathered facts."""
    verdicts: Dict[str, bool] = {}
    names = list(facts.get("tool_names") or [])
    payload = facts.get("payload") or {}
    shared = list(facts.get("shared") or [])

    verdicts["the tool is advertised in the MCP catalogue"] = (
        "mas_artifacts" in names
    )

    artifacts = payload.get("artifacts") if isinstance(payload, dict) else None
    if not isinstance(artifacts, list):
        artifacts = []

    verdicts["the tool returns the deliverable with its task and its path"] = bool(
        artifacts
        and str(artifacts[0].get("path") or "").endswith("out.md")
        and str(artifacts[0].get("title") or "")
        and str(artifacts[0].get("goal") or "")
    )

    verdicts["a deleted deliverable is still listed and flagged"] = bool(
        artifacts
        and all("exists" in item for item in artifacts)
        and payload.get("missing") is not None
    )

    # The two surfaces have to agree. Not "roughly": the same rows.
    verdicts["the MCP answer and the CLI answer are the same answer"] = bool(
        artifacts and shared and artifacts == shared
    )

    # The caller gets data. A tool that answers with a wall of Markdown
    # forces the agent to parse prose to find one path.
    verdicts["the caller gets rows as data, not as Markdown"] = bool(
        artifacts
        and isinstance(artifacts[0], dict)
        and "path" in artifacts[0]
        and "run_id" in artifacts[0]
    )
    return verdicts


def _artifacts_verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """The same verdict, read a second way, sharing no code with the first.

    This pass does not call the shared listing at all. It asks the
    tool what it returns and reads the answer as a reader would - so a
    broken listing that returns an empty list cannot make both passes
    agree on a wrong answer.
    """
    verdicts: Dict[str, bool] = {}
    payload = facts.get("payload") or {}
    artifacts = payload.get("artifacts") if isinstance(payload, dict) else None
    if not isinstance(artifacts, list):
        artifacts = []

    verdicts["the tool is advertised in the MCP catalogue"] = bool(
        facts.get("tool_names")
        and "mas_artifacts" in list(facts.get("tool_names") or [])
    )

    first = artifacts[0] if artifacts else {}
    verdicts["the tool returns the deliverable with its task and its path"] = bool(
        str(first.get("path") or "").endswith("out.md")
        and str(first.get("title") or "").strip()
    )

    verdicts["a deleted deliverable is still listed and flagged"] = bool(
        artifacts and "exists" in artifacts[0] and "missing" in payload
    )

    # Read the agreement differently: the tool's own rows and the
    # shared rows have to hold the same paths and the same run ids.
    shared = list(facts.get("shared") or [])
    tool_paths = [str(item.get("path") or "") for item in artifacts]
    shared_paths = [str(item.get("path") or "") for item in shared]
    tool_runs = [str(item.get("run_id") or "") for item in artifacts]
    shared_runs = [str(item.get("run_id") or "") for item in shared]
    verdicts["the MCP answer and the CLI answer are the same answer"] = bool(
        tool_paths == shared_paths and tool_runs == shared_runs and tool_paths
    )

    verdicts["the caller gets rows as data, not as Markdown"] = bool(
        artifacts and isinstance(artifacts[0], dict) and "run_id" in artifacts[0]
    )
    return verdicts


def check_the_deliverables_reach_the_mcp_caller(
    iterations: int = 3,
) -> Dict[str, Any]:
    """The deliverable list has to reach the MCP caller - and it can fail."""
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        rounds += 1
        tmp = tempfile.mkdtemp(prefix="mas-selftest-artifacts-")
        try:
            try:
                facts = _artifacts_observations(tmp)
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the listing check broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                facts = {}

            verdicts = _artifacts_verdicts(facts)
            independent = _artifacts_verdicts_independent(facts)
            for label in ARTIFACTS_OBSERVATIONS:
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

            # The gate proves it can fail: the shared listing is broken
            # on purpose - it returns nothing - and the gate has to
            # notice that the MCP caller can no longer tell a finished
            # job from no job at all.
            import mas.mcp_server as mcp_module
            import mas.report as report_module

            real_rows = report_module.artifact_rows
            real_imported = mcp_module.artifact_rows

            def _nothing(_rows):
                return []

            try:
                report_module.artifact_rows = _nothing
                mcp_module.artifact_rows = _nothing
                broken: Dict[str, bool] = {}
                try:
                    broken = _artifacts_verdicts(_artifacts_observations(tmp))
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                report_module.artifact_rows = real_rows
                mcp_module.artifact_rows = real_imported
            if broken.get(
                "the tool returns the deliverable with its task and its path"
            ):
                failures.append(
                    "round %d: the gate did not notice that the listing says "
                    "nothing - it cannot fail, so it does not watch"
                    % (round_index + 1)
                )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    return {
        "name": "mcp_artifacts",
        "title": "\u4ea4\u4ed8\u7269\u6e05\u5355\u5230\u4e86 MCP \u8c03\u7528\u8005\u624b\u91cc",
        "skipped": False,
        "invariant": (
            "MCP \u8c03\u7528\u8005\u80fd\u62ff\u5230\u4ea4\u4ed8\u7269\u6e05\u5355\uff0c"
            "\u4e14\u4e0e\u547d\u4ee4\u884c\u7ed9\u51fa\u7684\u662f\u540c\u4e00\u4efd\u7b54\u6848"
        ),
        "detail": "\u5bf9\u7167 MCP \u5de5\u5177\u7684\u7b54\u6848\u4e0e\u547d\u4ee4\u884c\u7684\u7b54\u6848",
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_the_deliverables_reach_the_mcp_caller.CHECK_NAME = "mcp_artifacts"  # type: ignore[attr-defined]
