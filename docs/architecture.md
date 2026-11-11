# 架构

## 组件

```
                       ┌──────────────┐
   goal + criteria ───▶│  LoopEngine  │───▶ RunResult(status, scores, artifact)
                       └──────┬───────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
   ┌─────────┐          ┌──────────┐          ┌──────────┐
   │ Provider│          │  Ledger  │          │ Tickets  │
   │ mock /  │          │ JSONL    │          │ 人工升级  │
   │ oai /   │          │ append   │          │ 工单     │
   │ anthropic│         │ only     │          └──────────┘
   └─────────┘          └──────────┘
```

- **Provider**：只负责「给定提示词，返回文本」。判定权不在模型手里。
- **Ledger**：append-only 的 JSONL，每轮一条 `closure` 条目，外加 `planner` 条目。
- **Tickets**：循环无法自行闭合时的升级对象，携带完整上下文。
- **Roles**：五个角色的系统提示词与任务状态模板。

## 一次循环的状态机

```
        ┌──────────┐
        │ planner  │  仅第一轮，产出 plan
        └────┬─────┘
             ▼
        ┌──────────┐
   ┌───▶│  worker  │  产出交付物 artifact
   │    └────┬─────┘
   │         ▼
   │    ┌──────────┐
   │    │ verifier │  机检 + 模型判定 -> scores
   │    └────┬─────┘
   │         ▼
   │    ┌──────────┐
   │    │ reflect  │  仅失败时，产出 issues
   │    └────┬─────┘
   │         ▼
   │    ┌──────────┐
   │    │   lead   │  close / revise / escalate
   │    └────┬─────┘
   │         │
   │    close ─────────────▶ 闭合（closed）
   │         │
   │         └─ revise ────┐
   │                      └──▶ 下一轮
   │
   └── escalate ──────────▶ 人工工单（escalated）
```

## 判定优先级

1. **机检规则优先**：只要验收标准带 `check`，结果由 `evaluate_check()` 决定，模型说什么都不影响。
2. **模型判定兜底**：没有机检规则的标准才看 `verifier` 的 `scores`。
3. **未判定不通过**：模型没给出的判定记为 `unjudged`（`source: model-missing`），判不通过。
4. **可选标准不阻塞**：`required: false` 的标准只记录。

这一层顺序是整个项目最重要的设计决定：**模型的乐观不能覆盖确定性证据。**

## 四道终止路径

| 终止 | 触发条件 | 结果 |
| --- | --- | --- |
| 闭合 | 全部必需标准通过 | `closed` |
| 升级 | 停滞次数 ≥ `stagnation_limit`，或 token 触顶 | `escalated` + 工单 |
| 预算 | 迭代 / 墙钟触顶 | `budget_exhausted` |
| 错误 | Provider 抛错 | `error`（账本里留痕） |

## 停滞判定

对**产物文本**取 SHA-256。连续多轮哈希相同 → 判定卡死（模型在原地打转）。
注意这比「答案看起来一样」更严格：文本有任何变化哈希就变，因此它抓的是**真正的原地打转**，而不是「同一个意思换了说法」。

## 状态目录

```
~/.mas/
├─ config.json
├─ ledgers/runs.jsonl
└─ tickets/T-YYYYMMDD-HHMMSS-xxxx.json
```

全部可用环境变量 `MAS_STATE_DIR` 覆盖，测试因此不会污染真实状态。

## 关键文件

| 文件 | 职责 |
| --- | --- |
| `mas/engine.py` | 闭环引擎：判定、预算、停滞、闭合/升级 |
| `mas/providers.py` | 三种模型适配 + 离线模拟器 |
| `mas/ledger.py` | 账本读写 |
| `mas/tickets.py` | 工单生命周期 |
| `mas/mcp_server.py` | MCP stdio 服务（JSON-RPC 2.0） |
| `mas/doctor.py` | 安装自检 |
| `mas/demo.py` | 三个离线场景 |
