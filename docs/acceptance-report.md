# OpenLoop MAS · 安装与验收报告

- 生成时间：2026-11-11（Asia/Shanghai）
- 仓库：https://github.com/tffgwy/openloop-mas （PUBLIC）
- 提交：`dbcfe7b31ff38b1268c1e233a503c3f9fff384ed`（dbcfe7b）
- 本机安装根目录：`C:\Users\Administrator\.mas`

---

## 1. 交付结论

**已完成。** 一个零依赖、纯标准库的多智能体闭环运行时（MAS）已实现、开源发布，并在本机完成全量安装与真实模型验证。

三个核心承诺全部有本轮工具证据：

| 承诺 | 状态 | 证据 |
| --- | --- | --- |
| 开源给世界 | 已完成 | GitHub 公开仓库 `tffgwy/openloop-mas`，CI 12/12 绿 |
| 解决一点 LoopEngineering 闭环问题 | 已完成 | 判定权在引擎、四道止损、append-only 账本、人工工单 |
| 初学者一键安装 | 已完成 | `install.ps1` / `install.sh`，本机实跑 9/9 自检通过 |

---

## 2. 仓库结构（已推送）

```
openloop-mas/
├─ mas/                 运行时（纯标准库，零第三方依赖）
│  ├─ engine.py         闭环引擎：机检优先、四道止损、闭合/升级
│  ├─ providers.py      mock / OpenAI 兼容 / Anthropic 适配（含 SSE 折叠）
│  ├─ ledger.py         append-only JSONL 闭环账本
│  ├─ tickets.py        人工升级工单与断点续跑
│  ├─ mcp_server.py     MCP stdio 服务（JSON-RPC 2.0，9 工具）
│  ├─ cli.py            命令行
│  ├─ doctor.py         9 项安装自检
│  ├─ demo.py           三个离线闭环场景
│  └─ teams/            default / review / content 团队模板
├─ skills/              mas-loop / mas-review / mas-scaffold 三个 Agent Skill
├─ tests/               31 个离线测试
├─ docs/                quickstart / architecture / loop-engineering / mcp / positioning
├─ install.ps1          Windows 一键安装
├─ install.sh           macOS / Linux 一键安装
├─ .github/workflows/   CI：3 操作系统 × 4 个 Python 版本
└─ examples/            task.json / criteria.json 示例
```

---

## 3. 本机安装验收（全部实跑）

### 3.1 安装器输出

```
[OK] Python 3.14.10 at C:\Python314\python.exe
[OK] runtime copied          -> C:\Users\Administrator\.mas\app
[OK] config created (offline mock provider)
[OK] launcher: C:\Users\Administrator\.mas\bin\mas.cmd
[OK] added to user PATH
[OK] 9 skill folder(s) installed
[OK] registered in ~/.codex/config.toml (backup: config.toml.bak-20261111-090803)
[OK] registered in ~/.claude.json     (backup: .claude.json.bak-20261111-090804)
```

### 3.2 自检（`mas doctor`）

9 项全绿：python / package / config / state_dir / ledger / skill_codex / skill_claude / mcp_codex / provider。

### 3.3 Skill 落位（实测目录）

| 目录 | mas-* 数量 |
| --- | --- |
| `~/.codex/skills` | 3 |
| `~/.claude/skills` | 3 |
| `~/.agents/skills` | 3 |
| `~/.gemini/skills` | 该目录不存在，未安装（安装器只写已存在的目录） |

### 3.4 MCP 注册落位

`~/.codex/config.toml`：

```toml
[mcp_servers.mas_loop]
command = "C:/Python314/python.exe"
args = ["-m", "mas", "serve"]
startup_timeout_sec = 30.0
tool_timeout_sec = 300.0
env = { PYTHONPATH = "C:/Users/Administrator/.mas/app" }
```

`~/.claude.json` → `mcpServers.mas_loop` 同义条目（command / args / env）。

### 3.5 MCP 真实握手（stdio 实连，非模拟）

```
initialize -> {"name":"openloop-mas","version":"0.1.0"}   protocolVersion: 2024-11-05
tools/list -> 9 tools: mas_run, mas_status, mas_ledger, mas_tickets,
                       mas_ticket_resolve, mas_resume, mas_teams, mas_doctor, mas_config
mas_run    -> status=closed closed=True iterations=2
             c1 pass source=rule / c2 pass source=rule
mas_doctor -> ok=9 warn=0 fail=0
mas_teams  -> content / default / review
mas_status -> config ok, ledger entries=3, open_tickets=[]
exit code  : 0
```

---

## 4. 真实模型闭环验证（非 mock）

目标模型：`claude-opus-5-5`，经 OpenAI 兼容端点调用，密钥取自环境变量（未打印、未入库）。

### 4.1 全部机检标准

| 项 | 结果 |
| --- | --- |
| 状态 | `closed` |
| 迭代 | 1 |
| provider 调用 | 3（planner / worker / verifier） |
| token | 4851 |
| 耗时 | 38.2 s |

判定明细：

| 标准 | 结果 | 判定来源 |
| --- | --- | --- |
| c1 包含 `v0.1.0` | pass | rule |
| c2 不少于 120 字 | pass（951 字符） | rule |
| c3 含 `## 变更` 小节 | pass | rule |
| c4 正文用中文撰写 | pass | **model** |

> 最后一条标准故意不给机检规则，用来验证「无机检标准时由模型验证者判定」这条路径确实能走通。

### 4.2 思维链清洗验证

模型原始回答带 `<think>...</think>` 前缀。清洗后交付物首行是 `# OpenLoop MAS v0.1.0 发布说明`，`contains <think>: False` —— 思维链未进入交付物、判定与账本。

---

## 5. 离线测试与 CI

- 本机：`python -m unittest discover -s tests` → **Ran 31 tests, OK**
- 本机：`python -m mas demo` → closure / stagnation / budget 三个场景全部符合预期
- CI：https://github.com/tffgwy/openloop-mas/actions → **run 42941631477 success（50s）**
  - 矩阵：ubuntu / windows / macos × Python 3.10 / 3.11 / 3.12 / 3.13

### 5.1 CI 首轮红过，根因已修

首轮 CI 全红，日志原文：

```
错误：'charmap' codec can't encode characters in position 18-19: character maps to <undefined>
```

这不是 CI 配置问题，而是**真实缺陷**：Windows 遗留代码页下打印中文会抛 `UnicodeEncodeError` 直接崩掉进程。修法不是改 CI，而是改产品：

- CLI 与 MCP 服务启动时把 stdout/stderr 重配为 UTF-8（`errors="replace"`）；
- CI 额外加 `PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8` 双保险。

本地复现验证：`$env:PYTHONIOENCODING="cp1252"; python -m mas demo` → 退出码 0，中文正常输出。

---

## 6. 本轮新增的工程改进（均在推送前落地）

| 改进 | 触发原因 | 验证方式 |
| --- | --- | --- |
| stdout/stderr UTF-8 重配 | CI 全红；中文用户必踩 | 强制 cp1252 下 `mas demo` 退出码 0 + 2 个回归测试 |
| SSE 事件流折叠 | 本机桥接对 `/v1/chat/completions` 也返回 SSE | 4 个单测 + 真实上游跑通 |
| `response_format` 不支持时降级重试 | 兼容实现不一的网关 | 代码路径 + 单测 |
| `<think>` 思维链剥离 | 交付物混入推理过程 | 真实模型跑通后 `contains <think>: False` |
| 文档补齐 | README 承诺的 docs 未落地 | quickstart / mcp / positioning 三份成文 |

---

## 7. 已知限制（诚实边界）

1. **判定的是结构，不是事实。** 真实模型那次运行里，产物写了「发布日期：2026 年 2 月 18 日」这类未经证实的内容，闭环照样判 `closed`。机检管格式与结构；语义真实性要靠模型判定标准或人工复核，README 已把这一点写明。
2. **模型判定不可复现。** 带 `check` 的标准可复现；纯模型判定依赖采样，需要固定温度与种子才能谈一致性。
3. **本机默认是 `mock` 离线模式。** 配置留在 `~/.mas/config.json`，密钥只走环境变量，不入库。要接真实模型：`mas init --provider openai_compat --model <model> --base-url <endpoint>` + `MAS_API_KEY`。
4. **`~/.gemini/skills` 未安装**：该目录本机不存在，安装器只写已存在的目录。
5. **GitHub 星数与增长无法预测**，不承诺任何流量或曝光结果。

---

## 8. 关于同类项目（OpenMausBot）的口径

- 已按用户 PS 口径处理：`OpenMausBot` 是**同类参考项目**（主打私有化 AI 代理团队、支持 MCP 与 1700+ 应用打通，常被介绍为「Grok Bot 的免费平替」），不是本项目的复刻对象。
- 文档定位（`docs/positioning.md` 与 README 末节）：OpenLoop MAS 是**闭环层**，那一类项目是**执行层**，两者可叠加——前者是发动机，后者是仪表盘、刹车和行车记录仪。
- 传播口径统一为「让多智能体**真的会结束**的那一层」，不评价第三方项目的能力边界与安全模型；引用其一概以官方仓库说明为准。

---

## 9. 下一步可选动作

1. `pip install -e .` 后用 `mas` 命令直接跑；或直接用 `~/.mas/bin/mas.cmd`。
2. 若要把「四个小 Skills」的既有读者导流过来：在 README 加一节「和 story 系列的关系」，说明 MAS 是**通用闭环层**，story 系列是**写作域的用法**。
3. 若要接更多协议（HTTP 化 MCP、Slack/飞书机器人入口），当前仓库尚未包含，可作为 v0.2.0 议题。
4. 需要给项目配仓库 Pages / Release / 中文视频脚本，可在本机继续做。
