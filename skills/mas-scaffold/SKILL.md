---
name: mas-scaffold
description: 为新项目搭建多智能体团队骨架与验收标准模板（团队角色提示词、criteria JSON、CI 门禁），当用户要"搭一套 agent 团队 / 生成验收标准 / 把 MAS 接进现有项目 / 新项目接入 MAS"时使用。
---

# 项目接入（OpenLoop MAS）

## 产出物

给一个项目接入 MAS，通常需要三样东西：

1. **团队模板**：`mas/teams/<name>.json`，定义各角色的系统提示词与语言。
2. **验收标准文件**：`criteria.json`，把"做完没有"翻译成可机检的条目。
3. **入口命令**：把 `mas run ...` 挂进脚本或 CI，用退出码当门禁。

## 建议流程

```bash
# 1) 看现有团队模板
mas teams

# 2) 新建团队：复制 mas/teams/default.json 改角色提示词，放进项目的 mas/teams/
# 3) 为每个任务写 criteria.json
cat > criteria.json <<'JSON'
[
  {"id": "c1", "desc": "产出包含目标段落", "check": {"type": "contains", "arg": "目标段落"}},
  {"id": "c2", "desc": "长度达标", "check": {"type": "min_chars", "arg": 800}}
]
JSON

# 4) 跑一次，退出码即门禁
mas run --goal "产出首稿" --criteria-file criteria.json || exit 1
```

## 接入 MCP（给 IDE / 编码助手用）

在 `~/.codex/config.toml` 或 `~/.claude.json` 里注册：

```toml
[mcp_servers.mas_loop]
command = "python"
args = ["-m", "mas", "serve"]
startup_timeout_sec = 30.0
```

## 什么时候不要上 MAS

- 只有一次性、无验收标准的小请求 —— 直接做更快。
- 任务本质是开放式创作且**没有**任何可核验标准 —— 先把标准想清楚，否则闭环只会空转。
- 已经在用别的编排器且它已有验收与账本机制 —— 不要为了上 MAS而上 MAS。
