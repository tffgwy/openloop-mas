# -*- coding: utf-8 -*-
"""Removing the rows the old self-test left behind in the ledger.

The gap
-------
Before v0.7.92 the self-test approved and undid a draft *without*
isolating the state directory, so every run left rows in
``<state>/ledgers/runs.jsonl`` whose workspace pointed at a temporary
folder that was deleted right afterwards. The rows stayed; the chains
they pointed at did not. ``mas report`` therefore reported audit
problems that had no cause.

This module removes exactly those rows - and nothing else. A row is
only removed when *both* hold:

* it is a draft decision (``draft_approve`` / ``draft_undo``), and
* its workspace points into a temporary directory.

Any row that does not meet both conditions is a record of a real
decision and is kept. The original ledger is copied to a timestamped
backup before anything is written, so the operation is reversible.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import pathlib
import shutil
from typing import Any, Dict, List

#: The events the old self-test used to write into the main ledger.
SELFTEST_EVENTS = ("draft_approve", "draft_undo")


def _looks_temporary(workspace: str) -> bool:
    """Whether a workspace path points at a temporary directory.

    The check is deliberately narrow. It matches the operating
    system's temporary directories and the ``mas-*`` prefixes the
    self-test itself used, and nothing else - a real workspace is
    never inside one of those.
    """
    if not workspace:
        return False
    normalized = workspace.replace("/", "\\").lower()
    for marker in ("\\temp\\", "\\tmp\\", "\\appdata\\local\\temp\\"):
        if marker in normalized:
            return True
    leaf = normalized.rsplit("\\", 1)[-1]
    return leaf.startswith(("mas-audit-", "mas-selftest-"))


def classify_rows(lines: List[str]) -> Dict[str, Any]:
    """Split ledger lines into the ones to keep and the ones to drop.

    Returns the kept lines verbatim, the dropped lines verbatim, and
    a count of each - so the caller can show the user what was
    removed before it removes anything.
    """
    kept: List[str] = []
    dropped: List[str] = []
    unparsable = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            row = json.loads(stripped)
        except ValueError:
            # A line we cannot read is not a line we may destroy.
            unparsable += 1
            kept.append(line)
            continue
        if not isinstance(row, dict):
            unparsable += 1
            kept.append(line)
            continue
        event = str(row.get("event") or row.get("role") or "")
        workspace = str(row.get("workspace") or "")
        if event in SELFTEST_EVENTS and _looks_temporary(workspace):
            dropped.append(line)
        else:
            kept.append(line)
    return {
        "kept": kept,
        "dropped": dropped,
        "unparsable": unparsable,
        "dropped_count": len(dropped),
        "kept_count": len(kept),
    }


def clean_selftest_ledger(state_dir: Any,
                          dry_run: bool = False) -> Dict[str, Any]:
    """Remove the self-test rows from the ledger of ``state_dir``.

    The ledger is copied to ``runs.jsonl.bak-<timestamp>`` before the
    new content is written. If ``dry_run`` is true, nothing is
    written at all - the result only says what *would* happen.
    """
    base = pathlib.Path(state_dir)
    ledger = base / "ledgers" / "runs.jsonl"
    result: Dict[str, Any] = {
        "state_dir": str(base),
        "ledger": str(ledger),
        "exists": ledger.is_file(),
        "dry_run": bool(dry_run),
        "dropped_count": 0,
        "kept_count": 0,
        "unparsable": 0,
        "backup": "",
        "written": False,
    }
    if not ledger.is_file():
        result["message"] = "\u8d26\u672c\u4e0d\u5b58\u5728\uff0c\u6ca1\u6709\u9700\u8981\u6e05\u7406\u3002"
        return result

    try:
        text = ledger.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        result["error"] = "\u8bfb\u53d6\u8d26\u672c\u5931\u8d25\uff1a%s" % exc
        return result

    lines = text.splitlines(keepends=True)
    split = classify_rows(lines)
    result["dropped_count"] = split["dropped_count"]
    result["kept_count"] = split["kept_count"]
    result["unparsable"] = split["unparsable"]

    if split["dropped_count"] == 0:
        result["message"] = (
            "\u8d26\u672c\u91cc\u6ca1\u6709\u81ea\u68c0\u6cc4\u6f0f\u7684\u884c\uff0c\u4e0d\u9700\u8981\u6e05\u7406\u3002"
        )
        return result

    if dry_run:
        result["message"] = (
            "\u5e72\u8fd0\uff1a\u5c06\u5220\u9664 %d \u884c\u81ea\u68c0\u6cc4\u6f0f\uff0c\u4fdd\u7559 %d \u884c\u3002"
            % (split["dropped_count"], split["kept_count"])
        )
        return result

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = ledger.with_name("runs.jsonl.bak-%s" % stamp)
    try:
        shutil.copy2(ledger, backup)
    except OSError as exc:
        result["error"] = "\u5907\u4efd\u8d26\u672c\u5931\u8d25\uff1a%s" % exc
        return result
    result["backup"] = str(backup)

    tmp = ledger.with_name("runs.jsonl.new")
    try:
        tmp.write_text("".join(split["kept"]), encoding="utf-8")
        shutil.move(str(tmp), str(ledger))
    except OSError as exc:
        with contextlib.suppress(OSError):
            tmp.unlink()
        result["error"] = "\u5199\u5165\u8d26\u672c\u5931\u8d25\uff1a%s" % exc
        result["backup"] = str(backup)
        return result

    result["written"] = True
    result["message"] = (
        "\u5df2\u5220\u9664 %d \u884c\u81ea\u68c0\u6cc4\u6f0f\uff0c\u4fdd\u7559 %d \u884c\uff1b\u5907\u4efd\u5728 %s"
        % (split["dropped_count"], split["kept_count"], backup)
    )
    return result
