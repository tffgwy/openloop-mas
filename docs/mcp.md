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

## 4. 工具清单（9 个）

| 工具 | 作用 | 关键参数 |
| --- | --- | --- |
| `mas_run` | 跑一个闭环任务，返回闭合状态与逐条判定 | `goal`（必填）、`criteria`、`team`、`max_iterations` |
| `mas_status` | 当前配置、账本规模、未处理工单 | 无 |
| `mas_ledger` | 读闭环账本 | `run_id`（可选过滤）、`limit` |
| `mas_tickets` | 列出人工升级工单 | `status`（可选） |
| `mas_ticket_resolve` | 标记工单已处理 | `ticket_id`（必填）、`note` |
| `mas_resume` | 带人工意见继续跑 | `ticket_id`（必填）、`note` |
| `mas_teams` | 列出团队模板 | 无 |
| `mas_doctor` | 安装自检（9 项） | 无 |
| `mas_config` | 读取 / 更新配置 | `set`（可选，键值对） |

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
