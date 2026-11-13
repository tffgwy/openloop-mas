---
name: mas-loop
description: 用 OpenLoop MAS 跑一个可验收的多智能体闭环任务（规划→执行→验证→复盘→闭合），当用户要求"多智能体协作 / 闭环 / 迭代到通过 / 自动验收 / MAS / loop engineering"，或需要把一件事交给多个角色分工作业并拿到可核验结果时使用。也用于查看闭环账本与人工工单。
---

# MAS 闭环（OpenLoop MAS）

## 它解决什么

普通的多智能体协作最容易出问题的地方不是"不够聪明"，而是**不闭合**：一轮轮聊下去，没有验收标准、没有退出条件、没有过程记录，最后只能说"差不多了"。

OpenLoop MAS 把闭环写成代码约束：

| 环节 | 角色 | 产出 |
| --- | --- | --- |
| 计划 | planner | 步骤与风险 |
| 执行 | worker | **交付物本身** |
| 验证 | verifier | 逐条验收标准的通过/不通过 + 证据 |
| 复盘 | reflector | 失败原因与下一轮改法 |
| 闭合 | lead | close / revise / escalate |

只有当"产物 + 逐条判定 + 决策 + 账本条目"四件套齐全，循环才算闭合；否则状态只会是
`closed` / `escalated`（升级人工）/ `budget_exhausted`（预算耗尽），没有"差不多完成"。

## 怎么用（CLI）

```bash
# 1) 离线自检：不需要任何密钥，验证闭环引擎本身
mas demo

# 2) 带机检验收标准跑一个任务（机检标准由引擎判定，不靠模型自觉）
mas run --goal "写一份发布说明" \
  --criteria '{"id":"c1","desc":"包含版本号","check":{"type":"contains","arg":"v0.1.0"}}' \
  --criteria '{"id":"c2","desc":"不少于 200 字","check":{"type":"min_chars","arg":200}}'

# 3) 验收标准较多时用文件
mas run --goal "..." --criteria-file criteria.json

# 4) 看过程账本 / 人工工单
mas ledger --tail 20
mas tickets
mas resume T-20261111-085346-4396 --note "按用户口径重写结论段"
```

## 怎么用（MCP）

注册后（安装器会自动写入 `~/.codex/config.toml` 的 `mcp_servers.mas_loop`），
在 Codex / Claude Code 里可以直接调用工具：

- `mas_run` — 跑一个闭环，返回 `status` 与逐条 `scores`
- `mas_status` — 当前配置、账本规模、未处理工单
- `mas_ledger` — 读闭环账本（JSONL，可按 run_id 过滤）
- `mas_tickets` / `mas_ticket_resolve` / `mas_resume` — 人工介入闭环
- `mas_teams` / `mas_doctor` / `mas_config`


## v0.2 编排与观测（新增）

### 并行候选选优

同一目标可以并发跑多个候选视角，由引擎按「机检通过数 → 长度 → 下标」确定性选优，
不依赖模型自觉挑最好的：

```bash
mas run --goal "写一份发布说明" --criteria-file criteria.json \
  --parallel 3 --perspective thorough
```

可用视角：`direct`（默认）、`thorough`、`concise`、`structured`、`example_first`、`risk_first`。

### 故障转移（provider 链）

配置里写多个 provider，某个挂掉或超时自动切下一个：

```json
"fallbacks": [
  {"provider": "openai_compat", "base_url": "https://primary/v1", "model": "m1"},
  {"provider": "openai_compat", "base_url": "https://backup/v1", "model": "m2"}
]
```

### 对抗评审聚合

多个评审人格（`strict` / `skeptic` / `completeness` / `reader`）投票，
`review_mode` 可选 `all`（全票通过）、`majority`（过半）、`any`（任一通过）。
机检结果永远压过模型主观判断。

### 观测与回放

```bash
mas report              # 最近一次运行的 Markdown 报告
mas replay <run_id>    # 按账本逐轮回放每一步输入输出
mas export --format json   # 导出账本
```

HTTP/SSE 供外部系统接入：

```bash
mas http --port 8765
# GET  /health   存活探测
# GET  /tools    工具清单
# POST /call     {"tool": "mas_run", "arguments": {...}}
# GET  /events   SSE 实时账本流
```

### 初始化、自检与基准

```bash
mas init --wizard   # 交互式 10 问：provider / 模型 / 团队 / 编排参数 / 回退链
mas verify          # 真实跑一遍闭环并给结论（provider / closure / ledger / roles 四项）
mas bench --repeats 2   # 离线基准：闭环率、迭代数、token、延迟、账本一致性
```

`mas verify` 与 `mas bench` 都不需要密钥，适合放进 CI 当冒烟门禁。

## 写验收标准的规矩（关键）

1. **能用机检就别用模型判定。** 支持的 `check.type`：
   `contains`、`not_contains`、`min_chars`、`max_chars`、`regex`、`lines_min`、`section`、`json_keys`。
   带机检的标准由引擎判定，结果可复现；不带机检的标准才交给模型验证者主观判定。

   各类型的**精确语义**（实测确认，写标准时按这个来，否则会白跑一轮）：

   | 类型 | 判定方式 | 容易踩的坑 |
   | --- | --- | --- |
   | `contains` | 产物包含给定字符串；`arg` 可传数组，**全部命中**才算通过 | 数组里漏写一项就整条失败 |
   | `not_contains` | 产物**不含**任何给定字符串 | 传数组时是「一个都不能出现」 |
   | `min_chars` / `max_chars` | 产物的**字符数**（含空白与换行） | 不是字数也不是字节数 |
   | `regex` | `re.search` 匹配，**自动带 DOTALL**（`.` 能跨行） | 想跨行不用加 `(?s)`；找子串不是全串 |
   | `lines_min` | **非空行**数（空行不计） | 段落之间空行分隔不影响计数 |
   | `section` | 产物里存在 `## <arg>` 这一行 | **必须是二级标题 `##`**，`#` 或 `###` 都不算 |
   | `json_keys` | 产物是 JSON 对象且含全部给定键（可用数组） | 产物不是合法 JSON 就直接判不通过 |

   未知类型会被判为 `unjudged`（**不通过**），不会静默放过。
2. **没机检标准的标准默认判不通过**（`unjudged`），不会因为模型口头说"可以了"就放行。
   写标准时也可以用 CLI 简写 `--criteria 'regex:def dedupe.*set'`（类型:参数），
   简写会自动变成机检标准；类型名写错或参数不是数字时**不会**被当成简写，
   会退回成一条纯描述（也就是不会有机检）。
3. 标准要能对应读者可见的事实，不是"质量好""逻辑清晰"这种无法核验的话。
4. `required: false` 的标准不阻塞闭合，只做记录。

## 停止条件

- 迭代数、墙钟时长、token 三重预算，任意触顶即 `budget_exhausted`。
- 连续多轮产出**完全相同**的产物（默认 3 次）判定为停滞，升级人工工单，不空转烧钱。
- 工单里带齐：目标、验收标准、最后一版产物、失败原因，人工一句 `mas resume` 即可继续。

## 相关技能

- `mas-review`：多视角对抗式审查，用同一套闭环机制做质量门。
- `mas-scaffold`：为新项目生成团队模板与验收标准骨架。
