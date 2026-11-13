"""Command line interface for OpenLoop MAS."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any, Dict, List

from mas import __version__
from mas.bench import format_bench, run_bench
from mas.config import load_config, load_persisted_config, save_config
from mas.demo import run_scenarios
from mas.doctor import run_doctor, summarize
from mas.engine import LoopEngine
from mas.installer import (
    format_report,
    format_sync_report,
    format_uninstall_report,
    run_install,
    run_sync,
    run_uninstall,
)
from mas.orchestration import CANDIDATE_ANGLES
from mas.report import build_report, ledger_markdown, render_markdown, replay_trace
from mas.setup import format_verify, run_verify, run_wizard
from mas.teams import list_teams, load_team
from mas.util import force_utf8_output, parse_criterion_shorthand


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
        criteria.append(_parse_criterion(text))
    return criteria


def _parse_criterion(text: Any) -> Any:
    """Accept a JSON criterion object, a ``<type>:<arg>`` shorthand, or a
    plain-text criterion.

    ``--criteria '{"id":"c1","check":{...}}'`` is the documented form and
    must not degrade into a raw description string, otherwise the machine
    check silently never runs. The shorthand ``--criteria 'regex:def'`` is
    the convenient form and must not degrade either. The shorthand parser
    itself lives in :mod:`mas.util` so that the engine applies the same
    rules to criteria arriving through the MCP or HTTP server.
    """
    if not isinstance(text, str):
        return text
    stripped = text.strip()
    if stripped.startswith("{"):
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
    path = save_config(config)
    _print({"config_path": str(path), "config": config}, args.json)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    config = load_config()
    criteria = _load_criteria(args)
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
    print(f"状态：{result.status}（迭代 {result.iterations} 次，token {result.tokens_used}）")
    print(f"账本：{result.ledger_path}")
    for score in result.scores:
        flag = "PASS" if score["pass"] else "FAIL"
        print(f"  [{flag}] {score['id']} ({score['source']}) {score['note']}")
    if result.ticket_id:
        print(f"已升级为人工工单：{result.ticket_id}")
        print(f"  继续：mas resume {result.ticket_id} --note \"你的意见\"")
    if result.error:
        print(f"说明：{result.error}")
    print("--- 交付物预览 ---")
    print(result.artifact[:1500])
    return 0 if result.closed else 2


def cmd_demo(args: argparse.Namespace) -> int:
    results = run_scenarios(args.scenario)
    if args.json:
        _print(results, True)
    else:
        for row in results:
            flag = "OK " if row.get("ok") else "FAIL"
            print(
                f"[{flag}] {row['scenario']:<11} 期望={row['expect']:<18} 实际={row['status']:<18} "
                f"迭代={row.get('iterations')} 票={row.get('ticket_id') or '-'}"
            )
    return 0 if all(row.get("ok") for row in results) else 1


def cmd_status(args: argparse.Namespace) -> int:
    config = load_config()
    engine = _engine(config)
    tickets = engine.tickets.list()
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
            "waiting_human": [t["id"] for t in tickets if t.get("state") == "waiting_human"],
            "resolved": [t["id"] for t in tickets if t.get("state") == "resolved"],
        },
    }
    _print(payload, args.json)
    return 0


def cmd_ledger(args: argparse.Namespace) -> int:
    config = load_config()
    engine = _engine(config)
    rows = engine.ledger.tail(args.tail)
    if args.run_id:
        rows = [row for row in rows if row.get("run_id") == args.run_id]
    if args.json:
        _print({"path": str(engine.ledger.path), "entries": rows}, True)
    else:
        for row in rows:
            print(
                f"#{row.get('seq')} {row.get('ts')} {row.get('role'):<9} "
                f"iter={row.get('iteration')} {row.get('verdict', '')} {row.get('decision', '')} "
                f"sha={str(row.get('artifact_sha', ''))[:10]}"
            )
    return 0


def cmd_tickets(args: argparse.Namespace) -> int:
    config = load_config()
    engine = _engine(config)
    tickets = engine.tickets.list(args.state)
    if args.json:
        _print({"tickets": tickets}, True)
    else:
        for ticket in tickets:
            title = (ticket.get("payload") or {}).get("title", "")
            print(f"{ticket['id']}  {ticket.get('state'):<14} {ticket.get('kind'):<16} {title}")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    config = load_config()
    engine = _engine(config)
    try:
        result = engine.resume(args.ticket_id, args.note or "")
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.json:
        _print(result.to_dict(), True)
    else:
        print(f"状态：{result.status}（迭代 {result.iterations} 次）")
        for score in result.scores:
            flag = "PASS" if score["pass"] else "FAIL"
            print(f"  [{flag}] {score['id']} {score['note']}")
    return 0 if result.closed else 2


def cmd_report(args: argparse.Namespace) -> int:
    """Render a Markdown closure report from the ledger."""
    config = load_config()
    engine = _engine(config)
    rows = engine.ledger.entries()
    if args.run_id:
        rows = [row for row in rows if row.get("run_id") == args.run_id]
    report = build_report(rows)
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
    if args.run_id:
        rows = [row for row in rows if row.get("run_id") == args.run_id]
    if not rows:
        print("账本为空，没有可回放的运行。", file=sys.stderr)
        return 1
    trace = replay_trace(rows)
    if args.json:
        _print(trace, True)
        return 0
    print(f"回放 {trace['steps_count']} 条账本记录")
    for step in trace["steps"]:
        mark = "*" if step.get("closed") else "-"
        print(
            f"{mark} #{step.get('seq')} iter={step.get('iteration')} "
            f"{step.get('role')} {step.get('decision') or ''} {step.get('detail') or ''}".rstrip()
        )
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Export the raw ledger as a Markdown table."""
    config = load_config()
    engine = _engine(config)
    rows = engine.ledger.entries()
    if args.run_id:
        rows = [row for row in rows if row.get("run_id") == args.run_id]
    markdown = ledger_markdown(rows)
    if args.out:
        path = pathlib.Path(args.out).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
        print(f"账本已导出：{path}")
        return 0
    print(markdown)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Run a real closed loop and report whether it closes."""
    config = load_config()
    result = run_verify(config)
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


def cmd_doctor(args: argparse.Namespace) -> int:
    config = load_config()
    results = run_doctor(config, allow_network=not getattr(args, "no_network", False))
    if args.json:
        _print(summarize(results), True)
    else:
        for row in results:
            print(f"[{row['status'].upper():<4}] {row['check']:<14} {row['detail']}")
    counts = summarize(results)["counts"]
    return 1 if counts.get("fail") else 0


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
    add_json(init)
    init.set_defaults(func=cmd_init)

    run = sub.add_parser("run", help="运行一个闭环任务")
    run.add_argument("--goal", required=True, help="任务目标")
    run.add_argument("--title", default="")
    run.add_argument("--criteria", action="append", help="验收标准（可重复）")
    run.add_argument("--criteria-file", help="JSON 形式的验收标准文件")
    run.add_argument("--team")
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
    add_json(ledger)
    ledger.set_defaults(func=cmd_ledger)

    tickets = sub.add_parser("tickets", help="查看人工工单")
    tickets.add_argument("--state", choices=["waiting_human", "resolved"])
    add_json(tickets)
    tickets.set_defaults(func=cmd_tickets)

    resume = sub.add_parser("resume", help="带着人工意见继续一个被升级的循环")
    resume.add_argument("ticket_id")
    resume.add_argument("--note", default="")
    add_json(resume)
    resume.set_defaults(func=cmd_resume)

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

    return parser


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
