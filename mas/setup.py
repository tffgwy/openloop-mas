"""Interactive setup wizard and the closed-loop verifier.

``mas init --wizard`` walks a beginner through the four decisions that
matter: which backend, which model, where the state lives and how hard the
loop should work. ``mas verify`` runs a real closed loop with the current
configuration and reports whether the machinery actually closes - not
whether the files exist.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

from mas.config import DEFAULT_CONFIG, load_config, load_persisted_config, save_config
from mas.engine import LoopEngine
from mas.teams import load_team

BACKENDS: List[Dict[str, str]] = [
    {
        "id": "mock",
        "label": "mock（离线演示，无需任何密钥）",
        "model": "mock-1",
        "base_url": "",
    },
    {
        "id": "openai_compat",
        "label": "openai_compat（OpenAI 兼容端点：OpenAI / Grok / Kimi / DeepSeek / Qwen / vLLM）",
        "model": "gpt-4o-mini",
        "base_url": "https://api.openai.com/v1",
    },
    {
        "id": "anthropic",
        "label": "anthropic（Claude Messages API）",
        "model": "claude-sonnet-4-5",
        "base_url": "https://api.anthropic.com",
    },
]

ENV_HINTS = {
    "openai_compat": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        return default
    return answer or default


def _ask_int(prompt: str, default: int, low: int = 1, high: int = 64) -> int:
    raw = _ask(prompt, str(default))
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(low, min(high, value))


def _ask_choice(prompt: str, options: List[Tuple[str, str]], default_index: int = 0) -> str:
    print(prompt)
    for index, (_, label) in enumerate(options, 1):
        print(f"  {index}) {label}")
    raw = _ask("请选择", str(default_index + 1))
    try:
        index = int(raw)
    except ValueError:
        index = default_index + 1
    if 1 <= index <= len(options):
        return options[index - 1][0]
    return options[default_index][0]


def run_wizard(current: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run the interactive setup wizard and return the resulting config."""
    # The wizard writes the configuration back, so it must start from the
    # persisted view. Otherwise a temporary MAS_STATE_DIR from the
    # environment would be baked into the user's configuration file.
    config = load_persisted_config() if current is None else json.loads(json.dumps(current))
    print("OpenLoop MAS 初始化向导")
    print("=" * 40)
    print("回答几个问题即可生成配置；随时回车用默认值。")
    print("")

    labels = [(item["id"], item["label"]) for item in BACKENDS]
    provider = _ask_choice("1) 选择模型后端：", labels, 0)

    backend = next((b for b in BACKENDS if b["id"] == provider), BACKENDS[0])
    config["provider"] = provider
    config["model"] = _ask("2) 模型名称", str(config.get("model") or backend["model"]))
    config["base_url"] = _ask("3) API 根地址（mock 可留空）", str(config.get("base_url") or backend["base_url"]))

    if provider != "mock":
        hint = ENV_HINTS.get(provider, "MAS_API_KEY")
        env_name = _ask("4) 密钥所在的环境变量名", str(config.get("api_key_env") or hint))
        config["api_key_env"] = env_name
        if not os.environ.get(env_name):
            print(f"   提示：当前环境没有 {env_name}，运行前请先设置它（配置里不会存明文密钥）。")
    else:
        config["api_key_env"] = str(config.get("api_key_env") or "MAS_API_KEY")

    config["team"] = _ask("5) 团队模板（default / content / review）", str(config.get("team") or "default"))
    config["state_dir"] = _ask(
        "6) 状态目录（存放账本与工单）",
        str(config.get("state_dir") or DEFAULT_CONFIG["state_dir"]),
    )
    # A state directory must be a path, not a team name. If the answer does
    # not look like a path, fall back to the default rather than silently
    # creating a directory called after a template.
    if "/" in config["state_dir"] or "\\" in config["state_dir"] or config["state_dir"].startswith("~"):
        pass
    elif config["state_dir"] in ("default", "content", "review", ""):
        config["state_dir"] = DEFAULT_CONFIG["state_dir"]

    orchestration = dict(config.get("orchestration") or {})
    workers = _ask_int("7) 并行候选 worker 数量（1=单 worker，3=三路竞争）", int(orchestration.get("parallel_workers", 1)))
    reviewers = _ask_int("8) 独立评审者数量（1=单评审，3=三路交叉验证）", int(orchestration.get("reviewers", 1)))
    mode = _ask_choice(
        "9) 评审聚合方式：",
        [
            ("all", "all：一票否决，全部通过才算通过"),
            ("majority", "majority：多数通过即通过"),
            ("any", "any：任一通过即通过"),
        ],
        0,
    )
    config["orchestration"] = {
        "parallel_workers": workers,
        "reviewers": reviewers,
        "review_mode": mode,
    }
    config["budget"] = dict(config.get("budget") or DEFAULT_CONFIG["budget"])
    config["budget"]["max_iterations"] = _ask_int(
        "10) 最大迭代轮数", int(config["budget"].get("max_iterations", 8)), low=1, high=64
    )

    # A fallback chain lets the loop survive one backend dying. It is
    # optional, so the default answer keeps the single-provider setup.
    print("")
    print("11) 故障转移回退链（可留空跳过）")
    print("   每行一个：provider,base_url,model")
    print("   例如：openai_compat,https://backup.example.com/v1,kimi-k2")
    fallbacks: List[Dict[str, str]] = []
    while True:
        raw = _ask("   回退项（回车结束）", "")
        if not raw:
            break
        parts = [part.strip() for part in raw.replace(" ", ",").split(",") if part.strip()]
        if len(parts) < 3:
            print("   格式不对，需要 provider、base_url、model 三项，已跳过。")
            continue
        fallbacks.append({"provider": parts[0], "base_url": parts[1], "model": parts[2]})
    if fallbacks:
        config["fallbacks"] = fallbacks
    else:
        config["fallbacks"] = list(config.get("fallbacks") or [])
    if config["fallbacks"]:
        print(f"   已记录 {len(config['fallbacks'])} 个回退后端。")

    path = save_config(config)
    print("")
    print(f"配置已写入：{path}")
    print("下一步：mas verify   # 跑一次真实闭环自检")
    return config


def run_verify(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run a real closed loop and report whether it closed.

    The check is deliberately end to end: a synthetic task with machine
    checkable criteria is executed with the *current* provider
    configuration. A mock backend always closes; a real backend only
    closes if the whole chain - planning, working, verification - works.
    """
    config = config or load_config()
    checks: List[Dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str, level: str = "ok") -> None:
        checks.append({"check": name, "status": "ok" if ok else level, "detail": detail})

    provider_name = str(config.get("provider") or "mock")
    add("provider", True, f"{provider_name} / {config.get('model')}")

    started = time.time()
    state_dir = tempfile.mkdtemp(prefix="mas-verify-")
    local = json.loads(json.dumps(config))
    local["state_dir"] = state_dir
    # The verify run gets its own budget so it never eats the user's budget.
    local["budget"] = dict(local.get("budget") or {})
    local["budget"]["max_iterations"] = min(2, int(local["budget"].get("max_iterations", 2)))
    local["budget"]["deadline_sec"] = 180

    team = load_team(local.get("team"))
    engine = LoopEngine(local, team=team)
    criteria = [
        {"id": "c1", "desc": "包含验收标记", "check": {"type": "contains", "arg": "MAS_VERIFY_OK"}},
        {"id": "c2", "desc": "正文长度达标", "check": {"type": "min_chars", "arg": 120}},
    ]
    result = engine.run("输出一段包含 MAS_VERIFY_OK 的短文，用于验证闭环是否真的能跑通。", criteria)
    elapsed = round(time.time() - started, 2)

    if result.closed:
        add("closure", True, f"闭环成功，迭代 {result.iterations} 次，用时 {elapsed}s")
    else:
        add(
            "closure",
            False,
            f"未闭环（状态 {result.status}）：{result.error or '; '.join(result.issues) or '无'}",
            "fail",
        )

    ledger_entries = len(engine.ledger.entries())
    if ledger_entries:
        add("ledger", True, f"{ledger_entries} 条账本记录")
    else:
        add("ledger", False, "账本为空", "fail")

    roles = {row.get("role") for row in engine.ledger.entries()}
    missing = {"planner", "closure"} - roles
    if missing:
        add("roles", False, f"账本缺少角色：{sorted(missing)}", "warn")
    else:
        add("roles", True, "planner / closure 均已入账")

    counts = {"ok": 0, "warn": 0, "fail": 0}
    for row in checks:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {
        "ok": counts.get("fail", 0) == 0,
        "counts": counts,
        "checks": checks,
        "elapsed_sec": elapsed,
        "provider": provider_name,
        "model": config.get("model"),
        "state_dir": state_dir,
        "token_usage": result.tokens_used,
    }


def format_verify(result: Dict[str, Any]) -> str:
    lines = ["闭环自检结果"]
    lines.append("-" * 40)
    for row in result["checks"]:
        lines.append(f"[{row['status'].upper():<4}] {row['check']:<12} {row['detail']}")
    lines.append("-" * 40)
    lines.append(
        f"结论：{'通过' if result['ok'] else '未通过'}　"
        f"（provider={result['provider']} model={result['model']} 用时={result['elapsed_sec']}s）"
    )
    return "\n".join(lines)
