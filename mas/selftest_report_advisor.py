# -*- coding: utf-8 -*-
"""The sixty-first gate: the advisor's own words have to reach the report.

The gap
-------
The advisor - the lead - is asked exactly one question: some
acceptance criterion has no verdict at all, so what is missing? Its
answer is the only sentence in the whole loop that says *what* the
gap is, instead of saying that there is one.

That answer used to travel almost everywhere, and to exactly one
place it did not reach. Measured, with a probe provider:

    === does the ledger hold the advisor's own words? ===
      lead rows: 2
        has LEAD_REASON: True
        has LEAD_NOTE  : True
    === does the REPORT show the advisor's own words? ===
      report has LEAD_REASON: False     <-- the report could not see it
      report has LEAD_NOTE  : True      <-- only by accident, via issues

The ledger held it. The ticket held it. The replay trace held it. The
report - the page a human actually reads after the run - did not.

The reason is small and precise. ``run_summary()`` collected the
``reflector`` rows and built a reflection reading for every round,
and it never collected the ``lead`` rows at all. The advisor's
answer therefore had no place to live in the report, so it was not
rendered - and a report that does not mention an advisor sounds
exactly like a run that never consulted one.

That is the hole this gate closes: **a capability that exists, and a
main view that does not consume it, is a capability nobody has.**

The fix
------
``run_summary()`` now collects the advisor's rows as well, attaches
each answer to the round it was given in, and the Markdown shows it
right next to the reflection of that round - cause and effect next
to each other. The JSON payload carries the same answers, so a
script can ask the question without parsing Markdown.

What does **not** change: the advisor stays an advisor. It still may
not overrule a machine check, and it still cannot close a run. This
gate watches the *wiring*, not the power.

This gate watches:

* the report shows the advisor's own reason;
* the report shows what the advisor decided;
* the report shows the advisor's judgement of the criterion;
* the report attaches the advice to the round it was given in;
* the report shows the advisor's answer when the loop did not
  iterate;
* a run without an advisor says nothing about one;
* the advisor's answer reaches the report exactly once per round,
  not twice (the fallback must not duplicate the timeline);
* the advisor's answer does not appear in a run that never
  consulted one.

The break is on the **collection**: the advisor's rows stop being
collected, so the report cannot see them again. That is exactly
what the code did before this release - and the point of the break
is that the ledger still holds the answer, so the report is blind
while the truth is on disk.
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile
from typing import Any, Dict, List, Optional, Tuple

#: The observations the gate makes.
REPORT_ADVISOR_OBSERVATIONS = (
    "the report shows the advisor's own reason",
    "the report shows what the advisor decided",
    "the report shows the advisor's judgement of the criterion",
    "the report attaches the advice to the round it was given in",
    "the report shows the advisor's answer when the loop did not iterate",
    "a run without an advisor says nothing about one",
    "the advisor's answer reaches the report once per round, not twice",
    "the advisor's answer does not appear in a run that never consulted one",
)

#: The advisor's own reason. The marker is unique on purpose: a
#: repeated string would also show up in the tail of a clipped text,
#: and the gate would read a broken world as a working one.
ADVISOR_REASON = "advisor-reason-marker-ADVISOR-REASON-UNIQUE-120"

#: The advisor's own note about the criterion it judged. It is
#: different from the reason on purpose - the gate asks for the note
#: in the judgement and for the reason in the advice, and a single
#: marker would let one of the two observations pass by accident.
ADVISOR_NOTE = "advisor-note-marker-ADVISOR-NOTE-UNIQUE-120"

#: The word the report uses for the advisor's advice.
ADVISOR_WORD = "\u4e3b\u7ebf\u7684\u5efa\u8bae"


class _Env:
    """Borrow the environment variables the gate has to borrow.

    The restore is absolute: a variable that was not there before is
    removed again.
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


class _Probe:
    """A provider whose advisor answers whatever the gate asked for.

    The verifier stays silent about the criterion, so the criterion
    has no verdict - which is the only situation in which the advisor
    is consulted at all.
    """

    name = "probe"
    model = "probe-model"

    def __init__(self, silent_verifier: bool = True) -> None:
        self.silent_verifier = silent_verifier
        self.calls: List[Tuple[str, str]] = []

    def complete(
        self,
        *,
        system: str = "",
        prompt: str = "",
        role: str = "worker",
        expect_json: bool = False,
        max_tokens: int = 4000,
    ) -> Dict[str, Any]:
        self.calls.append((role, prompt))
        usage = {"total_tokens": 10}
        if role == "lead":
            return {
                "text": json.dumps(
                    {
                        "decision": "revise",
                        "reason": ADVISOR_REASON,
                        "judged": [
                            {"id": "c1", "met": False, "note": ADVISOR_NOTE}
                        ],
                    }
                ),
                "usage": usage,
                "raw": {},
            }
        if role == "verifier":
            scores = [] if self.silent_verifier else [
                {"id": "c1", "pass": False, "note": "the machine decided"}
            ]
            return {
                "text": json.dumps(
                    {"verdict": "fail", "scores": scores, "issues": []}
                ),
                "usage": usage,
                "raw": {},
            }
        if role == "reflector":
            return {
                "text": json.dumps(
                    {
                        "summary": "reflected",
                        "issues": ["change this"],
                        "next_action": "revise",
                    }
                ),
                "usage": usage,
                "raw": {},
            }
        if role == "planner":
            return {
                "text": json.dumps({"summary": "s", "plan": ["a"], "risks": []}),
                "usage": usage,
                "raw": {},
            }
        return {"text": "deliverable", "usage": usage, "raw": {}}


def _config(root: pathlib.Path, max_iterations: int = 3) -> Dict[str, Any]:
    """A configuration that keeps the run inside the gate's own folder."""
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True)
    return {
        "provider": "mock",
        "model": "probe-model",
        "state_dir": str(state),
        "workspace": str(state / "ws"),
        "budget": {
            "max_iterations": max_iterations,
            "max_tokens": 1000000,
            "deadline_sec": 60,
        },
        "orchestration": {
            "parallel_workers": 1,
            "reviewers": 1,
            "review_mode": "all",
        },
        "team": {"language": "zh"},
    }


def _criteria() -> List[Dict[str, Any]]:
    """One criterion with no machine check.

    A criterion with a machine check is decided by the machine, and an
    advisor that disagrees with a fact is not consulted at all - so
    the advisor could not be observed at all with one.
    """
    return [{"id": "c1", "desc": "must contain the keyword"}]


def _observe(
    root: pathlib.Path,
    silent_verifier: bool = True,
    max_iterations: int = 3,
) -> Dict[str, Any]:
    """Run one real loop and read what the report says about it."""
    from mas.engine import LoopEngine
    from mas.report import build_report, render_markdown

    config = _config(root, max_iterations)
    config_path = root / "config.json"
    probe = _Probe(silent_verifier=silent_verifier)
    with _Env(str(config["state_dir"]), str(config_path)):
        engine = LoopEngine(config, provider=probe)
        result = engine.run(goal="make a thing", criteria=_criteria())
    rows = engine.ledger.entries()
    report = build_report(rows)
    return {
        "rows": rows,
        "report": report,
        "markdown": render_markdown(report),
        "lead_rows": [
            row for row in rows if str(row.get("role") or "") == "lead"
        ],
        "runs": list(report.get("runs") or []),
        "status": str(result.status or ""),
    }


def _run_of(facts: Dict[str, Any]) -> Dict[str, Any]:
    """The first run of the report - the one this gate looks at."""
    runs = list(facts.get("runs") or [])
    return runs[0] if runs else {}


def _timeline_of(facts: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The round-by-round view of the run."""
    return list(_run_of(facts).get("timeline") or [])


def _verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """What the gate concludes from the Markdown it read.

    This reading looks at the *page* - what a human reads. A second
    reading, further down, looks at the payload.
    """
    verdicts: Dict[str, bool] = {}
    markdown = str(facts.get("markdown") or "")
    lead_rows = list(facts.get("lead_rows") or [])
    timeline = _timeline_of(facts)

    # The advisor's own words have to be *in the page*.
    verdicts["the report shows the advisor's own reason"] = (
        ADVISOR_REASON in markdown
    )
    verdicts["the report shows what the advisor decided"] = (
        "the advisor decided: revise" in markdown
    )
    verdicts["the report shows the advisor's judgement of the criterion"] = (
        "[c1]" in markdown
        and ADVISOR_NOTE in markdown
        and ("not met" in markdown or "\u672a\u6ee1\u8db3" in markdown)
    )

    # The advice belongs to the round it was given in. Every round
    # the advisor spoke in has to carry it, and no round that it did
    # not speak in may carry one.
    rounds_with_advice = [
        entry for entry in timeline if str(entry.get("advisor") or "")
    ]
    verdicts["the report attaches the advice to the round it was given in"] = (
        bool(lead_rows)
        and bool(timeline)
        and len(rounds_with_advice) == len(lead_rows)
        and all(
            str(entry.get("advisor") or "") for entry in rounds_with_advice
        )
        and all(
            ADVISOR_REASON in str(entry.get("advisor") or "")
            for entry in rounds_with_advice
        )
    )

    # Once per round - not twice. The timeline hangs the advice on its
    # round, and the run-level fallback must not print it a second
    # time. A duplicated answer is a report that says the same thing
    # twice, which is how a reader stops trusting the page.
    advice_lines = [
        line
        for line in markdown.splitlines()
        if ADVISOR_WORD in line
    ]
    verdicts["the advisor's answer reaches the report once per round, not twice"] = (
        bool(lead_rows)
        and len(advice_lines) == len(lead_rows)
        and all(
            ADVISOR_REASON in line for line in advice_lines
        )
    )

    # A run that never consulted an advisor must not invent one. The
    # page has to be silent about an advisor that was never there.
    silent = facts.get("silent") or {}
    silent_markdown = str(silent.get("markdown") or "")
    verdicts["a run without an advisor says nothing about one"] = (
        not list(silent.get("lead_rows") or [])
        and ADVISOR_WORD not in silent_markdown
        and ADVISOR_REASON not in silent_markdown
    )
    verdicts[
        "the advisor's answer does not appear in a run that never consulted one"
    ] = (
        ADVISOR_REASON not in silent_markdown
        and ADVISOR_NOTE not in silent_markdown
    )

    # The single-round run: the loop did not iterate, so the report
    # takes the other branch - and the advisor's answer still has to
    # be there. A branch that forgets the advisor is exactly the kind
    # of hole that only shows up in a run that closed in one go.
    single = facts.get("single") or {}
    single_markdown = str(single.get("markdown") or "")
    verdicts[
        "the report shows the advisor's answer when the loop did not iterate"
    ] = (
        bool(single.get("lead_rows"))
        and ADVISOR_REASON in single_markdown
        and ADVISOR_WORD in single_markdown
    )
    return verdicts


def _verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """The same conclusions, read from the payload instead of the text.

    Two readings that agree are a fact; one reading is a guess about
    how the words happen to be spelled.
    """
    verdicts: Dict[str, bool] = {}
    run = _run_of(facts)
    lead_rows = list(facts.get("lead_rows") or [])
    timeline = _timeline_of(facts)
    advisories = list(run.get("advisories") or [])

    verdicts["the report shows the advisor's own reason"] = (
        ADVISOR_REASON in str(run.get("advisor_reason") or "")
        or any(
            ADVISOR_REASON in str(item.get("reason") or "")
            for item in advisories
        )
    )
    verdicts["the report shows what the advisor decided"] = (
        str(run.get("advisor_decision") or "") == "revise"
        or any(
            str(item.get("decision") or "") == "revise"
            for item in advisories
        )
        or any(
            str(entry.get("advisor_decision") or "") == "revise"
            for entry in timeline
        )
    )
    verdicts["the report shows the advisor's judgement of the criterion"] = (
        any(
            str(item.get("id") or "") == "c1"
            and ADVISOR_NOTE in str(item.get("note") or "")
            for advice in advisories
            for item in (advice.get("judgements") or [])
            if isinstance(item, dict)
        )
        or any(
            any(
                str(item.get("id") or "") == "c1"
                and ADVISOR_NOTE in str(item.get("note") or "")
                for item in (entry.get("advisor_judgements") or [])
                if isinstance(item, dict)
            )
            for entry in timeline
        )
    )
    verdicts["the report attaches the advice to the round it was given in"] = (
        bool(lead_rows)
        and bool(advisories)
        and len(advisories) == len(lead_rows)
        and [
            int(item.get("iteration") or 0) for item in advisories
        ] == [
            int(entry.get("iteration") or 0)
            for entry in timeline
            if str(entry.get("advisor") or "")
        ]
        and all(
            ADVISOR_REASON in str(entry.get("advisor_reason") or "")
            for entry in timeline
            if str(entry.get("advisor") or "")
        )
    )
    verdicts[
        "the advisor's answer reaches the report once per round, not twice"
    ] = (
        bool(lead_rows)
        and int(run.get("advisories_count") or 0) == len(lead_rows)
        and len(advisories) == len(lead_rows)
    )

    silent = facts.get("silent") or {}
    silent_run = list(silent.get("runs") or [])
    silent_run = silent_run[0] if silent_run else {}
    verdicts["a run without an advisor says nothing about one"] = (
        not list(silent.get("lead_rows") or [])
        and int(silent_run.get("advisories_count") or 0) == 0
        and not list(silent_run.get("advisories") or [])
    )
    verdicts[
        "the advisor's answer does not appear in a run that never consulted one"
    ] = (
        not list(silent.get("lead_rows") or [])
        and not any(
            str(entry.get("advisor") or "") for entry in (silent_run.get("timeline") or [])
        )
    )

    single = facts.get("single") or {}
    single_run = list(single.get("runs") or [])
    single_run = single_run[0] if single_run else {}
    verdicts[
        "the report shows the advisor's answer when the loop did not iterate"
    ] = (
        bool(single.get("lead_rows"))
        and int(single_run.get("advisories_count") or 0) >= 1
        and any(
            ADVISOR_REASON in str(entry.get("advisor") or "")
            for entry in (single_run.get("timeline") or [])
        )
    )
    return verdicts


def _facts_of(root: pathlib.Path) -> Dict[str, Any]:
    """Everything the gate looks at, gathered in one place."""
    facts: Dict[str, Any] = {}
    facts.update(_observe(root / "multi"))
    facts["silent"] = _observe(
        root / "silent", silent_verifier=False
    )
    facts["single"] = _observe(
        root / "single", max_iterations=1
    )
    return facts


def check_the_report_shows_the_advisor(iterations: int = 2) -> Dict[str, Any]:
    """The sixty-first gate."""
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        rounds += 1
        root = pathlib.Path(tempfile.mkdtemp(prefix="mas-selftest-advisor-"))
        try:
            try:
                facts = _facts_of(root)
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the check broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                facts = {}

            verdicts = _verdicts(facts)
            independent = _verdicts_independent(facts)
            for label in REPORT_ADVISOR_OBSERVATIONS:
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
            # The break is on the **collection**: the advisor's rows
            # stop being collected by the report, so the report
            # cannot see them again. That is exactly what the code
            # did before this release. The ledger still holds the
            # answer - which is the point: the report is blind while
            # the truth is on disk.
            import mas.report as report_module

            real_summary = report_module.run_summary

            def _no_advisors(rows, *args, _real=real_summary, **kwargs):
                """The report collects the advisor's rows and then drops them."""
                summary = _real(rows, *args, **kwargs)
                summary["advisories"] = []
                summary["advisories_count"] = 0
                timeline = summary.get("timeline") or []
                for entry in timeline:
                    entry["advisor"] = ""
                    entry["advisor_decision"] = ""
                    entry["advisor_reason"] = ""
                    entry["advisor_judgements"] = []
                return summary

            try:
                report_module.run_summary = _no_advisors
                broken: Dict[str, bool] = {}
                try:
                    broken = _verdicts(
                        _facts_of(
                            pathlib.Path(
                                tempfile.mkdtemp(prefix="mas-selftest-advisor-broken-")
                            )
                        )
                    )
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                report_module.run_summary = real_summary

            if broken.get("the report shows the advisor's own reason") or (
                broken.get(
                    "the report attaches the advice to the round it was given in"
                )
            ):
                failures.append(
                    "round %d: the gate did not notice that the advisor's "
                    "answer stops reaching the report - it cannot fail, so "
                    "it does not watch" % (round_index + 1)
                )
        finally:
            pass

    return {
        "name": "report_shows_the_advisor",
        "title": "the advisor's own words have to reach the report",
        "skipped": False,
        "invariant": (
            "the advisor's answer is in the ledger, in the ticket and in "
            "the replay trace - and the report, the page a human reads, "
            "never collects the advisor's rows at all, so a run that "
            "consulted an advisor reads like a run that never did"
        ),
        "detail": (
            "the report showed the reflection of every round and never "
            "the advice of the advisor; the page a human reads after "
            "the run could not see what the advisor had concluded"
        ),
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_the_report_shows_the_advisor.CHECK_NAME = (  # type: ignore[attr-defined]
    "report_shows_the_advisor"
)
