# DeepSeek Harness 集成（DSH 插件）

把 OpenLoop MAS 的闭环能力带进 [DeepSeek Harness](https://github.com/deepseek-ai) 对话：
规划 → 执行 → **机检验收** → 复盘 → 闭合 / 升级人工工单。

本目录下的 `dsh-openloop-mas` 是一个 **DSH 插件包**，源码在
`integrations/dsh-openloop-mas/`。

---

## 1. 它做什么（以及为什么不重写闭环）

插件**不实现**任何闭环逻辑。它通过 **MCP**（stdio 上的换行分隔 JSON-RPC 2.0）
调用本地 OpenLoop MAS 运行时：

```
DSH 对话  ->  dsh-openloop-mas 插件  ->  MCP (JSON-RPC/stdio)  ->  mas.mcp_server
                                                                    |
                                                          机检 / 判定 / 账本 / 工单
```

这样做的理由很实在：

- **账本只有一份**。DSH 里跑的闭环和 Codex / Claude Code 里跑的闭环写进
  **同一个 append-only 账本**，回放时能对上；
- **判定权不在插件手里**。机检永远压过模型主观判断；缺席的评审在 `all` 模式下
  判**不通过**（不闭合）——这些都在 MAS 引擎里，插件只是调用方；
- **不解析 CLI 输出**。走 MCP 就不会因为输出格式变了而失效。

插件暴露 6 个工具：

| 工具 | 用途 |
| --- | --- |
| `mas_run` | 跑一个闭环任务，返回状态 + 逐条验收判定 + 票数 + 账本路径 |
| `mas_status` | 当前配置、运行数、未结工单 |
| `mas_ledger` | 读闭环账本（可按 `run_id` 过滤） |
| `mas_tickets` | 列出人工升级工单 |
| `mas_report` | 渲染某次运行的 Markdown 报告 |
| `mas_doctor` | 运行环境自检 |

---

## 2. 安装

前置条件：

- **DSH >= 0.2.2-rc.1**（声明兼容区间 `>=0.2.2-rc.1 <0.3.0`）
- **Node.js >= 22.19**
- 已装好 OpenLoop MAS 运行时（默认 `~/.mas`）

### 方式 A：一键安装脚本（推荐）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -InstallDshPlugin
```

这个开关**默认关闭**：主线安装仍然是零依赖的纯 Python，初学者不需要
Node、DSH 或 npm。

脚本会：`npm pack` 打包插件 → `dsh plugin --profile <profile> add` 安装 →
失败时**降级成警告**并把 tarball 路径与手动安装命令打印出来（不会让整个安装崩掉）。

可指定 profile：

```powershell
powershell -File .\install.ps1 -InstallDshPlugin -DshProfile cli
```

### 方式 B：手动

```bash
cd integrations/dsh-openloop-mas
npm pack            # -> dsh-openloop-mas-0.7.13.tgz

dsh plugin --profile desktop add "dsh-openloop-mas@file:<绝对路径>/dsh-openloop-mas-0.7.13.tgz"
dsh plugin --profile desktop list dsh-openloop-mas --depth 0
```

> **`desktop` profile 由 DSH 桌面应用独占管理**：命令行会报
> `profile "desktop" is managed exclusively by the Electron application`。
> 这种情况下改用 `--profile cli`，或在 DSH 的插件面板里安装。

---

## 3. 配置

```json
{
  "dsh-openloop-mas": {
    "masHome": "C:/Users/<you>/.mas",
    "python": "C:/Python314/python.exe",
    "timeoutMs": 900000
  }
}
```

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `masHome` | `""` | OpenLoop MAS 安装根目录。留空则只依赖 PATH 上的 `mas` 运行时 |
| `python` | `""` | 解释器。留空则从 `masHome` 推断，再退回 `python` |
| `timeoutMs` | `900000` | 单次 MCP 调用超时。闭环可能要跑好几轮，默认 15 分钟 |

---

## 4. 在对话里怎么用

> "用 mas_run 跑一个闭环：目标是写一份发布说明，验收标准是包含 `## 结论`
> 标题且不少于 200 字，用 3 位评审、all 模式。"

插件会把判定、票数（`votes: "3/3"`）、缺席票（`absent_votes`）和账本路径
一并返回——**和 CLI / MCP 客户端看到的是同一份事实**。

---

## 5. 目录结构

```
integrations/dsh-openloop-mas/
├─ package.json          插件清单（name / version / engines / dsh.bundle.patch）
├─ cordis.patch.yml      bundle patch 层：DSH 启动时自动挂载本插件
├─ lib/
│  ├─ index.js           DSH 胶水层：Config / apply / inject / 6 个工具
│  └─ mcp-client.js      纯 Node 的 MCP 客户端（零依赖，可独立测试）
├─ tests/
│  └─ mcp-client.test.mjs
└─ README.md
```

`mcp-client.js` 刻意**不依赖 DSH**，因此可以在没有 DSH 主机的环境里做单元测试：

```bash
node --test integrations/dsh-openloop-mas/tests/
```

---

## 6. 与 MOKI 资料库的分工

这两者**不是同一个项目**，装在一起也不冲突：

| | MOKI 资料库（`dsh-moki-library`） | OpenLoop MAS（`dsh-openloop-mas`） |
| --- | --- | --- |
| 定位 | 资料与草稿的**本地资源管理器** | 多智能体**闭环运行时** |
| 产物 | 文件 / 文件夹 / 网页链接的整理，多格式预览 | 验收标准 + 判定 + 账本 + 工单 |
| 编辑模型 | 修改先入**草稿**，用户确认后写回原文件 | 产物由 agent 产出，按**机检规则**判定 |
| 交互 | 选区（文字 / 单元格 / 页面区域）→ `Alt+D` 唤起 DSH 对话 | 对话里调用 `mas_run` 等工具 |

**组合起来**：MOKI 负责把资料整理成一份可交付的产物，
OpenLoop MAS 负责判定这份产物到底过不过。

```bash
# 两个插件可以同时装
dsh plugin --profile desktop add "dsh-moki-library@file:<绝对路径>/dsh-moki-library-0.1.73.tgz" --allow-build=sharp
dsh plugin --profile desktop add "dsh-openloop-mas@file:<绝对路径>/dsh-openloop-mas-0.7.13.tgz"
```

---

## 7. 边界说明（已验证 / 未验证）

**已验证（本轮实测）**：

- **插件在真实 DSH 宿主里成功激活**：`dsh --profile cli headless` 启动时打印
  `[openloop-mas] ready: python=python`；
- **6 个工具在真实 `defineTool` 下全部注册通过**（schema 校验通过）：
  `mas_run` / `mas_status` / `mas_ledger` / `mas_tickets` / `mas_report` /
  `mas_doctor`；
- `dsh --profile cli --dump-config` 能看到插件被正确合成进 profile：
  `- id: openloop-mas / name: dsh-openloop-mas / config: {masHome, python, timeoutMs}`；
- 纯 Node MCP 客户端 `listTools` 返回 **12 个工具**，`mas_status` 返回真实账本
  （127 条记录、2 张未结工单）；
- `mas_run` 端到端闭环：`status=closed`、`closed=true`、评审 `votes=3/3`、
  token 65299；
- Node 单元测试 **5/5 通过**；
- 隔离安装（`-SkipPath -SkipMcp -SkipDemo`）通过；
- `-InstallDshPlugin` 路径在 DSH 命令失败时**优雅降级**（打印 tarball 路径与
  手动安装命令，不崩溃）；
- `npm pack` 产物：`dsh-openloop-mas-0.7.13.tgz`（5 个文件，约 14 KB 解包）；
- `dsh plugin --profile cli add` 安装成功，且**不再出现**
  `declares no dsh.bundle` 警告。

**过程中被真实宿主抓出来的两个 bug（都已修）**：

1. `dsh.profile.bundles` 放错了位置。DSH 认的是 `dsh.bundle.patch`（指向
   `cordis.patch.yml`），放错的结果是插件被装成普通依赖、**不挂载**，
   pnpm 会警告 `declares no dsh.bundle`；
2. 工具参数 schema 不满足 DSH 的 JSON Schema 子集：`items.additionalProperties`
   必须**显式**为 `true` 或 `false`，否则 `defineTool` 直接抛
   `JsonSchemaError`，整个插件 entry 不激活（`1 entry did not activate`）。
   这是**真实宿主才暴露**的——离线单测完全测不出来。

**未验证**：

- **没有在 DSH 桌面应用（Electron）里实测**——本机 `desktop` profile 由桌面应用
  独占管理，命令行报 `profile "desktop" is managed exclusively by the Electron
  application`；验证是在 **cli profile + headless 宿主**下完成的；
- 本机 DSH 运行时实测为 **0.2.1-rc.5**，**低于** MOKI 声明的 `>=0.2.2-rc.1`。
  本插件自身没有声明版本下限（只用 `ctx.tools` 这个稳定接口），
  但**没有在 0.2.2+ 上实测过**；
- headless 模式在等待本机模型端点时挂起（与插件无关），因此**没有在 headless
  里跑完一次真实的 `mas_run` 工具调用**；`mas_run` 本身是通过 Node MCP 客户端
  直连运行时验证的（结果见上）；
- 本机 `dsh.cmd` shim 存在**批处理递归溢出**问题（多个 shim 副本互调），
  这是本机环境问题，不是本插件的问题——插件安装走的是
  `dsh plugin --profile <p> add` 的 pnpm 路径，不受影响。

---

## 8. 相关文档

- `docs/mcp.md` —— MCP 工具清单与协议细节
- `docs/architecture.md` —— 判定优先级（缺席评审不构成弃权票）
- `docs/openmausbot-integration.md` —— OpenMausBot 集成
- `docs/acceptance-report.md` —— 完整验收证据
