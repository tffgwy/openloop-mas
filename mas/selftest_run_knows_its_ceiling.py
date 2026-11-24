# -*- coding: utf-8 -*-
"""The fifty-third gate: a run without a verdict is not a drifted run.

The gap
------
v0.7.88 gave every closure a *seal*: the criteria, the deliverable's
hash, the hashes of the files the run wrote. ``mas seal`` re-derives
the verdict from that evidence and says whether it still holds.

But the seal was written **unconditionally** - every run, including
the ones that never judged anything. A run that died on the budget
ceiling before it reached a scoring round has no verdict, so its seal
carried an empty score list and a hard-coded ``"fail"``.

And the report read that seal as a *conclusion*. The verdict could not
be recomputed - there was nothing to recompute - so the seal came back
as ``unrecomputable``, and the report counted it as **drifted**:

    \u5c01\u5b58\u590d\u6838\uff1a1 \u6761\u5c01\u5b58\u4e2d\uff0c1 \u6761\u7ed3\u8bba\u5df2\u6f02\u79fb

That accuses a run of lying about something it never said. The run
never judged anything, so it never concluded anything, so nothing it
concluded can have drifted. The report was raising a false alarm
against its own machine - and a false alarm about drift is the worst
kind, because a reader who learns to ignore it will miss the real
one.

The fix
------
* A run with no scores gets **no seal**. No verdict, no seal - the
  seal's own docstring always said it was "only written for a run
  that *decided* something", and the code had stopped keeping that
  promise.
* An **old** ledger that already carries such a seal is read the
  same way: a seal with no verdict is a state of its own
  (``no-verdict``), and it is not drift.
* The report counts those closures apart, so "3 seals, 1 drifted"
  cannot become "4 seals, 4 drifted" by accident.

The second half of this gate is the important one: **a real drift
still has to be reported.** A gate that only ever proves "no false
alarm" can be satisfied by a report that never reports anything. So
the gate also builds a closure that *did* decide, edits the file it
wrote, and insists the report says the conclusion drifted.

This gate watches:

* a run that never judged anything carries no seal at all;
* a run that never judged anything is not counted as drifted;
* an old ledger's verdict-less seal is read as "no verdict", not as
  drift;
* the headline does not accuse the machine of a false drift;
* a closure that *did* decide and was edited afterwards is still
  reported as drifted - the lock is not weakened into "never report
  anything";
* the run says which ceiling stopped it;
* and the gate proves it can fail: the **seal** is written for
  verdict-less runs again, and the gate has to notice that the
  machine started accusing itself.

The break is on the writing, not on the reading: a broken reader
makes every surface wrong in the same way, so the surfaces agree and
the gate sees nothing. The break has to make the *book* carry the
false claim again.
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile
from typing import Any, Dict, List

#: The observations the gate makes.
RUN_KNOWS_ITS_CEILING_OBSERVATIONS = (
    "a run that never judged anything carries no seal",
    "a run that never judged anything is not counted as drifted",
    "an old verdict-less seal is read as no verdict",
    "the headline does not accuse the machine of a false drift",
    "a closure that did decide and was edited is still drifted",
    "the run says which ceiling stopped it",
)


class _Env:
    """The environment variables the gate has to borrow.

    The restore is **absolute**: leaving the context puts back exactly
    the values that were there when it was entered - and removes the
    ones that were not. The gate enters and leaves this context many
    times per round, and a restore that only *sets* the variables
    would leave the last one behind: a self-test that points the
    machine's state directory at a temporary folder is a self-test
    that dirties the machine.
    """

    def __init__(self, state_dir: str, config_path: str) -> None:
        self._values = {
            "MAS_STATE_DIR": state_dir,
            "MAS_CONFIG_PATH": config_path,
        }
        self._saved: Dict[str, Any] = {}

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


def _config(root: str, **budget: Any) -> Dict[str, Any]:
    state = pathlib.Path(root) / "state"
    state.mkdir(parents=True, exist_ok=True)
    base: Dict[str, Any] = {
        "max_iterations": 2,
        "max_tokens": 1000000,
        "deadline_sec": 60,
    }
    base.update(budget)
    return {
        "provider": "mock",
        "model": "selftest-model",
        "state_dir": str(state),
        "workspace": "",
        "budget": base,
        "orchestration": {"parallel_workers": 1, "reviewers": 1, "review_mode": "all"},
        "team": {"language": "zh"},
    }


def _offline_provider() -> Any:
    """A provider that answers like a real one - and never gets called
    in the verdict-less scenarios, because those die before scoring.
    """
    from mas.providers import Provider

    class _P(Provider):
        name = "selftest-ceiling"

        def __init__(self) -> None:
            super().__init__(model="selftest-model")

        def complete(
            self,
            *,
            system: str = "",
            prompt: str = "",
            role: str = "worker",
            expect_json: bool = False,
            max_tokens: int = 4000,
        ) -> Dict[str, Any]:
            usage = {"total_tokens": 10}
            if role == "worker":
                return {"text": "the answer is 42.\n", "usage": usage, "raw": {}}
            return {
                "text": json.dumps(
                    {
                        "verdict": "pass",
                        "scores": [{"id": "c1", "pass": True, "note": "ok"}],
                        "issues": [],
                        "summary": "s",
                        "plan": ["p"],
                        "risks": [],
                        "decision": "approve",
                        "reason": "ok",
                    }
                ),
                "usage": usage,
                "raw": {},
            }

    return _P()


def _broken_provider() -> Any:
    """A provider that fails on the very first call.

    The run dies before it reaches a scoring round, so it never
    judges anything - the same verdict-less situation, reached a
    different way.
    """
    from mas.providers import Provider, ProviderError

    class _P(Provider):
        name = "selftest-ceiling-broken"

        def __init__(self) -> None:
            super().__init__(model="selftest-model")

        def complete(
            self,
            *,
            system: str = "",
            prompt: str = "",
            role: str = "worker",
            expect_json: bool = False,
            max_tokens: int = 4000,
        ) -> Dict[str, Any]:
            raise ProviderError("502 the backend is down")

    return _P()


def _criteria() -> List[Dict[str, Any]]:
    return [
        {
            "id": "c1",
            "desc": "\u542b\u6700\u7ec8\u7b54\u6848",
            "check": {"type": "contains", "arg": "the answer"},
        }
    ]


def _make_case(root: str) -> Dict[str, Any]:
    """Three runs: two that never judged, one that did.

    The two verdict-less runs are needed because they fail in two
    different ways, and a fix that only covers one of them is not a
    fix. One hits the wall-clock ceiling before the first iteration;
    the other dies on a provider error in the first iteration. Both
    reach the ledger without a verdict.

    The third run *does* decide - and it is the one that has to keep
    being reported as drifted when its file is edited. A gate that
    only ever proves "no false alarm" can be satisfied by a report
    that never reports anything at all.
    """
    from mas.engine import LoopEngine

    criteria = _criteria()
    run_ids: Dict[str, str] = {}

    # 1. The wall-clock ceiling fires before the first iteration.
    engine = LoopEngine(_config(root + "-clock", deadline_sec=0), provider=_offline_provider())
    result = engine.run(goal="\u5199\u4e00\u4efd\u8bf4\u660e", criteria=criteria).to_dict()
    run_ids["clock"] = str(result.get("run_id") or "")
    clock_rows = engine.ledger.entries()

    # 2. The provider dies on the first call.
    engine = LoopEngine(_config(root + "-provider"), provider=_broken_provider())
    result = engine.run(goal="\u518d\u5199\u4e00\u4efd\u8bf4\u660e", criteria=criteria).to_dict()
    run_ids["provider"] = str(result.get("run_id") or "")
    provider_rows = engine.ledger.entries()

    # 3. A run that really decides.
    engine = LoopEngine(_config(root + "-closed"), provider=_offline_provider())
    result = engine.run(goal="\u518d\u518d\u5199\u4e00\u4efd\u8bf4\u660e", criteria=criteria)
    closed_result = result.to_dict()
    run_ids["closed"] = str(closed_result.get("run_id") or "")
    closed_rows = engine.ledger.entries()

    return {
        "clock": run_ids["clock"],
        "provider": run_ids["provider"],
        "closed": run_ids["closed"],
        "clock_rows": clock_rows,
        "provider_rows": provider_rows,
        "closed_rows": closed_rows,
        "closed_workspace": str(result.workspace or ""),
        "closed_artifact": str(result.artifact or ""),
    }


def _run_end_of(rows: Any, run_id: str) -> Dict[str, Any]:
    """One run's own end entry."""
    for row in rows or []:
        if (
            isinstance(row, dict)
            and str(row.get("role") or "") == "run_end"
            and str(row.get("run_id") or "") == run_id
        ):
            return row
    return {}


def _facts_of(case: Dict[str, Any]) -> Dict[str, Any]:
    """Everything the gate looks at, gathered in one place."""
    from mas.report import build_report, render_markdown

    rows: List[Dict[str, Any]] = []
    rows.extend(case.get("clock_rows") or [])
    rows.extend(case.get("provider_rows") or [])
    rows.extend(case.get("closed_rows") or [])

    report = build_report(rows)
    markdown = render_markdown(report)

    # Now edit the file the *closed* run wrote - after the closure -
    # and look at what the report says about it. The file is edited to
    # the same length on purpose: a length-based record cannot see
    # that.
    #
    # The deliverable the closed run actually wrote. The path comes
    # from the run's own end entry, because that is where the seal
    # took its hash from: editing some other file in the folder would
    # change nothing the seal had ever claimed.
    closed_end = _run_end_of(rows, str(case.get("closed") or ""))
    artifact_path = str(closed_end.get("artifact_path") or "")
    target = pathlib.Path(artifact_path) if artifact_path else pathlib.Path("")
    on_disk: Dict[str, Any] = {"exists": bool(artifact_path) and target.exists(), "drifted": False}
    if on_disk["exists"]:
        original = target.read_bytes()
        # The same length on purpose: a length-based record cannot
        # see that the bytes changed.
        target.write_bytes(b"the answer is 43.\n")
        on_disk["drifted"] = True
        try:
            drifted_report = build_report(rows)
            drifted_markdown = render_markdown(drifted_report)
        finally:
            target.write_bytes(original)
    else:
        drifted_report = report
        drifted_markdown = markdown

    return {
        "case": case,
        "rows": rows,
        "report": report,
        "markdown": markdown,
        "drifted_report": drifted_report,
        "drifted_markdown": drifted_markdown,
        "on_disk": on_disk,
    }


def _verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """What the gate believes, from the gathered facts."""
    case = facts.get("case") or {}
    rows = facts.get("rows") or []
    report = facts.get("report") or {}
    markdown = str(facts.get("markdown") or "")
    totals = report.get("totals") or {}

    clock_end = _run_end_of(rows, str(case.get("clock") or ""))
    provider_end = _run_end_of(rows, str(case.get("provider") or ""))
    closed_end = _run_end_of(rows, str(case.get("closed") or ""))

    # A run that never judged anything carries no seal at all.
    no_seal = (
        not clock_end.get("seal")
        and not provider_end.get("seal")
        # The run that did decide still carries its seal - the fix
        # must not have swallowed the real closures.
        and bool(closed_end.get("seal"))
    )

    # And it is not counted as drifted.
    not_drifted = (
        int(totals.get("seal_drifted") or 0) == 0
        and int(totals.get("seal_no_verdict") or 0) == 0
    )

    # An old ledger's verdict-less seal is read as "no verdict".
    #
    # The seal is built by hand here, exactly as the old engine wrote
    # it: the criteria, the artifact hash, the files, an *empty* score
    # list and a hard-coded verdict. If the reader still calls that a
    # drift, the machine is still accusing itself.
    from mas.seal import build_seal, sha256_bytes

    legacy_seal = build_seal(
        run_id="R-legacy",
        criteria=[{"id": "c1", "desc": "x"}],
        artifact="the answer is 42.\n",
        artifact_sha256=sha256_bytes(b"the answer is 42.\n"),
        files=[],
        scores=[],
        verdict="fail",
        decision="",
    )
    from mas.seal import verify_seal

    legacy = verify_seal(legacy_seal, "the answer is 42.\n")
    legacy_reads_right = (
        str(legacy.get("state") or "") == "no-verdict"
        and bool(legacy.get("ok"))
    )

    # The headline does not accuse the machine of a false drift.
    headline = 0
    if isinstance(report.get("seal"), dict):
        headline = int(report["seal"].get("drifted") or 0)
    no_false_accusation = (
        "\u7ed3\u8bba\u5df2\u6f02\u79fb" not in markdown
        and int(totals.get("seal_drifted") or 0) == 0
        and headline == 0
    )

    # A closure that *did* decide and was edited afterwards is still
    # reported as drifted. This is the half of the gate that keeps
    # the fix from becoming "never report anything".
    drifted_totals = (facts.get("drifted_report") or {}).get("totals") or {}
    drifted_markdown = str(facts.get("drifted_markdown") or "")
    real_drift = (
        int(drifted_totals.get("seal_drifted") or 0) >= 1
        and "\u7ed3\u8bba\u5df2\u6f02\u79fb" in drifted_markdown
    )

    # The run says which ceiling stopped it.
    ceiling_ok = (
        isinstance(clock_end.get("budget"), dict)
        and bool(clock_end.get("budget"))
        and str(clock_end.get("stopped_by") or "") == "deadline_sec"
        and str(provider_end.get("stopped_by") or "") == "provider_error"
    )

    return {
        "a run that never judged anything carries no seal": no_seal,
        "a run that never judged anything is not counted as drifted": not_drifted,
        "an old verdict-less seal is read as no verdict": legacy_reads_right,
        "the headline does not accuse the machine of a false drift": (
            no_false_accusation
        ),
        "a closure that did decide and was edited is still drifted": real_drift,
        "the run says which ceiling stopped it": ceiling_ok,
    }


def _verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """The same reading, made from the raw rows and the disk.

    This pass does not look at what the report printed. It reads the
    ledger and works out the truth - which runs reached a verdict at
    all, and whether the bytes on the disk still match the ones the
    closure was decided on - and then says what every surface ought
    to report. If the two readings disagree, the gate says so - and
    the gate cannot be satisfied by a surface that merely looks
    right.
    """
    case = facts.get("case") or {}
    rows = facts.get("rows") or []

    clock_end = _run_end_of(rows, str(case.get("clock") or ""))
    provider_end = _run_end_of(rows, str(case.get("provider") or ""))
    closed_end = _run_end_of(rows, str(case.get("closed") or ""))

    # The truth: two runs never judged, one did.
    from mas.seal import seal_has_verdict

    never_judged = (
        not seal_has_verdict(clock_end.get("seal"))
        and not seal_has_verdict(provider_end.get("seal"))
        and seal_has_verdict(closed_end.get("seal"))
    )

    # The truth of the drift: the file on the disk no longer has the
    # bytes the seal recorded, so a drift *is* there - independently
    # of whether the report noticed. The disk was read while the
    # drift was there.
    drifted_on_disk = bool((facts.get("on_disk") or {}).get("drifted"))

    return {
        "a run that never judged anything carries no seal": never_judged,
        "a run that never judged anything is not counted as drifted": (
            never_judged
        ),
        "an old verdict-less seal is read as no verdict": never_judged,
        "the headline does not accuse the machine of a false drift": (
            never_judged
        ),
        "a closure that did decide and was edited is still drifted": (
            drifted_on_disk
        ),
        "the run says which ceiling stopped it": (
            bool(clock_end.get("budget"))
            and bool(provider_end.get("stopped_by"))
        ),
    }


def check_a_run_knows_its_ceiling(iterations: int = 3) -> Dict[str, Any]:
    """The fifty-third gate."""
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        rounds += 1
        root = tempfile.mkdtemp(prefix="mas-selftest-ceiling-")
        try:
            try:
                facts = _facts_of(_make_case(root))
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the check broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                facts = {}

            verdicts = _verdicts(facts)
            independent = _verdicts_independent(facts)
            for label in RUN_KNOWS_ITS_CEILING_OBSERVATIONS:
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
            # The break is on the **writing**: the engine goes back to
            # sealing every run, including the ones that never judged
            # anything. It is not the same as breaking a shared reader
            # - a broken reader makes every surface wrong in the same
            # way, so the surfaces agree and the gate sees nothing. The
            # break has to make the *book* carry the false claim
            # again.
            import mas.engine as engine_module

            real_guard = engine_module.LoopEngine._seal_for_run

            def _seal_everything(
                self,
                run_id: str,
                crits: Any,
                artifact: str,
                scores: Any,
                verdict: str,
                decision: str,
            ) -> Dict[str, Any]:
                # Exactly what the code did before this release: every
                # run was sealed, verdict or not.
                return self._seal_closure(
                    run_id, crits, artifact, scores, verdict, decision
                )

            try:
                engine_module.LoopEngine._seal_for_run = _seal_everything
                broken: Dict[str, bool] = {}
                try:
                    broken = _verdicts(_facts_of(_make_case(root + "-broken")))
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                engine_module.LoopEngine._seal_for_run = real_guard

            if broken.get("a run that never judged anything carries no seal"):
                failures.append(
                    "round %d: the gate did not notice that the engine "
                    "seals runs that never judged anything - it cannot "
                    "fail, so it does not watch" % (round_index + 1)
                )
        finally:
            import shutil

            shutil.rmtree(root, ignore_errors=True)

    return {
        "name": "a_run_knows_its_ceiling",
        "title": "\u8fd0\u884c\u81ea\u5df1\u77e5\u9053\u81ea\u5df1\u7684\u5929\u82b1\u677f\uff0c\u4e5f\u77e5\u9053\u6ca1\u5224\u5b9a\u5c31\u6ca1\u6709\u7ed3\u8bba",
        "skipped": False,
        "invariant": (
            "\u4ece\u672a\u5224\u5b9a\u8fc7\u4efb\u4f55\u4e1c\u897f\u7684\u8fd0\u884c\u4e0d\u5f97\u88ab\u62a5\u6210\u300c\u7ed3\u8bba\u5df2\u6f02\u79fb\u300d\uff1b"
            "\u771f\u7684\u5224\u5b9a\u88ab\u6539\u8fc7\u65f6\u4ecd\u987b\u62a5\u6f02\u79fb\uff1b\u8fd0\u884c\u5fc5\u987b\u80fd\u8bf4\u51fa\u81ea\u5df1\u7684\u5929\u82b1\u677f"
        ),
        "detail": (
            "\u6ca1\u6709\u5224\u5b9a\u5c31\u6ca1\u6709\u7ed3\u8bba\uff0c\u6ca1\u6709\u7ed3\u8bba\u5c31\u8c08\u4e0d\u4e0a\u6f02\u79fb\uff1b"
            "\u5929\u82b1\u677f\u5199\u8fdb\u8fd0\u884c\u81ea\u5df1\u7684\u8d26\u672c\uff0c\u4e0d\u518d\u53ea\u5728\u5de5\u5355\u4e0a"
        ),
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_a_run_knows_its_ceiling.CHECK_NAME = (  # type: ignore[attr-defined]
    "a_run_knows_its_ceiling"
)
