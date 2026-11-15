"""Installation self-check.

Every check returns a machine readable verdict so that the one-click
installer, the CI pipeline and the MCP ``mas_doctor`` tool all report the
same truth.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
from typing import Any, Dict, List

from mas.config import config_path, load_config, state_dir, user_config_path
from mas.probe import probe_from_config, probe_instruction_echo

#: What each check *means*, in words a person can act on.
#:
#: The check names are for machines (``skill_codex``, ``mcp_claude``).
#: A beginner who has never heard of an MCP server cannot tell from
#: "skill_codex 未安装" whether that is a problem, what it costs them,
#: or what to do about it. This table is the sentence that answers
#: "what is this and do I care?".
CHECK_PLAIN_LANGUAGE: Dict[str, str] = {
    "python": "Python 版本够不够新（3.10 以上才能跑）",
    "package": "运行时本体装好了没有",
    "config": "你的设置文件在不在（没有也能跑，用默认的 mock）",
    "orchestration": "几个智能体并行、几个评审、用哪种投票规则",
    "state_dir": "账本、工单和交付物存放的目录",
    "ledger": "账本文件能不能写（历史记录存这儿）",
    "skills_source": "包内自带的 skill 模板在不在（安装用的源）",
    "skill_codex": "给 Codex 装的 skill：让 Codex 会用这个闭环工具",
    "skill_claude": "给 Claude 装的 skill：让 Claude 会用这个闭环工具",
    "skills_sync": "已装的 skill 是不是和包内最新版本一致（不一致就同步）",
    "mcp_codex": "给 Codex 注册的 MCP 服务：让 Codex 能直接调 mas 的工具",
    "mcp_claude": "给 Claude 注册的 MCP 服务：让 Claude 能直接调 mas 的工具",
    "provider": "模型后端配好了没有（mock 是离线演示模式）",
    "endpoint": "模型端点连得上吗",
    "instruction": "模型端点认不认角色指令（不认就等于没有闭环）",
    "file_checks": "能不能读真实文件（不能的话验收会变成空话）",
}

#: The checks that are *optional* - the loop runs without them, they
#: only make the loop reachable from an agent.
OPTIONAL_CHECKS = frozenset(
    {"skill_codex", "skill_claude", "skills_sync", "mcp_codex", "mcp_claude"}
)

#: The checks that decide whether the loop can run at all.
ESSENTIAL_CHECKS = frozenset({"python", "package", "state_dir", "ledger", "file_checks"})

#: The checks that are neither required nor optional: the loop runs
#: without them, but a real user is expected to act on them. The config
#: file is the classic example - the runtime falls back to sane defaults,
#: so it is a "建议" rather than a "必需", and calling it required while
#: also reporting it as a warning is a contradiction.
RECOMMENDED_CHECKS = frozenset({"config"})


def _check(name: str, status: str, detail: str) -> Dict[str, Any]:
    return {"check": name, "status": status, "detail": detail}


def run_doctor(config: Dict[str, Any] | None = None, allow_network: bool = True) -> List[Dict[str, Any]]:
    config = config or load_config()
    results: List[Dict[str, Any]] = []

    version = sys.version_info
    if version >= (3, 10):
        results.append(_check("python", "ok", f"{version.major}.{version.minor}.{version.micro}"))
    else:
        results.append(
            _check("python", "fail", f"{version.major}.{version.minor} < 3.10, 需要 Python 3.10+")
        )

    try:
        import mas  # noqa: F401

        results.append(_check("package", "ok", f"mas {getattr(mas, '__version__', '?')} 可导入"))
    except Exception as exc:  # pragma: no cover
        results.append(_check("package", "fail", f"无法导入 mas: {exc}"))

    cfg_file = user_config_path()
    if cfg_file.exists():
        results.append(_check("config", "ok", f"{cfg_file}"))
    else:
        legacy = config_path(config)
        if legacy.exists():
            results.append(_check("config", "ok", f"{legacy}（v0.1 位置）"))
        else:
            results.append(_check("config", "warn", f"未找到 {cfg_file}，运行 mas init 生成"))

    orchestration = config.get("orchestration") or {}
    results.append(
        _check(
            "orchestration",
            "ok",
            "并行 {w} 路 / 评审 {r} 路 / {m} / 回退 {f} 个".format(
                w=orchestration.get("parallel_workers", 1),
                r=orchestration.get("reviewers", 1),
                m=orchestration.get("review_mode", "all"),
                f=len(config.get("fallbacks") or []),
            ),
        )
    )

    root = state_dir(config)
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".doctor-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        results.append(_check("state_dir", "ok", str(root)))
    except OSError as exc:
        results.append(_check("state_dir", "fail", f"不可写: {exc}"))

    ledger = root / "ledgers" / "runs.jsonl"
    try:
        ledger.parent.mkdir(parents=True, exist_ok=True)
        results.append(_check("ledger", "ok", str(ledger)))
    except OSError as exc:
        results.append(_check("ledger", "fail", f"不可写: {exc}"))

    # The skills travel inside the package. If they are missing from the
    # installed runtime, `mas install` cannot do its job - and that is a
    # broken installation, not a user mistake.
    from mas.installer import PACKAGED_SKILLS, skills_source

    source = skills_source()
    if source is None:
        results.append(_check("skills_source", "fail", "包内找不到 mas/skills 目录"))
    else:
        missing = [
            name for name in PACKAGED_SKILLS if not (source / name / "SKILL.md").exists()
        ]
        if missing:
            results.append(
                _check("skills_source", "fail", f"{source} 缺少: {', '.join(missing)}")
            )
        else:
            results.append(
                _check("skills_source", "ok", f"{source}（{len(PACKAGED_SKILLS)} 个 skill）")
            )

    home = pathlib.Path(os.path.expanduser("~"))
    for label, target in (
        ("skill_codex", home / ".codex" / "skills" / "mas-loop" / "SKILL.md"),
        ("skill_claude", home / ".claude" / "skills" / "mas-loop" / "SKILL.md"),
    ):
        if target.exists():
            results.append(_check(label, "ok", str(target)))
        else:
            results.append(_check(label, "warn", f"未安装: {target}（运行 mas install）"))

    # Presence is not enough. An installed skill whose content no longer
    # matches the packaged copy is drift: the agent is following a skill
    # the runtime knows nothing about. This is exactly what survived an
    # upgrade silently in 0.6.0 -> 0.7.0, so it gets its own check.
    from mas.installer import skill_sync_report

    sync = skill_sync_report()
    rows = sync.get("rows") or []
    drift = [row for row in rows if row.get("status") == "drift"]
    missing = [row for row in rows if row.get("actual") == "missing"]
    if sync.get("error"):
        results.append(_check("skills_sync", "fail", str(sync["error"])))
    elif missing:
        names = ", ".join(sorted({row["skill"] for row in missing}))
        results.append(
            _check(
                "skills_sync",
                "warn",
                f"未安装: {names}（运行 mas install）",
            )
        )
    elif drift:
        detail = ", ".join(
            f"{row['host']}/{row['skill']}" for row in drift[:6]
        )
        if len(drift) > 6:
            detail += f" 等 {len(drift)} 项"
        results.append(
            _check(
                "skills_sync",
                "warn",
                f"内容与包内版本不一致: {detail}（运行 mas install 同步）",
            )
        )
    else:
        results.append(
            _check("skills_sync", "ok", f"{len(rows)} 个已安装 skill 均与包内版本一致")
        )

    from mas.installer import _codex_block_is_current

    codex_cfg = home / ".codex" / "config.toml"
    if codex_cfg.exists():
        text = codex_cfg.read_text(encoding="utf-8", errors="ignore")
        if "[mcp_servers.mas_loop]" not in text:
            results.append(_check("mcp_codex", "warn", "~/.codex/config.toml 未注册 mcp_servers.mas_loop"))
        elif _codex_block_is_current(text, sys.executable):
            results.append(_check("mcp_codex", "ok", "已在 ~/.codex/config.toml 注册 mcp_servers.mas_loop"))
        else:
            # The entry exists but points at an interpreter or a package
            # path that is not this one - the server could not start.
            results.append(
                _check(
                    "mcp_codex",
                    "warn",
                    "mcp_servers.mas_loop 已注册但指向失效的运行时（运行 mas install 刷新）",
                )
            )
    else:
        results.append(_check("mcp_codex", "warn", "未找到 ~/.codex/config.toml"))

    claude_cfg = home / ".claude.json"
    if claude_cfg.exists():
        try:
            data = json.loads(claude_cfg.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        servers = data.get("mcpServers") if isinstance(data, dict) else None
        if not isinstance(servers, dict) or "mas_loop" not in servers:
            results.append(_check("mcp_claude", "warn", "~/.claude.json 未注册 mas_loop"))
        else:
            from mas.installer import _claude_entry_is_current

            if _claude_entry_is_current(servers["mas_loop"], sys.executable):
                results.append(_check("mcp_claude", "ok", "已在 ~/.claude.json 注册 mas_loop"))
            else:
                results.append(
                    _check(
                        "mcp_claude",
                        "warn",
                        "mas_loop 已注册但指向失效的运行时（运行 mas install 刷新）",
                    )
                )
    else:
        results.append(_check("mcp_claude", "warn", "未找到 ~/.claude.json"))

    provider = str(config.get("provider") or "mock")
    env_name = str(config.get("api_key_env") or "MAS_API_KEY")
    key = os.environ.get(env_name) or str(config.get("api_key") or "")
    base_url = str(config.get("base_url") or "")
    if provider == "mock":
        results.append(_check("provider", "ok", "mock（离线模式，无需密钥）"))
    elif key and base_url:
        results.append(_check("provider", "ok", f"{provider} / {config.get('model')} @ {base_url}"))
    elif base_url:
        # No key, but an endpoint is configured. Keyless local gateways are
        # a legitimate setup, so this is not a missing piece - the endpoint
        # handshake below reports whether the server actually accepts us.
        results.append(
            _check(
                "provider",
                "ok",
                f"{provider} / {config.get('model')} @ {base_url}（免鉴权端点，由握手探测确认）",
            )
        )
    else:
        missing = []
        if not key:
            missing.append(f"密钥(env {env_name})")
        missing.append("base_url")
        results.append(
            _check("provider", "warn", f"{provider} 缺少: {', '.join(missing)}（离线 mock 模式仍可用）")
        )

    # A live handshake tells the user which dialect their gateway speaks
    # *before* the first deliverable is at stake. It is skipped in mock
    # mode and when the endpoint is not configured yet.
    if provider != "mock" and base_url and allow_network:
        # Probe whenever an endpoint is configured. A missing API key is
        # *not* a reason to skip: local gateways (the Codex Responses
        # bridge, Ollama-free local proxies) are often keyless, and the
        # server's own answer - 200, 401 or 400 - is the useful signal.
        try:
            live = probe_from_config(config, api_key=key)
        except Exception as exc:  # pragma: no cover - defensive
            live = {"status": "warn", "dialect": "unknown", "detail": f"探测异常: {exc}"}
        results.append(_check("endpoint", live.get("status", "warn"), str(live.get("detail", ""))))
        # A reachable endpoint is not the same as a *usable* one. On a
        # Responses-style gateway the system instruction used to be
        # dropped while the endpoint still answered normally - so the
        # endpoint check said "ok" and every role contract in the loop
        # was silently discarded. This check asks the model to repeat a
        # token that only the system instruction mentions, and reports
        # honestly when the instruction was not applied.
        try:
            echo = probe_instruction_echo(
                provider=provider,
                base_url=base_url,
                model=str(config.get("model") or ""),
                api_key=key,
                timeout_sec=float(config.get("timeout_sec", 20) or 20),
            )
        except Exception as exc:  # pragma: no cover - defensive
            echo = {"status": "warn", "detail": f"指令校验异常: {exc}"}
        results.append(
            _check("instruction", echo.get("status", "warn"), str(echo.get("detail", "")))
        )
    elif provider != "mock" and not allow_network:
        results.append(_check("endpoint", "warn", "已按 --no-network 跳过握手探测"))
        results.append(_check("instruction", "warn", "已按 --no-network 跳过指令生效校验"))
    elif provider != "mock":
        results.append(_check("endpoint", "warn", "端点未配置，跳过握手探测"))
        results.append(_check("instruction", "warn", "端点未配置，跳过指令生效校验"))

    # The file-level checks are a *capability*, not a setting: if the
    # runtime cannot read the workspace, every file check would silently
    # degrade to "unjudged" and the loop would fall back to the model's
    # prose - exactly the fake closure this project exists to stop. So it
    # is exercised here, once, on a real temporary file.
    results.append(_file_check_capability())

    return results


def _file_check_capability() -> Dict[str, Any]:
    """Exercise one file check against a real temporary file."""
    import tempfile

    from mas.filechecks import evaluate_file_check

    try:
        with tempfile.TemporaryDirectory() as tmp:
            target = pathlib.Path(tmp) / "probe.md"
            target.write_text("probe\n", encoding="utf-8")
            outcome = evaluate_file_check(
                "file_contains",
                {"path": "probe.md", "value": "probe"},
                pathlib.Path(tmp),
            )
    except Exception as exc:  # pragma: no cover - defensive
        return _check("file_checks", "fail", f"无法执行文件级机检: {exc}")
    if outcome.get("pass") is True:
        return _check("file_checks", "ok", "文件级机检可用（读真实磁盘）")
    return _check(
        "file_checks",
        "fail",
        f"文件级机检异常: {outcome.get('note', outcome)}",
    )


def summarize(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    counts = {"ok": 0, "warn": 0, "fail": 0}
    for row in results:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {"counts": counts, "checks": results}


def explain(name: str) -> str:
    """The plain-language sentence for a check, or an empty string."""
    return CHECK_PLAIN_LANGUAGE.get(name, "")


def is_optional(name: str) -> bool:
    """True when the loop runs fine without this check."""
    return name in OPTIONAL_CHECKS


def is_recommended(name: str) -> bool:
    """True when the check is expected to be acted on, but not required."""
    return name in RECOMMENDED_CHECKS


def verdict(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Decide what the installation is good for, in words.

    ``mas doctor`` used to print a list of check names and exit zero.
    A beginner read six WARN lines and one zero, and had no way to tell
    whether the tool worked. The answer is a single sentence:

    * the loop cannot run at all - something essential is broken;
    * the loop runs, but an agent cannot reach it - the optional parts
      (skills and the MCP registration) are missing;
    * everything is in place.
    """
    by_name = {row["check"]: row for row in results}
    broken = [
        row["check"]
        for row in results
        if row["status"] == "fail" and row["check"] in ESSENTIAL_CHECKS
    ]
    broken_optional = [
        row["check"]
        for row in results
        if row["status"] == "fail" and row["check"] not in ESSENTIAL_CHECKS
    ]
    missing_optional = [
        row["check"]
        for row in results
        if row["status"] == "warn" and row["check"] in OPTIONAL_CHECKS
    ]
    missing_recommended = [
        row["check"]
        for row in results
        if row["status"] == "warn" and row["check"] in RECOMMENDED_CHECKS
    ]
    if broken:
        state = "broken"
        headline = "闭环跑不起来：{} 有硬伤，先修它。".format("、".join(broken))
        action = "看上面那一条的详情修好它，然后重新运行 mas doctor 复查。"
    elif broken_optional:
        state = "degraded"
        headline = "闭环能跑，但 {} 没就位。".format("、".join(broken_optional))
        action = "运行 mas install 补齐；如果还不行，看上面那一条的详情。"
    elif missing_optional:
        state = "agent_not_reachable"
        headline = (
            "闭环能跑，但智能体接不上：{} 还没装。".format("、".join(missing_optional))
        )
        action = "运行 mas install，一条命令装上 skill 并注册 MCP 服务。"
    else:
        state = "ready"
        headline = "一切就绪：闭环能跑，智能体也接得上。"
        action = "可以开始干活了：mas run --goal \"...\" --criteria \"...\"。"
    if missing_recommended and state != "broken":
        action = "{} 另外建议运行 mas init 生成配置。".format(action.rstrip("。 "))
    return {
        "state": state,
        "headline": headline,
        "action": action,
        # The loop itself only depends on the essential checks. A broken
        # skill or MCP registration makes the tool *unreachable from an
        # agent*, but `mas run` still works - that is a different problem
        # with a different fix, and conflating the two made the summary
        # say the loop was dead when it was merely disconnected.
        "loop_usable": not broken,
        "agent_reachable": not missing_optional and not broken_optional,
        "broken": broken,
        "missing_optional": missing_optional,
        "missing_recommended": missing_recommended,
        "checks": {
            name: {
                "status": by_name[name]["status"],
                "detail": by_name[name]["detail"],
                "plain": explain(name),
                "optional": is_optional(name),
                "recommended": is_recommended(name),
            }
            for name in by_name
        },
    }


def format_doctor(results: List[Dict[str, Any]]) -> str:
    """Render the self-check so that a person can act on it.

    The old rendering was one line per check: a machine name and a
    path. That is a report for the tool, not for the person who just
    installed the tool. This one says what each check means, separates
    the parts that stop the loop from the parts that only make the loop
    reachable from an agent, and ends with one sentence and one command.
    """
    lines: List[str] = []
    for row in results:
        plain = explain(row["check"])
        if is_optional(row["check"]):
            tag = "可选"
        elif is_recommended(row["check"]):
            tag = "建议"
        else:
            tag = "必需"
        mark = {"ok": "[OK  ]", "warn": "[注意]", "fail": "[坏了]"}.get(
            row["status"], "[?   ]"
        )
        lines.append(f"{mark} {row['check']}（{tag}）")
        lines.append(f"       {row['detail']}")
        if plain:
            lines.append(f"       是什么：{plain}")
    counts = summarize(results)["counts"]
    lines.append("")
    lines.append(
        "小结：{} 项正常，{} 项注意，{} 项坏了".format(
            counts.get("ok", 0), counts.get("warn", 0), counts.get("fail", 0)
        )
    )
    return "\n".join(lines) + "\n"
