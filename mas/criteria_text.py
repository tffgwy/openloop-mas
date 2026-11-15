# -*- coding: utf-8 -*-
"""Turn a machine check into the sentence a person can read.

The acceptance criteria are the contract between the user and the loop.
The user writes them as ``contains:结论`` or ``file_exists:out/report.md``,
and the engine decides them one at a time. But the report of that decision
was ``[PASS] c1 (rule) all required strings present`` - a serial number
and an English sentence, neither of which says *which* of the standards the
user wrote was checked.

So two things were missing:

* the **text of the standard** - the user wrote three of them, and the
  output named ``c1``, ``c2``, ``c3``;
* a **plain reading of the rule** - "all required strings present" does
  not tell the user what "required" means here.

This module produces both. It is deliberately data-only: no engine state,
no imports from the engine (which would be circular), and every function
is total - an unknown check type produces a sentence that says so rather
than raising.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "、".join(str(item) for item in value)
    return str(value)


#: How a text-level check reads in plain Chinese. ``{arg}`` is replaced
#: by the argument of the check.
CHECK_TYPE_READING: Dict[str, str] = {
    "contains": "正文里要出现「{arg}」",
    "not_contains": "正文里不能出现「{arg}」",
    "min_chars": "正文至少 {arg} 个字",
    "max_chars": "正文最多 {arg} 个字",
    "lines_min": "正文至少 {arg} 行",
    "regex": "正文要匹配这个正则：{arg}",
    "section": "正文里要有「{arg}」这一节",
    "json_keys": "JSON 里要有这些键：{arg}",
}

#: How a file-level check reads in plain Chinese. The file checks carry
#: their argument as a dict (``{"path": ..., "value": ...}``), so they
#: get their own placeholders - ``{path}`` and ``{value}`` - and are
#: never rendered by stringifying a dict.
FILE_CHECK_TYPE_READING: Dict[str, str] = {
    "file_exists": "文件 {path} 必须存在",
    "file_contains": "文件 {path} 里要出现「{value}」",
    "file_not_contains": "文件 {path} 里不能出现「{value}」",
    "file_regex": "文件 {path} 要匹配这个正则：{value}",
    "file_min_bytes": "文件 {path} 至少 {value} 字节",
    "file_max_bytes": "文件 {path} 最多 {value} 字节",
    "file_lines_min": "文件 {path} 至少 {value} 行",
    "file_sha256": "文件 {path} 的 sha256 要等于 {value}",
    "file_mtime_newer": "文件 {path} 的修改时间要晚于 {value}",
}


def _resolve_file_arg(arg: Any) -> tuple:
    """Split a file check argument into its path and its expectation.

    The engine hands the file checks ``{"path": ..., "value": ...}``.
    A check that only needs the path (``file_exists``) has no
    ``value`` key at all, and a malformed argument may have neither -
    both fall back to an empty string rather than a dict repr.
    """
    if isinstance(arg, dict):
        path = arg.get("path")
        value = arg.get("value")
        return _as_text(path), _as_text(value)
    return _as_text(arg), ""


def check_reading(check: Optional[Dict[str, Any]]) -> str:
    """The plain reading of one machine check.

    An empty string means "this criterion has no machine check" - the
    reviewer decides it instead, and there is no rule to translate.
    """
    if not isinstance(check, dict):
        return ""
    kind = str(check.get("type") or "").lower()
    if not kind:
        return ""
    raw_arg = check.get("arg")
    if kind in FILE_CHECK_TYPE_READING:
        path, value = _resolve_file_arg(raw_arg)
        template = FILE_CHECK_TYPE_READING[kind]
        return template.replace("{path}", path or "（没写路径）").replace("{value}", value or "（没写期望值）")
    template = CHECK_TYPE_READING.get(kind)
    if template is None:
        return f"一条 {kind} 规则"
    arg = _as_text(raw_arg)
    if not arg:
        return f"一条 {kind} 规则（没写具体内容）"
    return template.replace("{arg}", arg)


def criterion_reading(criterion: Any) -> str:
    """The sentence that names what a criterion asked for.

    The user's own description wins when they wrote one; the machine
    check is the fallback, because a criterion written as a bare
    ``contains:结论`` shorthand has no description of its own.
    """
    desc = ""
    required = True
    check: Optional[Dict[str, Any]] = None
    if isinstance(criterion, dict):
        desc = str(criterion.get("desc") or "").strip()
        required = bool(criterion.get("required", True))
        check = criterion.get("check") if isinstance(criterion.get("check"), dict) else None
    else:
        desc = str(getattr(criterion, "desc", "") or "").strip()
        required = bool(getattr(criterion, "required", True))
        raw_check = getattr(criterion, "check", None)
        check = raw_check if isinstance(raw_check, dict) else None

    reading = check_reading(check)
    kind = str((check or {}).get("type") or "").lower()
    # A shorthand criterion carries the raw text as its description
    # ("contains:结论"). Repeating that next to the reading would say
    # the same thing twice, so the reading replaces it. A criterion
    # with a *written* description keeps it - and then the reading is
    # appended, because the description says what the user meant and
    # the rule says what will actually be checked.
    is_raw_shorthand = bool(kind) and desc.lower().startswith(kind + ":")
    if not desc:
        desc = reading or "（这条标准没有写文字说明）"
    elif is_raw_shorthand:
        desc = reading
    elif reading and desc != reading:
        desc = f"{desc}（机检：{reading}）"
    if not required:
        desc = f"{desc}（可选，不算闭环）"
    return desc


def describe_scores(
    scores: List[Dict[str, Any]],
    criteria: Optional[List[Any]] = None,
) -> List[Dict[str, Any]]:
    """Attach the human reading of each criterion to its score.

    The scores carry an id; the criteria carry the text. This joins them
    so the CLI can print one line per standard that a user can match
    against the list they wrote.
    """
    by_id: Dict[str, Any] = {}
    for index, criterion in enumerate(criteria or []):
        if isinstance(criterion, dict):
            cid = str(criterion.get("id") or f"c{index + 1}")
        else:
            cid = str(getattr(criterion, "id", "") or f"c{index + 1}")
        by_id[cid] = criterion

    described: List[Dict[str, Any]] = []
    for score in scores or []:
        item = dict(score)
        cid = str(score.get("id") or "")
        criterion = by_id.get(cid)
        if criterion is not None:
            item["reading"] = criterion_reading(criterion)
        else:
            item.setdefault("reading", "")
        described.append(item)
    return described
