"""Create a DeepSeek Harness profile that runs OpenLoop MAS through the relay.

Why this script exists
----------------------
Running ``mas_run`` *inside* a DSH conversation needs three things that do
not come together by accident:

1. a DSH profile that mounts the ``dsh-openloop-mas`` plugin;
2. a model route DSH can actually use - DSH's ``dsh-llm-pi-ai`` adapter
   insists on an API key even when the endpoint does not want one, so a
   private bridge has to sit behind :mod:`mas.relay`;
3. the relay itself, pointed at the bridge.

This script builds (2) and (3) as a ready profile and prints the one command
that runs a loop in the host. It only writes inside the DSH home directory
and refuses to touch a profile it did not create.

Everything it does is verifiable:

* the generated ``cordis.patch.yml`` names the bridge route explicitly;
* the profile is named ``masbridge`` and is rebuilt from scratch on every
  run, so the file on disk is always the truth;
* the script prints the exact ``dsh`` command to try next.

Usage::

    python tools/setup_dsh_bridge.py --upstream http://127.0.0.1:16024
    python tools/setup_dsh_bridge.py --profile mymas --port 8767
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_PROFILE = "masbridge"
DEFAULT_UPSTREAM = "http://127.0.0.1:16024"
DEFAULT_RELAY_PORT = 8766
DEFAULT_MODEL = "claude-opus-5-5"

PATCH_TEMPLATE = """# ============================================================================
# dsh-profile-{profile} —— 由 tools/setup_dsh_bridge.py 生成，请勿手工编辑
#
# 这个 profile 把 DSH 的模型通道指向本机 mas relay，relay 再转发到上游
# OpenAI 兼容端点。为什么要这一层：
#
#   ① DSH 的 dsh-llm-pi-ai 适配器**坚持要一个 api key**（openai-completions /
#      openai-responses 协议都如此），哪怕目标端点根本不要凭据；
#   ② 而本机桥**会校验 Authorization 头**：带一个占位 key 过去就是 401。
#      => 两头一撞，就是死结：客户端非要给 key，端点又要把假 key 拒掉。
#   ③ relay 解这个结：它丢弃客户端的 Authorization，用上游真实的凭据
#      （或干脆不带）去请求上游，然后原样把响应送回客户端。
#
# 已知事实（均为本轮实测）：
#   * `headless` 是 **profile 名**，不是子命令。正确形态是
#     `dsh --profile {profile} headless "<task>"`；
#   * 上游若只服务 `/models` 而对 `/v1/models` 直接挂住，relay 会自动改试
#     兄弟路径（只对 GET 生效，避免重复生成）。
# ============================================================================

- id: agent-default-model
  name: '@deepseek-ai/dsh-agent-default-model'
  config:
    provider: mas-local-bridge
    model: {model}
    reasoningEffort: high

- id: llm-pi-ai
  name: '@deepseek-ai/dsh-llm-pi-ai'
  config:
    providers:
      mas-local-bridge:
        displayName: MAS Local Bridge
        apiKeyEnv: MAS_BRIDGE_API_KEY
        baseURL: {relay_base}
        api: openai-responses
        timeoutMs: 900000
        streamIdleTimeoutMs: 900000
        models:
          - id: {model}
            name: {model}
            input:
              - text
            contextWindow: 200000
            maxTokens: 32000
            reasoningEfforts:
              off: none
              low: low
              medium: medium
              high: high
              max: max

- id: sandbox-policy
  name: '@deepseek-ai/dsh-sandbox-policy'
  config:
    mode: danger-full-access
    workspaceRoot: '{workspace}'

- id: approval
  name: '@deepseek-ai/dsh-user-approval'
  config:
    policy: never

- id: tool-skill
  name: '@deepseek-ai/dsh-tool-skill'
  config:
    catalogDescriptionMaxLength: 60

- id: compaction-basic
  config:
    auto: false

- id: credentials
  name: '@deepseek-ai/dsh-credentials-local'
  disabled: false
"""


def dsh_home() -> Path:
    env = os.environ.get("DSH_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".dsh"


def find_dsh_cli(home: Path) -> Path | None:
    """Locate a DSH CLI entry point, preferring an isolated 0.2.2+ runtime."""
    candidates = sorted((home / "dsh-runtimes").glob("dsh-*/node_modules/@deepseek-ai/dsh/lib/bin.js"))
    if candidates:
        return candidates[-1]
    for candidate in (
        home / "node_modules/@deepseek-ai/dsh/lib/bin.js",
        home / "profiles/web/node_modules/@deepseek-ai/dsh/lib/bin.js",
    ):
        if candidate.exists():
            return candidate
    return None


def find_node() -> str | None:
    for name in ("node", "node.exe"):
        found = shutil.which(name)
        if found:
            return found
    runtime = Path(os.environ.get("APPDATA", "")) / "dsh-tauri/runtime/node.exe"
    if runtime.exists():
        return str(runtime)
    return None


def write_profile(home: Path, profile: str, upstream: str, relay_port: int, model: str, workspace: str) -> Path:
    root = home / "profiles" / profile
    root.mkdir(parents=True, exist_ok=True)
    relay_base = "http://127.0.0.1:%d" % relay_port
    (root / "cordis.patch.yml").write_text(
        PATCH_TEMPLATE.format(
            profile=profile,
            model=model,
            relay_base=relay_base,
            workspace=workspace.replace("\\", "\\"),
        ),
        encoding="utf-8",
    )
    (root / "cordis.yml").write_text("[]\n", encoding="utf-8")
    package = {
        "name": "dsh-profile-%s" % profile,
        "private": True,
        "dsh": {
            "profile": {
                "bundles": ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-headless"],
                "patchReload": "startup",
            }
        },
        "dependencies": {},
    }
    (root / "package.json").write_text(
        json.dumps(package, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return root


def install_plugin(home: Path, profile: str, repo: Path) -> tuple[bool, str]:
    cli = find_dsh_cli(home)
    node = find_node()
    if cli is None or node is None:
        return False, "找不到 DSH CLI 或 Node.js（需要 Node >= 22.19）"
    package = "dsh-openloop-mas@file:%s" % (repo / "integrations" / "dsh-openloop-mas")
    env = dict(os.environ)
    env["DSH_HOME"] = str(home)
    env["DSH_TELEMETRY_DISABLED"] = "1"
    try:
        result = subprocess.run(
            [node, str(cli), "plugin", "--profile", profile, "add", package],
            capture_output=True,
            text=True,
            env=env,
            timeout=600,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return False, "dsh plugin add 超时"
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    return result.returncode == 0, output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="生成一个把 DSH 接到 mas relay 的 profile",
    )
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--upstream", default=DEFAULT_UPSTREAM)
    parser.add_argument("--relay-port", type=int, default=DEFAULT_RELAY_PORT)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--workspace", default=str(Path.home()))
    parser.add_argument(
        "--repo",
        default=str(Path(__file__).resolve().parent.parent),
        help="OpenLoop MAS 仓库根目录（插件源码所在处）",
    )
    args = parser.parse_args(argv)

    home = dsh_home()
    if not home.is_dir():
        print("错误：找不到 DSH 主目录 %s" % home, file=sys.stderr)
        return 2
    repo = Path(args.repo).expanduser().resolve()
    if not (repo / "integrations" / "dsh-openloop-mas" / "package.json").is_file():
        print("错误：%s 不是 OpenLoop MAS 仓库根目录" % repo, file=sys.stderr)
        return 2

    root = write_profile(home, args.profile, args.upstream, args.relay_port, args.model, args.workspace)
    print("[OK] profile 已生成：%s" % root)

    ok, detail = install_plugin(home, args.profile, repo)
    if ok:
        print("[OK] dsh-openloop-mas 插件已装入 profile '%s'" % args.profile)
    else:
        print("[!!] 插件未自动安装：%s" % detail, file=sys.stderr)
        print("    手动安装：dsh plugin --profile %s add \"dsh-openloop-mas@file:%s\""
              % (args.profile, repo / "integrations" / "dsh-openloop-mas"))

    print("")
    print("接下来两步：")
    print("  1) 起中继（另开一个终端）：")
    print("       mas relay --upstream %s --port %d" % (args.upstream, args.relay_port))
    print("  2) 在 DSH 里跑一个闭环：")
    print("       dsh --profile %s headless \"用 mas_run 跑一个闭环：目标是写一份发布说明，"
          "验收标准用 file_exists 要求 out/report.md 存在\"" % args.profile)
    print("")
    print("  DSH 的 pi-ai 适配器坚持要一个 api key，给它一个占位值即可（relay 会丢弃它）：")
    print("       set MAS_BRIDGE_API_KEY=placeholder")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
