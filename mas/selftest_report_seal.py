# -*- coding: utf-8 -*-
"""The twenty-ninth gate: the report has to see the drift.

The gap
-------
v0.7.88 gave every closure a *seal*: the acceptance criteria, the
hash of the deliverable's bytes, the hash of every file the run wrote.
``mas seal`` re-derives the verdict from that sealed evidence and says
whether it still holds.

But the report never looked. The report is the screen a human reads
*after* the run - it is where "did this work?" is actually answered -
and it kept saying "closed", "machine-closed", "resolved", for a
conclusion whose bytes had been edited underneath. The seal could
prove a drift and the report would still not mention it. A capability
that the main view does not consume is a capability nobody has.

So this gate watches the *report*, not the seal:

* a healthy closure is reported as still holding;
* a closure whose file was edited afterwards is reported as drifted -
  in the problem overview, in the run overview, and in the detail of
  the run;
* the JSON payload carries the reconciliation, so a script can ask
  the question without parsing Markdown;
* a problem that is closed on paper says so instead of "resolved".

And the gate proves it can fail: the report's seal check is broken on
purpose - every seal is reported as holding - and the gate has to
notice that the report can no longer tell a healthy closure from a
drifted one.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import tempfile
from typing import Any, Dict, List

#: The observations the gate makes, and the two readings of them.
REPORT_SEAL_OBSERVATIONS = (
    "a healthy closure is reported as still holding",
    "the problem overview says a drifted conclusion drifted",
    "the run overview does not call a drifted closure OK",
    "the detail of the run names the file that was edited",
    "the JSON payload carries the seal reconciliation",
)


def _report_seal_observations(root: Any) -> Dict[str, Any]:
    """Run a real closure offline and look at what the report says."""
    from mas.engine import LoopEngine
    from mas.providers import Provider
    from mas.report import build_report, render_markdown

    class _Writer(Provider):
        name = "selftest-report-seal"

        def complete(self, *, system: str = "", prompt: str = "", role: str = "worker",
                     expect_json: bool = False, max_tokens: int = 4000) -> Dict[str, Any]:
            usage = {"total_tokens": 10}
            if role == "worker":
                text = (
                    "\u4ea4\u4ed8\u7269\u6b63\u6587\n"
                    '<<<MAS:FILE path="notes/finding.txt">>>>\n'
                    "\u7ed3\u8bba\uff1aSELFTEST\n"
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

    seen: Dict[str, Any] = {}
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
        goal="\u5199\u4e00\u4efd\u7ed3\u8bba",
        criteria=[
            {"id": "c1", "desc": "\u542b\u6b63\u6587", "check": {"type": "contains", "arg": "\u6b63\u6587"}},
            {"id": "c2", "desc": "\u6587\u4ef6\u5199\u51fa\u6765\u4e86", "check": {"type": "file_exists", "arg": "notes/finding.txt"}},
        ],
    )
    seen["closed"] = bool(result.closed)
    rows = engine.ledger.entries()
    seen["rows"] = rows

    # The report of a healthy closure.
    healthy_report = build_report(rows)
    seen["healthy_markdown"] = render_markdown(healthy_report)
    seen["healthy_json"] = healthy_report

    # Now edit the file the run wrote - after the closure - and look
    # at what the report says about it. The file is edited to the same
    # length on purpose: a length-based record cannot see that.
    target = pathlib.Path(str(result.workspace or "")) / "notes" / "finding.txt"
    seen["target_exists"] = target.exists()
    if not target.exists():
        return seen
    original = target.read_bytes()
    tampered = "\u7ed3\u8bba\uff1aEDITED\n".encode("utf-8")
    target.write_bytes(tampered)
    try:
        drifted_report = build_report(rows)
        seen["drifted_markdown"] = render_markdown(drifted_report)
        seen["drifted_json"] = drifted_report
        # The independent reading needs the disk as it stands *now* -
        # while the drift is there. It used to look afterwards, when
        # the file had already been put back, so it saw a healthy
        # disk and disagreed with the report for no reason at all.
        seen["on_disk"] = _files_on_disk(rows)
    finally:
        target.write_bytes(original)

    # And the healthy report again, to be sure the gate does not
    # confuse "it was drifted once" with "it is drifted".
    seen["restored_markdown"] = render_markdown(build_report(rows))
    return seen


def _files_on_disk(rows: Any) -> List[Dict[str, Any]]:
    """The hash of every file the sealed runs wrote, read from disk.

    Read straight from the ledger and the disk - the report is not
    consulted at all - so this is a second, independent account of
    whether the bytes on the disk still match the ones the closure
    was decided on.
    """
    from mas.seal import sha256_file

    out: List[Dict[str, Any]] = []
    for row in rows or []:
        if str(row.get("role") or "") != "run_end":
            continue
        seal = row.get("seal")
        if not isinstance(seal, dict):
            continue
        for item in seal.get("files") or []:
            if not isinstance(item, dict):
                continue
            absolute = str(item.get("absolute") or "")
            wanted = str(item.get("sha256") or "")
            if not absolute or not wanted:
                continue
            path = pathlib.Path(absolute)
            current = sha256_file(path) if path.exists() else ""
            out.append(
                {
                    "path": str(item.get("path") or absolute),
                    "wanted": wanted,
                    "current": current,
                    "drifted": bool(current) and current != wanted,
                }
            )
    return out


def _split_tables(markdown: str) -> Dict[str, str]:
    """The two tables of the report, cut apart."""
    out: Dict[str, str] = {}
    if "## \u95ee\u9898\u6982\u89c8" in markdown and "## \u8fd0\u884c\u6982\u89c8" in markdown:
        out["problems"] = markdown.split("## \u95ee\u9898\u6982\u89c8", 1)[1].split("## \u8fd0\u884c\u6982\u89c8", 1)[0]
    if "## \u8fd0\u884c\u6982\u89c8" in markdown and "## \u8fd0\u884c `" in markdown:
        out["runs"] = markdown.split("## \u8fd0\u884c\u6982\u89c8", 1)[1].split("## \u8fd0\u884c `", 1)[0]
    return out


def _report_seal_verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """What the gate concludes from what it saw."""
    verdicts: Dict[str, bool] = {}

    healthy = str(facts.get("healthy_markdown") or "")
    verdicts["a healthy closure is reported as still holding"] = (
        "\u5c01\u5b58\uff1a\u2713 \u7ed3\u8bba\u4ecd\u7136\u6210\u7acb" in healthy
    )

    drifted = str(facts.get("drifted_markdown") or "")
    tables = _split_tables(drifted)
    problems_table = tables.get("problems") or ""
    runs_table = tables.get("runs") or ""

    verdicts["the problem overview says a drifted conclusion drifted"] = (
        "\u7ed3\u8bba\u5df2\u6f02\u79fb" in problems_table
        and "\u5df2\u89e3\u51b3" not in problems_table
    )

    # The run overview must not put a green mark next to a drifted
    # closure. "OK" would mean "this closed and still holds".
    ok_row = [line for line in runs_table.splitlines() if line.startswith("| `R-")]
    verdicts["the run overview does not call a drifted closure OK"] = (
        bool(ok_row)
        and all("OK" not in line.split("|")[3] for line in ok_row)
    )

    verdicts["the detail of the run names the file that was edited"] = (
        "\u6587\u4ef6\u88ab\u6539\u8fc7" in drifted
        and "notes/finding.txt" in drifted
    )

    payload = facts.get("drifted_json") or {}
    seal = payload.get("seal") if isinstance(payload, dict) else None
    verdicts["the JSON payload carries the seal reconciliation"] = (
        isinstance(seal, dict)
        and int(seal.get("sealed") or 0) >= 1
        and int(seal.get("drifted") or 0) >= 1
        and seal.get("ok") is False
    )
    return verdicts


def _report_seal_verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """A second, independent reading of the same facts.

    This reading goes back to the ledger rows and to the disk and asks
    the same questions with different code: it never looks at the
    report's own seal field, and it never looks at the rendered text.
    """
    verdicts: Dict[str, bool] = {}

    rows = facts.get("rows") or []
    run_end = [row for row in rows if str(row.get("role") or "") == "run_end"]
    sealed = [row for row in run_end if isinstance(row.get("seal"), dict)]
    verdicts["a healthy closure is reported as still holding"] = (
        bool(facts.get("closed")) and bool(sealed)
    )

    # Independently: re-verify the seal against the disk.
    from mas.seal import verify_seal

    holds = False
    for row in sealed:
        seal = row.get("seal") or {}
        path = str(row.get("artifact_path") or "")
        artifact = None
        if path and pathlib.Path(path).exists():
            artifact = pathlib.Path(path).read_text(encoding="utf-8")
        outcome = verify_seal(seal, artifact)
        if outcome.get("state") == "holds":
            holds = True
    verdicts["the problem overview says a drifted conclusion drifted"] = holds

    # Independently: the file on the disk no longer has the bytes the
    # seal recorded, so a drift *is* there - independently of whether
    # the report noticed. The disk was read while the drift was there.
    on_disk = facts.get("on_disk") or []
    drifted_on_disk = any(bool(item.get("drifted")) for item in on_disk)
    verdicts["the run overview does not call a drifted closure OK"] = drifted_on_disk

    verdicts["the detail of the run names the file that was edited"] = drifted_on_disk

    # Independently: the report's payload has to *count* the drift.
    payload = facts.get("drifted_json") or {}
    totals = payload.get("totals") if isinstance(payload, dict) else None
    verdicts["the JSON payload carries the seal reconciliation"] = (
        isinstance(totals, dict)
        and int(totals.get("sealed") or 0) >= 1
        and int(totals.get("seal_drifted") or 0) >= 1
    )
    return verdicts


def check_the_report_sees_the_drift(iterations: int = 5) -> Dict[str, Any]:
    """The report has to see the drift - and the gate can fail."""
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        root = pathlib.Path(tempfile.mkdtemp(prefix="mas-report-seal-"))
        rounds += 1
        try:
            try:
                facts = _report_seal_observations(root)
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the report check broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                facts = {}

            verdicts = _report_seal_verdicts(facts)
            independent = _report_seal_verdicts_independent(facts)
            for label in REPORT_SEAL_OBSERVATIONS:
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

            # The gate proves it can fail: the report's seal check is
            # broken on purpose - every seal is reported as holding -
            # and the gate has to notice that the report can no longer
            # tell a healthy closure from a drifted one.
            import mas.report as report_module

            real_state = report_module._seal_state

            def _seal_that_always_holds(seal, artifact_path, _real=real_state):
                outcome = _real(seal, artifact_path)
                outcome["state"] = "holds"
                outcome["ok"] = True
                outcome["drifted_files"] = []
                outcome["missing_files"] = []
                outcome["problems"] = []
                return outcome

            try:
                report_module._seal_state = _seal_that_always_holds
                broken: Dict[str, bool] = {}
                try:
                    broken = _report_seal_verdicts(_report_seal_observations(root))
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                report_module._seal_state = real_state
            if broken.get("the problem overview says a drifted conclusion drifted"):
                failures.append(
                    "round %d: the gate did not notice that the report's "
                    "seal check is broken - it cannot fail, so it does not watch"
                    % (round_index + 1)
                )
        finally:
            shutil.rmtree(root, ignore_errors=True)

    return {
        "name": "report_seal",
        "title": "\u590d\u76d8\u62a5\u544a\u770b\u5f97\u89c1\u7ed3\u8bba\u6f02\u79fb",
        "skipped": False,
        "invariant": (
            "\u62a5\u544a\u590d\u76d8\u4f1a\u91cd\u7b97\u5c01\u5b58\uff1a\u5224\u5b9a\u4e4b\u540e\u88ab\u6539\u8fc7\u7684\u6587\u4ef6"
            "\u8ba9\u95ee\u9898\u6982\u89c8\u3001\u8fd0\u884c\u6982\u89c8\u548c JSON \u90fd\u8bf4\u51fa\u201c\u7ed3\u8bba\u5df2\u6f02\u79fb\u201d"
        ),
        "detail": "\u590d\u76d8\u62a5\u544a\u662f\u5426\u770b\u5f97\u89c1\u5c01\u5b58\u6f02\u79fb\uff1amas report",
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_the_report_sees_the_drift.CHECK_NAME = "report_seal"  # type: ignore[attr-defined]
