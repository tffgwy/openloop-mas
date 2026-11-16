# -*- coding: utf-8 -*-
"""v0.7.72: the draft layer - an edit becomes a draft, not a fait accompli.

The gap
-------
A run writes straight into the workspace. That is right for a *new*
file: nothing is lost when the loop creates ``notes/finding.txt``. It
is wrong for an *existing* file: the loop overwrites the human's own
work without ever showing what it changed. The reader of this project
asked for the MOKI rule - "the change goes into a draft first, and only
a human confirmation writes it back to the original file" - and the
engine had no way to honour it.

What this module does
---------------------
It is a draft store for *edits to existing files*:

* ``stage_draft`` takes the bytes a candidate wants to write over an
  existing file and records them as a **draft**: the original file is
  not touched, and the draft carries a readable diff of what would
  change.
* ``approve_draft`` writes the draft back to the original file - and
  only then. Nothing else writes.
* ``reject_draft`` drops the draft. The original file is exactly as it
  was, because it was never changed.
* ``list_drafts`` / ``get_draft`` report what is waiting.

Two properties are load-bearing:

* **Nothing is written before the approval.** A draft that is never
  approved leaves the file byte-identical.
* **A rejection changes nothing.** The draft is disposable; the
  original is not.

Why the draft lives next to the workspace
-----------------------------------------
The draft is stored *inside* the workspace, under ``.mas-drafts/``, so
that it travels with the folder it belongs to. A draft that lives in a
central store is a draft that gets lost when the folder is copied, and
a draft that gets lost is a change that can never be reviewed.
"""
from __future__ import annotations

import contextlib
import difflib
import json
import os
import pathlib
import secrets
from typing import Any, Dict, List, Optional, Tuple

from mas.util import ensure_dir, now_iso, short_sha

#: The folder inside a workspace that holds the drafts.
DRAFTS_DIRNAME = ".mas-drafts"

#: The file inside the state directory that holds the human token.
HUMAN_TOKEN_FILENAME = "human.token"

#: The states a draft can be in.
DRAFT_PENDING = "pending"
DRAFT_APPROVED = "approved"
DRAFT_REJECTED = "rejected"


def drafts_dir(workspace: Any) -> pathlib.Path:
    """The folder that holds the drafts of one workspace."""
    return pathlib.Path(workspace) / DRAFTS_DIRNAME


def _draft_file(workspace: Any, draft_id: str) -> pathlib.Path:
    return drafts_dir(workspace) / ("%s.json" % draft_id)


def _safe_id(draft_id: Any) -> str:
    """A draft id that is safe as a file name."""
    text = "".join(
        ch if (ch.isalnum() or ch in "-_") else "_" for ch in str(draft_id or "")
    ).strip("._")
    return text or "draft"


def _relative(workspace: Any, absolute: Any) -> str:
    """The path of a file, relative to the workspace."""
    base = pathlib.Path(workspace)
    target = pathlib.Path(absolute)
    try:
        return str(target.resolve().relative_to(base.resolve())).replace("\\", "/")
    except (ValueError, OSError):
        return str(target).replace("\\", "/")


def _diff_lines(before: str, after: str, relative: str) -> List[str]:
    """A readable, unified-ish diff of one change."""
    diff = difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile=relative,
        tofile="%s (draft)" % relative,
        lineterm="",
        n=3,
    )
    return list(diff)


def _decode(data: bytes) -> str:
    """The readable form of a file's bytes."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def stage_draft(
    workspace: Any,
    relative_path: str,
    data: bytes,
    reason: str = "",
    source: str = "",
) -> Dict[str, Any]:
    """Record a proposed change to an existing file as a draft.

    The original file is **not** touched. The draft carries the bytes
    the change would produce, a diff against the current contents, and
    the reason the change was proposed.

    The draft is refused for a file that does not exist: a draft for a
    missing file is not an *edit*, it is a creation, and a creation is
    what the normal write path is for.
    """
    base = pathlib.Path(workspace)
    target = base / str(relative_path or "").strip().replace("\\", "/")
    try:
        resolved = target.resolve()
        resolved.relative_to(base.resolve())
    except (ValueError, OSError) as exc:
        raise ValueError("refusing to draft a change outside the workspace: %s" % relative_path) from exc
    if not resolved.exists() or not resolved.is_file():
        raise ValueError("cannot draft a change to a file that does not exist: %s" % relative_path)

    before = resolved.read_bytes()
    draft_id = "D-%s" % secrets.token_hex(4)
    relative = _relative(base, resolved)
    diff = _diff_lines(_decode(before), _decode(data), relative)
    payload = {
        "id": draft_id,
        "workspace": str(base),
        "path": relative,
        "absolute": str(resolved),
        "state": DRAFT_PENDING,
        "reason": str(reason or ""),
        "source": str(source or ""),
        "created_at": now_iso(),
        "resolved_at": None,
        "before_sha256": short_sha(_decode(before), 64),
        "after_sha256": short_sha(_decode(data), 64),
        "before_bytes": len(before),
        "after_bytes": len(data),
        "changed": before != data,
        "diff": diff,
    }
    ensure_dir(drafts_dir(base))
    _draft_file(base, draft_id).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    # The bytes themselves live next to the draft: a diff is a report of
    # a change, not the change. Approving the draft has to write the
    # *bytes* the editor produced, so they are stored as well.
    (drafts_dir(base) / ("%s.data" % draft_id)).write_bytes(data)
    return payload


def get_draft(workspace: Any, draft_id: str) -> Optional[Dict[str, Any]]:
    """One draft, or ``None`` when there is no such draft."""
    path = _draft_file(workspace, _safe_id(draft_id))
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def list_drafts(workspace: Any, state: Optional[str] = None) -> List[Dict[str, Any]]:
    """The drafts of a workspace, newest last."""
    folder = drafts_dir(workspace)
    if not folder.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for path in sorted(folder.glob("D-*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        if state is None or str(data.get("state")) == str(state):
            rows.append(data)
    return rows


def _draft_bytes(workspace: Any, draft_id: str) -> bytes:
    """The bytes a draft stands for."""
    path = drafts_dir(workspace) / ("%s.data" % _safe_id(draft_id))
    if not path.exists():
        return b""
    return path.read_bytes()


def approve_draft(
    workspace: Any,
    draft_id: str,
    note: str = "",
) -> Dict[str, Any]:
    """Write a draft back to the file it changes - and only then.

    This is the *only* place where a draft becomes a real change to the
    original file. Until this is called, the file is byte-identical to
    what it was.
    """
    draft = get_draft(workspace, draft_id)
    if draft is None:
        raise KeyError("unknown draft: %s" % draft_id)
    if draft.get("state") != DRAFT_PENDING:
        raise ValueError(
            "draft %s is %s, not pending" % (draft.get("id"), draft.get("state"))
        )
    base = pathlib.Path(workspace)
    target = pathlib.Path(str(draft.get("absolute") or ""))
    data = _draft_bytes(workspace, str(draft.get("id") or ""))
    try:
        target.resolve().relative_to(base.resolve())
    except (ValueError, OSError) as exc:
        raise ValueError("refusing to write outside the workspace: %s" % draft.get("path")) from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    draft["state"] = DRAFT_APPROVED
    draft["resolved_at"] = now_iso()
    draft["note"] = str(note or "")
    _draft_file(base, str(draft.get("id"))).write_text(
        json.dumps(draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return draft


def reject_draft(
    workspace: Any,
    draft_id: str,
    note: str = "",
) -> Dict[str, Any]:
    """Drop a draft. The original file is not touched at all.

    A rejection changes nothing - that is the whole point. The file
    keeps the bytes it had, and the draft is marked so a reader can see
    that the change was seen and turned down.
    """
    draft = get_draft(workspace, draft_id)
    if draft is None:
        raise KeyError("unknown draft: %s" % draft_id)
    if draft.get("state") != DRAFT_PENDING:
        raise ValueError(
            "draft %s is %s, not pending" % (draft.get("id"), draft.get("state"))
        )
    draft["state"] = DRAFT_REJECTED
    draft["resolved_at"] = now_iso()
    draft["note"] = str(note or "")
    _draft_file(workspace, str(draft.get("id"))).write_text(
        json.dumps(draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return draft


def draft_overlay(workspace: Any) -> Dict[str, bytes]:
    """The pending drafts, as an overlay for the machine checks.

    A check that reads a file the loop wants to *change* should see the
    changed version - otherwise the check judges the old bytes and the
    loop can never close on its own proposal. The overlay is exactly
    the same idea the parallel candidate ranking uses, and it never
    touches the disk.
    """
    overlay: Dict[str, bytes] = {}
    for draft in list_drafts(workspace, DRAFT_PENDING):
        absolute = str(draft.get("absolute") or "")
        if not absolute:
            continue
        overlay[absolute] = _draft_bytes(workspace, str(draft.get("id") or ""))
    return overlay


def draft_roots(base: Any) -> List[pathlib.Path]:
    """The folders of ``base`` that may hold drafts.

    A run writes into ``<state_dir>/artifacts/<run_id>/``, so the
    draft sits one folder deeper than the artifacts root the user
    usually names. The named folder is the first candidate and its
    sub-folders follow, so a reader does not have to know the id of
    the run that produced the draft.
    """
    root = pathlib.Path(base)
    roots = [root]
    try:
        for child in sorted(root.iterdir()):
            if child.is_dir() and child.name != DRAFTS_DIRNAME:
                roots.append(child)
    except OSError:
        pass
    return roots


def drafts_in(base: Any, state: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every draft under ``base``, newest last.

    This is the one place that knows how to look into the
    sub-folders, so the CLI and the MCP server answer the same
    question the same way.
    """
    rows: List[Dict[str, Any]] = []
    for root in draft_roots(base):
        rows.extend(list_drafts(root, state))
    return rows


def find_draft(base: Any, draft_id: str) -> Optional[Tuple[pathlib.Path, Dict[str, Any]]]:
    """The folder that owns a draft, and the draft itself.

    ``None`` when no such draft exists.
    """
    for root in draft_roots(base):
        draft = get_draft(root, draft_id)
        if draft is not None:
            return root, draft
    return None


def human_token_path(state_dir: Any) -> pathlib.Path:
    """Where the human token lives inside a state directory."""
    return pathlib.Path(state_dir) / HUMAN_TOKEN_FILENAME


def ensure_human_token(state_dir: Any) -> str:
    """The human token of this machine, created if it does not exist.

    The token is what separates *proposing* a change from *making* it.
    An agent can stage a draft without any secret at all - proposing
    is safe, because the file does not change. But approving a draft
    writes to a file the human owns, so it needs a token that only the
    human can read.

    The token is created lazily by a *human* action (the CLI), and
    the file is made unreadable to other users on the platforms that
    have such a thing.
    """
    path = human_token_path(state_dir)
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    token = secrets.token_hex(16)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token + "\n", encoding="utf-8")
    with contextlib.suppress(OSError):
        # Windows has no POSIX mode bits. The folder is the protection
        # there, and the token is only an obstacle, never a wall.
        os.chmod(path, 0o600)
    return token


def read_human_token(state_dir: Any) -> str:
    """The human token, or an empty string when there is none."""
    path = human_token_path(state_dir)
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def verify_human_token(state_dir: Any, token: Any) -> bool:
    """Whether a token opens the approval door.

    An empty token never opens it: a machine that has no token yet
    cannot be talked into approving anything.
    """
    expected = read_human_token(state_dir)
    given = str(token or "").strip()
    if not expected or not given:
        return False
    return secrets.compare_digest(expected, given)
