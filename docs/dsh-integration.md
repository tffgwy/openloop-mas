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
npm pack            # -> dsh-openloop-mas-0.7.16.tgz

dsh plugin --profile desktop add "dsh-openloop-mas@file:<绝对路径>/dsh-openloop-mas-0.7.16.tgz"
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

## 6. 让 DSH 真的能说话：`mas relay` + bridge profile

上一节说「插件已激活」，但那**不等于**模型能用。DSH 的模型通道由
`@deepseek-ai/dsh-llm-pi-ai` 注册，它对 `openai-completions` / `openai-responses`
两种协议都**坚持要一个 API key**；而私有桥（自己机器上的端点）常常**不要 key**，
甚至会因为一个占位 key 直接 `401`。这是协议契约和端点契约打架，不是配置问题。

`mas relay` 就是解这个结的那一层（完整说明见 [docs/relay.md](relay.md)）：

```
DSH (非要 key)  ──►  mas relay  ──►  私有桥 (不要 key，或要另一把 key)
```

### 6.1 一键生成 bridge profile

```powershell
python tools/setup_dsh_bridge.py --profile masbridge --relay-port 8766
```

实测输出（本轮真实跑通）：

```
[OK] profile 已生成：C:\Users\<you>\.dsh\profiles\masbridge
[OK] dsh-openloop-mas 插件已装入 profile 'masbridge'
```

它生成 `cordis.patch.yml` / `cordis.yml` / `package.json`，并自动执行
`dsh plugin --profile masbridge add dsh-openloop-mas@file:<repo>/integrations/dsh-openloop-mas`。

生成的 `cordis.patch.yml` 里关键三行：

```yaml
- id: agent-default-model
  config:
    provider: mas-local-bridge
    model: claude-opus-5-5

- id: llm-pi-ai
  config:
    providers:
      mas-local-bridge:
        apiKeyEnv: MAS_BRIDGE_API_KEY
        baseURL: http://127.0.0.1:8766
        api: openai-responses
```

### 6.2 起中继 + 跑闭环

```powershell
# 终端 1
mas relay --upstream http://127.0.0.1:16024 --port 8766 --verbose

# 终端 2：pi-ai 坚持要 key，给个占位值即可（relay 会丢弃它）
$env:MAS_BRIDGE_API_KEY = "placeholder"
dsh --profile masbridge headless "用 mas_run 跑一个闭环：目标是写一份发布说明，验收标准用 file_exists 要求 out/report.md 存在"
```

> **`headless` 是 profile 名，不是子命令。** 正确形态是
> `dsh --profile <name> headless "<task>"`；写成 `dsh headless "<task>"` 能跑，
> 但 `dsh --profile X web` 会报 `too many arguments`——`web` 宿主另有语法。

### 6.3 本轮真实跑通的闭环（证据）

在 DSH 宿主（DSH `0.2.2-rc.3`，Node `22.23.3`）内，模型自主调用 `mas_run`：

| 字段 | 值 |
| --- | --- |
| `status` | `closed` |
| `closed` | `true` |
| `iterations` | `1` |
| `tokens_used` | `4468` |
| `run_id` | `R-9644103b33` |
| 逐条判定 | `[{"id":"c1","pass":true,"note":"file exists: out/report.md","source":"file","blocking":true}]` |

产物**真的写进了磁盘**：`out/report.md`，1663 字节。

关键点：判定里的 `source:"file"` 表示这条判定来自**读真实文件系统**，
不是模型自述「我写好了」。

### 6.4 引擎防假闭环的正面证据

同一轮里，模型第一次调 `mas_run` 时把文件级机检的参数**写错了**（平铺在
`check` 里，而不是包在 `check.arg` 对象中），引擎在**开跑前**就拒绝了：

```json
{"error":"验收标准里有无法执行的机检规则: - c1: missing file path"}
```

这次拒绝发生在**零 token、账本零污染**的阶段——引擎把「标准写漏」和
「标准不通过」严格区分开，不会让一条残缺规则退化成「交给模型主观判定」然后放行。

---

## 7. 与 MOKI 资料库的分工

### 7.1 先说清楚：MOKI ≠ OpenMausBot

这两个名字经常被混着提，但**完全是不同的项目**：

| | MOKI 资料库 | OpenMausBot |
| --- | --- | --- |
| 仓库 | [JKZeng-cell/moki-library-for-dsh](https://github.com/JKZeng-cell/moki-library-for-dsh) | [milind-soni/OpenMausBot](https://github.com/milind-soni/OpenMausBot) |
| 身份 | **DeepSeek Harness 插件**（`dsh-moki-library`） | 独立开源的 **Grok Bot 平替**（聊天界面 + 多智能体 + MCP + 1700+ 应用） |
| 定位 | 给 DSH 加一个**本地资源管理器** | 一整套**私有化 AI 代理团队** |
| 与 MAS 的关系 | **同宿主并列插件** | 通过 **MCP** 挂载 MAS 的 12 个工具 |

MOKI 做的事，按官方仓库说明：文件夹和网页链接统一整理、多格式预览
（PDF / Office / 图片 / 音视频 / 字体 / 压缩包目录 / 常见 3D 模型）、
选中文字或单元格或页面区域后按 `Alt+D` 唤起 AI 对话、
**修改先进草稿、用户确认后才写回原文件**。

### 7.2 两者怎么配合

| | MOKI 资料库 | OpenLoop MAS |
| --- | --- | --- |
| 定位 | 资料与草稿的**本地资源管理器** | 多智能体**闭环运行时** |
| 产物 | 文件 / 文件夹 / 网页链接的整理，多格式预览 | 验收标准 + 判定 + 账本 + 工单 |
| 编辑模型 | 修改先入**草稿**，用户确认后写回原文件 | 产物由 agent 产出，按**机检规则**判定 |
| 交互 | 选区 → `Alt+D` 唤起 DSH 对话 | 对话里调用 `mas_run` 等 6 个工具 |

一句话：**MOKI 负责把资料整理成一份可交付的产物，MAS 负责判定这份产物到底过不过。**

### 7.3 正确装法（实测过的坑）

```bash
# MOKI（需要 --allow-build=sharp，它是原生依赖）
dsh plugin --profile web add "dsh-moki-library@file:<绝对路径>/dsh-moki-library-0.1.73.tgz" --allow-build=sharp

# MAS
dsh plugin --profile web add "dsh-openloop-mas@file:<绝对路径>/dsh-openloop-mas-0.7.16.tgz"
```

**实测到的几条硬事实**：

- MOKI 声明兼容 `dsh >=0.2.2-rc.1 <0.3.0`，需要 **Node >= 22.19**；
  本机原 DSH 是 `0.2.1-rc.5`（**低于**下限），所以另装了隔离运行时 `0.2.2-rc.3`；
- MOKI 是 **web-platform 插件**，依赖 `webServer` / `connection` 能力。
  在 **CLI / headless profile** 里它会一直 **pending**（能力没挂上）——这不是装坏了，
  是宿主形态不对；
- 正确形态是 **`web` profile** 或**单独隔离的 profile**（本轮用的是 `masmoki`），
  两者可以同时装；
- 端口用 `--port` 指定，不要跟默认端口撞；
- **DSH web 宿主有认证**：必须先 `GET /?token=<TOKEN>` 拿到 `set-cookie: dsh-auth-*`，
  之后带 cookie 才能访问 `/api/*`。

### 7.4 怎么判断 MOKI「真的激活了」

判据不是「装上了」，而是**请求打到了 MOKI 自己的路由**：

- 带 cookie 和 `x-moki-library: 1` 头打 `/moki-library/*`，
  返回的是 **MOKI 自己的业务错误（403 / 400）**，而不是 DSH 的 404；
- 拿到 MOKI 自己的业务错误 = 插件已激活、路由已挂载；
- 拿到 404 = 路由没挂上。

> ⚠️ 本机 DSH 运行时**没有暴露** `/api/plugins`、`/api/tools`、`/api/health` 这类路由
> （实测全部 404），别拿它们当健康检查。

### 7.5 MOKI 尚未验证的部分

- **DSH 桌面应用（Electron）内的 MOKI 与 `mas_run` 均未实测**——桌面 profile 由
  Electron 独占管理；
- MOKI 的**多格式预览**与**草稿写回**流程未做功能级验证（本轮只验证到「路由激活」）；
- MOKI 与 MAS **同 profile 内的工具协同**未实测。

---

## 8. 边界说明（已验证 / 未验证）

**已验证（本轮实测）**：

- **插件在真实 DSH 宿主里成功激活**：`dsh --profile cli headless` 启动时打印
  `[openloop-mas] ready: python=python`；
- **6 个工具在真实 `defineTool` 下全部注册通过**（schema 校验通过）：
  `mas_run` / `mas_status` / `mas_ledger` / `mas_tickets` / `mas_report` /
  `mas_doctor`；
- **DSH 宿主内 `mas_run` 跑出真实闭环**：`status=closed`、`closed=true`、
  `iterations=1`、`tokens_used=4468`、`run_id=R-9644103b33`，
  判定 `source="file"`，产物 `out/report.md` 1663 字节确实落盘；
- **本机桥 profile 由脚本一键生成并跑通**：`tools/setup_dsh_bridge.py`
  生成 `masbridge2` profile → 装插件 → 起 relay → headless 闭环
  → `status=closed` / `closed=true` / `tokens_used=4744` / `run_id=R-f2434f4854`
  → `out/report2.md` 2725 字节落盘；
- `dsh --profile cli --dump-config` 能看到插件被正确合成进 profile：
  `- id: openloop-mas / name: dsh-openloop-mas / config: {masHome, python, timeoutMs}`；
- 纯 Node MCP 客户端 `listTools` 返回 **12 个工具**，`mas_status` 返回真实账本；
- Node 单元测试 **5/5 通过**；
- 隔离安装（`-SkipPath -SkipMcp -SkipDemo`）通过；
- `-InstallDshPlugin` 路径在 DSH 命令失败时**优雅降级**；
- `npm pack` 产物：`dsh-openloop-mas-0.7.16.tgz`；
- `dsh plugin --profile cli add` 安装成功，且**不再出现**
  `declares no dsh.bundle` 警告。

**过程中被真实宿主抓出来的 bug（都已修）**：

1. `dsh.profile.bundles` 放错了位置。DSH 认的是 `dsh.bundle.patch`（指向
   `cordis.patch.yml`），放错的结果是插件被装成普通依赖、**不挂载**，
   pnpm 会警告 `declares no dsh.bundle`；
2. 工具参数 schema 不满足 DSH 的 JSON Schema 子集：`items.additionalProperties`
   必须**显式**为 `true` 或 `false`，否则 `defineTool` 直接抛
   `JsonSchemaError`，整个插件 entry 不激活（`1 entry did not activate`）。
   这是**真实宿主才暴露**的——离线单测完全测不出来；
3. **协议契约死结**：宿主坚持要 api key、私有端点又拒收占位 key，导致
   「插件激活了但模型用不了」。这是本轮新发现并用 `mas relay` 解决的。

**未验证**：

- **没有在 DSH 桌面应用（Electron）里实测**——本机 `desktop` profile 由桌面应用
  独占管理，命令行报 `profile "desktop" is managed exclusively by the Electron
  application`；验证是在 **cli profile + headless 宿主**下完成的；
- 真实端点实测**只覆盖** `claude-opus-5-5` @ `127.0.0.1:16024/v1`（免鉴权）；
  Kimi / DeepSeek / Qwen / 原生 OpenAI 本机无端点，**未实测**；
- `mas relay` 的 **replace 模式未在本机实测**（本机桥不需要凭据），只有单元测试覆盖；
- 判定的是**结构与事实**（文件在不在、条款过没过），**语义正确性仍需模型评审**；
- 本机 `dsh.cmd` shim 存在**批处理递归溢出**问题（多个 shim 副本互调），
  这是本机环境问题，不是本插件的问题——插件安装走的是
  `dsh plugin --profile <p> add` 的 pnpm 路径，不受影响。

---

## 9. 相关文档

- `docs/relay.md` —— `mas relay` 的用途、两种鉴权模式、安全边界
- `docs/mcp.md` —— MCP 工具清单与协议细节
- `docs/architecture.md` —— 判定优先级（缺席评审不构成弃权票）
- `docs/openmausbot-integration.md` —— OpenMausBot 集成
- `docs/acceptance-report.md` —— 完整验收证据
