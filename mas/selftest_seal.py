# -*- coding: utf-8 -*-
"""The twenty-eighth gate: a closure is sealed to the bytes it decided on.

The gap
-------
The project promises one thing above all others: *the same artifact and
the same standard always produce the same verdict*. Up to v0.7.87 that
promise was kept in the engine, at the moment of the decision - and
then dropped on the floor. The ledger recorded the deliverable's hash
and the acceptance criteria stayed in the engine's memory. Two things
followed from that:

* nobody could recompute a verdict. The criteria were never written
  down, so a reader could only trust the conclusion;
* the files a worker wrote were recorded by *length*, not by content.
  A file edited after the run - even to the same length - left no trace,
  and the run kept saying ``closed / pass``.

That is the last hole in the loop: the conclusion was not anchored to
the bytes it was based on.

What this gate watches
----------------------
* a closed run seals its closure - the criteria, the deliverable hash,
  the hash of every file it wrote, the per-criterion verdicts;
* the seal can be re-verified from the disk, and the verdict comes out
  the same;
* a file that is edited *after* the closure is reported as drift, and
  the report says so instead of still saying "closed";
* a criterion that only a model could judge is reported as
  model-judged, not as recomputed;
* the command line can ask the question and get an answer.

And the gate proves it can fail: the drift detection is broken on
purpose, and the gate has to notice that it can no longer tell a real
drift from a healthy closure.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import tempfile
from typing import Any, Dict, List

#: The observations the gate makes, and the two readings of them.
SEAL_OBSERVATIONS = (
    "a closed run seals its closure",
    "the seal carries the criteria it was decided on",
    "the files the run wrote are sealed by content",
    "a healthy seal re-verifies from the disk",
    "a file edited after the closure is reported as drift",
    "a model-judged criterion is not reported as recomputed",
    "the command line can ask whether the closure still holds",
)


def _seal_observations(root: Any) -> Dict[str, Any]:
    """Run a real closure offline and look at what it left behind."""
    from mas.engine import LoopEngine
    from mas.providers import Provider
    from mas.seal import seal_report, verify_seal

    class _Writer(Provider):
        name = "selftest-seal"

        def complete(self, *, system: str = "", prompt: str = "", role: str = "worker",
                     expect_json: bool = False, max_tokens: int = 4000) -> Dict[str, Any]:
            usage = {"total_tokens": 10}
            if role == "worker":
                text = (
                    "交付物正文\n"
                    '<<<MAS:FILE path="notes/finding.txt">>>\n'
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
                                {"id": "c2", "pass": True, "note": "ok"},
                                {"id": "c3", "pass": True, "note": "reads well"},
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
        goal="写一份结论",
        criteria=[
            {"id": "c1", "desc": "含正文", "check": {"type": "contains", "arg": "正文"}},
            {"id": "c2", "desc": "文件写出来了", "check": {"type": "file_exists", "arg": "notes/finding.txt"}},
            # A criterion with no machine check: only a model can judge
            # it, so the seal must say so instead of pretending it was
            # recomputed.
            {"id": "c3", "desc": "内容是否通顺"},
        ],
    )
    seen["closed"] = bool(result.closed)
    rows = engine.ledger.entries()
    seen["rows"] = rows

    run_end = [
        row for row in rows if str(row.get("role") or "") == "run_end"
    ]
    seen["run_end_count"] = len(run_end)
    seal = (run_end[-1].get("seal") if run_end else None)
    seen["seal"] = seal if isinstance(seal, dict) else None

    # The seal on its own: does it re-verify?
    artifact = ""
    artifact_path = str((run_end[-1].get("artifact_path") if run_end else "") or "")
    if artifact_path and pathlib.Path(artifact_path).exists():
        artifact = pathlib.Path(artifact_path).read_text(encoding="utf-8")
    seen["artifact"] = artifact
    seen["healthy"] = verify_seal(seal, artifact) if seal else {}

    # Now edit the file the run wrote - *after* the closure - and look
    # at what the seal says about it. The file is edited to the same
    # length on purpose: a length-based record cannot see that.
    written = str(result.workspace or "")
    target = pathlib.Path(written) / "notes" / "finding.txt"
    seen["target_exists"] = target.exists()
    if target.exists():
        from mas.seal import sha256_file

        original = target.read_bytes()
        seen["original_bytes"] = original
        seen["original_hash"] = sha256_file(target)
        tampered = "结论：SELFTST\n".encode("utf-8")
        target.write_bytes(tampered)
        seen["tampered_hash"] = sha256_file(target)
        seen["same_length"] = len(tampered) == len(original)
        seen["drifted"] = verify_seal(seal, artifact) if seal else {}
        report = seal_report(rows)
        seen["report"] = report
        seen["drift_seen"] = bool(
            (report.get("drifted") or 0) >= 1
            or not report.get("ok")
        )
        # Put the file back, so the ledger can be checked again.
        target.write_bytes(original)
        seen["restored_hash"] = sha256_file(target)
        seen["restored"] = verify_seal(seal, artifact) if seal else {}

    # The command line has to be able to answer the question.
    import subprocess
    import sys

    env = dict(os.environ)
    env["MAS_STATE_DIR"] = str(state)
    env["PYTHONPATH"] = str(pathlib.Path(__file__).resolve().parents[1])
    env["PYTHONUTF8"] = "1"
    proc = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "-m",
            "mas",
            "seal",
            "--json",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=120,
    )
    seen["cli_code"] = proc.returncode
    seen["cli_text"] = (proc.stdout or "") + (proc.stderr or "")
    try:
        seen["cli_json"] = json.loads(proc.stdout or "{}")
    except ValueError:
        seen["cli_json"] = {}
    return seen


def _seal_verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """What the gate concludes from what it saw."""
    verdicts: Dict[str, bool] = {}
    seal = facts.get("seal") or {}
    criteria = [c for c in (seal.get("criteria") or []) if isinstance(c, dict)]

    verdicts["a closed run seals its closure"] = (
        bool(facts.get("closed"))
        and bool(seal)
        and int(facts.get("run_end_count") or 0) >= 1
        and bool(str(seal.get("sha256") or ""))
    )

    verdicts["the seal carries the criteria it was decided on"] = (
        len(criteria) >= 2
        and any(
            str(c.get("id") or "") == "c1"
            and isinstance(c.get("check"), dict)
            for c in criteria
        )
    )

    files = [f for f in (seal.get("files") or []) if isinstance(f, dict)]
    verdicts["the files the run wrote are sealed by content"] = (
        bool(files)
        and all(str(f.get("sha256") or "") for f in files)
        and all(
            str(f.get("path") or "").replace("\\", "/") == "notes/finding.txt"
            for f in files
        )
    )

    healthy = facts.get("healthy") or {}
    verdicts["a healthy seal re-verifies from the disk"] = (
        bool(healthy.get("ok"))
        and str(healthy.get("state") or "") == "holds"
    )

    drifted = facts.get("drifted") or {}
    verdicts["a file edited after the closure is reported as drift"] = (
        bool(drifted)
        and not drifted.get("ok")
        and bool(drifted.get("drifted_files"))
        and str(drifted.get("state") or "") == "drifted"
    )

    model_judged = list(healthy.get("model_judged") or [])
    recomputed = list(healthy.get("recomputed") or [])
    verdicts["a model-judged criterion is not reported as recomputed"] = (
        "c3" in model_judged
        and "c3" not in recomputed
        and "c1" in recomputed
    )

    code = facts.get("cli_code")
    cli = facts.get("cli_json") or {}
    results = cli.get("results") if isinstance(cli, dict) else None
    verdicts["the command line can ask whether the closure still holds"] = (
        isinstance(code, int)
        and code == 0
        and isinstance(cli, dict)
        and int(cli.get("sealed") or 0) >= 1
        and isinstance(results, list)
        and bool(results)
        and all(
            str(item.get("state") or "") in ("holds", "drifted")
            for item in results
            if isinstance(item, dict)
        )
    )
    return verdicts


def _seal_verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """A second, independent reading of the same facts.

    This reading does not use the verdict report at all: it goes back
    to the ledger rows and to the disk, and asks the same questions
    with different code.
    """
    verdicts: Dict[str, bool] = {}

    rows = facts.get("rows") or []
    run_end = [row for row in rows if str(row.get("role") or "") == "run_end"]
    seals = [row.get("seal") for row in run_end if isinstance(row.get("seal"), dict)]
    verdicts["a closed run seals its closure"] = (
        bool(facts.get("closed")) and bool(seals)
    )

    # The criteria are read out of the seal's own rows, one by one.
    ids: List[str] = []
    has_check = False
    for seal in seals:
        for raw in seal.get("criteria") or []:
            if not isinstance(raw, dict):
                continue
            ids.append(str(raw.get("id") or ""))
            if isinstance(raw.get("check"), dict):
                has_check = True
    verdicts["the seal carries the criteria it was decided on"] = (
        "c1" in ids and "c2" in ids and has_check
    )

    # The files are hashed here, from the disk, and compared with what
    # the seal claims - not with what a report said.
    from mas.seal import sha256_file

    sealed_hashes = []
    for seal in seals:
        for row in seal.get("files") or []:
            if isinstance(row, dict):
                sealed_hashes.append(str(row.get("sha256") or ""))
    paths = []
    for seal in seals:
        for row in seal.get("files") or []:
            if isinstance(row, dict):
                paths.append(str(row.get("absolute") or ""))
    on_disk = [sha256_file(p) for p in paths if p]
    verdicts["the files the run wrote are sealed by content"] = (
        bool(sealed_hashes)
        and all(sealed_hashes)
        and bool(on_disk)
        and all(a == b for a, b in zip(sealed_hashes, on_disk, strict=False))
    )

    # The healthy verdict is re-derived here from the seal's own
    # digest: an untouched seal hashes to the same value again.
    from mas.seal import seal_digest

    verdicts["a healthy seal re-verifies from the disk"] = (
        bool(seals)
        and all(
            str(seal.get("sha256") or "") == seal_digest(seal) for seal in seals
        )
        and not (facts.get("healthy") or {}).get("problems")
    )

    # The drift is re-derived from the recorded observations: the hash
    # the file had when the run closed, and the hash it had after the
    # edit. They differ - and the file is the same length either way,
    # so a length-based record could not have seen it.
    drifted_now = False
    for seal in seals:
        for row in seal.get("files") or []:
            if not isinstance(row, dict):
                continue
            sealed_hash = str(row.get("sha256") or "")
            if not sealed_hash:
                continue
            if sealed_hash != str(facts.get("tampered_hash") or ""):
                drifted_now = True
    verdicts["a file edited after the closure is reported as drift"] = (
        bool(facts.get("same_length"))
        and bool(facts.get("original_hash"))
        and bool(facts.get("tampered_hash"))
        and str(facts.get("original_hash")) != str(facts.get("tampered_hash"))
        and drifted_now
    )

    # The model-judged criterion is the one without a machine check.
    no_check: List[str] = []
    with_check: List[str] = []
    for seal in seals:
        for raw in seal.get("criteria") or []:
            if not isinstance(raw, dict):
                continue
            if isinstance(raw.get("check"), dict):
                with_check.append(str(raw.get("id") or ""))
            else:
                no_check.append(str(raw.get("id") or ""))
    verdicts["a model-judged criterion is not reported as recomputed"] = (
        "c3" in no_check and "c3" not in with_check and "c1" in with_check
    )

    code = facts.get("cli_code")
    cli = facts.get("cli_json") or {}
    verdicts["the command line can ask whether the closure still holds"] = (
        code == 0
        and isinstance(cli, dict)
        and bool(cli.get("results"))
        and int(cli.get("sealed") or 0) == len(cli.get("results") or [])
    )
    return verdicts


def check_a_closure_is_sealed_to_the_bytes_it_decided_on(
    iterations: int = 5,
) -> Dict[str, Any]:
    """A closure is sealed to the bytes it decided on - and the gate can fail."""
    failures: List[str] = []
    rounds = 0
    for round_index in range(max(1, int(iterations))):
        root = pathlib.Path(tempfile.mkdtemp(prefix="mas-seal-"))
        rounds += 1
        try:
            try:
                facts = _seal_observations(root)
            except Exception as exc:  # noqa: BLE001 - the failure is the subject
                failures.append(
                    "round %d: the seal check broke: %s: %s"
                    % (round_index + 1, type(exc).__name__, exc)
                )
                facts = {}

            verdicts = _seal_verdicts(facts)
            independent = _seal_verdicts_independent(facts)
            for label in SEAL_OBSERVATIONS:
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

            # The gate proves it can fail: the drift detection is broken
            # on purpose - every file is reported as unchanged - and the
            # gate has to notice that it can no longer tell a real
            # drift from a healthy closure.
            import mas.seal as seal_module

            real_sha256_file = seal_module.sha256_file

            seen_hashes: Dict[str, str] = {}

            def _sha256_that_never_moves(
                path, _seen=seen_hashes, _real=real_sha256_file
            ):
                # The digest of a file that never changes: the first
                # time a path is asked about, the hash it has *then* is
                # remembered and answered for ever. An edited file is
                # therefore never noticed - which is the bug the gate
                # has to catch.
                key = str(path)
                if key not in _seen:
                    _seen[key] = _real(path)
                return _seen[key]

            try:
                seal_module.sha256_file = _sha256_that_never_moves
                broken: Dict[str, bool] = {}
                try:
                    broken = _seal_verdicts(_seal_observations(root))
                except Exception:  # noqa: BLE001 - a crash counts as "noticed"
                    broken = {}
            finally:
                seal_module.sha256_file = real_sha256_file
            if broken.get("a file edited after the closure is reported as drift"):
                failures.append(
                    "round %d: the gate did not notice that the drift "
                    "detection is broken - it cannot fail, so it does not watch"
                    % (round_index + 1)
                )
        finally:
            shutil.rmtree(root, ignore_errors=True)

    return {
        "name": "closure_seal",
        "title": "闭环结论封在它判定时依据的字节上",
        "skipped": False,
        "invariant": (
            "闭环把判定所依据的验收标准、交付物字节和 worker 写出的文件"
            "一起封进账本；事后可以独立重算，文件被改过就说出来"
        ),
        "detail": "复核闭环封存：mas seal",
        "iterations": rounds,
        "failures": failures,
        "ok": not failures,
    }


check_a_closure_is_sealed_to_the_bytes_it_decided_on.CHECK_NAME = "closure_seal"  # type: ignore[attr-defined]
