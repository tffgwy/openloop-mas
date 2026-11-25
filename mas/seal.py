# -*- coding: utf-8 -*-
"""v0.7.88: the closure is sealed to the bytes it was decided on.

The gap
-------
A closed loop claims something very strong: *the same artifact and the
same standard always produce the same verdict*. That claim was made
about the artifact text, but the ledger only ever recorded the
deliverable's hash and the acceptance criteria lived in the engine's
memory. Two consequences followed:

* the criteria were never written down, so a reader could not recompute
  the verdict - they could only trust it;
* the files a worker wrote were recorded by *length*, not by content,
  so a file could be rewritten after the run and the ledger would not
  notice. The run would still say ``closed / pass``.

Both holes break the same promise. A verdict that cannot be recomputed
is a claim, not a proof.

The seal
--------
At the moment the loop decides, it writes down *what it decided on*:

* the acceptance criteria, in full;
* the sha256 of the deliverable's bytes;
* the sha256 of every file the run wrote, and its length;
* the per-criterion verdicts.

``verify_seal`` then re-derives the verdict from the sealed evidence
alone: it re-reads the files from the disk, re-runs the machine checks
against the sealed criteria, and compares the result with the sealed
verdict. A file that was edited afterwards shows up as a drift; a
standard that cannot be re-run shows up as *unrecomputable* rather
than as a silent pass.

The honest limit
----------------
Only criteria with a machine check can be re-run. A criterion a model
judged is sealed as the model's answer, with its source, and it is
reported as *model-judged* - not recomputed. A seal therefore proves
"the machine-checked parts still hold", and it says out loud which
parts rest on a model.

The seal is a record, not a notarisation. Anybody who rewrites the
seal and the ledger together can produce a seal that verifies again.
What it buys is that an *ordinary* edit - somebody fixing a file after
the fact - becomes visible.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
from typing import Any, Dict, List, Optional, Tuple

from mas.util import now_iso

#: The version of the seal format. A reader that does not know this
#: version says so instead of guessing what a missing field means.
SEAL_VERSION = 1

#: The ledger role of the entry that carries the seal.
SEAL_ROLE = "run_end"

#: The state of a closure that never judged anything.
#:
#: A run that stopped on the budget ceiling before it reached a
#: scoring round has no verdict. Older ledgers sealed it anyway -
#: with an empty score list and a hard-coded "fail" - and the report
#: read that seal as a conclusion that had drifted. That accused a
#: run that never said anything of having lied about it.
#:
#: So the state exists: a seal with no verdict is *not* a drifted
#: conclusion, it is the absence of one. It is neither good nor bad -
#: there is nothing to recompute.
SEAL_STATE_NO_VERDICT = "no-verdict"

#: The state of a closure whose files were changed afterwards - by a
#: human, with an approval on the record.
#:
#: The seal compares the bytes on the disk with the bytes it was
#: decided on. When they differ, the honest reading is "somebody
#: changed this after the run closed" - and until v0.11.2 there was
#: only one word for that: *drifted*. A drifted conclusion is an
#: accusation. It says "the run lied about what it decided on".
#:
#: But a human who reviews a deliverable and approves a change is
#: *doing the loop's job*, not attacking it. The approval is on the
#: record - it is in the hash-linked audit chain, with the hash of
#: the bytes it wrote down. So the seal can tell the two apart:
#:
#: * the bytes on the disk are the bytes of an **approved** change ->
#:   the conclusion still holds, and the file was *taken over* by a
#:   person. The state is ``taken-over``;
#: * the bytes on the disk are **anything else** -> nobody approved
#:   that, so the conclusion drifted. The lock is not weakened.
#:
#: The two are not the same claim, and the difference is the whole
#: point: "a person changed this and said so" and "somebody changed
#: this behind our back" must never be filed under one word.
SEAL_STATE_TAKEN_OVER = "taken-over"


def seal_has_verdict(seal: Any) -> bool:
    """Whether this seal carries a conclusion at all.

    A seal without a single score row is a seal about nothing. This
    is the question the report asks before it counts a seal as
    drifted - and it is asked of the *seal*, not of the run, so an
    old ledger is read the same way as a new one.
    """
    if not isinstance(seal, dict) or not seal:
        return False
    scores = seal.get("scores")
    if not isinstance(scores, list):
        return False
    return any(isinstance(item, dict) for item in scores)


def _event_of(row: Dict[str, Any]) -> str:
    """The event name of a row - chain entries and ledger rows differ."""
    return str(row.get("event") or row.get("role") or "").strip()


def _same_workspace(left: Any, right: Any) -> bool:
    """Whether two workspace names point at the same folder.

    The same folder can be written down in more than one way - a
    short path, a long path, a path with a different case - and the
    decision records and the seals are written by different parts of
    the machine. Comparing them as plain strings would therefore
    miss real approvals and invent fake ones.

    The comparison is made on the *resolved* path, so it is a
    statement about the folder on the disk and not about the spelling
    of its name. Two empty names are the same (both mean "no
    workspace"), but an empty name never matches a real one: a
    decision that does not say where it happened cannot be used to
    excuse a change in a known folder.
    """
    first = str(left or "").strip()
    second = str(right or "").strip()
    if not first or not second:
        return not first and not second
    if first == second:
        return True
    try:
        return os.path.normcase(
            str(pathlib.Path(first).resolve())
        ) == os.path.normcase(str(pathlib.Path(second).resolve()))
    except (OSError, ValueError):
        return False


def approved_bytes(
    rows: Any,
    workspace: str = "",
) -> Dict[str, List[Dict[str, Any]]]:
    """The bytes a human approved, read off the audit chain.

    The map is keyed by the sha256 of the approved bytes, and every
    entry says *which* file was changed, *in which folder* and *when*
    it was approved, so the seal can name the approval instead of
    only counting it. One hash can carry several entries: the same
    bytes can be approved in two different folders, and those are two
    different facts.

    Where the evidence comes from
    ----------------------------
    The **audit chain** is the authority. It is a hash-linked record
    - every entry carries the hash of the one before it - so an
    approval that was edited on its own stops verifying, and a
    broken chain is visible instead of silent.

    The main ledger is only a **fallback**, and only for a workspace
    that has no chain at all - a workspace from before the chain
    existed. Where a chain exists it wins, and where the chain does
    *not* verify, nothing from that workspace is believed: an
    approval nobody can verify does not excuse a change.

    Two rules keep the rest honest:

    * **Only approvals count.** An undo takes the approval back, so
      the bytes of a change that was taken back are no longer
      standing - they are not in the map.
    * **The undo that cancels it has to be read first.** The
      decisions are read in the order they happened, so the pairing
      is positional - the same way ``mas undos`` pairs them - and a
      change that was approved, taken back and approved again leaves
      exactly one standing approval.
    """
    from mas.undos import APPROVE_EVENT, decision_rows, pair_decisions

    result: Dict[str, List[Dict[str, Any]]] = {}

    def _collect(source_rows: Any, default_workspace: str = "") -> None:
        """Add the standing approvals of one source to the map."""
        # The folder is stamped onto every row before anything else
        # happens. The chain's own entries do not carry it - the chain
        # *is* the folder - and the pairing of an approval with the
        # undo that takes it back is done by (folder, draft), so a row
        # without a folder would be paired with the wrong neighbour.
        normalized: List[Dict[str, Any]] = []
        for row in source_rows or []:
            if not isinstance(row, dict):
                continue
            item = dict(row)
            if not str(item.get("workspace") or "").strip():
                item["workspace"] = str(default_workspace or "")
            normalized.append(item)
        ordered = decision_rows(normalized)
        # The undos that cancel an approval are found first, so the
        # approval they cancel can be left out.
        records, _unmatched = pair_decisions(ordered)
        taken_back = {
            (str(record.get("workspace") or ""), str(record.get("draft_id") or ""))
            for record in records
        }
        for row in ordered:
            if _event_of(row) != APPROVE_EVENT:
                continue
            digest = str(row.get("approved_sha256") or "")
            if not digest:
                continue
            name = str(row.get("workspace") or default_workspace or "")
            key = (name, str(row.get("draft_id") or ""))
            if key in taken_back:
                # The approval was taken back. The bytes it wrote are
                # no longer standing - and a change that was undone is
                # exactly the kind of edit that must not be excused.
                continue
            entry = {
                "sha256": digest,
                "path": str(row.get("path") or ""),
                "workspace": name,
                "draft_id": key[1],
                "note": str(row.get("note") or ""),
                "ts": str(row.get("ts") or ""),
            }
            # The same bytes can be approved twice - a person approves,
            # changes their mind, approves again. The newest approval
            # is the one that stands, so it wins the key.
            bucket = result.setdefault(digest, [])
            bucket[:] = [
                e for e in bucket
                if not (e["workspace"] == name and e["draft_id"] == key[1])
            ]
            bucket.append(entry)

    # The workspaces this ledger knows about.
    workspaces: List[str] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("workspace") or "").strip()
        if name and name not in workspaces:
            workspaces.append(name)
    if workspace and workspace not in workspaces:
        workspaces.append(workspace)

    from mas.audit import chain_for

    covered: List[str] = []
    for name in workspaces:
        try:
            chain = chain_for(name)
        except OSError:
            chain = None
        if chain is None:
            continue
        covered.append(name)
        verification = chain.verify()
        if not verification.get("ok"):
            # The chain of this workspace is broken. Nothing from it
            # is believed: an approval nobody can verify must not be
            # able to excuse a change.
            continue
        # The chain's own entries do not carry the folder they live
        # in, so it is filled in from the chain itself.
        _collect(chain.entries(), str(getattr(chain, "workspace", "") or name))

    # The ledger copy is used only for the workspaces that have no
    # chain of their own - the ones from before the chain existed.
    fallback_rows = [
        row
        for row in (rows or [])
        if isinstance(row, dict)
        and str(row.get("workspace") or "").strip() not in covered
    ]
    _collect(fallback_rows)
    return result


def _approval_for(
    approvals: Optional[Dict[str, List[Dict[str, Any]]]],
    digest: str,
    workspace: str,
    path: str = "",
) -> Optional[Dict[str, Any]]:
    """The approval that wrote these bytes - or ``None``.

    The bytes have to match *and* the approval has to have happened
    in the same folder, for the same file. An approval of a file in
    another folder is not an approval of this one, even when the
    bytes are identical - two files can hold the same content and
    be two different files.
    """
    if not approvals or not digest:
        return None
    candidates = approvals.get(digest)
    if not isinstance(candidates, list):
        return None
    for entry in candidates:
        if not isinstance(entry, dict):
            continue
        if not _same_workspace(entry.get("workspace"), workspace):
            continue
        if path:
            approved_path = str(entry.get("path") or "")
            if approved_path and approved_path.replace("\\", "/") != str(path).replace("\\", "/"):
                continue
        return entry
    return None


def approvals_in_workspace(workspace: Any) -> Dict[str, List[Dict[str, Any]]]:
    """The standing approvals of one workspace, read off its chain.

    This is what a surface that holds *one* seal - and not the whole
    ledger - reads. The report has a run's rows, not the machine's
    ledger, so it cannot gather the decisions itself; without this the
    report would call every approved change a drift, and the same
    closure would be called "still holds" by one surface and "drifted"
    by another. The rule of this project is that every face says the
    same thing about the same thing, so the evidence is gathered in
    one place and read by all of them.

    A workspace with no chain yields nothing - there is no approval
    to believe.
    """
    if not workspace:
        return {}
    from mas.audit import chain_for

    try:
        chain = chain_for(workspace)
    except OSError:
        return {}
    if chain is None:
        return {}
    if not chain.verify().get("ok"):
        # A broken chain is not a source of truth.
        return {}
    return approved_bytes([], workspace=str(getattr(chain, "workspace", "") or workspace))


def sha256_bytes(data: bytes) -> str:
    """The sha256 of some bytes, as a lowercase hex digest."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Any) -> str:
    """The sha256 of a file's bytes, or "" when it cannot be read."""
    try:
        return sha256_bytes(pathlib.Path(path).read_bytes())
    except (OSError, TypeError, ValueError):
        return ""


def canonical(payload: Dict[str, Any]) -> str:
    """The canonical text of a seal - the bytes the digest is over.

    The keys are sorted and the separators are tight, so the same seal
    hashes to the same value on every machine. A digest that changed
    because of the *machine* rather than because of the *content* would
    make the whole seal useless.
    """
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def seal_digest(payload: Dict[str, Any]) -> str:
    """The digest of a seal, without its own digest field."""
    body = {k: v for k, v in payload.items() if k != "sha256"}
    return sha256_bytes(canonical(body).encode("utf-8"))


def file_rows(files: Any) -> List[Dict[str, Any]]:
    """The written files of a run, each with the hash of its bytes.

    The engine's ``files`` list only knows the *length* of every file
    it wrote. Two files of the same length are indistinguishable, so a
    file that was edited after the run - even to the same length - left
    no trace. This adds the content hash.
    """
    rows: List[Dict[str, Any]] = []
    for row in files or []:
        if not isinstance(row, dict):
            continue
        absolute = str(row.get("absolute") or "")
        digest = str(row.get("sha256") or "")
        if not digest and absolute:
            digest = sha256_file(absolute)
        rows.append(
            {
                "path": str(row.get("path") or ""),
                "absolute": absolute,
                "bytes": int(row.get("bytes") or 0),
                "sha256": digest,
            }
        )
    return rows


def build_seal(
    *,
    run_id: str,
    criteria: Any,
    artifact: str,
    artifact_sha256: str,
    files: Any,
    scores: Any,
    verdict: str,
    decision: str,
    workspace: str = "",
) -> Dict[str, Any]:
    """Build the seal of one closed (or failed) run.

    ``criteria`` are the acceptance criteria as they were *used*, the
    ``artifact`` is the deliverable text, ``files`` are the files the run
    wrote, and ``scores`` are the per-criterion verdicts.
    """
    criteria_rows: List[Dict[str, Any]] = []
    for item in criteria or []:
        if isinstance(item, dict):
            criteria_rows.append(dict(item))
    score_rows: List[Dict[str, Any]] = []
    for item in scores or []:
        if isinstance(item, dict):
            score_rows.append(
                {
                    "id": str(item.get("id") or ""),
                    "pass": item.get("pass"),
                    "source": str(item.get("source") or ""),
                    "blocking": bool(item.get("blocking", True)),
                }
            )
    seal: Dict[str, Any] = {
        "version": SEAL_VERSION,
        "run_id": str(run_id or ""),
        "sealed_at": now_iso(),
        "criteria": criteria_rows,
        "artifact_sha256": str(artifact_sha256 or ""),
        "artifact_chars": len(artifact or ""),
        "files": file_rows(files),
        "scores": score_rows,
        "verdict": str(verdict or ""),
        "decision": str(decision or ""),
        "workspace": str(workspace or ""),
    }
    seal["sha256"] = seal_digest(seal)
    return seal


def _recompute_scores(
    criteria: List[Dict[str, Any]],
    artifact: str,
    workspace: str,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Re-run the machine checks of the sealed criteria.

    Only criteria with a machine check can be decided again. A criterion
    without one was judged by a model, and a model cannot be re-run from
    a record - so it is reported as model-judged instead of silently
    counted as a pass.
    """
    from mas.engine import Criterion, evaluate_check

    problems: List[str] = []
    scores: List[Dict[str, Any]] = []
    config = {"workspace": str(workspace or "")}
    for index, raw in enumerate(criteria or []):
        criterion = Criterion.from_raw(raw, index)
        check = criterion.check
        if not check:
            scores.append(
                {
                    "id": criterion.id,
                    "pass": None,
                    "source": "model-judged",
                    "recomputed": False,
                }
            )
            continue
        outcome = evaluate_check(criterion, artifact, config)
        scores.append(
            {
                "id": criterion.id,
                "pass": outcome.get("pass"),
                "source": str(outcome.get("source") or "rule"),
                "recomputed": True,
                "note": str(outcome.get("note") or ""),
            }
        )
    return scores, problems


def verify_seal(
    seal: Any,
    artifact: Optional[str] = None,
    approvals: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    artifact_bytes: Optional[bytes] = None,
) -> Dict[str, Any]:
    """Check a sealed closure against the disk as it stands now.

    The seal is re-derived from three directions:

    * the *digest* - did the seal itself change?
    * the *files* - do the files the run wrote still hold the bytes
      the run wrote?
    * the *verdict* - do the machine checks still decide the same way
      against the same criteria?

    A seal that cannot be recomputed at all is reported as
    ``unrecomputable`` - not as a pass. A verdict that no longer holds
    is reported as ``drifted``.

    The approvals
    -------------
    ``approvals`` are the byte hashes a human approved, read by
    :func:`approved_bytes`. They are what tells *why* a file changed.

    A change that was approved is not a drift: it is a **take-over**,
    and the state says so. A change that was not approved - by
    anybody, for any reason - is still a drift. The lock is not
    weakened: the approval has to be a real one, for this file, in
    this folder.

    ``artifact_bytes`` are the bytes of the deliverable **as they
    are on the disk**. They matter for one reason: the seal records
    the hash of the deliverable's *text*, while an approval records
    the hash of the *bytes* it wrote. On a machine that rewrites
    line endings on the way to the disk, those two hashes are about
    different things and cannot be compared - so the approval would
    never match, and every approved change would be reported as a
    drift. That is the same trap v0.7.77 fell into, one layer up: a
    comparison across two bases is not a comparison at all.
    """
    report: Dict[str, Any] = {
        "ok": False,
        "state": "unknown",
        "run_id": str((seal or {}).get("run_id") or ""),
        "problems": [],
        "drifted_files": [],
        "missing_files": [],
        "recomputed": [],
        "model_judged": [],
        # The files a human took over after the run closed. They are
        # kept apart from the drifted ones: one is a change with a
        # name on it, the other is a change without one.
        "taken_over_files": [],
        "taken_over": [],
        # Notes about the take-over. They are *not* problems: a
        # take-over is a change with a name on it, and a problem
        # would turn it back into the accusation it is not.
        "notes": [],
    }
    if not isinstance(seal, dict) or not seal:
        report["state"] = "missing"
        report["problems"].append("no seal on this closure")
        return report

    version = int(seal.get("version") or 0)
    if version != SEAL_VERSION:
        report["state"] = "unknown-version"
        report["problems"].append(
            "seal format version %s is not the version this reader knows (%s)"
            % (version, SEAL_VERSION)
        )
        return report

    # 1. The seal itself.
    expected = seal_digest(seal)
    stored = str(seal.get("sha256") or "")
    if stored != expected:
        report["state"] = "tampered"
        report["problems"].append(
            "the seal was changed after it was written (digest %s != %s)"
            % (stored[:12] or "none", expected[:12])
        )
        return report

    workspace = str(seal.get("workspace") or "")
    criteria = [c for c in (seal.get("criteria") or []) if isinstance(c, dict)]

    # When the caller did not gather the approvals itself, the seal
    # reads the chain of the folder it was taken in. A surface that
    # holds one seal and not the whole ledger - the report, the
    # status page - then says the same thing about the same closure
    # as the command that has the ledger.
    if approvals is None:
        approvals = approvals_in_workspace(workspace)

    # 2. The files.
    for row in seal.get("files") or []:
        if not isinstance(row, dict):
            continue
        absolute = str(row.get("absolute") or "")
        wanted = str(row.get("sha256") or "")
        if not absolute:
            continue
        path = pathlib.Path(absolute)
        if not path.exists():
            report["missing_files"].append(str(row.get("path") or absolute))
            continue
        current = sha256_file(path)
        if wanted and current != wanted:
            # Somebody changed this file after the run closed. The
            # question is whether a human said so. An approval of
            # exactly these bytes, in exactly this folder, for
            # exactly this file is a take-over; anything else is a
            # drift.
            approval = _approval_for(
                approvals,
                current,
                workspace,
                str(row.get("path") or ""),
            )
            if approval:
                report["taken_over_files"].append(
                    {
                        "path": str(row.get("path") or absolute),
                        "sha256": current,
                        "draft_id": str(approval.get("draft_id") or ""),
                        "approved_at": str(approval.get("ts") or ""),
                        "note": str(approval.get("note") or ""),
                    }
                )
            else:
                report["drifted_files"].append(str(row.get("path") or absolute))

    # 3. The verdict.
    #
    # A seal with no scores has no verdict. It cannot be recomputed -
    # there is nothing to recompute - and it cannot have drifted
    # either: a conclusion that was never reached cannot stop
    # holding. Reporting it as "drifted" accused a run that never
    # judged anything of lying.
    if not seal_has_verdict(seal):
        report["state"] = SEAL_STATE_NO_VERDICT
        report["ok"] = True
        report["problems"].append(
            "this run never reached a verdict, so there is no conclusion "
            "to recompute and none to drift"
        )
        return report

    if artifact is None:
        report["state"] = "unrecomputable"
        report["problems"].append(
            "the deliverable text is not available, so the verdict cannot be re-run"
        )
        return report

    sealed_artifact_sha = str(seal.get("artifact_sha256") or "")
    current_artifact_sha = sha256_bytes((artifact or "").encode("utf-8"))
    if sealed_artifact_sha and current_artifact_sha != sealed_artifact_sha:
        # The deliverable itself changed. That is the same question
        # as for the files the run wrote: was a human the one who
        # changed it, and did they approve it?
        #
        # The deliverable is not one of the "files the run wrote" -
        # it is the artifact itself - so the approval is matched on
        # the bytes and on the folder, and the file name is taken
        # from the approval when there is one.
        # The approval is looked up by the bytes on the disk, because
        # that is the basis an approval is recorded on. When the
        # bytes are not known, the text hash is the only thing left
        # - and on a machine that does not rewrite line endings the
        # two are the same number anyway.
        approval_digest = (
            sha256_bytes(artifact_bytes)
            if artifact_bytes is not None
            else current_artifact_sha
        )
        approval = _approval_for(approvals, approval_digest, workspace)
        if approval:
            report["taken_over"].append(
                {
                    "path": str(approval.get("path") or ""),
                    "sha256": current_artifact_sha,
                    "draft_id": str(approval.get("draft_id") or ""),
                    "approved_at": str(approval.get("ts") or ""),
                    "note": str(approval.get("note") or ""),
                }
            )
            report["state"] = SEAL_STATE_TAKEN_OVER
            report["ok"] = True
            report["notes"].append(
                "the deliverable was changed after the run closed, and a "
                "human approved that change (draft %s) - the conclusion "
                "still holds, but the file was taken over by a person"
                % (approval.get("draft_id") or "unknown")
            )
        else:
            report["state"] = "drifted"
            report["problems"].append(
                "the deliverable changed after the run closed (sha %s != %s)"
                % (current_artifact_sha[:12], sealed_artifact_sha[:12])
            )

    scores, problems = _recompute_scores(criteria, artifact, workspace)
    for problem in problems:
        report["problems"].append(problem)

    sealed_scores = {
        str(item.get("id") or ""): item
        for item in (seal.get("scores") or [])
        if isinstance(item, dict)
    }
    for score in scores:
        cid = str(score.get("id") or "")
        if not score.get("recomputed"):
            report["model_judged"].append(cid)
            continue
        report["recomputed"].append(cid)
        sealed = sealed_scores.get(cid)
        if sealed is None:
            report["problems"].append(
                "criterion %s was sealed without a verdict" % cid
            )
            continue
        if bool(sealed.get("pass")) != bool(score.get("pass")):
            report["problems"].append(
                "criterion %s now decides %s, the seal says %s"
                % (cid, score.get("pass"), sealed.get("pass"))
            )

    # A problem here is a real one: a criterion that now decides
    # differently, or a file that was changed without an approval.
    # The take-over notes are kept out of it on purpose - a note is
    # an explanation, not an accusation.
    if report["problems"] or report["drifted_files"] or report["missing_files"]:
        report["state"] = "drifted"
        report["ok"] = False
        return report

    # Something was taken over by a human, and nothing drifted. The
    # conclusion still holds - the bytes it was decided on are not
    # the bytes on the disk, but the difference is one a person
    # approved and said so about. That is not the same as a drift,
    # and it must not be reported as one.
    if report["taken_over"] or report["taken_over_files"]:
        report["state"] = SEAL_STATE_TAKEN_OVER
        report["ok"] = True
        return report

    report["state"] = "holds"
    report["ok"] = True
    return report


def seal_report(rows: Any) -> Dict[str, Any]:
    """Check every sealed closure in a ledger.

    Rows are the ledger entries. The seals are read from the
    ``run_end`` entries, and every seal is verified on its own.

    The human approvals are read from the same rows, so a change
    that a person approved is told apart from a change that nobody
    approved.
    """
    approvals = approved_bytes(rows)
    results: List[Dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("role") or "") != SEAL_ROLE:
            continue
        seal = row.get("seal")
        if not isinstance(seal, dict) or not seal:
            continue
        artifact = _artifact_from_row(row)
        outcome = verify_seal(seal, artifact, approvals, _artifact_bytes_from_row(row))
        outcome["run_id"] = str(row.get("run_id") or outcome.get("run_id") or "")
        outcome["artifact_path"] = str(row.get("artifact_path") or "")
        results.append(outcome)

    drifted = [r for r in results if not r.get("ok")]
    taken_over = [
        r for r in results if r.get("state") == SEAL_STATE_TAKEN_OVER
    ]
    return {
        "sealed": len(results),
        "ok": not drifted,
        "holds": len([r for r in results if r.get("state") == "holds"]),
        # The closures that never reached a verdict. They are not
        # drifted conclusions - there was no conclusion - and they
        # are counted apart so a reader can see how many closures
        # actually say something.
        "no_verdict": len(
            [r for r in results if r.get("state") == SEAL_STATE_NO_VERDICT]
        ),
        # The closures a human took over: the conclusion still holds,
        # but the file was changed afterwards with an approval on the
        # record. They are counted apart from the drift - one is a
        # change with a name on it, the other is a change without one.
        "taken_over": len(taken_over),
        "drifted": len(drifted),
        "results": results,
    }


def _artifact_from_row(row: Dict[str, Any]) -> Optional[str]:
    """The deliverable text of a run, read from the disk if it is there."""
    path = str(row.get("artifact_path") or "")
    if not path:
        return None
    try:
        return pathlib.Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _artifact_bytes_from_row(row: Dict[str, Any]) -> Optional[bytes]:
    """The deliverable's bytes as they are on the disk, or ``None``.

    The bytes and the text are not the same thing on every machine:
    a file written with ``write_text`` can hold ``\\r\\n`` where the
    text holds ``\\n``. The approval of a change is recorded over the
    bytes, so the comparison has to be made over the bytes too.
    """
    path = str(row.get("artifact_path") or "")
    if not path:
        return None
    try:
        return pathlib.Path(path).read_bytes()
    except OSError:
        return None
