"""Installation self-check.

Every check returns a machine readable verdict so that the one-click
installer, the CI pipeline and the MCP ``mas_doctor`` tool all report the
same truth.
"""

from __future__ import annotations

import os
import pathlib
import sys
from typing import Any, Dict, List

from mas.config import config_path, load_config, state_dir, user_config_path


def _check(name: str, status: str, detail: str) -> Dict[str, Any]:
    return {"check": name, "status": status, "detail": detail}


def run_doctor(config: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
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

    home = pathlib.Path(os.path.expanduser("~"))
    for label, target in (
        ("skill_codex", home / ".codex" / "skills" / "mas-loop" / "SKILL.md"),
        ("skill_claude", home / ".claude" / "skills" / "mas-loop" / "SKILL.md"),
    ):
        if target.exists():
            results.append(_check(label, "ok", str(target)))
        else:
            results.append(_check(label, "warn", f"未安装: {target}"))

    codex_cfg = home / ".codex" / "config.toml"
    if codex_cfg.exists():
        text = codex_cfg.read_text(encoding="utf-8", errors="ignore")
        if "[mcp_servers.mas_loop]" in text:
            results.append(_check("mcp_codex", "ok", "已在 ~/.codex/config.toml 注册 mcp_servers.mas_loop"))
        else:
            results.append(_check("mcp_codex", "warn", "~/.codex/config.toml 未注册 mcp_servers.mas_loop"))
    else:
        results.append(_check("mcp_codex", "warn", "未找到 ~/.codex/config.toml"))

    provider = str(config.get("provider") or "mock")
    if provider == "mock":
        results.append(_check("provider", "ok", "mock（离线模式，无需密钥）"))
    else:
        env_name = str(config.get("api_key_env") or "MAS_API_KEY")
        key = os.environ.get(env_name) or str(config.get("api_key") or "")
        base_url = str(config.get("base_url") or "")
        if key and base_url:
            results.append(_check("provider", "ok", f"{provider} / {config.get('model')} @ {base_url}"))
        else:
            missing = []
            if not key:
                missing.append(f"密钥(env {env_name})")
            if not base_url:
                missing.append("base_url")
            results.append(
                _check("provider", "warn", f"{provider} 缺少: {', '.join(missing)}（离线 mock 模式仍可用）")
            )

    return results


def summarize(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    counts = {"ok": 0, "warn": 0, "fail": 0}
    for row in results:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {"counts": counts, "checks": results}
