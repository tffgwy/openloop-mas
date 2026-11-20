"""Command line interface for OpenLoop MAS."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from typing import Any, Dict, List, Optional, Tuple

from mas import __version__
from mas.bench import format_bench, run_bench
from mas.cleanup import clean_selftest_ledger
from mas.config import load_config, load_persisted_config, save_config
from mas.demo import run_scenarios
from mas.doctor import format_doctor, run_doctor, summarize, verdict
from mas.engine import LoopEngine
from mas.handoff import file_listing_reading
from mas.installer import (
    format_report,
    format_sync_report,
    format_uninstall_report,
    run_install,
    run_sync,
    run_uninstall,
)
from mas.orchestration import CANDIDATE_ANGLES
from mas.reason_text import continuation_command, reason_hint, reason_text
from mas.relay import serve_relay
from mas.report import (
    artifact_rows,
    build_report,
    closure_verdict_for,
    format_artifacts,
    format_closure,
    group_runs,
    group_threads,
    ledger_markdown,
    render_markdown,
    replay_trace,
    run_summary,
    verdict_word,
)
from mas.setup import format_verify, run_verify, run_wizard
from mas.teams import list_teams, load_team
from mas.util import force_utf8_output, parse_criterion_shorthand
from mas.verdict_text import (
    decision_reading,
    issue_reading,
    note_reading,
    role_reading,
    source_reading,
    status_reading,
)


def _both(raw: Any, reading: str) -> str:
    """Show the word and the raw name, side by side.

    "规划 (planner)" reads as a sentence and still greps. The reader
    gets the meaning without a lookup; the script gets the exact token
    the engine wrote.
    """
    raw_text = str(raw or "").strip()
    if not raw_text:
        return ""
    if not reading or reading == raw_text:
        return raw_text
    return f"{reading} ({raw_text})"


def _print(payload: Any, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if isinstance(payload, (dict, list)):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(payload)


def _load_criteria(args: argparse.Namespace) -> List[Any]:
    criteria: List[Any] = []
    if getattr(args, "criteria_file", None):
        path = pathlib.Path(args.criteria_file).expanduser()
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("criteria") or []
        if not isinstance(data, list):
            raise ValueError("criteria 文件必须是 JSON 数组或含 criteria 数组的对象")
        criteria.extend(data)
    for text in getattr(args, "criteria", None) or []:
        parsed = _parse_criterion(text)
        if isinstance(parsed, list):
            criteria.extend(parsed)
        else:
            criteria.append(parsed)
    return criteria


def _parse_criterion(text: Any) -> Any:
    """Accept a JSON criterion object, a ``<type>:<arg>`` shorthand, or a
    plain-text criterion.

    ``--criteria '{"id":"c1","check":{...}}'`` is the documented form and
    must not degrade into a raw description string, otherwise the machine
    check silently never runs. A JSON *array* of criteria is accepted the
    same way and is returned as a list, so the caller can extend its
    criteria collection instead of nesting it. The shorthand ``--criteria 'regex:def'`` is
    the convenient form and must not degrade either. The shorthand parser
    itself lives in :mod:`mas.util` so that the engine applies the same
    rules to criteria arriving through the MCP or HTTP server.
    """
    if not isinstance(text, str):
        return text
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"--criteria 不是合法 JSON: {exc}") from exc
    parsed = parse_criterion_shorthand(stripped)
    # Only a real shorthand becomes a structured criterion. A plain
    # description stays a string, so the CLI contract does not change.
    if not parsed.get("check"):
        return stripped
    return parsed


def _engine(config: Dict[str, Any], team: str | None = None) -> LoopEngine:
    return LoopEngine(config, team=load_team(team or config.get("team")))


def cmd_install(args: argparse.Namespace) -> int:
    """Install the agent skills and register the MCP server.

    This is the step that turns ``pip install openloop-mas`` into a
    *working* setup. It is the same work ``install.ps1`` / ``install.sh``
    do, but it runs from the installed package, so it works the same way on
    every platform.
    """
    report = run_install(
        python_exe=args.python,
        skip_skills=args.skip_skills,
        skip_mcp=args.skip_mcp,
    )
    if getattr(args, "json", False):
        _print(report, as_json=True)
    else:
        print(format_report(report))
        print("")
        print("next:")
        print("  mas doctor        # 自检")
        print("  mas demo          # 离线跑通三个闭环场景")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    """Bring the installed skills back in line with the packaged ones.

    This is the repair path for drift: the packaged skills are the truth,
    and after an upgrade (or an accidental hand-edit) the installed copy
    can be stale. ``mas sync`` reports what drifted and fixes it.
    """
    report = run_sync(dry_run=getattr(args, "dry_run", False))
    if getattr(args, "json", False):
        _print(report, as_json=True)
    else:
        print(format_sync_report(report))
    return 0 if not report.get("errors") else 1


def _current_state_dir() -> pathlib.Path:
    """The state directory the user actually uses.

    The same resolution the rest of the CLI uses: the persisted
    configuration, with the environment override applied on top. The
    upgrade command has to look at *this* directory - looking anywhere
    else would report "nothing to upgrade" while the old runtime kept
    serving.
    """
    config = load_config()
    raw = str(config.get("state_dir") or "")
    return pathlib.Path(os.path.expanduser(os.path.expandvars(raw))).resolve()


def cmd_upgrade(args: argparse.Namespace) -> int:
    """Replace an outdated installed runtime with the running one.

    The one-click installer copies the runtime into ``<install-dir>/app``.
    Refreshing the skills and the MCP registration does not change that
    copy, so an old runtime can keep serving while every check says
    "registered". This command replaces it and says what it did.
    """
    from mas.upgrade import (
        format_shadow_upgrade_report,
        format_upgrade_report,
        upgrade_runtime,
        upgrade_shadowed_runtimes,
    )

    state = _current_state_dir()
    report = upgrade_runtime(state, force=getattr(args, "force", False))
    shadow_report = None
    if getattr(args, "fix_shadow", False):
        shadow_report = upgrade_shadowed_runtimes(state, force=getattr(args, "force", False))
    if getattr(args, "json", False):
        payload = dict(report)
        if shadow_report is not None:
            payload["shadow"] = shadow_report
        _print(payload, as_json=True)
        return 1 if report.get("errors") or (shadow_report or {}).get("errors") else 0
    print(format_upgrade_report(report))
    if shadow_report is not None:
        print("")
        print(format_shadow_upgrade_report(shadow_report))
    if report.get("upgraded"):
        print("")
        print("下一步：运行 mas install 让 MCP 注册指向新版本")
    return 1 if report.get("errors") or (shadow_report or {}).get("errors") else 0


def cmd_runtime_status(args: argparse.Namespace) -> int:
    """Show the runtime that is installed on disk next to the running one."""
    from mas.upgrade import format_runtime_status, runtime_status

    status = runtime_status(_current_state_dir())
    if getattr(args, "json", False):
        _print(status, as_json=True)
    else:
        print(format_runtime_status(status))
    return 0


def cmd_shadow(args: argparse.Namespace) -> int:
    """Show which ``mas`` the user actually runs when they type it.

    The PATH can hold several copies of this program. The installer
    puts one there, but a ``pip install`` puts another one there too -
    usually *earlier*, so the old copy wins. This command names the
    winner and the version it would run, so the discrepancy is visible
    instead of surprising.
    """
    from mas.shadow import format_shadow_status, shadow_status

    status = shadow_status()
    if getattr(args, "json", False):
        _print(status, as_json=True)
    else:
        print(format_shadow_status(status))
    return 1 if status.get("shadowed") else 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    """Remove the agent skills and the MCP server registration.

    The runtime package and the ``~/.mas`` state directory are kept: the
    ledger in there is the history of every closed loop and cannot be
    regenerated. ``pip uninstall openloop-mas`` removes the package.
    """
    report = run_uninstall(
        skip_skills=args.skip_skills,
        skip_mcp=args.skip_mcp,
    )
    if getattr(args, "json", False):
        _print(report, as_json=True)
    else:
        print(format_uninstall_report(report))
        print("")
        print("next:")
        print("  pip uninstall openloop-mas   # 移除运行时")
        print("  rm -rf ~/.mas                # 移除配置与账本（不可恢复）")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    if getattr(args, "wizard", False):
        run_wizard()
        return 0
    # Start from the persisted view: the environment keeps overriding the
    # values at runtime, so the file must not be rewritten with the
    # environment merged into it.
    config = load_persisted_config()
    if args.provider:
        config["provider"] = args.provider
    if args.model:
        config["model"] = args.model
    if args.base_url:
        config["base_url"] = args.base_url
    if args.team:
        config["team"] = args.team
    if args.state_dir:
        config["state_dir"] = args.state_dir
    if args.api_key_env:
        config["api_key_env"] = args.api_key_env
    if args.max_iterations:
        config["budget"]["max_iterations"] = args.max_iterations
    if args.deadline_sec:
        config["budget"]["deadline_sec"] = args.deadline_sec
    if args.max_tokens:
        config["budget"]["max_tokens"] = args.max_tokens
    if args.stagnation_limit:
        config["stagnation_limit"] = args.stagnation_limit
    # A state directory the user named on the command line is a
    # decision, not an injection - it survives the temporary-directory
    # guard even when it points into the system temp folder.
    path = save_config(config, explicit_state_dir=args.state_dir)
    _print({"config_path": str(path), "config": config}, args.json)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    config = load_config()
    criteria = _load_criteria(args)
    workspace = str(getattr(args, "workspace", "") or "").strip()
    if workspace:
        config["workspace"] = workspace
    engine = _engine(config, args.team)
    result = engine.run(
        goal=args.goal,
        criteria=criteria,
        title=getattr(args, "title", "") or "",
        parallel=getattr(args, "parallel", None),
        reviewers=getattr(args, "reviewers", None),
        review_mode=getattr(args, "review_mode", None),
        perspective=getattr(args, "perspective", "") or "",
    )
    if args.json:
        _print(result.to_dict(), True)
        return 0 if result.closed else 2
    # The status in words, with the raw value kept visible: the
    # words are for the reader, the raw value is what a reader
    # greps for. A status the engine never wrote keeps its own
    # text - nothing is invented here.
    status_text = status_reading(result.status)
    status_cell = (
        status_text
        if status_text == result.status
        else f"{status_text}（{result.status}）"
    )
    print(f"状态：{status_cell}（迭代 {result.iterations} 次，token {result.tokens_used}）")
    print(f"账本：{result.ledger_path}")
    # Say where the deliverable landed. The preview below is a teaser;
    # the file is the product, and the user has to be able to open it
    # without going hunting through the state directory.
    if result.artifact_path:
        print(f"交付物：{result.artifact_path}")
    # The files the run wrote. The deliverable is one file; a run that
    # wrote three notes produced three notes, and the user could not
    # see the other two.
    files = _result_files(result)
    if files:
        names = "、".join(str(row.get("path") or "") for row in files)
        print(f"本次生成的文件：{names}")
    for score in result.scores:
        flag = "PASS" if score["pass"] else "FAIL"
        optional = "" if score.get("blocking", True) else "，可选"
        # The note is written in the engine's vocabulary
        # ("all required strings present"). It describes the rule, and
        # the rule is already printed below as the reading - so the
        # line said the same thing twice, in a language the user did
        # not write in. The reading replaces it; an untranslatable
        # note keeps its original text.
        reading_note = note_reading(score.get("note"))
        source = source_reading(score.get("source"))
        detail = reading_note or str(score.get("note") or "")
        if source and detail:
            detail = f"{source}：{detail}"
        elif source:
            detail = source
        print(f"  [{flag}] {score['id']}{optional} {detail}")
        # The serial number alone does not tell the user which of the
        # standards they wrote this is. The reading is the sentence
        # that names it - without it, a report with three standards
        # says "c1 PASS, c2 FAIL" and the reader has to guess which
        # one failed.
        reading = str(score.get("reading") or "")
        if reading:
            print(f"        要求：{reading}")
    if result.ticket_id:
        print(f"已升级为人工工单：{result.ticket_id}")
        print(
            "  继续："
            + continuation_command(
                str(result.ticket_id),
                result.error,
                result.to_dict().get("budget"),
            )
        )
    if result.error:
        # The reason the run stopped, in words. The engine writes
        # it in its own vocabulary ("budget: reached
        # max_iterations=1"); a reader who does not know that
        # vocabulary learns nothing from the line - it says that
        # something happened, not what to do about it. The reading
        # also names the command that raises the ceiling. An
        # unknown reason keeps its own text.
        print(f"说明：{reason_text(result.error)}")
        hint = reason_hint(result.error)
        if hint:
            print(f"  {hint}")
    print("--- 交付物预览 ---")
    print(result.artifact[:1500])
    return 0 if result.closed else 2


#: What each demo scenario *proves*, in words. The three lines the demo
#: used to print were machine truth ("expect=closed actual=closed") that
#: told a beginner nothing about what had just been proven.
DEMO_SCENARIO_MEANING = {
    "closure": "场景一 · 会验收：交付物不合格就重来，直到真的满足验收条件。",
    "stagnation": "场景二 · 会止损：一直产不出合格结果时停下来开工单，而不是烧光预算。",
    "budget": "场景三 · 会说实话：预算用完就报预算耗尽，绝不假装做完了。",
}


def cmd_demo(args: argparse.Namespace) -> int:
    results = run_scenarios(args.scenario)
    if args.json:
        _print(results, True)
    else:
        for row in results:
            flag = "OK " if row.get("ok") else "FAIL"
            meaning = DEMO_SCENARIO_MEANING.get(row["scenario"], "")
            print(f"{meaning}")
            # The expectation and the outcome in words. The demo is
            # the first screen a beginner sees, and "expect=closed
            # actual=closed" is machine truth: it says what the
            # tool recorded, not what the demo proved. The raw
            # values stay visible so a reader can grep for them.
            expect_text = status_reading(row["expect"])
            actual_text = status_reading(row["status"])
            expect_cell = (
                expect_text
                if expect_text == row["expect"]
                else f"{expect_text}（{row['expect']}）"
            )
            actual_cell = (
                actual_text
                if actual_text == row["status"]
                else f"{actual_text}（{row['status']}）"
            )
            print(
                f"[{flag}] {row['scenario']:<11} 期望={expect_cell} 实际={actual_cell} "
                f"迭代={row.get('iterations')} 票={row.get('ticket_id') or '-'}"
            )
            print("")
        ok_all = all(row.get("ok") for row in results)
        if ok_all:
            print("三个场景全部符合预期：闭环引擎会验收、会止损、会如实报告预算耗尽。")
            print("下一步：mas init --wizard  接上你自己的模型，然后 mas run 跑一个真任务。")
        else:
            print("有场景不符合预期 - 上面标 FAIL 的那行就是问题所在。")
        print("（这是离线演示，不联网、不调真模型、不花钱。）")
    return 0 if all(row.get("ok") for row in results) else 1


def cmd_status(args: argparse.Namespace) -> int:
    """Say where this machine stands - in words, not in a JSON dump.

    The status command printed a raw JSON payload and nothing else.
    That is fine for a machine and useless for the beginner the project
    is written for: the JSON says "entries: 27" and "waiting_human:
    [T-...]" and never says "you have 1 deliverable and 1 ticket
    waiting for you". A status page that a person cannot read is not a
    status page.
    """
    config = load_config()
    engine = _engine(config)
    tickets = engine.tickets.list()
    rows = engine.ledger.entries()
    # The deliverables this machine produced, and the runs they came
    # from. A status page that omits the results of the work is a
    # status page about the plumbing.
    artifacts = []
    for group in group_runs(rows):
        summary = run_summary(group["run_id"], group["rows"])
        path = str(summary.get("artifact_path") or "")
        if not path:
            continue
        artifacts.append(
            {
                "run_id": summary["run_id"],
                "title": summary.get("title") or summary.get("goal") or "",
                "path": path,
                "exists": pathlib.Path(path).exists(),
                "chars": int(summary.get("artifact_chars") or 0),
            }
        )
    waiting = [t["id"] for t in tickets if t.get("state") == "waiting_human"]
    resolved = [t["id"] for t in tickets if t.get("state") == "resolved"]
    # The draft queue. A draft is a change the loop proposed and did
    # not make, so it is work that is waiting for a person - exactly
    # like a ticket, and exactly as invisible if nobody reads it.
    drafts = drafts_of_machine(config, rows)
    pending_drafts = [d["id"] for d in drafts if d.get("state") == "pending"]
    approved_drafts = [d["id"] for d in drafts if d.get("state") == "approved"]
    # The payload of every waiting ticket. The continuation command
    # needs the reason and the budget of the stopped run to build a
    # command that can actually work.
    ticket_payloads = {
        t["id"]: (t.get("payload") or {})
        for t in tickets
        if t.get("state") == "waiting_human"
    }
    # A ticket id on its own does not say which problem is waiting.
    # Keep the title next to the id so the status page names the
    # problem, not just its serial number.
    ticket_titles = {
        t["id"]: (
            (t.get("payload") or {}).get("title")
            or (t.get("payload") or {}).get("goal")
            or ""
        )
        for t in tickets
    }
    runs = [
        run_summary(group["run_id"], group["rows"]) for group in group_runs(rows)
    ]
    # The drafts reconciled against the ledger. A run that closed
    # while its change is still waiting for a human is the sharpest
    # fact this page can tell: the machine says "done", the disk says
    # "nothing happened", and only the draft says why.
    try:
        from mas.drafts_reading import draft_reading

        draft_queue = draft_reading(rows, drafts, runs=runs)
    except Exception:  # noqa: BLE001 - status must never crash
        draft_queue = {}
    # Runs are attempts; problems are what the human has to deal with.
    # A problem that was resumed three times is one open problem, not
    # four - the status page has to say so, or the queue and the
    # report disagree about how much is left to do.
    threads = group_threads(runs)
    payload = {
        "config": {
            "provider": config.get("provider"),
            "model": config.get("model"),
            "team": config.get("team"),
            "state_dir": config.get("state_dir"),
            "budget": config.get("budget"),
        },
        "ledger": engine.ledger.summary(),
        "tickets": {
            "waiting_human": waiting,
            "resolved": resolved,
        },
        # The changes that were proposed and are waiting for a human.
        # A status page that counts tickets but not drafts says the
        # queue is empty while changes are sitting in it.
        "drafts": {
            "total": len(drafts),
            "pending": pending_drafts,
            "approved": approved_drafts,
        },
        # The reconciliation of the queue against the ledger: how many
        # drafts are waiting, and how many closed runs are waiting for
        # their own change to be approved.
        "draft_reading": {
            "pending_count": int(draft_queue.get("pending_count") or 0),
            "approved_count": int(draft_queue.get("approved_count") or 0),
            "closed_with_pending": list(
                draft_queue.get("closed_with_pending") or []
            ),
            "closed_with_pending_count": int(
                draft_queue.get("closed_with_pending_count") or 0
            ),
            "problems": list(draft_queue.get("problems") or []),
        },
        # How much work this machine has done, and how much of it
        # actually closed. "entries: 27" says how many lines the log
        # has; "3 runs, 2 closed" says how the work went.
        "runs": {
            "total": len(runs),
            "closed": len([r for r in runs if r.get("closed")]),
            "open": len([r for r in runs if not r.get("closed")]),
        },
        # The problems, as opposed to the attempts on them.
        "problems": {
            "total": len(threads),
            "open": len([t for t in threads if not t.get("closed")]),
            "closed": len([t for t in threads if t.get("closed")]),
            "items": threads,
        },
        # The human decisions, and whether their chain is still whole.
        # A status page that does not mention the audit chain cannot
        # tell the reader that a wrong approval was taken back - and
        # cannot tell them that the record of it was tampered with.
        "audit": {
            "chain_count": 0,
            "entries": 0,
            "ok": True,
            "problems": [],
        },
        # The deliverables, and whether they are still on disk.
        "artifacts": {
            "count": len(artifacts),
            "missing": len([a for a in artifacts if not a["exists"]]),
            "items": artifacts,
        },
    }
    # The audit chain of the human decisions. A failure here must not
    # break the status page - it is reported as a finding instead.
    try:
        from mas.audit import audit_report

        artifacts_root = pathlib.Path(
            os.path.expanduser(os.path.expandvars(str(config.get("state_dir") or "")))
        ) / "artifacts"
        audit = audit_report(artifacts_root, rows)
        payload["audit"] = {
            "chain_count": int(audit.get("chain_count") or 0),
            "entries": int(audit.get("entries") or 0),
            "ok": bool(audit.get("ok")),
            "problems": list(audit.get("problems") or []),
        }
    except Exception:  # noqa: BLE001 - status must never crash
        pass

    if args.json:
        _print(payload, True)
        return 0

    budget = config.get("budget") or {}
    print(f"provider：{config.get('provider')} / {config.get('model')}")
    if str(config.get("provider") or "mock") == "mock":
        # "provider: mock" is a machine word. A beginner who just
        # installed the tool reads it and has no idea whether the thing
        # is working or broken - it is neither. It is the offline
        # demonstration mode: the loop runs, but no real model is
        # called. Say that, and say what to change.
        print("        （离线演示模式：闭环会跑，但调的是内置假模型，不联网、不花钱）")
        print("        要接真模型：mas init --provider openai_compat --model <模型> --base-url <端点>")
    print(f"状态目录：{config.get('state_dir')}")
    print(
        f"账本：{payload['ledger']['entries']} 条  "
        f"运行 {payload['runs']['total']} 次  "
        f"已闭环 {payload['runs']['closed']}  "
        f"未闭环 {payload['runs']['open']}"
    )
    # Attempts and problems are different numbers. A single problem
    # that was resumed three times is four runs and one problem, and
    # the page has to make that difference visible - otherwise the
    # user concludes that four things need their attention.
    print(
        f"待解决问题：{payload['problems']['open']} 个"
        f"（共 {payload['problems']['total']} 个问题）"
    )
    # The deliverables come first among the things to look at: they are
    # what the user came for.
    audit = payload.get("audit") or {}
    if int(audit.get("chain_count") or 0):
        mark = "完好" if audit.get("ok") else "有问题"
        print(
            f"审计链：{audit.get('chain_count')} 条  "
            f"{audit.get('entries')} 个人工决定  {mark}"
        )
        for problem in list(audit.get("problems") or [])[:3]:
            print(f"  ⚠ {problem}")
    print(f"交付物：{payload['artifacts']['count']} 份")
    for item in artifacts:
        flag = "OK " if item["exists"] else "缺失"
        print(f"  [{flag}] {item['title'] or '-'}  {item['chars']} 字  {item['path']}")
    if payload["artifacts"]["missing"]:
        print(f"  ⚠ 有 {payload['artifacts']['missing']} 份交付物文件已不在原处。")
    if waiting:
        print(f"待处理工单：{len(waiting)} 个")
        for ticket_id in waiting:
            label = ticket_titles.get(ticket_id) or ""
            if label:
                print(f"  {ticket_id}  {label}")
            print(
                "    →  "
                + continuation_command(
                    ticket_id,
                    (ticket_payloads.get(ticket_id) or {}).get("reason"),
                    (ticket_payloads.get(ticket_id) or {}).get("budget"),
                )
            )
    else:
        print("待处理工单：0 个")
    if resolved:
        print(f"已解决工单：{len(resolved)} 个")
    # The changes that were proposed and not made. A pending draft is
    # work that is waiting for a person, so it belongs next to the
    # tickets - and a closed run whose draft is still pending belongs
    # even higher up, because that run says "done" while the file on
    # the disk is still the old one.
    pending_draft_items = [
        d for d in drafts if str(d.get("state") or "") == "pending"
    ]
    if pending_draft_items:
        print(f"待批草稿：{len(pending_draft_items)} 个（改动还没落盘）")
        for draft in pending_draft_items:
            label = str(draft.get("path") or "")
            print(f"  {draft.get('id')}  {label}" if label else f"  {draft.get('id')}")
            print(f"    →  mas drafts approve {draft.get('id')}")
    else:
        print("待批草稿：0 个")
    # The closed runs that are waiting for their own change. This is
    # the fact a reader must not have to infer: the run reported a
    # closed loop, and the file is still the old one.
    closed_pending = list(
        (payload.get("draft_reading") or {}).get("closed_with_pending") or []
    )
    if closed_pending:
        print(
            f"⚠ 闭环待写：{len(closed_pending)} 次运行报了闭环，"
            "但它的改动还排在人前面（文件没变）"
        )
        for item in closed_pending:
            paths = ", ".join(item.get("paths") or []) or "-"
            print(f"  {item.get('run_id')}  {paths}")
            print(f"    →  mas drafts approve {item.get('draft_ids')[0]}")
    print(
        f"预算：迭代 {budget.get('max_iterations')} / "
        f"{budget.get('deadline_sec')} 秒 / {budget.get('max_tokens')} token"
    )
    # A status page on a machine that has never run anything is a page
    # of zeroes. Zeroes are not a diagnosis - they do not say whether
    # the tool is broken or simply has not been used yet. Say which.
    if not rows:
        print("")
        print("还没跑过任何一次闭环，所以上面全是 0。这不是故障。")
        print("  先看它怎么跑：mas demo")
        print("  再跑一个真任务：mas run --goal \"你的任务\" --criteria \"contains:关键词\"")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    """Check the human-decision chain: has it been changed?

    Every approval and every undo leaves a hash-linked entry next to
    the workspace it belongs to, and a copy in the main ledger. This
    command says whether the chain still verifies and whether the two
    copies still agree.

    A finding is not a failure of the tool - it is the tool doing its
    job: it says *what* disagrees, and where.
    """
    from mas.audit import audit_report

    config = load_config()
    engine = _engine(config)
    rows = engine.ledger.entries()
    workspace = str(getattr(args, "workspace", "") or "").strip()
    explicit = bool(workspace)
    if not workspace:
        base = pathlib.Path(
            os.path.expanduser(os.path.expandvars(str(config.get("state_dir") or "")))
        ) / "artifacts"
    else:
        base = pathlib.Path(workspace).expanduser()
    report = audit_report(base, rows)
    if getattr(args, "json", False):
        _print(report, True)
        return 0 if report.get("ok") else 1
    if not report.get("chain_count"):
        message = "还没有任何审计链：还没有人批准或撤销过改动。"
        if explicit:
            print(message)
        else:
            print(message)
            print(f"  工作区：{base}")
        return 0
    print(f"审计链 {report['chain_count']} 条，共 {report['entries']} 个事件：")
    for chain in report.get("chains") or []:
        mark = "OK" if chain.get("ok") else "有问题"
        print(f"  [{mark}] {chain.get('path')}  {chain.get('entries')} 个事件")
        for name, count in sorted((chain.get("events") or {}).items()):
            print(f"        {name}: {count}")
    problems = list(report.get("problems") or [])
    if problems:
        print()
        print(f"发现 {len(problems)} 个问题：")
        for problem in problems:
            print(f"  - {problem}")
        print()
        print("审计链被改过，或账本里的副本与链不一致。")
        return 1
    print()
    print("审计链完好：每一条记录都还是写下去时的那一条，账本副本与链一致。")
    # The audit is not only about *whether* the record is intact. It is
    # also about what the record says. A chain that verifies and holds
    # three taken-back approvals is intact - and has something to say
    # about the human's decisions.
    from mas.undos import format_undos, undo_report

    undos = undo_report(rows)
    if undos.get("records"):
        print()
        print(format_undos(undos))
    return 0


def cmd_seal(args: argparse.Namespace) -> int:
    """Check whether the closed loops still hold.

    Every closure is sealed to the bytes it was decided on: the
    acceptance criteria, the hash of the deliverable, the hash of every
    file the run wrote. This command re-derives the verdict from that
    sealed evidence and says whether it still holds - or whether
    somebody edited the files after the fact.

    A verdict that cannot be recomputed is a claim, not a proof, and
    this command is where that difference becomes visible.
    """
    from mas.seal import seal_report

    config = load_config()
    engine = _engine(config)
    rows = engine.ledger.entries()
    run_id = str(getattr(args, "run_id", "") or "").strip()
    if run_id:
        rows = [row for row in rows if str(row.get("run_id") or "") == run_id]
    report = seal_report(rows)
    if getattr(args, "json", False):
        _print(report, True)
        return 0 if report.get("ok") else 1
    if not report.get("sealed"):
        print("还没有任何封存：这份账本里的循环是在封存这一格补上之前跑的。")
        print("  新跑的循环会带上封存；想看已有的闭环：mas report")
        return 0
    print(f"封存 {report['sealed']} 条，其中仍然成立 {report['holds']} 条。")
    for item in report.get("results") or []:
        state = str(item.get("state") or "")
        mark = "OK " if item.get("ok") else "有变化"
        print(f"  [{mark}] {item.get('run_id')}  {state}")
        for problem in item.get("problems") or []:
            print(f"        - {problem}")
        for path in item.get("drifted_files") or []:
            print(f"        - 文件被改过：{path}")
        for path in item.get("missing_files") or []:
            print(f"        - 文件不在原处：{path}")
    if report.get("ok"):
        print()
        print("每一份封存都还成立：判定所依据的字节没有变过，结论可以重算。")
        return 0
    print()
    print("有封存不再成立：判定之后有东西被改动了，或结论已经无法重算。")
    return 1


def cmd_undos(args: argparse.Namespace) -> int:
    """Show the approvals that were taken back.

    The ledger is a river of events. This command is the list a person
    actually wants: *which of the things I approved had to be taken
    back, and how much did each one take back?*

    It reads the decision records - the ones the audit chain and the
    main ledger both hold - and pairs each undo with the approval it
    cancels. Nothing is guessed: an undo with no standing approval is
    reported as unmatched rather than paired with a neighbour.
    """
    from mas.undos import format_undos, undo_report

    config = load_config()
    engine = _engine(config)
    rows = engine.ledger.entries()

    workspace = str(getattr(args, "workspace", "") or "").strip()
    explicit = bool(workspace)
    if not workspace:
        base = pathlib.Path(
            os.path.expanduser(os.path.expandvars(str(config.get("state_dir") or "")))
        ) / "artifacts"
    else:
        base = pathlib.Path(workspace).expanduser()

    draft_id = str(getattr(args, "draft_id", "") or "").strip()
    # The chain is the authoritative record, so it is preferred - but
    # the ledger holds the copies, and a machine that has the ledger
    # but no chain (an older workspace) still has the answer.
    from mas.undos import workspace_rows

    chain_rows = workspace_rows(base)
    if chain_rows:
        rows = chain_rows
    report = undo_report(rows)

    if draft_id:
        from mas.undos import draft_history

        history = draft_history(rows, draft_id)
        if getattr(args, "json", False):
            _print(
                {
                    "workspace": str(base),
                    "draft_id": draft_id,
                    "approvals": history.get("approvals"),
                    "undos": history.get("undos"),
                    "ever_undone": history.get("ever_undone"),
                    "records": history.get("records"),
                },
                True,
            )
            return 0
        print("草稿 %s：批准 %d 次，撤销 %d 次" % (
            draft_id, history.get("approvals", 0), history.get("undos", 0)))
        if history.get("ever_undone"):
            print("  这个草稿的批准被撤销过 —— 这一点只保存在审计记录里。")
            print("  （草稿自身只记最新状态，所以第二次批准后文件上又写着“approved”。）")
        else:
            print("  这个草稿的批准没有被撤销过。")
        for record in history.get("records") or []:
            print("  批准：%s → 撤销：%s" % (
                record.get("approve_ts") or "-", record.get("undo_ts") or "-"))
            count = record.get("reverted_line_count")
            if isinstance(count, (int, float)):
                print("      这次撤销让 %d 行改动退回原样" % int(count))
        return 0

    if getattr(args, "json", False):
        _print(
            {
                "workspace": str(base),
                "records": report.get("records"),
                "unmatched": report.get("unmatched"),
                "totals": report.get("totals"),
            },
            True,
        )
        return 0

    print(format_undos(report))
    if not explicit:
        print()
        print(f"  工作区：{base}")
    print("  提示：加 --json 可以拿到原始字段（脚本用）")
    return 0


def cmd_ledger(args: argparse.Namespace) -> int:
    """Show the append-only ledger.

    An empty ledger used to print nothing at all. A command that
    prints nothing looks like a command that failed, and the beginner
    who just installed the tool cannot tell the difference between
    "nothing happened yet" and "something went wrong". An empty
    ledger now says so, and says what to run to fill it.
    """
    config = load_config()
    engine = _engine(config)
    rows = engine.ledger.tail(args.tail)
    if args.run_id:
        rows = [row for row in rows if row.get("run_id") == args.run_id]
    # The filters answer the questions the plain listing cannot: which
    # approvals were taken back, and what happened to one draft. They
    # narrow what is shown - they never change what the ledger holds.
    undos_only = bool(getattr(args, "undone", False))
    draft_filter = str(getattr(args, "draft", "") or "").strip()
    if undos_only or draft_filter:
        from mas.undos import decision_rows, pair_decisions

        decisions = decision_rows(rows)
        if undos_only:
            records, _unmatched = pair_decisions(decisions)
            # The undo *events* are shown, so the reader sees the same
            # rows the ledger holds - plus the approval each one
            # cancels, which is the point of asking.
            undo_ids = {str(record.get("undo_seq") or "") for record in records}
            extra: List[Dict[str, Any]] = []
            for record in records:
                approve_seq = str(record.get("approve_seq") or "")
                for row in decisions:
                    if str(row.get("seq") or "") == approve_seq:
                        extra.append(row)
                        break
            rows = [
                row
                for row in decisions
                if str(row.get("seq") or "") in undo_ids
            ] + extra
        if draft_filter:
            rows = [
                row
                for row in rows
                if str(row.get("draft_id") or "") == draft_filter
            ]
    if args.json:
        _print({"path": str(engine.ledger.path), "entries": rows}, True)
        return 0
    if not rows:
        if undos_only:
            print("没有被撤销的批准：目前每一次批准都还站着。")
            print("  想看全部记录：mas ledger")
            return 0
        print("账本还是空的：还没有任何一次循环记录。")
        print(f"  账本文件：{engine.ledger.path}")
        print("  先跑一次：mas run --goal \"你的任务\" --criteria \"contains:关键词\"")
        print("  想先看样例：mas demo")
        return 0
    for row in rows:
        # Every column used to carry the engine's own word: the role
        # ("closure"), the verdict ("pass"), the decision ("close"). A
        # reader who has not read the source code cannot tell whether
        # "closure" is a good thing. The columns now say what they
        # mean, and the raw values stay in the ledger and in --json.
        role = _both(row.get("role"), role_reading(row.get("role")))
        verdict = _both(row.get("verdict"), verdict_word(row.get("verdict")))
        decision = _both(row.get("decision"), decision_reading(row.get("decision")))
        bits = [f"#{row.get('seq')}", str(row.get("ts") or ""), role]
        if str(row.get("role") or "") in ("draft_approve", "draft_undo"):
            # A human decision does not belong to a round of the loop.
            # Printing "round 0" for it would be a lie - there was no
            # round - so the line shows the file instead.
            detail = str(row.get("path") or "")
            draft_id = str(row.get("draft_id") or "")
            if draft_id:
                bits.append(draft_id)
            note = str(row.get("note") or "")
            if detail:
                bits.append(detail)
            if note:
                bits.append(f"（{note}）")
            print("  ".join(bits))
            continue
        bits.append(f"第{row.get('iteration')}轮")
        if verdict:
            bits.append(verdict)
        if decision:
            bits.append(decision)
        sha = str(row.get("artifact_sha") or "")
        if sha:
            bits.append(f"产物 {sha[:10]}")
        print("  ".join(bits))
    print()
    print(f"共 {len(rows)} 条记录（完整账本：{engine.ledger.path}）")
    print("  提示：加 --json 可以拿到原始字段（脚本用）")
    return 0


#: The reading of a blocking issue. The implementation lives in
#: ``verdict_text`` so the report can use it too - the report cannot
#: import this module, because this module imports the report.
_issue_reading = issue_reading

def cmd_tickets(args: argparse.Namespace) -> int:
    """Show the human ticket queue - and say what to do with each one.

    The old listing was one line of ids and kinds: "T-2026...
    waiting_human loop_escalation some title". A person standing in
    front of an escalated run could not tell *what* was blocked, *why*
    the loop gave up, or *where the last result is* - the three
    things needed to decide whether to resume it. Every ticket now
    carries its task, its reason, its last deliverable and the exact
    command that continues it. An empty queue says so instead of
    printing nothing.
    """
    config = load_config()
    engine = _engine(config)
    tickets = engine.tickets.list(args.state)
    if args.json:
        _print({"tickets": tickets}, True)
        return 0
    if not tickets:
        if args.state:
            print(f"没有 {args.state} 状态的工单。")
        else:
            print("工单队列是空的：所有循环要么自己闭环了，要么还没开始。")
            print("  想看样例：mas demo")
        return 0

    waiting = [t for t in tickets if t.get("state") == "waiting_human"]
    resolved = [t for t in tickets if t.get("state") == "resolved"]

    def show(ticket: Dict[str, Any]) -> None:
        payload = ticket.get("payload") or {}
        title = payload.get("title") or payload.get("goal") or "(无标题)"
        print(f"{ticket['id']}  [{ticket.get('state')}]  {title}")
        goal = payload.get("goal") or ""
        if goal and goal != title:
            print(f"  任务：{goal}")
        run_id = payload.get("run_id") or ""
        if run_id:
            print(f"  运行：{run_id}")
        # The runs that belong to this problem. One ticket can cover
        # several runs - every resume makes another attempt on the same
        # problem - and the queue has to show that they are one problem
        # and not several.
        runs = [r for r in (payload.get("runs") or []) if r]
        if len(runs) > 1:
            print(f"  关联运行：{len(runs)} 次尝试（{runs[0]} … {runs[-1]}）")
        issues = payload.get("issues") or []
        criteria = payload.get("criteria") or []
        if issues:
            print(f"  卡在：{_issue_reading(issues[0], criteria)}")
            if len(issues) > 1:
                print(f"        （另有 {len(issues) - 1} 项未通过）")
        reason = payload.get("reason") or ""
        if reason:
            # In words, for the same reason as the run screen: the
            # ticket is what a human picks up, so its reason is
            # read by a human.
            print(f"  原因：{reason_text(reason)}")
            hint = reason_hint(reason)
            if hint:
                print(f"    {hint}")
        attempts = payload.get("attempts")
        if attempts:
            # The wording of this line is part of the tool's contract
            # with the people reading it: "已尝试" is what they look
            # for. The cumulative figure is added next to it, not
            # instead of it.
            print(f"  已尝试：{attempts} 轮（本次）")
        # The cumulative cost of the problem, across every resume. A
        # ticket that only shows the last attempt's round count makes
        # a problem that burned fifteen rounds look like a one-round
        # problem.
        total_rounds = payload.get("iterations_total")
        if total_rounds is None:
            total_rounds = payload.get("attempts_total")
        if total_rounds is not None and int(total_rounds or 0) > int(attempts or 0):
            print(f"  累计已烧：{int(total_rounds)} 轮（含历次续跑）")
        last = payload.get("last_artifact") or ""
        if last:
            print(f"  最后产物：{len(last)} 字")
        note = ticket.get("note") or ""
        if note:
            print(f"  人工意见：{note}")
        decision = ticket.get("decision") or ""
        if decision:
            print(f"  处理结果：{decision}")
        if ticket.get("state") == "waiting_human":
            # The command has to be one that can work. A ticket that
            # stopped on a ceiling continued with the same ceiling
            # stops at the same place again, so the command now comes
            # with a wider budget for the ceiling that stopped it.
            print(
                "  接着干："
                + continuation_command(
                    str(ticket["id"]),
                    reason,
                    payload.get("budget"),
                )
            )
            # A human who has already written the answer should not be
            # told to let the machine try again. The handback path puts
            # their deliverable through the *same* acceptance criteria
            # the loop was held to.
            print(
                "  交回交付物：mas handback %s --file <你的文件>"
                % ticket["id"]
            )
            # Where to edit. The ticket has always carried the folder
            # the run worked in, but no command ever printed it, so a
            # human who wanted to fix the files had to go and find the
            # folder by hand first. The folder is now named here, with
            # the command that hands it back.
            workspace = str(payload.get("workspace") or "").strip()
            if workspace:
                print("  在这里改：%s" % workspace)
                print(
                    "  改完交回：mas handback %s --workspace \"%s\""
                    % (ticket["id"], workspace)
                )
        print()

    if waiting:
        print(f"待处理工单：{len(waiting)} 个")
        print()
        for ticket in waiting:
            show(ticket)
    if resolved:
        print(f"已解决工单：{len(resolved)} 个")
        print()
        for ticket in resolved:
            show(ticket)
    return 0


def _print_handback_result(
    result: Any,
    args: argparse.Namespace,
    folder: bool = False,
) -> int:
    """Print the outcome of a handback - text or folder.

    Both kinds end in the same place: the same acceptance criteria
    decided it, and the same ticket either closes or stays open. The
    only difference the reader needs is *where the work came from* -
    one file, one blob of text, or a folder they edited in place.
    """
    if getattr(args, "json", False):
        _print(result.to_dict(), True)
        return 0 if result.closed else 2
    status_reading_text = status_reading(result.status)
    cell = (
        status_reading_text
        if status_reading_text == result.status
        else f"{status_reading_text}（{result.status}）"
    )
    print(f"状态：{cell}")
    print(f"工单：{args.ticket_id}")
    if folder:
        print(f"工作区：{result.workspace}")
        if result.files:
            print(f"交回的文件：{file_listing_reading(result.files)}")
            for row in result.files[:12]:
                print(f"  - {row.get('path')}")
            if len(result.files) > 12:
                print(f"  （另有 {len(result.files) - 12} 个文件）")
    for score in result.scores:
        flag = "PASS" if score["pass"] else ("?" if score["pass"] is None else "FAIL")
        optional = "" if score.get("blocking", True) else "，可选"
        reading_note = note_reading(score.get("note"))
        source = source_reading(score.get("source"))
        detail = reading_note or str(score.get("note") or "")
        if source and detail:
            detail = f"{source}：{detail}"
        elif source:
            detail = source
        print(f"  [{flag}] {score['id']}{optional} {detail}")
        reading = str(score.get("reading") or "")
        if reading:
            print(f"        要求：{reading}")
    if result.closed:
        print("人工交付物通过验收，工单已结。")
        if result.artifact_path:
            print(f"交付物：{result.artifact_path}")
    else:
        print(f"人工交付物没有通过验收：{result.error}")
        print(f"工单 {args.ticket_id} 仍在人工队列里，失败项已经记在票上。")
        if folder:
            # A refused folder handoff changes nothing on the disk:
            # the human's edits are still there. Saying so is part of
            # the answer - otherwise the reader may think the work was
            # thrown away and start over.
            print("你改过的文件原样保留在工作区里，可以接着改。")
    return 0 if result.closed else 2


def cmd_handback(args: argparse.Namespace) -> int:
    """Hand a human-written deliverable back into the loop.

    The ticket queue used to have exactly one answer: resume the loop
    and let the machine try again. A human who had already written the
    answer - the case that actually happens when a loop escalates -
    had nowhere to put it. This command takes the human's deliverable
    and puts it through *the same acceptance standards* the loop was
    held to. The standards come from the ticket, so they are the ones
    the task was written with.

    The deliverable may be a file or plain text. A file is the usual
    case: the human edited what the run produced.
    """
    config = load_config()
    engine = _engine(config)
    # A folder handoff: the human edited the files the run produced and
    # hands the folder back. The folder is judged as it stands, and
    # only a deliverable that passes is written back.
    if getattr(args, "workspace", None):
        try:
            result = engine.handback_workspace(
                args.ticket_id,
                args.note or "",
                args.workspace,
            )
        except KeyError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return _print_handback_result(result, args, folder=True)
    text = args.text or ""
    if getattr(args, "file", None):
        path = pathlib.Path(args.file).expanduser()
        if not path.exists():
            print(f"找不到文件：{path}", file=sys.stderr)
            return 1
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"读不了文件：{exc}", file=sys.stderr)
            return 1
    try:
        result = engine.handback(
            args.ticket_id,
            text,
            args.note or "",
        )
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return _print_handback_result(result, args, folder=False)


def cmd_resume(args: argparse.Namespace) -> int:
    config = load_config()
    engine = _engine(config)
    try:
        result = engine.resume(
            args.ticket_id,
            args.note or "",
            max_iterations=getattr(args, "max_iterations", None),
            deadline_sec=getattr(args, "deadline_sec", None),
            max_tokens=getattr(args, "max_tokens", None),
        )
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.json:
        _print(result.to_dict(), True)
    else:
        # Same wording as ``mas run``: the status in words, the raw
        # value kept visible.
        resume_status = status_reading(result.status)
        resume_cell = (
            resume_status
            if resume_status == result.status
            else f"{resume_status}（{result.status}）"
        )
        print(f"状态：{resume_cell}（迭代 {result.iterations} 次）")
        # A resumed loop is the expected path for an escalated run, so
        # its result was the one most likely to be lost. Say where the
        # deliverable is.
        if result.artifact_path:
            print(f"交付物：{result.artifact_path}")
        files = _result_files(result)
        if files:
            names = "、".join(str(row.get("path") or "") for row in files)
            print(f"本次生成的文件：{names}")
        for score in result.scores:
            flag = "PASS" if score["pass"] else "FAIL"
            optional = "" if score.get("blocking", True) else "，可选"
            # Same wording as ``mas run``: the note in words, where the
            # verdict came from, and the standard underneath. A resumed
            # run is read by the same person, in the same session, and
            # the two screens must not disagree about what happened.
            reading_note = note_reading(score.get("note"))
            source = source_reading(score.get("source"))
            detail = reading_note or str(score.get("note") or "")
            if source and detail:
                detail = f"{source}：{detail}"
            elif source:
                detail = source
            print(f"  [{flag}] {score['id']}{optional} {detail}")
            reading = str(score.get("reading") or "")
            if reading:
                print(f"        要求：{reading}")
        if not result.closed:
            # The ticket stays open on purpose - say so, so the human
            # knows the escalation did not disappear.
            print(f"工单 {args.ticket_id} 仍未解决，仍在人工队列里：mas tickets")
    return 0 if result.closed else 2


def _result_files(result: Any) -> List[Dict[str, Any]]:
    """The files a run wrote, read back from the ledger.

    The engine books them on the run's end entry, so the CLI shows
    what is *on the disk* and not what the run believes it wrote.
    """
    files = getattr(result, "files", None)
    if isinstance(files, list):
        return [row for row in files if isinstance(row, dict)]
    return []


def cmd_files(args: argparse.Namespace) -> int:
    """Index a folder: what is in it, and what kind of file each thing is.

    A run writes its work into a folder, and until now the only way to
    see that folder was to open it in an explorer. The reader of this
    repository asked for the other half of a local resource manager:
    one place where every file is listed, named by its own header, and
    described by the facts that file carries - the pages of a PDF, the
    family of a font, the duration of a recording.

    With no argument the command indexes every run workspace on this
    machine, so "what did I produce?" is one command away.
    """
    from mas.resources import describe_folder, facts_for, format_summary

    roots: List[str] = []
    labels: List[str] = []
    if args.path:
        roots.append(str(args.path))
        labels.append(str(args.path))
    else:
        config = load_config()
        root = pathlib.Path(
            os.path.expanduser(os.path.expandvars(str(config.get("state_dir") or "")))
        ) / "artifacts"
        if root.exists():
            for child in sorted(root.iterdir()):
                if child.is_dir():
                    roots.append(str(child))
                    labels.append(child.name)

    indexes: List[Dict[str, Any]] = []
    for label, target in zip(labels, roots, strict=False):
        index = describe_folder(target)
        index["label"] = label
        indexes.append(index)

    if args.json:
        _print(
            {
                "folders": indexes,
                "count": len(indexes),
                "files": sum(int(i["count"]) for i in indexes),
                "bytes": sum(int(i["bytes"]) for i in indexes),
            },
            True,
        )
        return 0

    if not indexes:
        print("还没有任何工作区。跑一次 `mas run` 就会生成。")
        return 0
    total = sum(int(i["count"]) for i in indexes)
    print(f"共 {len(indexes)} 个工作区，{total} 个文件：")
    for index in indexes:
        print()
        print(f"[{index['label']}]  {index['count']} 个文件  {index['size']}")
        for summary in format_summary(index):
            print(f"    {summary['format']:<10} {summary['count']} 个")
        for row in index["files"]:
            facts = facts_for(row)
            tail = ("  " + "、".join(facts)) if facts else ""
            print(f"    {row['size']:>9}  {row['format']:<10} {row['relative']}{tail}")
            if row.get("reason"):
                print(f"              ⚠ 读不开：{row['reason']}")
    return 0


def cmd_preview(args: argparse.Namespace) -> int:
    """Show what one file holds, in the terminal.

    The preview reads the file's own content: a PDF's words come out of
    its compressed stream, a Word document out of its archive, an image
    out of its pixels. A file with no text says so - the reader is told
    what the file *is* instead of being shown a blank screen.
    """
    from mas.resources import preview_file

    target = pathlib.Path(args.path).expanduser()
    if not target.exists():
        message = f"找不到这个文件：{target}"
        if getattr(args, "json", False):
            _print({"error": "file_not_found", "message": message, "found": False}, True)
        else:
            print(message, file=sys.stderr)
        return 2
    if target.is_dir():
        message = f"这是一个文件夹，不是文件：{target}\n用 `mas files {target}` 看里面的东西。"
        if getattr(args, "json", False):
            _print({"error": "not_a_file", "message": message, "found": False}, True)
        else:
            print(message, file=sys.stderr)
        return 2

    preview = preview_file(target, max_chars=int(args.max_chars))
    if args.json:
        _print(preview, True)
        return 0
    print(f"{preview['name']}  {preview['format']}  {preview['size']}")
    if preview.get("facts"):
        print("  " + "、".join(preview["facts"]))
    if preview.get("reason"):
        print(f"  ⚠ {preview['reason']}")
    text = str(preview.get("text") or "")
    if text.strip():
        print()
        print(text)
        if preview.get("truncated"):
            print()
            print("（正文太长，这里只显示了开头一部分）")
    elif not preview.get("reason"):
        print()
        print("（这个格式没有可预览的正文）")
    return 0


def _draft_roots(base: pathlib.Path) -> List[pathlib.Path]:
    """The folders that may hold drafts.

    The folder the reader names is the first candidate; its
    sub-folders follow, because a run writes into
    `<artifacts>/<run_id>/` and the draft sits next to the file it
    changes.
    """
    from mas.drafts import draft_roots

    return draft_roots(base)


def _all_drafts(
    roots: List[pathlib.Path],
    state: Optional[str],
) -> List[Dict[str, Any]]:
    """Every draft of every root, newest last."""
    from mas.drafts import list_drafts

    rows: List[Dict[str, Any]] = []
    for root in roots:
        rows.extend(list_drafts(root, state))
    return rows


def _find_draft(
    roots: List[pathlib.Path],
    draft_id: str,
) -> Optional[Tuple[pathlib.Path, Dict[str, Any]]]:
    """The root that owns a draft, and the draft itself."""
    from mas.drafts import get_draft

    for root in roots:
        draft = get_draft(root, draft_id)
        if draft is not None:
            return root, draft
    return None


def _draft_clash(draft: Dict[str, Any]) -> Dict[str, Any]:
    """Whether the file moved under a draft while it waited.

    A draft that was made against an older version of a file would
    throw the newer work away when it is approved, so the clash is
    reported wherever drafts are shown.
    """
    from mas.drafts import draft_conflict

    if str(draft.get("state") or "") != "pending":
        return {}
    try:
        clash = draft_conflict(str(draft.get("workspace") or ""), draft)
    except Exception:  # noqa: BLE001 - a broken draft must not break the listing
        return {}
    return clash if clash.get("conflict") else {}


def _draft_histories(
    roots: List[pathlib.Path],
    rows: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """What the audit record says about each draft in a list.

    The draft file holds only the newest state of a draft, so it
    cannot say "this was approved and then taken back". The audit
    chain can. This reads the chains of the workspaces the drafts
    live in and answers, for every draft, how often it was approved
    and how often that approval was taken back.

    A missing chain is not an error - it only means this workspace
    has no decisions yet.
    """
    from mas.undos import draft_history, workspace_rows

    wanted = {str(row.get("id") or "") for row in rows if row.get("id")}
    if not wanted:
        return {}
    histories: Dict[str, Dict[str, Any]] = {}
    for root in roots:
        try:
            chain_rows = workspace_rows(root)
        except Exception:  # noqa: BLE001 - a broken chain must not break the listing
            continue
        if not chain_rows:
            continue
        for draft_id in wanted:
            if draft_id in histories:
                continue
            history = draft_history(chain_rows, draft_id)
            if not history.get("approvals"):
                continue
            records = history.get("records") or []
            reverted = None
            if records:
                counts = [
                    record.get("reverted_line_count")
                    for record in records
                    if isinstance(record.get("reverted_line_count"), (int, float))
                ]
                if counts:
                    reverted = sum(int(count) for count in counts)
            history["reverted_line_count"] = reverted
            histories[draft_id] = history
    return histories


def cmd_drafts(args: argparse.Namespace) -> int:
    """Show the drafts of a workspace, and approve or reject them.

    A draft is a proposed change to a file that already exists. It
    sits next to the file until a human approves it, and approving it
    is the only thing that writes. The command is the human side of
    that rule: ``mas drafts`` lists what is waiting, ``mas drafts show
    <id>`` prints the diff, ``mas drafts approve <id>`` writes it back
    and ``mas drafts reject <id>`` throws it away.
    """
    from mas.drafts import DraftConflict, approve_draft, reject_draft

    action = str(getattr(args, "action", "") or "list")
    if action == "token":
        # The token belongs to the machine, not to a workspace: it is
        # what tells a proposal apart from an approval. It is asked
        # for before any workspace is looked at, so a machine that has
        # not run anything yet can still hand one out.
        from mas.drafts import ensure_human_token

        config = load_config()
        state = pathlib.Path(
            os.path.expanduser(os.path.expandvars(str(config.get("state_dir") or "")))
        )
        token = ensure_human_token(state)
        if getattr(args, "json", False):
            _print({"token": token, "state_dir": str(state)}, True)
            return 0
        print(token)
        return 0

    if action == "ticket":
        # Signing a ticket needs the human token, and the ticket is
        # bound to one draft. The token is asked for on the command
        # line so the human can paste it from wherever they keep it -
        # and so an agent that only has a ticket can never sign one.
        from mas.drafts import issue_ticket

        config = load_config()
        state = pathlib.Path(
            os.path.expanduser(os.path.expandvars(str(config.get("state_dir") or "")))
        )
        target = str(getattr(args, "draft_id", "") or "").strip()
        if not target:
            message = "签发票据要指明草稿编号：mas drafts ticket <草稿编号>"
            if getattr(args, "json", False):
                _print({"error": "draft_id_required", "message": message}, True)
            else:
                print(message, file=sys.stderr)
            return 2
        ticket_action = str(getattr(args, "ticket_action", "") or "approve").strip().lower()
        try:
            ticket = issue_ticket(
                state,
                target,
                action=ticket_action,
                human_token=getattr(args, "token", ""),
            )
        except ValueError as exc:
            message = str(exc)
            if getattr(args, "json", False):
                _print({"error": "ticket_refused", "message": message}, True)
            else:
                print(message, file=sys.stderr)
            return 2
        if getattr(args, "json", False):
            _print(
                {
                    "ticket_id": ticket.get("id"),
                    "ticket": ticket.get("secret"),
                    "draft_id": ticket.get("draft_id"),
                    "action": ticket.get("action"),
                    "state_dir": str(state),
                },
                True,
            )
            return 0
        print(ticket.get("secret"))
        return 0

    workspace = str(getattr(args, "workspace", "") or "").strip()
    explicit = bool(workspace)
    if not workspace:
        config = load_config()
        workspace = str(
            os.path.expanduser(os.path.expandvars(str(config.get("state_dir") or "")))
        )
        workspace = os.path.join(workspace, "artifacts")
    base = pathlib.Path(workspace).expanduser()
    if not base.exists():
        if not explicit:
            # The default workspace does not exist yet - that is not a
            # failure, it just means no run has produced anything on
            # this machine. A first-time reader should not be met with
            # an error code for an empty folder.
            message = "还没有任何工作区：先跑一次 mas run。"
            if getattr(args, "json", False):
                _print({"workspace": str(base), "drafts": [], "count": 0,
                        "message": message}, True)
            else:
                print(message)
            return 0
        message = f"找不到这个工作区：{base}"
        if getattr(args, "json", False):
            _print({"error": "workspace_not_found", "message": message}, True)
        else:
            print(message, file=sys.stderr)
        return 2

    # A run writes into `<state_dir>/artifacts/<run_id>/`, so the
    # drafts live one folder deeper than the default workspace. The
    # command walks *down* into the sub-folders, so a reader does not
    # have to know the id of the run that produced the draft.
    roots = _draft_roots(base)

    if action in ("", "list"):
        rows = _all_drafts(roots, getattr(args, "state", None))
        # What the draft's own state cannot hold. A draft that was
        # approved, taken back, and approved again is "approved" -
        # the fact that it was once wrong is only in the audit record.
        # The list shows it, so the reader does not have to know that
        # a second command exists.
        histories = _draft_histories(roots, rows)
        if getattr(args, "json", False):
            for row in rows:
                history = histories.get(str(row.get("id") or "")) or {}
                row["approvals"] = history.get("approvals", 0)
                row["undos"] = history.get("undos", 0)
                row["ever_undone"] = bool(history.get("ever_undone"))
                row["reverted_line_count"] = history.get("reverted_line_count")
            _print(
                {
                    "workspace": str(base),
                    "drafts": rows,
                    "count": len(rows),
                },
                True,
            )
            return 0
        if not rows:
            print("没有待确认的草稿。")
            return 0
        print(f"工作区 {base}：{len(rows)} 个草稿")
        for row in rows:
            changed = "有改动" if row.get("changed") else "无变化"
            print(
                f"  {row.get('id')}  {row.get('state')}  "
                f"{row.get('path')}  {changed}"
            )
            if row.get("reason"):
                print(f"        理由：{row.get('reason')}")
            if row.get("state") == "undone":
                print(
                    "        （已撤销：文件已退回改动前的样子，"
                    "这个草稿还在，可以再次批准）"
                )
            history = histories.get(str(row.get("id") or "")) or {}
            if history.get("ever_undone"):
                # The state says "approved"; the history says it was
                # wrong once. Both are true, and only the second one
                # is invisible without this line.
                reverted = history.get("reverted_line_count")
                size = (
                    "，这次撤销让 %d 行改动退回原样" % int(reverted)
                    if isinstance(reverted, (int, float))
                    else ""
                )
                print(
                    "        ⚠ 这个草稿的批准被撤销过 %d 次%s"
                    "（文件上的状态只记最新一次）" % (
                        int(history.get("undos") or 0), size
                    )
                )
            clash = _draft_clash(row)
            if clash:
                print(
                    "        ⚠ 文件在草稿等待期间被改过，批准会被拒"
                    "（除非加 --force）"
                )
        return 0

    draft_id = str(getattr(args, "draft_id", "") or "")
    if action == "conflict":
        # "Is this draft still safe to approve?" - asked without
        # changing anything. A human can check the whole list before
        # deciding to approve anything.
        if draft_id:
            found = _find_draft(roots, draft_id)
            if found is None:
                message = f"找不到这个草稿：{draft_id}"
                if getattr(args, "json", False):
                    _print({"error": "draft_not_found", "message": message}, True)
                else:
                    print(message, file=sys.stderr)
                return 2
            rows = [found[1]]
        else:
            rows = _all_drafts(roots, getattr(args, "state", None))
        clashes = []
        for row in rows:
            clash = _draft_clash(row)
            if clash:
                clashes.append(
                    {
                        "draft_id": row.get("id"),
                        "path": clash.get("path") or row.get("path"),
                        "expected_sha256": clash.get("expected_sha256"),
                        "current_sha256": clash.get("current_sha256"),
                    }
                )
        if getattr(args, "json", False):
            _print(
                {
                    "workspace": str(base),
                    "conflicts": clashes,
                    "count": len(clashes),
                    "checked": len(rows),
                },
                True,
            )
            return 0
        if not clashes:
            print(f"没有冲突：{len(rows)} 个草稿的文件都还在原样。")
            return 0
        print(f"{len(clashes)} 个草稿的文件在等待期间被改过：")
        for row in clashes:
            print(
                "  %s  %s  期望 %s  实际 %s"
                % (
                    row.get("draft_id"),
                    row.get("path"),
                    str(row.get("expected_sha256") or "")[:12],
                    str(row.get("current_sha256") or "")[:12],
                )
            )
        print("批准这些草稿需要加 --force。")
        return 0

    if action == "show":
        found = _find_draft(roots, draft_id)
        draft = found[1] if found else None
        if draft is None:
            message = f"找不到这个草稿：{draft_id}"
            if getattr(args, "json", False):
                _print({"error": "draft_not_found", "message": message}, True)
            else:
                print(message, file=sys.stderr)
            return 2
        if getattr(args, "json", False):
            _print({"draft": draft}, True)
            return 0
        print(f"草稿 {draft.get('id')}  {draft.get('state')}")
        print(f"文件：{draft.get('path')}")
        if draft.get("reason"):
            print(f"理由：{draft.get('reason')}")
        print(f"改动前 {draft.get('before_bytes')} 字节，改动后 {draft.get('after_bytes')} 字节")
        clash = _draft_clash(draft)
        if clash:
            print(
                "⚠ 文件在草稿等待期间被改过：期望 %s，实际 %s。"
                "批准会被拒，除非加 --force。"
                % (
                    str(clash.get("expected_sha256") or "")[:12],
                    str(clash.get("current_sha256") or "")[:12],
                )
            )
        print()
        for line in draft.get("diff") or []:
            print(line)
        return 0

    if action == "compare":
        # "What would I lose?" - asked before the --force. A conflict
        # says the file moved; this says *what* the human wrote there
        # and the draft would take back.
        found = _find_draft(roots, draft_id)
        if found is None:
            message = f"找不到这个草稿：{draft_id}"
            if getattr(args, "json", False):
                _print({"error": "draft_not_found", "message": message}, True)
            else:
                print(message, file=sys.stderr)
            return 2
        owner = found[0]
        draft = found[1]
        from mas.drafts import draft_compare

        try:
            comparison = draft_compare(owner, draft)
        except Exception as exc:  # noqa: BLE001 - a report must not crash
            message = f"没法比较这个草稿：{type(exc).__name__}: {exc}"
            if getattr(args, "json", False):
                _print({"error": "compare_failed", "message": message}, True)
            else:
                print(message, file=sys.stderr)
            return 2
        if getattr(args, "json", False):
            _print({"workspace": str(base), "comparison": comparison}, True)
            return 0
        print(f"草稿 {comparison.get('draft_id')}  {comparison.get('path')}")
        if comparison.get("conflict"):
            print(
                "⚠ 文件在草稿等待期间被改过：期望 %s，实际 %s。"
                % (
                    str(comparison.get("expected_sha256") or "")[:12],
                    str(comparison.get("current_sha256") or "")[:12],
                )
            )
        else:
            print("文件自草稿建立以来没有被改过。")
        if not comparison.get("readable"):
            if comparison.get("file_missing"):
                print("文件现在不在磁盘上了。")
            elif not comparison.get("baseline_available"):
                print("这份草稿没有留下基线（它是旧版本留下的），比不出逐行差异。")
            else:
                print("这个文件不是 UTF-8 文本，比不出逐行差异，只能比哈希。")
            print(
                "基线 %s / 当前 %s / 草稿 %s"
                % (
                    str(comparison.get("baseline_sha256") or "")[:12],
                    str(comparison.get("current_sha256") or "")[:12],
                    str(comparison.get("draft_sha256") or "")[:12],
                )
            )
            return 0
        lost = list(comparison.get("would_lose") or [])
        if lost:
            print()
            print(f"覆盖会丢掉这 {len(lost)} 行（你写的，草稿里没有）：")
            for line in lost:
                print("  - %s" % line)
        else:
            print()
            print("覆盖不会丢掉任何一行：草稿涵盖了你写的全部内容。")
        print()
        print("--- 你写的改动（基线 → 当前） ---")
        for line in comparison.get("baseline_to_current") or []:
            print(line)
        print()
        print("--- 草稿要写的改动（当前 → 草稿） ---")
        for line in comparison.get("current_to_draft") or []:
            print(line)
        return 0

    if action == "undo":
        # "Take it back." The draft kept the bytes the file held before
        # the change, so the undo puts the file back exactly - and the
        # draft stays, so the same change can be approved again once
        # the reason it was undone is understood.
        found = _find_draft(roots, draft_id)
        if found is None:
            message = f"找不到这个草稿：{draft_id}"
            if getattr(args, "json", False):
                _print({"error": "draft_not_found", "message": message}, True)
            else:
                print(message, file=sys.stderr)
            return 2
        owner = found[0]
        owner_draft = found[1]
        from mas.drafts import DraftUndoConflict, draft_undo_plan, undo_draft

        plan = draft_undo_plan(owner, owner_draft)
        if not getattr(args, "force", False):
            reason = str(plan.get("reason") or "")
            if reason == "not_approved":
                message = (
                    f"这个草稿还没批准过（当前状态 {owner_draft.get('state')}），"
                    "没什么可撤销的。"
                )
                if getattr(args, "json", False):
                    _print({"error": "draft_not_approved", "message": message}, True)
                else:
                    print(message, file=sys.stderr)
                return 2
            if reason == "no_baseline":
                message = (
                    "这个草稿没有留下改动前的基线（v0.7.76 之前建立的），"
                    "没有可以退回的内容。"
                )
                if getattr(args, "json", False):
                    _print({"error": "no_baseline", "message": message}, True)
                else:
                    print(message, file=sys.stderr)
                return 2
            if reason == "file_missing":
                message = f"文件已经不在磁盘上了：{owner_draft.get('path')}"
                if getattr(args, "json", False):
                    _print({"error": "file_missing", "message": message}, True)
                else:
                    print(message, file=sys.stderr)
                return 2
            if reason == "file_changed":
                lost = list(plan.get("would_lose") or [])
                message = (
                    "文件在批准之后又被改过，撤销被拒："
                    "批准时 %s，现在 %s。文件保持现在的内容不变；"
                    "确认要连这些改动一起退掉就用 --force。"
                    % (
                        str(plan.get("approved_sha256") or "")[:12],
                        str(plan.get("current_sha256") or "")[:12],
                    )
                )
                if getattr(args, "json", False):
                    _print(
                        {
                            "error": "undo_conflict",
                            "message": message,
                            "path": owner_draft.get("path"),
                            "approved_sha256": plan.get("approved_sha256"),
                            "current_sha256": plan.get("current_sha256"),
                            "would_lose": lost,
                            "would_lose_count": len(lost),
                            "draft_id": draft_id,
                            "state": owner_draft.get("state"),
                        },
                        True,
                    )
                else:
                    # The refusal and what it would cost belong
                    # together, and both go to stderr: this is an
                    # error report, not a result.
                    print(message, file=sys.stderr)
                    if lost:
                        print(
                            f"撤销会丢掉这 {len(lost)} 行（批准之后才写进去的）：",
                            file=sys.stderr,
                        )
                        for line in lost:
                            print("  - %s" % line, file=sys.stderr)
                return 2
        try:
            draft = undo_draft(
                owner,
                draft_id,
                str(getattr(args, "note", "") or ""),
                force=bool(getattr(args, "force", False)),
            )
        except DraftUndoConflict as exc:
            clash = dict(getattr(exc, "plan", {}) or {})
            message = str(exc)
            if getattr(args, "json", False):
                _print(
                    {
                        "error": "undo_conflict",
                        "message": message,
                        "path": clash.get("path") or owner_draft.get("path"),
                        "approved_sha256": clash.get("approved_sha256"),
                        "current_sha256": clash.get("current_sha256"),
                        "draft_id": draft_id,
                    },
                    True,
                )
            else:
                print(message, file=sys.stderr)
            return 2
        except KeyError:
            message = f"找不到这个草稿：{draft_id}"
            if getattr(args, "json", False):
                _print({"error": "draft_not_found", "message": message}, True)
            else:
                print(message, file=sys.stderr)
            return 2
        except ValueError as exc:
            message = str(exc)
            if getattr(args, "json", False):
                _print({"error": "undo_refused", "message": message}, True)
            else:
                print(message, file=sys.stderr)
            return 2
        message = f"已撤销：{draft.get('path')}（回到改动前的样子，草稿还在，可以再次批准）"
        if getattr(args, "json", False):
            _print({"draft": draft, "plan": plan, "message": message}, True)
            return 0
        print(message)
        return 0

    if action in ("approve", "reject"):
        found = _find_draft(roots, draft_id)
        if found is None:
            message = f"找不到这个草稿：{draft_id}"
            if getattr(args, "json", False):
                _print({"error": "draft_not_found", "message": message}, True)
            else:
                print(message, file=sys.stderr)
            return 2
        owner = found[0]
        owner_draft = found[1]
        try:
            if action == "approve":
                draft = approve_draft(
                    owner,
                    draft_id,
                    str(getattr(args, "note", "") or ""),
                    force=bool(getattr(args, "force", False)),
                )
                message = f"已写回：{draft.get('path')}"
            else:
                draft = reject_draft(owner, draft_id, str(getattr(args, "note", "") or ""))
                message = f"已丢弃：{draft.get('path')}"
        except DraftConflict as exc:
            # The file moved under the draft. The approval is refused
            # and the human is told *what* moved and *how* - not just
            # "no". The file on disk keeps the newer work.
            clash = dict(getattr(exc, "clash", {}) or {})
            message = (
                "文件在草稿等待期间被改过，批准被拒："
                "期望 %s，实际 %s。文件保持现在的内容不变；"
                "确认要覆盖就用 --force 重新批准。"
                % (
                    str(clash.get("expected_sha256") or "")[:12],
                    str(clash.get("current_sha256") or "")[:12],
                )
            )
            if getattr(args, "json", False):
                _print(
                    {
                        "error": "draft_conflict",
                        "message": message,
                        "path": clash.get("path") or owner_draft.get("path"),
                        "expected_sha256": clash.get("expected_sha256"),
                        "current_sha256": clash.get("current_sha256"),
                        "draft_id": draft_id,
                        "state": owner_draft.get("state"),
                    },
                    True,
                )
            else:
                print(message, file=sys.stderr)
            return 2
        except KeyError:
            message = f"找不到这个草稿：{draft_id}"
            if getattr(args, "json", False):
                _print({"error": "draft_not_found", "message": message}, True)
            else:
                print(message, file=sys.stderr)
            return 2
        except ValueError as exc:
            message = str(exc)
            if getattr(args, "json", False):
                _print({"error": "draft_refused", "message": message}, True)
            else:
                print(message, file=sys.stderr)
            return 2
        if getattr(args, "json", False):
            _print({"draft": draft, "message": message}, True)
            return 0
        print(message)
        return 0

    message = f"未知的草稿操作：{action}"
    if getattr(args, "json", False):
        _print({"error": "unknown_action", "message": message}, True)
    else:
        print(message, file=sys.stderr)
    return 2

def cmd_artifacts(args: argparse.Namespace) -> int:
    """List the deliverables this machine has produced.

    The state directory held the answers and no command could list
    them. A user who closed the terminal had no way back to their own
    results - the ledger was a log of calls, the report a review of a
    run, and neither answered the only question that matters after a
    while: "what did I actually get?"
    """
    config = load_config()
    engine = _engine(config)
    rows = engine.ledger.entries()
    rows, missing = _rows_for_run(rows, args.run_id)
    if missing:
        return _report_missing(missing, args, None)
    # The listing itself lives in ``mas.report``, because the MCP
    # surface needs the same answer and the two must never drift.
    items = artifact_rows(rows)
    if args.json:
        _print({"artifacts": items, "count": len(items)}, True)
        return 0
    print(format_artifacts(items))
    return 0


def cmd_closure(args: argparse.Namespace) -> int:
    """Judge whether the work is closed, without running anything.

    The report shows every number - the runs, the threads, the queue,
    the drafts, the seal - but reading it and working out the verdict
    was left to the reader. The rules were never written down anywhere
    the reader could reach them.

    So "closed" was a thing you could show and not a thing you could
    ask. This asks it: it reads the ledger, the tickets and the drafts
    and answers whether anything is still waiting - and when the answer
    is no, it names exactly which part is still open.

    It reads the existing records. It never starts a new loop, so it is
    safe to run at any time and costs nothing but a read.
    """
    config = load_config()
    engine = _engine(config)
    rows = engine.ledger.entries()
    rows, missing = _rows_for_run(rows, args.run_id)
    if missing:
        return _report_missing(missing, args, None)
    tickets = engine.tickets.list()
    if args.run_id:
        wanted = {
            str(row.get("ticket_id") or "")
            for row in rows
            if str(row.get("ticket_id") or "")
        }
        tickets = [t for t in tickets if str(t.get("id") or "") in wanted]
    drafts = drafts_of_machine(config, rows)
    if args.run_id:
        wanted_drafts = {
            str(item.get("id") or "")
            for row in rows
            if str(row.get("role") or "") == "draft"
            for item in (row.get("drafts") or [])
            if isinstance(item, dict)
        }
        drafts = [d for d in drafts if str(d.get("id") or "") in wanted_drafts]
    # The verdict lives in ``mas.report``, because the MCP surface
    # needs the same answer and the two must never drift.
    verdict = closure_verdict_for(rows, tickets, drafts=drafts)
    if args.json:
        _print(verdict, True)
    else:
        print(format_closure(verdict))
    return 0 if verdict["ok"] else 2


def _rows_for_run(rows, run_id):
    """Split the ledger into "what you asked for" and "what exists".

    Every command that takes ``--run-id`` used to filter the ledger and
    then say "the ledger is empty" when nothing came back. That was a
    lie in the common case: the ledger held twenty-two records for a
    different run, and the reader was told there was no history at all.
    A person who mistypes a run id - or follows a ticket that points at
    a run from another machine - needs to know which of the two it is.

    Returns ``(selected, missing)``:

    * ``selected`` - the rows that match;
    * ``missing`` - ``None`` when the request was satisfiable, or a
      message that says what went wrong.
    """
    if not run_id:
        return rows, None
    selected = [row for row in rows if row.get("run_id") == run_id]
    if selected:
        return selected, None
    known = sorted({str(row.get("run_id")) for row in rows if row.get("run_id")})
    if not rows:
        return selected, "账本里还没有任何运行记录。"
    if not known:
        return selected, "账本里还没有任何运行记录。"
    listed = "、".join(known[:8])
    if len(known) > 8:
        listed += " 等"
    return selected, (
        f"没有找到运行 {run_id}。\n"
        f"  账本里有这些运行：{listed}\n"
        f"  用 mas status 看一眼有哪些运行，用 mas replay --run-id <运行ID> 回放其中一次。"
    )


def _report_missing(message, args, payload):
    """Say that something was not found, and exit the way a miss should.

    ``--json`` consumers get a machine-readable object instead of prose,
    so a script can tell "not found" from "found nothing".
    """
    if getattr(args, "json", False):
        _print({"error": "run_not_found", "message": message, "found": False}, True)
    else:
        print(message, file=sys.stderr)
    return 2


def _state_dir_of(config) -> str:
    """The state directory of a given configuration, expanded."""
    raw = str((config or {}).get("state_dir") or "")
    return os.path.expanduser(os.path.expandvars(raw))


def drafts_of_machine(config, ledger_rows=None) -> List[Dict[str, Any]]:
    """Every draft of every workspace this machine owns.

    The walk lives in ``mas.drafts`` - one implementation, shared with
    the MCP server, so the two surfaces cannot drift apart on which
    drafts are waiting. A report that says "no drafts" while a change
    waits on the disk is the exact failure this project keeps fixing.
    """
    from mas.drafts import all_drafts_of_machine

    return all_drafts_of_machine(
        _state_dir_of(config),
        (config or {}).get("workspace"),
        ledger_rows,
    )


def _workspaces_of_drafts(rows) -> List[pathlib.Path]:
    """The workspaces the ledger says hold drafts.

    A draft row names the file the loop wanted to change, and the
    absolute path of that file. The workspace is the folder that file
    sits in - so the workspace can be read back out of the row. That
    matters because the workspace is *not* part of the saved
    configuration: it belongs to the run, and a reader that looked
    only at the configuration found nothing.
    """
    workspaces: List[pathlib.Path] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("role") or "") != "draft":
            continue
        for item in row.get("drafts") or []:
            if not isinstance(item, dict):
                continue
            absolute = str(item.get("absolute") or "").strip()
            relative = str(item.get("path") or "").strip().replace("\\", "/")
            if not absolute:
                continue
            depth = len([p for p in relative.split("/") if p]) or 1
            parents = list(pathlib.Path(absolute).parents)
            if len(parents) < depth:
                continue
            workspace = parents[depth - 1]
            if workspace not in workspaces:
                workspaces.append(workspace)
    return workspaces




def cmd_report(args: argparse.Namespace) -> int:
    """Render a Markdown closure report from the ledger."""
    config = load_config()
    engine = _engine(config)
    rows = engine.ledger.entries()
    rows, missing = _rows_for_run(rows, args.run_id)
    if missing:
        return _report_missing(missing, args, None)
    # The human ticket queue is read as well. The ledger says what
    # the machine did; the queue says what is still waiting for a
    # person - and a report that only reads the ledger leaves the
    # reader with work they cannot see.
    tickets = engine.tickets.list()
    if args.run_id:
        # A report about one run must not drag in the whole queue.
        # Only the tickets this run is part of are shown.
        wanted = {
            str(row.get("ticket_id") or "")
            for row in rows
            if str(row.get("ticket_id") or "")
        }
        tickets = [t for t in tickets if str(t.get("id") or "") in wanted]
    # The draft queue is read as well. A run that proposed a change
    # instead of making it leaves a draft behind - and a report that
    # never reads it says "closed" while the file on the disk is
    # still the old one.
    drafts = drafts_of_machine(config, rows)
    if args.run_id:
        # Only the drafts of this run are shown.
        wanted_drafts = {
            str(item.get("id") or "")
            for row in rows
            if str(row.get("role") or "") == "draft"
            for item in (row.get("drafts") or [])
            if isinstance(item, dict)
        }
        drafts = [d for d in drafts if str(d.get("id") or "") in wanted_drafts]
    report = build_report(rows, tickets, drafts=drafts)
    markdown = render_markdown(report)
    if args.out:
        path = pathlib.Path(args.out).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
        print(f"报告已写入：{path}")
        return 0
    if args.json:
        _print(report, True)
        return 0
    print(markdown)
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    """Replay a run from the ledger as an ordered trace."""
    config = load_config()
    engine = _engine(config)
    rows = engine.ledger.entries()
    rows, missing = _rows_for_run(rows, args.run_id)
    if missing:
        return _report_missing(missing, args, None)
    if not rows:
        # An empty ledger is not a failure of the reader's request,
        # but a JSON consumer must not receive empty stdout and a
        # non-zero code with nothing to parse. Both shapes now say
        # the same thing.
        message = "账本为空，没有可回放的运行。跑一次 mas run 就会产生记录；想看样例：mas demo"
        if args.json:
            _print(
                {
                    "error": "empty_ledger",
                    "found": False,
                    "message": message,
                    "steps": [],
                    "steps_count": 0,
                    "runs": [],
                    "threads": [],
                    "problems_open": [],
                },
                True,
            )
        else:
            print(message, file=sys.stderr)
        return 1
    trace = replay_trace(rows)
    if args.json:
        _print(trace, True)
        return 0
    print(f"回放 {trace['steps_count']} 条账本记录")
    # The steps are grouped by the run they belong to. A flat list of
    # twenty entries does not say whether the loop handled one problem
    # twice or two problems once - the grouping does.
    for block in trace.get("runs") or []:
        print()
        raw_status = block.get("status") or ""
        print(
            f"── 运行 {block.get('run_id')}"
            f"（{status_reading(raw_status)}（{raw_status}）"
            f"，{block.get('iterations')} 轮"
            f"，{block.get('tokens')} token）"
        )
        goal = block.get("goal") or ""
        if goal:
            print(f"   任务：{goal}")
        # Say when a person, not the loop, produced the answer. The
        # replay is what a reader opens to understand how a problem
        # was solved, and "how it was solved" includes "by whom".
        if block.get("handback"):
            print("   🤝 来源：人工交付物")
            human_note = block.get("human_note") or ""
            if human_note:
                print(f"      人工意见：{human_note}")
        for step in trace["steps"]:
            if step.get("run_id") != block.get("run_id"):
                continue
            mark = "*" if step.get("closed") else "-"
            detail = step.get("detail_reading") or step.get("detail") or ""
            # The role column says "handback" in the machine's own
            # word. The reader gets the word, and the raw name stays
            # beside it so a grep still works.
            role_raw = str(step.get("role") or "")
            role_text = role_reading(role_raw)
            role_cell = (
                role_raw
                if not role_text or role_text == role_raw
                else f"{role_text}（{role_raw}）"
            )
            print(
                f"  {mark} #{step.get('seq')} iter={step.get('iteration')} "
                f"{role_cell} {step.get('decision') or ''} {detail}".rstrip()
            )
        # An unfinished run needs somebody to pick it up. The replay
        # used to stop at the last line of the trace, so a reader who
        # replayed an escalated run learned what went wrong and not
        # what to do about it - the same gap the report closed in
        # v0.7.49, left open here.
        ticket_id = block.get("ticket_id") or ""
        if ticket_id and not block.get("closed"):
            print(
                f"   → 接着干：mas resume {ticket_id} --note \"你的意见\""
            )
    # The problems behind the runs, so "one problem, two attempts"
    # reads as one problem and two attempts.
    threads = trace.get("threads") or []
    # Every problem gets a row, even a single attempt: "one open problem"
    # is exactly the case where the reader most needs to see that it is
    # still open. The handover hint below says how to continue; this
    # says whether it is done.
    if threads:
        print()
        print("按问题分组：")
        for thread in threads:
            label = thread.get("title") or thread.get("goal") or thread.get("thread_id")
            state = "已解决" if thread.get("closed") else "待处理"
            # Who solved it. A problem a person finished has to look
            # different from a problem the loop finished, or the
            # queue quietly takes credit for a human's work.
            source = ""
            if thread.get("handback"):
                source = "　来源：人工完成"
            elif thread.get("closed"):
                source = "　来源：机器闭环"
            print(
                f"  [{state}] {label}"
                f"　{thread.get('run_count')} 次尝试"
                f"　{thread.get('iterations')} 轮{source}"
            )
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Export the raw ledger as a Markdown table."""
    config = load_config()
    engine = _engine(config)
    rows = engine.ledger.entries()
    rows, missing = _rows_for_run(rows, args.run_id)
    if missing:
        return _report_missing(missing, args, None)
    if not rows:
        # An empty table is a table that says nothing. A reader who
        # exports an empty ledger should be told what to do next,
        # not handed a header row and left to guess.
        if args.json:
            _print(
                {
                    "rows": [],
                    "count": 0,
                    "markdown": "",
                    "message": "账本为空，没有可导出的记录。跑一次 mas run 就会产生记录。",
                },
                True,
            )
            return 0
        print("账本为空，没有可导出的记录。")
        print("  跑一次 mas run 就会产生记录；想看样例：mas demo")
        return 0
    markdown = ledger_markdown(rows)
    if args.json:
        # ``--json`` was accepted but ignored, so a script that asked
        # for JSON got a Markdown table and had to parse prose. The
        # rows are the data; the Markdown is one rendering of them.
        _print(
            {
                "rows": rows,
                "count": len(rows),
                "markdown": markdown,
            },
            True,
        )
        return 0
    if args.out:
        path = pathlib.Path(args.out).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
        print(f"账本已导出：{path}")
        return 0
    print(markdown)
    return 0


def cmd_clean(args: argparse.Namespace) -> int:
    """Remove the rows the old self-test left in the ledger."""
    config = load_config()
    state_dir = os.path.expanduser(
        os.path.expandvars(str(config.get("state_dir") or ""))
    )
    dry_run = bool(getattr(args, "dry_run", False))
    result = clean_selftest_ledger(state_dir, dry_run=dry_run)
    if args.json:
        _print(result, True)
        return 0
    message = result.get("message") or ""
    if result.get("error"):
        print("错误：%s" % result["error"], file=sys.stderr)
        return 1
    print(message)
    if result.get("backup"):
        print("  备份：%s" % result["backup"])
    if not dry_run and result.get("dropped_count"):
        print("  接下来可以跑 mas report 看看账本是不是已经干净了。")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Run a real closed loop and report whether it closes."""
    config = load_config()

    def progress(message: str) -> None:
        # stderr, so that ``--json`` still emits nothing but JSON on
        # stdout. A beginner watching a three minute self-check needs
        # to see that something is happening.
        print(f"... {message}", file=sys.stderr, flush=True)

    result = run_verify(config, progress=progress)
    if args.json:
        _print(result, True)
    else:
        print(format_verify(result))
    return 0 if result["ok"] else 2


def cmd_bench(args: argparse.Namespace) -> int:
    """Benchmark the loop runtime against the offline mock backend."""
    result = run_bench(repeats=args.repeats, scenarios=args.scenario or None)
    if args.json:
        _print(result, True)
    else:
        print(format_bench(result))
    return 0 if result["summary"]["expectation_rate"] >= 1.0 else 1


def cmd_teams(args: argparse.Namespace) -> int:
    teams = list_teams()
    if args.json:
        _print({"teams": teams}, True)
    else:
        for team in teams:
            print(f"{team['name']:<12} {team['description']}")
    return 0


def cmd_selftest(args: argparse.Namespace) -> int:
    """Hammer the paths that used to fail at random.

    Two bugs in this project only showed up once in a while. A test
    that runs a path once cannot catch them, so the method that found
    them - hammer one path many times and watch the invariant - is a
    command of its own.

    Everything runs offline, against loopback servers started on the
    spot. No endpoint, no key, no network.
    """
    from mas.selftest import format_selftest, run_selftest

    result = run_selftest(
        iterations=args.iterations,
        only=args.only or None,
    )
    if args.json:
        _print(result, True)
    else:
        print(format_selftest(result), end="")
    return 0 if result["ok"] else 2


def cmd_doctor(args: argparse.Namespace) -> int:
    config = load_config()
    results = run_doctor(config, allow_network=not getattr(args, "no_network", False))
    final = verdict(results)
    if args.json:
        payload = summarize(results)
        payload["verdict"] = final
        _print(payload, True)
    else:
        print(format_doctor(results), end="")
        print("结论：" + final["headline"])
        print("下一步：" + final["action"])
    # Only a hard break of the loop itself is a failure. The optional
    # parts (skills, MCP registration) are warnings: the loop still runs
    # without them, and `mas doctor` must stay exit-0 on a fresh machine
    # so that CI can call it as a smoke test.
    return 1 if final["state"] == "broken" else 0


def cmd_serve(args: argparse.Namespace) -> int:
    from mas.mcp_server import serve

    return serve()


def cmd_http(args: argparse.Namespace) -> int:
    """Serve the MCP tool surface over HTTP + SSE."""
    from mas.http_server import serve_http

    return serve_http(args.host, args.port, args.verbose, args.token)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mas",
        description="OpenLoop MAS - 多智能体闭环运行时（loop engineering runtime）",
    )
    parser.add_argument("--version", action="version", version=f"openloop-mas {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_json(target: argparse.ArgumentParser) -> None:
        target.add_argument("--json", action="store_true", help="以 JSON 输出")

    install = sub.add_parser("install", help="安装 skills 并注册 MCP 服务器（pip 安装后必跑）")
    install.add_argument("--python", default=None, help="MCP 服务器使用的 Python 解释器")
    install.add_argument("--skip-skills", action="store_true", help="不安装 agent skills")
    install.add_argument("--skip-mcp", action="store_true", help="不注册 MCP 服务器")
    add_json(install)
    install.set_defaults(func=cmd_install)

    sync = sub.add_parser("sync", help="把已安装的 skills 同步回包内版本（修复漂移）")
    sync.add_argument("--dry-run", action="store_true", help="只报告漂移，不写入")
    add_json(sync)
    sync.set_defaults(func=cmd_sync)

    upgrade = sub.add_parser(
        "upgrade",
        help="把已安装的旧版运行时换成当前这版（升级真正的运行时本体）",
    )
    upgrade.add_argument("--force", action="store_true", help="版本相同也强制替换")
    upgrade.add_argument(
        "--fix-shadow",
        action="store_true",
        help="一并处理 PATH 上那些旧副本（你敲 mas 时实际跑的那份）",
    )
    add_json(upgrade)
    upgrade.set_defaults(func=cmd_upgrade)

    runtime = sub.add_parser(
        "runtime",
        help="看本机装的是哪一版运行时，和当前运行的差多少",
    )
    add_json(runtime)
    runtime.set_defaults(func=cmd_runtime_status)

    shadow = sub.add_parser(
        "shadow",
        help="看 PATH 上到底有几份 mas，你敲 mas 跑的是哪一份",
    )
    add_json(shadow)
    shadow.set_defaults(func=cmd_shadow)

    uninstall = sub.add_parser("uninstall", help="移除 skills 与 MCP 注册（保留账本）")
    uninstall.add_argument("--skip-skills", action="store_true", help="不移除 agent skills")
    uninstall.add_argument("--skip-mcp", action="store_true", help="不移除 MCP 注册")
    add_json(uninstall)
    uninstall.set_defaults(func=cmd_uninstall)

    init = sub.add_parser("init", help="生成/更新本地配置")
    init.add_argument("--wizard", action="store_true", help="交互式向导，一步步生成配置")
    init.add_argument("--provider", choices=["mock", "openai_compat", "anthropic"])
    init.add_argument("--model")
    init.add_argument("--base-url")
    init.add_argument("--team")
    init.add_argument("--state-dir")
    init.add_argument("--api-key-env")
    init.add_argument("--max-iterations", type=int)
    # The other three ceilings the loop has. They are settable
    # here for the same reason --max-iterations is: the reason a
    # run did not close points the reader at exactly one of them,
    # so the command it names has to exist.
    init.add_argument("--deadline-sec", type=int, help="这轮最多用多少秒")
    init.add_argument("--max-tokens", type=int, help="这轮最多用多少 token")
    init.add_argument("--stagnation-limit", type=int, help="交付物连续几次没变化就停下来")
    add_json(init)
    init.set_defaults(func=cmd_init)

    run = sub.add_parser("run", help="运行一个闭环任务")
    run.add_argument("--goal", required=True, help="任务目标")
    run.add_argument("--title", default="")
    run.add_argument("--criteria", action="append", help="验收标准（可重复）")
    run.add_argument("--criteria-file", help="JSON 形式的验收标准文件")
    run.add_argument("--team")
    run.add_argument(
        "--workspace",
        default="",
        help="文件级机检（file_exists/file_contains 等）解析路径的工作区目录，默认当前目录",
    )
    run.add_argument(
        "--parallel",
        type=int,
        help="并行候选的 worker 数量（>1 时并发产出多个视角，引擎按机检通过数选优）",
    )
    run.add_argument(
        "--perspective",
        choices=list(CANDIDATE_ANGLES),
        help="候选视角锚点（direct/thorough/concise/structured/example_first/risk_first）",
    )
    run.add_argument(
        "--reviewers",
        type=int,
        help="独立评审者数量（>1 时启用对抗评审聚合）",
    )
    run.add_argument(
        "--review-mode",
        choices=["all", "majority", "any"],
        help="多评审聚合规则：all 全票通过 / majority 过半 / any 任一",
    )
    add_json(run)
    run.set_defaults(func=cmd_run)

    demo = sub.add_parser("demo", help="离线演示三个闭环场景")
    demo.add_argument(
        "--scenario", default="all", choices=["all", "closure", "stagnation", "budget"]
    )
    add_json(demo)
    demo.set_defaults(func=cmd_demo)

    status = sub.add_parser("status", help="查看本机状态")
    add_json(status)
    status.set_defaults(func=cmd_status)

    ledger = sub.add_parser("ledger", help="查看闭环账本")
    ledger.add_argument("--tail", type=int, default=30)
    ledger.add_argument("--run-id")
    ledger.add_argument(
        "--undone",
        action="store_true",
        help="只看被撤销的批准（以及它们撤销掉的那次批准）",
    )
    ledger.add_argument(
        "--draft",
        default="",
        help="只看某个草稿的记录（用草稿编号）",
    )
    add_json(ledger)
    ledger.set_defaults(func=cmd_ledger)

    undos = sub.add_parser(
        "undos",
        help="哪些批准被撤销过：列出被推翻的人工批准",
    )
    undos.add_argument(
        "draft_id",
        nargs="?",
        default="",
        help="只看某个草稿（可选）",
    )
    undos.add_argument(
        "--workspace",
        default="",
        help="工作区文件夹（默认本机 artifacts 目录）",
    )
    add_json(undos)
    undos.set_defaults(func=cmd_undos)

    audit = sub.add_parser(
        "audit",
        help="检查人工决定的审计链：被改过么么",
    )
    audit.add_argument(
        "--workspace",
        default="",
        help="审计链所在的工作区文件夹（默认本机 artifacts 目录）",
    )
    add_json(audit)
    audit.set_defaults(func=cmd_audit)

    seal = sub.add_parser(
        "seal",
        help="复核闭环封存：结论还能不能按当初的字节重算",
    )
    seal.add_argument(
        "--run-id",
        default="",
        help="只检查某一次运行（默认检查全部）",
    )
    add_json(seal)
    seal.set_defaults(func=cmd_seal)

    tickets = sub.add_parser("tickets", help="查看人工工单")
    tickets.add_argument("--state", choices=["waiting_human", "resolved"])
    add_json(tickets)
    tickets.set_defaults(func=cmd_tickets)

    resume = sub.add_parser("resume", help="带着人工意见继续一个被升级的循环")
    resume.add_argument("ticket_id")
    resume.add_argument("--note", default="")
    # A takeover needs a budget of its own. The run that opened the
    # ticket stopped at a ceiling; continuing with the same ceiling
    # stops at the same place again. These three flags give *this*
    # run a different ceiling, without changing the machine-wide
    # default for every other task.
    resume.add_argument(
        "--max-iterations", type=int,
        help="这一轮最多跑多少轮（覆盖本机默认，不改配置）",
    )
    resume.add_argument(
        "--deadline-sec", type=int,
        help="这一轮最多用多少秒（覆盖本机默认，不改配置）",
    )
    resume.add_argument(
        "--max-tokens", type=int,
        help="这一轮最多用多少 token（覆盖本机默认，不改配置）",
    )
    add_json(resume)
    resume.set_defaults(func=cmd_resume)

    handback = sub.add_parser(
        "handback",
        help="把人工写的交付物交回闭环验收",
    )
    handback.add_argument("ticket_id")
    source = handback.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", help="人工交付物所在的文件")
    source.add_argument("--text", help="人工交付物的正文")
    # The folder handoff: the human edited the files the run produced,
    # in the run's own workspace, and hands the folder back. The folder
    # is judged as it stands and only written back if it passes.
    source.add_argument(
        "--workspace",
        help="人工改好的工作区文件夹（直接改原工作区里的文件再交回）",
    )
    handback.add_argument("--note", default="", help="人工说明")
    add_json(handback)
    handback.set_defaults(func=cmd_handback)

    artifacts = sub.add_parser("artifacts", help="列出本机产出的交付物")
    artifacts.add_argument("--run-id", help="只看某一次运行")
    add_json(artifacts)
    artifacts.set_defaults(func=cmd_artifacts)

    files = sub.add_parser(
        "files",
        help="索引工作区里的文件：每一个是什么格式、里面有什么",
    )
    files.add_argument(
        "path",
        nargs="?",
        default="",
        help="要索引的文件夹（不给就索引本机所有运行工作区）",
    )
    add_json(files)
    files.set_defaults(func=cmd_files)

    preview = sub.add_parser(
        "preview",
        help="预览一个文件：PDF/Word/图片等格式的内容",
    )
    preview.add_argument("path", help="要预览的文件")
    preview.add_argument(
        "--max-chars",
        type=int,
        default=4000,
        help="最多显示多少个字符（默认 4000）",
    )
    add_json(preview)
    preview.set_defaults(func=cmd_preview)

    drafts = sub.add_parser(
        "drafts",
        help="查看待确认的草稿：批准后才写回原文件",
    )
    drafts.add_argument(
        "action",
        nargs="?",
        default="list",
        choices=[
            "list",
            "show",
            "compare",
            "undo",
            "approve",
            "reject",
            "token",
            "ticket",
            "conflict",
        ],
        help="要做的动作（默认 list）",
    )
    drafts.add_argument("draft_id", nargs="?", default="", help="草稿编号")
    drafts.add_argument(
        "--workspace",
        default="",
        help="草稿所在的工作区文件夹（默认本机 artifacts 目录）",
    )
    drafts.add_argument(
        "--state",
        choices=["pending", "approved", "rejected", "undone"],
        help="只看某种状态的草稿",
    )
    drafts.add_argument("--note", default="", help="人工说明")
    drafts.add_argument(
        "--force",
        action="store_true",
        help="明知文件在草稿等待期间（或批准之后）被改过，仍要写回或撤销",
    )
    drafts.add_argument(
        "--token",
        default="",
        help="人类令牌：签发票据时用来证明是人签的",
    )
    drafts.add_argument(
        "--ticket-action",
        default="approve",
        choices=["approve", "reject"],
        help="这张票据允许的动作（默认 approve）",
    )
    add_json(drafts)
    drafts.set_defaults(func=cmd_drafts)

    report = sub.add_parser("report", help="生成闭环复盘报告（Markdown）")
    report.add_argument("--run-id", help="只报告某一次运行")
    report.add_argument("--out", help="写入指定文件")
    add_json(report)
    report.set_defaults(func=cmd_report)

    replay = sub.add_parser("replay", help="回放账本中的运行轨迹")
    replay.add_argument("--run-id", help="只回放某一次运行")
    add_json(replay)
    replay.set_defaults(func=cmd_replay)

    export = sub.add_parser("export", help="把账本导出为 Markdown 表格")
    export.add_argument("--run-id", help="只导出某一次运行")
    export.add_argument("--out", help="写入指定文件")
    add_json(export)
    export.set_defaults(func=cmd_export)

    clean = sub.add_parser(
        "clean",
        help="清理账本里的满外行（默认只清理自检泄漏）",
    )
    clean.add_argument(
        "--selftest-ledger",
        action="store_true",
        help="删除账本里指向临时目录的自检行（默认动作）",
    )
    clean.add_argument(
        "--dry-run",
        action="store_true",
        help="只告诉你会删掉什么，不真的删",
    )
    add_json(clean)
    clean.set_defaults(func=cmd_clean)

    closure = sub.add_parser("closure", help="判定当前工作区闭不闭合（只读，不跑闭环）")
    closure.add_argument("--run-id", dest="run_id", default="")
    add_json(closure)
    closure.set_defaults(func=cmd_closure)

    verify = sub.add_parser("verify", help="跑一次真实闭环自检")
    add_json(verify)
    verify.set_defaults(func=cmd_verify)

    bench = sub.add_parser("bench", help="离线基准测试")
    bench.add_argument("--repeats", type=int, default=3)
    bench.add_argument("--scenario", action="append", help="只跑指定场景（可重复）")
    add_json(bench)
    bench.set_defaults(func=cmd_bench)

    teams = sub.add_parser("teams", help="列出团队模板")
    add_json(teams)
    teams.set_defaults(func=cmd_teams)

    selftest = sub.add_parser(
        "selftest",
        help="隔失门禁：重复打磈易偶发的路径（离线）",
    )
    selftest.add_argument(
        "--iterations",
        type=int,
        default=200,
        help="每项自检打多少次（默认 200）",
    )
    selftest.add_argument(
        "--only",
        action="append",
        default=[],
        help="只跑某几项（可重复使用）",
    )
    selftest.add_argument("--json", action="store_true", help="输出 JSON")
    selftest.set_defaults(func=cmd_selftest)

    doctor = sub.add_parser("doctor", help="安装自检")
    add_json(doctor)
    doctor.add_argument(
        "--no-network",
        action="store_true",
        help="跳过端点握手探测（离线环境使用）",
    )
    doctor.set_defaults(func=cmd_doctor)

    serve = sub.add_parser("serve", help="以 MCP stdio 服务运行")
    serve.set_defaults(func=cmd_serve)

    http = sub.add_parser("http", help="以 HTTP + SSE 运行（远程接入）")
    http.add_argument("--host", default="127.0.0.1")
    http.add_argument("--port", type=int, default=8765)
    http.add_argument("--verbose", action="store_true")
    http.add_argument(
        "--token",
        default=None,
        help="鉴权令牌（默认读取环境变量 MAS_HTTP_TOKEN）；绑定非本机地址时必填",
    )
    http.set_defaults(func=cmd_http)

    relay = sub.add_parser(
        "relay",
        help="中继模式：把私有端点藏在「必须带 api key」的客户端后面",
    )
    relay.add_argument("--host", default="127.0.0.1")
    relay.add_argument("--port", type=int, default=8766)
    relay.add_argument(
        "--upstream",
        default="http://127.0.0.1:16024",
        help="上游 OpenAI 兼容端点（默认 %(default)s）",
    )
    relay.add_argument(
        "--upstream-key-env",
        default="",
        help="存放上游密钥的环境变量名；给了就用它替换客户端的 Authorization",
    )
    relay.add_argument(
        "--token",
        default=None,
        help="中继自身鉴权令牌（默认读取环境变量 MAS_RELAY_TOKEN）",
    )
    relay.add_argument("--timeout", type=float, default=900.0)
    relay.add_argument("--verbose", action="store_true")
    relay.set_defaults(func=cmd_relay)

    return parser


def cmd_relay(args: argparse.Namespace) -> int:
    """Run the OpenAI-compatible relay."""
    upstream_key = ""
    if getattr(args, "upstream_key_env", ""):
        upstream_key = os.environ.get(args.upstream_key_env, "").strip()
        if not upstream_key:
            print(
                f"错误：环境变量 {args.upstream_key_env} 为空或未设置。",
                file=sys.stderr,
            )
            return 2
    return serve_relay(
        args.host,
        args.port,
        args.upstream,
        upstream_key,
        args.token,
        args.verbose,
        args.timeout,
    )


def main(argv: List[str] | None = None) -> int:
    force_utf8_output()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
