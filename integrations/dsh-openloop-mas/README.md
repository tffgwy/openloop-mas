# dsh-openloop-mas

OpenLoop MAS 插件（DeepSeek Harness）。

把「会闭合的多智能体循环」带进 DSH 对话：规划 → 执行 → **机检验收** → 复盘 →
闭合 / 升级人工工单，每一轮都写进 **append-only 账本**。

## 它做什么

在 DSH 里直接可用 6 个工具：

| 工具 | 用途 |
| --- | --- |
| `mas_run` | 跑一个闭环任务，返回状态 + 逐条验收判定 + 票数 + 账本路径 |
| `mas_status` | 当前配置、运行数、未结工单 |
| `mas_ledger` | 读闭环账本（可按 run_id 过滤） |
| `mas_tickets` | 列出人工升级工单 |
| `mas_report` | 渲染某次运行的 Markdown 报告 |
| `mas_doctor` | 运行环境自检 |

插件**不重写闭环逻辑**：它通过 MCP（stdio JSON-RPC 2.0）调用本地 OpenLoop MAS
运行时，因此 DSH 对话里的判定与账本记录和 Codex / Claude Code 客户端**完全一致**。

## 前提

- **DSH >= 0.2.2-rc.1**（声明兼容区间 `>=0.2.2-rc.1 <0.3.0`）
- **Node.js >= 22.19**
- 已装好 OpenLoop MAS 运行时（默认在 `~/.mas`）

## 安装

前置：机器上已装好 OpenLoop MAS 运行时（默认在 `~/.mas`）。

```bash
# 从本仓库打包
cd integrations/dsh-openloop-mas
npm pack            # -> dsh-openloop-mas-0.7.13.tgz

# 装进 DSH Desktop profile
dsh plugin --profile desktop add "dsh-openloop-mas@file:<绝对路径>/dsh-openloop-mas-0.7.13.tgz"
dsh plugin --profile desktop list dsh-openloop-mas --depth 0
```

> `desktop` profile 由桌面应用独占管理，命令行会报
> `profile "desktop" is managed exclusively by the Electron application`。
> 这种情况下请在 DSH Desktop 的插件面板里安装，或改用 `--profile cli`。

## 配置

```json
{
  "dsh-openloop-mas": {
    "masHome": "C:/Users/<you>/.mas",
    "python": "C:/Python314/python.exe",
    "timeoutMs": 900000
  }
}
```

- `masHome`：OpenLoop MAS 安装根目录（`install.ps1` 写入的位置）。留空则只依赖
  PATH 上的 `mas` 运行时。
- `python`：解释器路径。留空则从 `masHome` 推断，再退回 `python`。
- `timeoutMs`：单次 MCP 调用超时。默认 900000（15 分钟）——闭环可能要跑好几轮。

## 前提与边界

- 本插件**只负责调用**，判定权在 OpenLoop MAS 引擎手里：机检结果永远压过模型
  主观判断；缺席的评审在 `all` 模式下判**不通过**（不闭合）。
- 插件不引入任何第三方 npm 依赖（只用 DSH 自带的 `@deepseek-ai/dsh-tools` 与
  `@deepseek-ai/schemastery`）。
- `lib/mcp-client.js` **零依赖**，可以脱离 DSH 单独跑单元测试：
  `node --test integrations/dsh-openloop-mas/tests/`。

## 挂载机制（容易踩的坑）

DSH 认的字段是 **`dsh.bundle.patch`**（指向 `cordis.patch.yml`），
不是 `dsh.profile.bundles`。放错的结果是：插件被当成普通依赖装上、
**根本不挂载**，pnpm 会警告 `declares no dsh.bundle`。

挂载后，DSH 启动时自动应用 patch：

```
- insert:
    - id: openloop-mas
      name: 'dsh-openloop-mas'
      config: ...
```

工具参数 schema 必须满足 DSH 的 JSON Schema 子集：`items.additionalProperties`
要**显式**为 `true` / `false`，否则 `defineTool` 会抛 `JsonSchemaError`，
整个插件 entry 不激活（`1 entry did not activate`）。

## 与 MOKI 的分工

MOKI 资料库（`dsh-moki-library`）管的是**资料与草稿**：本地文件/文件夹/链接管理、
多格式预览、Word/Excel/PPT 隔离草稿编辑、选区协同（`Alt+D` 唤起 DSH 对话），
修改先入草稿、用户确认后才写回原文件。

OpenLoop MAS 管的是**闭环**：验收标准、机检判定、账本、人工升级。

两者可以同时装，各管一段：MOKI 负责把资料整理成一份可交付的产物，
OpenLoop MAS 负责判定这份产物到底过不过。

## License

MIT
