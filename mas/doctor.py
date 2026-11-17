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
    "workspace": "每次运行的产物落在哪个文件夹（跑完就留得住）",
    "ledger": "账本文件能不能写（历史记录存这儿）",
    "skills_source": "包内自带的 skill 模板在不在（安装用的源）",
    "skill_codex": "给 Codex 装的 skill：让 Codex 会用这个闭环工具",
    "skill_claude": "给 Claude 装的 skill：让 Claude 会用这个闭环工具",
    "skills_sync": "已装的 skill 是不是和包内最新版本一致（不一致就同步）",
    "mcp_codex": "给 Codex 注册的 MCP 服务：让 Codex 能直接调 mas 的工具",
    "mcp_claude": "给 Claude 注册的 MCP 服务：让 Claude 能直接调 mas 的工具",
    "runtime": "本机装的是哪一版运行时（装了旧版就升级，别让它一直跑老代码）",
    "shadow_runtime": "你敲 mas 时究竟跑哪一份（PATH 上有多份时常跑的不是刚装的那份）",
    "provider": "模型后端配好了没有（mock 是离线演示模式）",
    "endpoint": "模型端点连得上吗",
    "instruction": "模型端点认不认角色指令（不认就等于没有闭环）",
    "file_checks": "能不能读真实文件（不能的话验收会变成空话）",
    "pypi": "这个包名在 PyPI 上能不能被 pip 安装（还没发布就记得下来）",
}

#: The checks that are *optional* - the loop runs without them, they
#: only make the loop reachable from an agent.
OPTIONAL_CHECKS = frozenset(
    {
        "skill_codex",
        "skill_claude",
        "skills_sync",
        "mcp_codex",
        "mcp_claude",
        "runtime",
        "shadow_runtime",
    }
)

#: The checks that decide whether the loop can run at all.
ESSENTIAL_CHECKS = frozenset(
    {"python", "package", "state_dir", "ledger", "workspace", "file_checks"}
)

#: The checks that are neither required nor optional: the loop runs
#: without them, but a real user is expected to act on them. The config
#: file is the classic example - the runtime falls back to sane defaults,
#: so it is a "建议" rather than a "必需", and calling it required while
#: also reporting it as a warning is a contradiction.
RECOMMENDED_CHECKS = frozenset({"config", "pypi"})


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

    # Where the runs write. A run that cannot make its own folder still
    # runs, but its files land in the caller's directory - which is
    # usually a temporary one, and the output is gone when the process
    # ends. So the folder has to be there and has to be writable.
    from mas.workspace import ensure_workspace, resolve_workspace

    workspace = resolve_workspace(root, "probe", config.get("workspace"))
    try:
        ensure_workspace(workspace)
        probe = pathlib.Path(workspace) / ".doctor-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        results.append(_check("workspace", "ok", str(workspace)))
    except OSError as exc:
        results.append(_check("workspace", "fail", f"不可写: {exc}"))

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

    # The runtime that is installed on disk next to the running one. An
    # old copy keeps serving the agents while every other check says
    # "registered" - so the version gap is worth its own line.
    try:
        from mas.upgrade import runtime_status

        status = runtime_status(
            pathlib.Path(
                os.path.expanduser(os.path.expandvars(str(config.get("state_dir") or "")))
            ).resolve()
        )
        outdated = int(status.get("outdated_count") or 0)
        if outdated:
            versions = ", ".join(
                f"{row['path']}（{row['version']}）"
                for row in (status.get("installed") or [])
                if row.get("outdated")
            )
            results.append(
                _check(
                    "runtime",
                    "warn",
                    f"本机装的运行时比你现在跑的这版旧：{versions}；"
                    f"运行 mas upgrade 升级",
                )
            )
        else:
            results.append(
                _check("runtime", "ok", f"本机装的运行时是 {status.get('running_version')}，不用升级")
            )
    except Exception as exc:  # pragma: no cover - defensive
        results.append(_check("runtime", "warn", f"查不到运行时版本：{exc}"))

    # Which copy does the user's own ``mas`` run? The runtime check
    # above looks at the copy the installer unpacked. If the PATH
    # reaches a *different* copy first - a pip install, an old
    # checkout - the user types ``mas`` and gets that one instead.
    # The installer would still say "everything is ready".
    try:
        from mas.shadow import shadow_status

        shadow = shadow_status()
        if shadow.get("shadowed"):
            winner = shadow.get("winner") or {}
            results.append(
                _check(
                    "shadow_runtime",
                    "warn",
                    "你敲 mas 时跑的是 {path}（{version}），"
                    "不是现在这一版（{running}）；"
                    "把它从 PATH 前面移掉，"
                    "或者跑 mas upgrade 把它换成现在这一版"
                    .format(
                        path=winner.get("path", "?"),
                        version=winner.get("version") or "读不出版本",
                        running=shadow.get("running_version", "?"),
                    ),
                )
            )
        else:
            results.append(
                _check(
                    "shadow_runtime",
                    "ok",
                    "你敲 mas 跑的就是现在这一版"
                    f"（{shadow.get('running_version', '?')}）",
                )
            )
    except Exception as exc:  # pragma: no cover - defensive
        results.append(_check("shadow_runtime", "warn", f"查不到 PATH 上的 mas：{exc}"))

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

    # Where this copy of the package came from, and whether the name
    # resolves on PyPI at all. A beginner who followed a link that says
    # "pip install openloop-mas" needs to know, before they try, whether
    # that name works - and if it does not, where the package actually
    # is. The answer is looked up, not assumed.
    results.append(_check_pypi_presence(allow_network))

    return results


#: The name this project publishes under.
PYPI_NAME = "openloop-mas"

#: Where a person goes to manage the publish credentials.
PYPI_PUBLISHING_URL = (
    "https://pypi.org/manage/project/%s/settings/publishing/" % PYPI_NAME
)

#: Where the built wheels live when the name is not on PyPI yet.
RELEASES_URL = "https://github.com/tffgwy/openloop-mas/releases/latest"


def _check_pypi_presence(allow_network: bool) -> Dict[str, Any]:
    """Is the package name live on PyPI, and what does that mean here?

    Three states, and they lead to different advice:

    * the name resolves - the package is published, install it with pip;
    * the name does not resolve - the project has never been created on
      PyPI, so ``pip install openloop-mas`` cannot work yet, and the
      answer is the release page, not a pip command;
    * the lookup failed - the network said nothing useful, which is not
      the same as "not published" and must not be reported as one.
    """
    if not allow_network:
        return _check("pypi", "warn", "已按 --no-network 跳过 PyPI 查询")

    import json
    import urllib.error
    import urllib.request

    url = "https://pypi.org/pypi/%s/json" % PYPI_NAME
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return _check(
                "pypi",
                "warn",
                "PyPI 上还没有 %s 这个名字，pip install %s 现在还用不了；"
                "可从 GitHub Releases 下载：%s" % (PYPI_NAME, PYPI_NAME, RELEASES_URL),
            )
        return _check("pypi", "warn", "查询 PyPI 失败: HTTP %s" % exc.code)
    except Exception as exc:  # noqa: BLE001 - a failed lookup is not a verdict
        return _check("pypi", "warn", "查询 PyPI 失败: %s" % exc)

    latest = str((payload.get("info") or {}).get("version") or "")
    return _check(
        "pypi",
        "ok",
        "%s 已发布（最新 %s）：pip install %s"
        % (PYPI_NAME, latest or "?", PYPI_NAME),
    )


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
    # `runtime` is optional in the sense that the loop runs without it -
    # but "optional" must not turn a version gap into "not installed".
    # The runtime is *there*, it is just old, and the fix is `mas upgrade`,
    # not `mas install`. Keeping it in its own bucket makes the sentence
    # honest.
    runtime_outdated = [
        row["check"]
        for row in results
        if row["status"] == "warn" and row["check"] == "runtime"
    ]
    # A shadow is not a missing piece either. The copy the user types is
    # *there* - it is just the wrong one, and the fix is to move it out
    # of the way (or replace it), not to "install" anything. Lumping it
    # in with the optional parts would send beginners to `mas install`,
    # which cannot change which copy the PATH reaches.
    shadowed = [
        row["check"]
        for row in results
        if row["status"] == "warn" and row["check"] == "shadow_runtime"
    ]
    missing_optional = [
        row["check"]
        for row in results
        if row["status"] == "warn"
        and row["check"] in OPTIONAL_CHECKS
        and row["check"] not in ("runtime", "shadow_runtime")
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
        if runtime_outdated:
            action += " 另外机器上装的运行时是旧版，运行 mas upgrade 升级。"
        if shadowed:
            action += " 另外 PATH 前面还有一份旧的 mas，你敲 mas 跑的是它。"
    elif shadowed:
        state = "shadowed_runtime"
        headline = (
            "闭环能跑，但你敲 mas 时跑的另一份旧版本，"
            "而不是刚装好的这一份。"
        )
        action = (
            "把 PATH 前面那份旧的 mas 移掉（或升级它），"
            "然后运行 mas upgrade 把它换成现在这一版。"
        )
    elif runtime_outdated:
        state = "runtime_outdated"
        headline = (
            "闭环能跑，但机器上装的运行时比你现在跑的这版旧，还在跑老代码。"
        )
        action = "运行 mas upgrade 把旧版换成现在这版，再运行 mas install 刷新注册。"
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
        "runtime_outdated": runtime_outdated,
        "shadowed_runtime": shadowed,
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
