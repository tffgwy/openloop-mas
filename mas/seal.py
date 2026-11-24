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


def verify_seal(seal: Any, artifact: Optional[str] = None) -> Dict[str, Any]:
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

    if report["problems"] or report["drifted_files"] or report["missing_files"]:
        report["state"] = "drifted"
        report["ok"] = False
        return report

    report["state"] = "holds"
    report["ok"] = True
    return report


def seal_report(rows: Any) -> Dict[str, Any]:
    """Check every sealed closure in a ledger.

    Rows are the ledger entries. The seals are read from the
    ``run_end`` entries, and every seal is verified on its own.
    """
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
        outcome = verify_seal(seal, artifact)
        outcome["run_id"] = str(row.get("run_id") or outcome.get("run_id") or "")
        outcome["artifact_path"] = str(row.get("artifact_path") or "")
        results.append(outcome)

    drifted = [r for r in results if not r.get("ok")]
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
