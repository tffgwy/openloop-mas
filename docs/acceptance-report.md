# OpenLoop MAS · 安装与验收报告

- 生成时间：2026-11-12（Asia/Shanghai）
- 版本：v0.2.0（v0.1.0 基线 `172f128`）
- 仓库：https://github.com/tffgwy/openloop-mas （PUBLIC）
- 本机安装根目录：`C:\Users\Administrator\.mas`
- 验证机：Windows / Python 3.14.10 / `C:\Python314\python.exe`

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

## 9. v0.2.0 升级验收（本轮实测证据）

### 9.1 交付内容

| 层 | 新增 | 关键文件 |
| --- | --- | --- |
| 能力层 | 并行多候选选优、对抗评审聚合（all/majority/any）、多后端故障转移 | `mas/orchestration.py` |
| 观测层 | 复盘报告、运行回放、账本导出 | `mas/report.py` |
| 体验层 | 交互式向导（11 问）、真实闭环自检、离线基准 | `mas/setup.py`、`mas/bench.py` |
| 传输层 | MCP over HTTP + SSE（`/health` `/tools` `/call` `/events`） | `mas/http_server.py` |
| 分发层 | 英文 README、静态文档站、Pages/Release 工作流、PyPI 发布工作流 | `README.en.md`、`tools/build_site.py`、`.github/workflows/` |

CLI 新增子命令：`report` `replay` `export` `verify` `bench` `http`；
`mas run` 新增旗标：`--parallel` `--perspective` `--reviewers` `--review-mode`。

### 9.2 测试与基准

| 项 | 结果 | 命令 |
| --- | --- | --- |
| 单元测试 | **Ran 76 tests, OK** | `python -m unittest discover -s tests` |
| 离线基准（repeats=2，6 次运行） | 闭环率 **100%**，平均迭代 1.67，平均耗时 29.7 ms，平均 token 926.7，账本一致性 **100%** | `python -m mas bench --repeats 2` |
| 闭环自检 | provider / closure / ledger / roles **四项全 OK** | `python -m mas verify` |
| 演示场景 | closure / stagnation / budget 三个场景全部符合预期 | `python -m mas demo` |

### 9.3 打包与分发实测

| 项 | 结果 |
| --- | --- |
| `python -m build` | 成功产出 `openloop_mas-0.2.0.tar.gz` + `openloop_mas-0.2.0-py3-none-any.whl` |
| `python -m twine check dist/*` | **PASSED**（wheel + sdist 双双 PASSED） |
| wheel 内容 | 18 个模块 + `mas/teams/*.json` 三个团队模板 + LICENSE + METADATA |
| 入口点 | `mas` / `mas-mcp` / `mas-http` 三条 console_script 均在 `entry_points.txt` |
| 隔离 venv 安装 | `pip install *.whl` **零第三方依赖**成功（退出码 0） |
| 隔离环境实测 | `mas --version` → `openloop-mas 0.2.0`；`mas verify` → 结论：通过；`mas-mcp --help` / `mas-http --help` 均正常 |

### 9.4 安装器实跑

| 安装器 | 环境 | 结果 |
| --- | --- | --- |
| `install.ps1` | 隔离 InstallDir + `-SkipPath -SkipMcp -SkipSkills` | 退出码 0，doctor / demo / bench / verify 四段全绿 |
| `install.sh` | Git Bash（`D:\git\bin\bash.exe`）隔离 HOME | `bash -n install.sh` 语法检查通过；实跑退出码 0，doctor / demo / bench / verify 四段全绿；生成的 launcher 直接执行 `mas --version` → `openloop-mas 0.2.0` |
| `install.sh` 内嵌 JSON | 静态解析 | `json.loads` 通过，`orchestration` 与 `fallbacks` 字段齐全 |

### 9.5 本轮修复的真实缺陷

| 缺陷 | 影响 | 修法 | 回归证据 |
| --- | --- | --- | --- |
| `--criteria` 的 JSON 字符串被当成纯文本描述 | **机检规则完全不生效**，标准永远判 `unjudged` → 循环无法闭合 | `mas/cli.py` 新增 `_parse_criterion`，JSON 字符串解析为对象 | `test_criteria_json_string_is_parsed_not_flattened`；实跑 `--parallel 3 --reviewers 3` 从 `escalated` 变 `closed` |
| 配置文件写在 `state_dir/config.json` | 向导里换了状态目录后，配置「丢失」，运行静默退回默认值 | 配置固定在 `~/.mas/config.json`；`state_dir` 只管账本；新增 `MAS_CONFIG_PATH` 覆盖；保留 v0.1 位置回落 | `ConfigLocationTest` 三条用例；跨状态目录读取验证通过 |
| 向导测试写用户真实配置 | 一跑测试就改掉用户 `~/.mas/config.json` | 测试用 `MAS_CONFIG_PATH` 隔离到临时目录 | `test_setup_bench.py` setUp 隔离；跑完用户配置保持 `max_iterations=8` |
| 向导未提供回退链入口 | v0.2 核心能力（故障转移）没有可发现入口 | 新增第 11 问：逐行录入 `provider,base_url,model` | `test_wizard_records_fallbacks` |
| `mas run` 未暴露编排参数 | 能力层做了并行/评审，CLI 用不上 | `mas run` 增加四个旗标；引擎 `run()` 支持按次覆盖 | `test_run_accepts_parallel_and_review_overrides`；CLI 实跑闭环 |
| `mas doctor` 不报编排状态 | 自检看不到并行/评审/回退配置 | 新增 `orchestration` 自检项 | `mas doctor` 输出含「并行 1 路 / 评审 1 路 / all / 回退 0 个」 |
| 引擎 `issues` 跨迭代累积 | 已闭环的运行仍显示「未通过原因」 | `passed` 时清空 `issues` | `mas/engine.py` |
| 报告状态映射错误 | 报告显示 `- pass` | 拆分为 `closed/escalated/not_closed/incomplete` 与独立 `verdict` | `mas/report.py` |

### 9.6 已知限制（v0.2 诚实边界）

1. **v0.2.0 代码尚未在真实模型端点上跑过。** v0.1.0 曾用 OpenAI 兼容端点 + `claude-opus-5-5` 验证过（见第 4 节）；v0.2 的并行候选、故障转移、对抗评审这三条新链路目前只有 mock 后端与注入式测试覆盖。真实端点验证属于待办。
2. **PyPI 尚未真实发布。** 本机无 `.pypirc`、无 `PYPI_API_TOKEN`，只做到 `twine check` 通过。发布工作流已就绪（`.github/workflows/release.yml`，tag 触发即 build → check → upload → GitHub Release），但需仓库 secret `PYPI_API_TOKEN` 才能执行。
3. **GitHub Pages 实际 URL 未验证。** `pages.yml` 已就绪，但 Pages 需要在仓库 Settings 中启用一次（source = GitHub Actions）才能部署。
4. **`install.sh` 未在原生 Linux / macOS 上实跑。** 本机无 WSL 发行版、无 GitHub Runner；已用 Git Bash 做语法检查与端到端实跑，但 macOS 特有的 `zsh` PATH 处理未覆盖。
5. **判定的是结构，不是事实**（沿用 v0.1 的边界，详见第 7 节）。
6. **GitHub 星数与增长无法预测**，不承诺任何流量或曝光结果。


---

## 10. 下一步可选动作

1. `pip install -e .` 后用 `mas` 命令直接跑；或直接用 `~/.mas/bin/mas.cmd`。
2. 若要把「四个小 Skills」的既有读者导流过来：在 README 加一节「和 story 系列的关系」，说明 MAS 是**通用闭环层**，story 系列是**写作域的用法**。
3. 若要接更多协议（HTTP 化 MCP、Slack/飞书机器人入口），当前仓库尚未包含，可作为 v0.2.0 议题。
4. 需要给项目配仓库 Pages / Release / 中文视频脚本，可在本机继续做。
