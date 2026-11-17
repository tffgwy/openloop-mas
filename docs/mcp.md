# MCP 接入说明

OpenLoop MAS 的 MCP 服务是**纯标准库实现的 JSON-RPC 2.0 over stdio**，
不需要 `pip install mcp`，也不需要 Node。

---

## 1. 启动

```bash
python -m mas serve
```

进程读 stdin 的 JSON-RPC 消息，写 stdout 的 JSON-RPC 响应。
stderr 只用于日志，**不要把任何东西打到 stdout**，否则会污染协议流。

---

## 2. 注册

### Codex（`~/.codex/config.toml`）

```toml
[mcp_servers.mas_loop]
command = "python"
args = ["-m", "mas", "serve"]
startup_timeout_sec = 30.0
tool_timeout_sec = 300.0
env = { PYTHONPATH = "<install-dir>/app" }
```

### Claude Code（`~/.claude.json`）

```json
{
  "mcpServers": {
    "mas_loop": {
      "command": "python",
      "args": ["-m", "mas", "serve"],
      "env": { "PYTHONPATH": "<install-dir>/app" }
    }
  }
}
```

### 任意客户端

用上面任意一种结构即可；只要能拉起 stdio 子进程并讲 JSON-RPC 2.0 就能接。

---

## 3. 握手

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"demo","version":"1.0"}}}
```

服务返回 `result.serverInfo` 与 `result.capabilities.tools`。
之后客户端发 `notifications/initialized`，再调 `tools/list` 或 `tools/call`。

---

## 4. 工具清单（18 个）

| 工具 | 作用 | 关键参数 |
| --- | --- | --- |
| `mas_run` | 跑一个闭环任务，返回闭合状态与逐条判定 | `goal`（必填）、`criteria`、`team`、`max_iterations` |
| `mas_status` | 当前配置、账本规模、未处理工单 | 无 |
| `mas_ledger` | 读闭环账本 | `run_id`（可选过滤）、`limit` |
| `mas_tickets` | 列出人工升级工单 | `status`（可选） |
| `mas_ticket_resolve` | 标记工单已处理 | `ticket_id`（必填）、`note` |
| `mas_resume` | 带人工意见继续跑 | `ticket_id`（必填）、`note` |
| `mas_handback` | 把人写的交付物交回，交给同一套验收 | `ticket_id`（必填）、`artifact` 或 `file` |
| `mas_report` | 生成闭环复盘报告 | `run_id`（可选）、`as_markdown` |
| `mas_replay` | 回放运行轨迹 | `run_id`（可选） |
| `mas_export` | 导出账本为 Markdown 表格 | `run_id`（可选） |
| `mas_teams` | 列出团队模板 | 无 |
| `mas_doctor` | 安装自检（9 项） | 无 |
| `mas_config` | 读取 / 更新配置 | `set`（可选，键值对） |
| `mas_drafts` | 列出待确认的草稿，或看某一份的 diff | `action`（`list` / `show`）、`draft_id`、`state`、`workspace` |
| `mas_draft_stage` | **提**一份改动；原文件一个字节都不动 | `path`（必填）、`content`（必填）、`reason`、`workspace` |
| `mas_draft_resolve` | 批准 / 驳回草稿；**批准要人类令牌或一次性票据** | `action`（`approve` / `reject`）、`draft_id`（必填）、`token`、`ticket`、`note`、`force` |
| `mas_draft_compare` | **比一比**覆盖会丢什么：基线 / 现在 / 草稿三方对比 | `draft_id`（必填）、`workspace` |
| `mas_draft_undo` | **撤回**一次已批准的改动：文件逐字节回到改动前；**要人类令牌或一次性票据** | `draft_id`（必填）、`token`、`ticket`、`note`、`force`、`workspace` |
| `mas_files` | 索引工作区：每个文件是什么格式、能读出什么 | `path`（可选，默认所有工作区）、`workspace` |
| `mas_preview` | 预览一个文件（PDF / Word / 文本…） | `path`（必填）、`workspace`、`max_chars` |

### 4.0.1 agent 能提草稿，但不能自己批

`mas_draft_stage` 让 agent **提**改动，`mas_draft_resolve` 让人**批**。
**批准要人类令牌**（`mas drafts token`）：令牌不对、空令牌、没令牌，
一律拒绝，而且**原文件不动**。
详见 **[草稿层文档](drafts.md)**。

### 4.0.2 一次性票据：给出去的是一个决定

常备令牌是**一把万能钥匙** —— 贴给 agent 一次，它就能批准**所有**草稿。
**票据**是窄门：`mas drafts ticket <草稿编号>` 用人类令牌签一张，
它**只对那一个草稿**生效、**用一次就烧掉**、**15 分钟过期**，
**盘上只存哈希**。

```json
{
  "name": "mas_draft_resolve",
  "arguments": {
    "action": "approve",
    "draft_id": "D-1a2b3c4d",
    "ticket": "<那张票据>"
  }
}
```

传了 `ticket` 就走票据这条路，**没传才回落到常备令牌**。
票据不对时报错会说明到底卡在哪：`ticket_already_used` /
`ticket_expired` / `ticket_bound_to_another_draft` /
`ticket_action_mismatch`。

### 4.0.3 并发写冲突：文件动过就拒绝写回

草稿记着它**当时对着的那份文件**的哈希。
人等草稿时改了文件 —— 批准会**把人的新内容静默覆盖**。
两个 agent 抢一个文件时，这就是**丢失更新**。

所以 `mas_draft_resolve` 现在**写之前先对一下**。对不上就**拒绝**：

```json
{
  "error": "draft_conflict",
  "message": "the file changed while the draft was waiting (expected 3bfc269594ef, found 7d98d5e2d094); approve with force to overwrite it anyway",
  "path": "notes.txt",
  "expected_sha256": "3bfc269594ef...",
  "current_sha256": "7d98d5e2d094..."
}
```

被拒时：**磁盘不动、草稿继续等、票据不烧**。

明知要覆盖，就传 `force`：

```json
{
  "name": "mas_draft_resolve",
  "arguments": {
    "action": "approve",
    "draft_id": "D-1a2b3c4d",
    "token": "<人类令牌>",
    "force": true
  }
}
```

`force` 是**给人的**开关：agent 不该自己决定覆盖掉别人的活。

### 4.0.4 覆盖之前先看清会丢什么

`mas_draft_compare` 让 agent 问出**同一个问题**：
「批准这份草稿，我会丢掉什么？」

```json
{
  "name": "mas_draft_compare",
  "arguments": {
    "draft_id": "D-1a2b3c4d",
    "workspace": "<工作区>"
  }
}
```

回答里有三段：**基线 → 现在**（你写的改动）、**现在 → 草稿**
（草稿要写的改动）、以及 `would_lose` ——
**覆盖会丢掉的那几行**（你写的、草稿里没有的）。
`would_lose` 为空就说明**覆盖不会丢掉你写的任何一行**。

**这个动作不改任何东西**，是一份报告。

`mas_status` 里还有 `conflicting_drafts` / `conflicting_draft_count`：
卡在冲突上的草稿是**人还欠的一个决定** —— 票据队列是给循环问问题用的，
不写进那里就等于**没人看见**。

### 4.0.5 批错了还能退回来

`mas_draft_undo` 让 agent 执行**撤回**：把一次已批准的改动收回来，
文件**逐字节**回到草稿建立时的样子。

```json
{
  "name": "mas_draft_undo",
  "arguments": {
    "draft_id": "D-1a2b3c4d",
    "token": "<人类令牌>",
    "workspace": "<工作区>"
  }
}
```

它和批准一样**需要人类令牌或一次性票据** ——
agent 能提草稿、能问代价，但**改一个已经写进文件的决定，得是人干的**。

它也和批准一样**先问代价**：文件在**批准之后**又被人改过的话，
撤销**被拒**，回答里会带 `would_lose` —— **撤回会丢掉的那几行**：

```json
{
  "error": "undo_conflict",
  "approved_sha256": "cdf843f6332b...",
  "current_sha256": "e7a872fcc345...",
  "would_lose": ["human-after-approval"],
  "would_lose_count": 1
}
```

确认要连这些改动一起退掉，就加 `"force": true`。

撤销之后**草稿还在**（状态 `undone`），同一个改动**还能再批准一次**。

`mas_status` 里还有 `undone_drafts` / `undone_draft_count`：
一个被撤销的改动**不是「还没发生」**，它说明**有一次批准是错的**，
而那份草稿还在原地等着被批准第二次。

### 4.1 `mas_run` 示例

请求：

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "mas_run",
    "arguments": {
      "goal": "写一份发布说明",
      "criteria": [
        {"id": "c1", "desc": "包含版本号", "check": {"type": "contains", "arg": "v0.1.0"}},
        {"id": "c2", "desc": "不少于 200 字", "check": {"type": "min_chars", "arg": 200}}
      ]
    }
  }
}
```

响应体（`content[0].text` 内为 JSON）包含：

```json
{
  "status": "closed",
  "iterations": 2,
  "tokens": 2140,
  "closed": true,
  "scores": [
    {"id": "c1", "pass": true, "note": "all required strings present", "source": "rule"},
    {"id": "c2", "pass": true, "note": "length 418 >= 200", "source": "rule"}
  ],
  "artifact": "...",
  "ticket": null,
  "ledger_path": "<state>/ledgers/runs.jsonl"
}
```

`status` 的四种取值：`closed` / `escalated` / `budget_exhausted` / `error`。

### 4.2 `mas_config` 示例

读取：

```json
{"name": "mas_config", "arguments": {}}
```

更新：

```json
{"name": "mas_config", "arguments": {"set": {"provider": "openai_compat", "model": "kimi-k2"}}}
```

`set` 是**深合并**，只改传入的键，其余保留。

---

## 5. 设计取舍

- **不引第三方 SDK**：MCP 协议面很小，自己实现 JSON-RPC 2.0 更可控，零安装成本，也避免 SDK 版本漂移。
- **状态在文件里**：账本、工单、配置都在 `~/.mas`（可用 `MAS_STATE_DIR` 覆盖），所以 MCP 重启不丢上下文，多个客户端可以共享同一份账本。
- **判定权在引擎**：MCP 只是入口，闭合与否始终由确定性规则决定，客户端拿到的 `scores` 里 `source: "rule"` 表示机检，`source: "model"` 表示模型判定，`unjudged` 表示模型没给判定（按不通过算）。
- **超时**：客户端侧建议 `tool_timeout_sec = 300` 以上；引擎侧另有 `budget.deadline_sec` 兜底。

---

## 6. 排障

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 服务一启动就退出 | `PYTHONPATH` 没指向安装目录 | 用安装器生成的绝对路径 |
| 客户端看不到工具 | 客户端没发 `tools/list` 或握手没走完 | 用 `initialize` → `notifications/initialized` → `tools/list` 手工验证 |
| stdout 出现非 JSON 文本 | 有人往 stdout 打了日志 | 日志统一走 stderr |
| 工具调用卡住 | 任务本身耗时长 | 调大 `tool_timeout_sec` 与 `budget.deadline_sec` |

手工验证：

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | python -m mas serve
```
