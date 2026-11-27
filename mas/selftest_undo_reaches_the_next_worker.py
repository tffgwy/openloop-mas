# -*- coding: utf-8 -*-
"""The sixty-second gate: the approvals a human took back have to reach
the next worker.

The gap
------
The undo of an approval is a **human** action that happens **outside**
the loop: a person approves a change, sees that it was wrong, and
pulls it back with ``mas drafts undo`` - usually minutes or hours
later, possibly while the loop is still running.

The fact was recorded. It is immutable, it is in the audit chain and
in the ledger, and it reached every surface a *person* reads. It
reached **no model at all**. Measured, with a probe provider:

    === worker #2 (the one after the human took the change back) ===
      sees the human's own note  : False
      sees that it was taken back: False

So the loop was told "rewrite the deliverable to address the issues
above", was handed the previous deliverable and the reflector's own
words, and knew **nothing** about the fact that a human had already
refused this very change. The loop proposed the same thing again. The
person refused it again. And again.

The second gap is the same root cause seen from the other side: a
resumed loop is a **new run** with a **new run id**, and the undo is
hung on the run that proposed the draft. A lookup by the new run's id
alone would find nothing - so the fact would die at exactly the
moment it mattered most: the moment somebody took the problem over.

The fix
------
The chain is read for the whole **thread** of a run, on every
iteration, and the fact travels into the next prompt in a block of
its own - with the counts, with the files, and above all with the
human's own note, which is the one part of the undo that no machine
can produce.

What does **not** change: the undo still puts the file back byte for
byte, the draft still survives the undo so the same change can be
approved again, and the approval still refuses to overwrite newer
work. This gate watches the *delivery* of the fact, not the undo
itself.

This gate watches:

* the next worker is shown the section about the taken-back changes;
* the next worker sees the human's own reason, in the human's words;
* the raw state of that prompt carries the advice too;
* the undo is counted **once** - the same chain is reachable from two
  folders, and a count that is twice the truth is worse than no count
  at all;
* a run that has nothing to do with the thread does **not** see the
  fact;
* a run that takes the thread over **does** see it;
* an approval that was taken back and then approved again is still on
  the record - "once was wrong" is a fact about the thread, not about
  the newest state of one file.

The break is on the **wiring**: the fact stops being read, or stops
being carried. The gate has to notice.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import tempfile
from typing import Any, Dict, List, Optional, Tuple

#: The observations the gate makes.
UNDO_REACHES_THE_NEXT_WORKER_OBSERVATIONS = (
    "the next worker is shown the section about the changes a human took back",
    "the next worker sees the human's own reason",
    "the next worker's raw state carries the undo advice",
    "the undo is counted once, not twice",
    "an unrelated run does not see the fact",
    "a run that takes the thread over still sees the fact",
    "an approval taken back and then approved again is still on the record",
)

#: The note the human wrote when they took the change back. The marker
#: is unique on purpose: a repeated string would also show up in the
#: tail of a clipped text, and the gate would read a broken world as a
#: working one.
UNDO_NOTE = "human-undo-note-marker-UNDO-REACHES-UNIQUE-120"

#: The heading of the block. It is the same word in both languages on
#: purpose - the heading is what the gate looks for, and it must not
#: have to know which language the team speaks.
SECTION_MARK = "CHANGES A HUMAN TOOK BACK"

#: The run that owns the taken-back approval.
OWNING_RUN = "R-undo"

#: A run that has nothing to do with the thread.
UNRELATED_RUN = "R-other"

#: The run that takes the thread over.
TAKEOVER_RUN = "R-new"

#: How many times the undo was taken.
TIMES_RE = re.compile(r"(\d+)\s+times")

#: How many lines were given back.
LINES_RE = re.compile(r"(\d+)\s+lines given back")


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
    """A provider that records every prompt it was given.

    The answers are deliberately dull - the gate is not interested in
    the artifact, it is interested in what the worker was *told*.
    """

    name = "probe"
    model = "probe-model"

    def __init__(self) -> None:
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
        if role == "planner":
            text = json.dumps({"summary": "s", "plan": ["a"], "risks": []})
        elif role == "reflector":
            text = json.dumps(
                {"summary": "r", "issues": ["i"], "next_action": "x"}
            )
        elif role == "verifier":
            text = json.dumps({"verdict": "fail", "scores": [], "issues": []})
        else:
            text = "deliverable body"
        return {"text": text, "usage": usage, "raw": {}}


def _config(root: pathlib.Path) -> Dict[str, Any]:
    """A configuration that keeps the run inside the gate's own folder."""
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True)
    return {
        "provider": "mock",
        "model": "probe-model",
        "state_dir": str(state),
        # No named workspace: every run gets a folder of its own under
        # the state directory. That is what makes the "unrelated run"
        # observation mean something - the two runs are genuinely
        # separate, and only the *thread* links them.
        "workspace": "",
        "budget": {
            "max_iterations": 1,
            "max_tokens": 1000000,
            "deadline_sec": 60,
        },
        "orchestration": {
            "parallel_workers": 1,
            "reviewers": 1,
            "review_mode": "all",
        },
    }


def _prepare_workspace(
    root: pathlib.Path,
    run_id: str = OWNING_RUN,
) -> Tuple[pathlib.Path, str]:
    """A change that a human approved and then took back.

    This is a *real* undo, not a written-down one: the file is
    changed, the change is approved, and then it is taken back with a
    note. The note is the part no machine can produce.
    """
    from mas.drafts import approve_draft, stage_draft, undo_draft

    workspace = root / "state" / "artifacts" / run_id
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "notes.md").write_bytes(b"old content\n")
    draft = stage_draft(
        workspace,
        "notes.md",
        b"new content\n",
        reason="the loop proposed a change",
        source="engine",
        run_id=run_id,
        task_id=run_id + "-1",
    )
    approve_draft(workspace, str(draft.get("id")), note="looks right")
    undo_draft(workspace, str(draft.get("id")), note=UNDO_NOTE)
    return workspace, str(draft.get("id"))


def _run(
    root: pathlib.Path,
    run_id: str,
    thread_runs: Optional[List[str]] = None,
) -> str:
    """Run one real loop and return the prompt of its worker."""
    from mas.engine import LoopEngine

    config = _config(root)
    probe = _Probe()
    with _Env(str(config["state_dir"]), str(root / "config.json")):
        engine = LoopEngine(
            config,
            provider=probe,
            team={"language": "en"},
        )
        engine.run(
            goal="make a thing",
            criteria=[{"id": "c1", "desc": "must contain the keyword"}],
            run_id=run_id,
            thread_runs=thread_runs,
            max_iterations=1,
        )
    workers = [prompt for role, prompt in probe.calls if role == "worker"]
    return workers[-1] if workers else ""


def _state_of(prompt: str) -> Dict[str, Any]:
    """The raw state a prompt carried."""
    marker = "<<MAS_STATE>>"
    if not prompt or marker not in prompt:
        return {}
    tail = prompt.split(marker, 1)[1]
    end = tail.find("<<END>>")
    if end == -1:
        return {}
    try:
        data = json.loads(tail[:end].strip())
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _section(prompt: str) -> str:
    """The block about the taken-back changes, as text.

    The block runs from its own heading to the next heading. A count
    that were read out of the whole prompt would also be read out of
    the *state* - and the state is a different channel.
    """
    index = prompt.find(SECTION_MARK)
    if index == -1:
        return ""
    rest = prompt[index:]
    following = rest.find("\n### ", 1)
    return rest if following == -1 else rest[:following]


def _facts_of(root: pathlib.Path) -> Dict[str, Any]:
    """Everything the gate looks at, gathered in one place."""
    from mas.drafts import approve_draft

    facts: Dict[str, Any] = {}
    # The environment is borrowed for the **whole** gathering, and not
    # only for the runs.
    #
    # An approval writes a copy of its decision into the *main
    # ledger*, and the ledger's location is found through the
    # configuration - which reads the environment. So an approval made
    # outside the borrowed environment lands in the **user's** ledger:
    # the gate would write its own decisions into the machine it is
    # supposed to be watching, and the suite's own isolation gate would
    # (correctly) report it.
    config = _config(root)
    with _Env(str(config["state_dir"]), str(root / "config.json")):
        workspace, draft_id = _prepare_workspace(root)
        # The run that owns the taken-back approval.
        facts["same"] = {"prompt": _run(root, OWNING_RUN)}
        # A run that has nothing to do with the thread.
        facts["unrelated"] = {"prompt": _run(root, UNRELATED_RUN)}
        # A run that takes the thread over: a *new* run, with a new id.
        facts["takeover"] = {
            "prompt": _run(root, TAKEOVER_RUN, thread_runs=[OWNING_RUN])
        }
        # The same change, approved again after the undo. The draft
        # survives the undo on purpose, and the chain remembers the
        # whole history - so "this was once wrong" has to still be
        # there.
        approve_draft(workspace, draft_id, note="approved again")
        facts["reapproved"] = {"prompt": _run(root, OWNING_RUN)}
    return facts


def _verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """What the gate believes, read from the text of the prompts.

    This reading looks at the prompt as text: what a person - or a
    model - would actually read.
    """
    verdicts: Dict[str, bool] = {}
    same = str((facts.get("same") or {}).get("prompt") or "")
    section = _section(same)
    times = TIMES_RE.search(section)
    lines = LINES_RE.search(section)
    verdicts[
        "the next worker is shown the section about the changes a human took back"
    ] = SECTION_MARK in same
    verdicts["the next worker sees the human's own reason"] = (
        UNDO_NOTE in same
    )
    verdicts["the next worker's raw state carries the undo advice"] = (
        '"undone_advice"' in same
    )
    verdicts["the undo is counted once, not twice"] = bool(
        times
        and lines
        and int(times.group(1)) == 1
        and int(lines.group(1)) >= 1
    )

    unrelated = str((facts.get("unrelated") or {}).get("prompt") or "")
    verdicts["an unrelated run does not see the fact"] = (
        SECTION_MARK not in unrelated and UNDO_NOTE not in unrelated
    )

    takeover = str((facts.get("takeover") or {}).get("prompt") or "")
    verdicts["a run that takes the thread over still sees the fact"] = (
        SECTION_MARK in takeover and UNDO_NOTE in takeover
    )

    reapproved = str((facts.get("reapproved") or {}).get("prompt") or "")
    reapproved_times = TIMES_RE.search(_section(reapproved))
    verdicts[
        "an approval taken back and then approved again is still on the record"
    ] = (
        UNDO_NOTE in reapproved
        and bool(reapproved_times)
        and int(reapproved_times.group(1)) == 1
    )
    return verdicts


def _verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """A second reading, made from the raw state the roles were given.

    The first reading looks at the prompt as text. This one parses
    the ``<<MAS_STATE>>`` block and asks what the state itself
    carried - because the brief and the state are two different
    channels, and a fact that reaches one of them is not necessarily
    in the other.
    """
    verdicts: Dict[str, bool] = {}
    same_state = _state_of(str((facts.get("same") or {}).get("prompt") or ""))
    advice = same_state.get("undone_advice") or {}
    if not isinstance(advice, dict):
        advice = {}
    verdicts[
        "the next worker is shown the section about the changes a human took back"
    ] = bool(advice)
    verdicts["the next worker sees the human's own reason"] = (
        UNDO_NOTE in json.dumps(advice, ensure_ascii=False)
    )
    verdicts["the next worker's raw state carries the undo advice"] = (
        "undone_advice" in same_state
    )
    verdicts["the undo is counted once, not twice"] = (
        int(advice.get("taken_back") or 0) == 1
        and int(advice.get("reverted_lines") or 0) >= 1
    )

    unrelated_state = _state_of(
        str((facts.get("unrelated") or {}).get("prompt") or "")
    )
    verdicts["an unrelated run does not see the fact"] = not bool(
        unrelated_state.get("undone_advice")
    )

    takeover_state = _state_of(
        str((facts.get("takeover") or {}).get("prompt") or "")
    )
    takeover_advice = takeover_state.get("undone_advice") or {}
    if not isinstance(takeover_advice, dict):
        takeover_advice = {}
    verdicts["a run that takes the thread over still sees the fact"] = (
        bool(takeover_advice)
        and UNDO_NOTE in json.dumps(takeover_advice, ensure_ascii=False)
    )

    reapproved_state = _state_of(
        str((facts.get("reapproved") or {}).get("prompt") or "")
    )
    reapproved_advice = reapproved_state.get("undone_advice") or {}
    if not isinstance(reapproved_advice, dict):
        reapproved_advice = {}
    verdicts[
        "an approval taken back and then approved again is still on the record"
    ] = (
        bool(reapproved_advice)
        and int(reapproved_advice.get("taken_back") or 0) == 1
        and UNDO_NOTE in json.dumps(reapproved_advice, ensure_ascii=False)
    )
    return verdicts


def check_undo_reaches_the_next_worker(iterations: int = 3) -> Dict[str, Any]:
    """The sixty-second gate."""
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        rounds += 1
        root = pathlib.Path(tempfile.mkdtemp(prefix="mas-selftest-undo2-"))
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
            for label in UNDO_REACHES_THE_NEXT_WORKER_OBSERVATIONS:
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
            # The break is on the **wiring**: the fact is no longer
            # read off the chain, or it is read and then dropped
            # before it reaches the prompt. That is exactly what the
            # code did before this release - the undo was immutable
            # and visible to every person, and invisible to every
            # model.
            import mas.engine as engine_module

            real_read = engine_module.LoopEngine._undone_advice

            def _nothing_read(self, _real=real_read):
                """Exactly what the code did before this release."""
                return {}

            try:
                engine_module.LoopEngine._undone_advice = _nothing_read
                broken: Dict[str, bool] = {}
                try:
                    broken = _verdicts(
                        _facts_of(
                            pathlib.Path(
                                tempfile.mkdtemp(prefix="mas-selftest-undo2-broken-")
                            )
                        )
                    )
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                engine_module.LoopEngine._undone_advice = real_read

            # The break has to be *visible*, and it has to be visible
            # as a broken observation - not as a crash. An empty
            # ``broken`` means the broken run did not even produce a
            # reading, and a gate that accepts that would pass
            # vacuously: it would report "I watched it fail" for a
            # break it never actually saw. So the empty reading is a
            # failure of the gate itself.
            if not broken:
                failures.append(
                    "round %d: the gate did not notice that the taken-back "
                    "approvals stop reaching the next worker - the broken "
                    "run produced no reading at all, so the gate cannot "
                    "prove that it can fail" % (round_index + 1)
                )
            elif broken.get(
                "the next worker sees the human's own reason"
            ) or broken.get(
                "a run that takes the thread over still sees the fact"
            ):
                failures.append(
                    "round %d: the gate did not notice that the taken-back "
                    "approvals stop reaching the next worker - it cannot "
                    "fail, so it does not watch" % (round_index + 1)
                )
        finally:
            pass

    return {
        "name": "undo_reaches_the_next_worker",
        "title": "the approvals a human took back have to reach the next worker",
        "skipped": False,
        "invariant": (
            "a human approves a change, takes it back, and the undo is "
            "recorded immutably - but the fact reaches every surface a "
            "person reads and no model at all, so the next attempt "
            "proposes the very same change again, and the person has to "
            "refuse it again, and again"
        ),
        "detail": (
            "the worker of the next attempt was told to fix the issues "
            "above and nothing about the change a human had already "
            "taken back; the human's own note - the one part of the "
            "undo no machine can produce - reached nobody"
        ),
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_undo_reaches_the_next_worker.CHECK_NAME = (  # type: ignore[attr-defined]
    "undo_reaches_the_next_worker"
)
