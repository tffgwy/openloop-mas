"""Command line interface for OpenLoop MAS."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any, Dict, List

from mas import __version__
from mas.config import load_config, save_config
from mas.demo import run_scenarios
from mas.doctor import run_doctor, summarize
from mas.engine import LoopEngine
from mas.teams import list_teams, load_team


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
        criteria.append(text)
    return criteria


def _engine(config: Dict[str, Any], team: str | None = None) -> LoopEngine:
    return LoopEngine(config, team=load_team(team or config.get("team")))


def cmd_init(args: argparse.Namespace) -> int:
    config = load_config()
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
    results = run_doctor(config)
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mas",
        description="OpenLoop MAS - 多智能体闭环运行时（loop engineering runtime）",
    )
    parser.add_argument("--version", action="version", version=f"openloop-mas {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_json(target: argparse.ArgumentParser) -> None:
        target.add_argument("--json", action="store_true", help="以 JSON 输出")

    init = sub.add_parser("init", help="生成/更新本地配置")
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

    teams = sub.add_parser("teams", help="列出团队模板")
    add_json(teams)
    teams.set_defaults(func=cmd_teams)

    doctor = sub.add_parser("doctor", help="安装自检")
    add_json(doctor)
    doctor.set_defaults(func=cmd_doctor)

    serve = sub.add_parser("serve", help="以 MCP stdio 服务运行")
    serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: List[str] | None = None) -> int:
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
