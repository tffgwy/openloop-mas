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


## v0.2 接入增强

### 并行与回退配置

在 `~/.mas/config.json`（或项目 `mas/config.json`）里：

```json
"orchestration": {"parallel_workers": 3, "reviewers": 3, "review_mode": "majority"},
"fallbacks": [
  {"provider": "openai_compat", "base_url": "https://primary/v1", "model": "m1"},
  {"provider": "openai_compat", "base_url": "https://backup/v1", "model": "m2"}
]
```

`fallbacks` 顺序即优先级，主 provider 失败或超时自动切换，不必在业务层写重试。

### CI 门禁三件套

```yaml
- run: python -m mas verify          # 真跑一遍闭环，结果即门禁
- run: python -m mas bench --repeats 1  # 离线基准，看闭环率与 token
- run: python -m mas run --goal "..." --criteria-file criteria.json
```

三者都不需要密钥即可在 PR 上跑。退出码 `0` = 通过，`2` = 未闭合。

### 远程接入

需要给外部系统一个 HTTP 入口时：`mas http --port 8765`，
提供 `/health`、`/tools`、`/call`、`/events`（SSE 账本流），可挂在反向代理后面。

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
