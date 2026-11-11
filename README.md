# OpenLoop MAS · 会闭合的多智能体系统

> 一个 **零依赖、纯标准库** 的多智能体闭环运行时：把「规划 → 执行 → 验证 → 复盘」写成**有验收标准、有退出条件、有过程账本**的工程循环。
> 一条命令安装，初学者也能跑起来。

[![license](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![deps](https://img.shields.io/badge/dependencies-0-orange.svg)]()

---

## 为什么做这个

多智能体协作最常见的失败不是「模型不够聪明」，而是**循环不闭合**：

- 没有可核验的验收标准 → 大家互相说「差不多了」，谁也说服不了谁；
- 没有退出条件 → 一轮轮往下聊，token 烧完还没有结论；
- 没有过程记录 → 出问题无法复盘，无法交接，无法追责；
- 没有止损 → 一个明显走不通的任务会一直重试到超时。

Loop Engineering 要解决的就是这件事：**让「做完没有」变成一个可以被机器判定的事实。**

OpenLoop MAS 的做法是把闭环写成代码约束，而不是寄望于提示词：

| 环节 | 角色 | 产出 | 谁来判定 |
| --- | --- | --- | --- |
| 计划 | `planner` | 步骤与风险 | — |
| 执行 | `worker` | **交付物本身** | — |
| 验证 | `verifier` | 逐条验收标准的通过/不通过 + 证据 | 引擎（机检优先） |
| 复盘 | `reflector` | 失败原因与下一轮改法 | — |
| 闭合 | `lead` | `close` / `revise` / `escalate` | 引擎 |

一个循环只有同时满足 **产物 + 逐条判定 + 决策 + 账本条目** 才算闭合；否则状态只有三种：`escalated`（升级人工）、`budget_exhausted`（预算止损）、`error`。**没有「差不多完成」这个状态。**

---

## 5 分钟上手

```bash
git clone https://github.com/tffgwy/openloop-mas.git
cd openloop-mas

# Windows
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
# macOS / Linux
bash install.sh

# 不需要任何 API Key，先看它自己跑通闭环
mas demo
```

`mas demo` 会离线跑三个场景，并把真实结果打出来：

```
[OK ] closure     期望=closed             实际=closed             迭代=2 票=-
[OK ] stagnation  期望=escalated          实际=escalated          迭代=4 票=T-20261111-085346-4396
[OK ] budget      期望=budget_exhausted   实际=budget_exhausted   迭代=1 票=-
```

> 注意：`mock` 模式里，**通过与否由引擎的机检规则判定**，不是模型自己说「我完成了」。
> mock 只是模拟执行者产出草稿，验证权始终在确定性检查器手里。

---

## 跑一个真实任务

```bash
mas run --goal "写一份 v0.1.0 的发布说明" \
  --criteria '{"id":"c1","desc":"包含版本号","check":{"type":"contains","arg":"v0.1.0"}}' \
  --criteria '{"id":"c2","desc":"不少于 200 字","check":{"type":"min_chars","arg":200}}'
```

输出：

```
状态：closed（迭代 2 次，token 2140）
账本：/home/you/.mas/ledgers/runs.jsonl
  [PASS] c1 (rule) all required strings present
  [PASS] c2 (rule) length 418 >= 200
--- 交付物预览 ---
...
```

### 支持的机检规则（由引擎判定，可复现）

| type | 含义 | 参数 |
| --- | --- | --- |
| `contains` / `not_contains` | 必须包含 / 禁止包含 | `arg`（字符串或数组） |
| `min_chars` / `max_chars` | 长度上下限 | `arg`（整数） |
| `regex` | 正则命中 | `arg`（模式） |
| `lines_min` | 非空行数下限 | `arg`（整数） |
| `section` | 必须包含某个 `## 标题` | `arg`（标题名） |
| `json_keys` | JSON 对象必须包含的键 | `arg`（键数组） |

没有机检规则的标准会交给模型验证者判定；但**如果模型没给出判定，默认判不通过**（`unjudged`），不会因为一句「看起来可以」就放行。
`"required": false` 的标准只记录、不阻塞闭合。

---

## 四道止损机制

1. **迭代预算**：`budget.max_iterations`，超出即 `budget_exhausted`。
2. **墙钟预算**：`budget.deadline_sec`。
3. **token 预算**：`budget.max_tokens`。
4. **停滞检测**：连续多轮产出**完全相同**的产物（默认 3 次，`stagnation_limit`）→ 判定卡死，**升级人工工单**，不再空转。

升级出来的工单带齐上下文（目标、验收标准、最后一版产物、失败原因、迭代历史），人工一句话就能接着跑：

```bash
mas tickets
mas resume T-20261111-085346-4396 --note "按用户口径重写结论段"
```

---

## 闭环账本

每一次循环都会向 `~/.mas/ledgers/runs.jsonl` 追加条目（append-only）：

```json
{"seq":3,"ts":"2026-11-11T08:53:46+00:00","run_id":"R-3f1c...","task_id":"R-3f1c...-1",
 "iteration":2,"role":"closure","provider":"mock","model":"mock-1",
 "artifact_sha":"9d1f...","verdict":"pass","decision":"close",
 "scores":[{"id":"c1","pass":true,"note":"all required strings present","source":"rule"}],
 "closed":true}
```

账本是闭环的**证据**：能回看、能复现、能交接、能写进 CI 门禁。

---

## 接入你自己的模型

运行时只用标准库，HTTP 走 `urllib`，所以任何 OpenAI 兼容端点都能接。

```bash
# Kimi / Moonshot
mas init --provider openai_compat --model kimi-k2 --base-url https://api.moonshot.cn/v1
export MAS_API_KEY=sk-...

# Claude
mas init --provider anthropic --model claude-sonnet-4-5
export MAS_API_KEY=sk-ant-...

# GPT / Grok / DeepSeek / Qwen / 本地 vLLM 等，OpenAI 兼容即可
mas init --provider openai_compat --model gpt-4o-mini --base-url https://api.openai.com/v1
```

适配器自带三种容错，兼容绝大多数「OpenAI 兼容」网关：

- 网关不支持 `response_format` 时自动降级重试一次，并从回答正文里抽取 JSON；
- 网关用 SSE 事件流回答（很多中转站和本地桥接都是这样）时自动折叠成标准 chat 结构；
- 模型在正文前吐 `<think>` 思维链时自动剥离，思维链不会进入交付物、判定和账本。

也可以完全用环境变量，不改配置文件：

```bash
export MAS_PROVIDER=openai_compat MAS_MODEL=your-model MAS_BASE_URL=https://... MAS_API_KEY=...
```

---

## 接进 MCP（Codex / Claude Code / 任何 MCP 客户端）

安装器会自动把下面这段写进 `~/.codex/config.toml`：

```toml
[mcp_servers.mas_loop]
command = "python"
args = ["-m", "mas", "serve"]
startup_timeout_sec = 30.0
tool_timeout_sec = 300.0
```

暴露的工具：

| 工具 | 用途 |
| --- | --- |
| `mas_run` | 跑一个闭环任务，返回闭合状态与逐条判定 |
| `mas_status` | 当前配置、账本规模、未处理工单 |
| `mas_ledger` | 读闭环账本（可按 `run_id` 过滤） |
| `mas_tickets` / `mas_ticket_resolve` / `mas_resume` | 人工介入闭环 |
| `mas_teams` | 列出团队模板 |
| `mas_doctor` | 安装自检 |
| `mas_config` | 读取 / 更新配置 |

协议实现是纯标准库的 JSON-RPC 2.0 over stdio —— **不需要 `pip install mcp`**。

---

## 随包附带的 Agent Skill

| Skill | 作用 |
| --- | --- |
| `mas-loop` | 跑闭环任务、写验收标准、读账本与工单 |
| `mas-review` | 多视角对抗式审查（执行者 / 验证者 / 复盘者分离） |
| `mas-scaffold` | 给新项目搭团队模板与 CI 门禁 |

安装器会把它们装进本机能找到的所有 skill 目录（`~/.codex/skills`、`~/.claude/skills`、`~/.agents/skills`、`~/.gemini/skills`）。

---

## 目录结构

```
openloop-mas/
├─ mas/                 运行时（纯标准库）
│  ├─ engine.py         闭环引擎：预算、停滞判定、闭合/升级
│  ├─ providers.py      mock / OpenAI 兼容 / Anthropic 适配
│  ├─ ledger.py         append-only 账本
│  ├─ tickets.py        人工升级工单
│  ├─ mcp_server.py     MCP stdio 服务
│  ├─ cli.py            命令行
│  ├─ doctor.py         安装自检
│  ├─ demo.py           三个离线场景
│  └─ teams/            团队模板（default / review / content）
├─ skills/              Agent Skill 包
├─ tests/               29 个离线测试
├─ install.ps1          Windows 一键安装
├─ install.sh           macOS / Linux 一键安装
└─ docs/                架构、方法论、MCP、定位说明
```

---

## 命令速查

```bash
mas init        # 生成/更新配置
mas run         # 跑一个闭环任务
mas demo        # 离线演示三个闭环场景
mas status      # 本机状态
mas ledger      # 闭环账本
mas tickets     # 人工工单
mas resume ID   # 带人工意见继续
mas teams       # 团队模板
mas doctor      # 安装自检
mas serve       # 以 MCP stdio 服务运行
```

`mas run` 退出码：`0` = 闭合，`2` = 未闭合（升级或预算耗尽）。CI 里可以直接当门禁用。

---

## 开发与测试

```bash
python -m unittest discover -s tests -v   # 29 个离线测试，不需要网络
python -m mas demo                        # 三个闭环场景
```

测试覆盖：机检规则、闭合/停滞/预算三条终止路径、规则判定压过模型判定、未判定标准不得通过、MCP 握手与工具调用、配置合并与环境变量覆盖、SSE 流折叠、思维链清洗。

---

## 定位说明（与 OpenMausBot 等项目的关系）

OpenLoop MAS **不是**「私有化 AI 代理团队」那一类产品的复刻，它补的是**另一层**：

- 那一类产品解决的是「把 Claude / GPT / Kimi 接进一个能聊天的代理团队，跑在本地或云端，能挂 MCP 和一堆应用」；
- OpenLoop MAS 解决的是「**这个团队跑完之后，怎么知道它真的做完了，而且过程可回放、可止损、可交接**」。

两者可以叠加：前者当执行层，后者当闭环层。
传播口径上可以把本项目讲成「让多智能体**真的会结束**的那一层」，而不是又一个聊天壳。

> 视频/介绍里如果需要引用同类项目（如 OpenMausBot 这类主打私有化代理团队、支持 MCP 与大量应用打通的方案），请以其官方仓库的说明为准：能力边界、安全模型、更新频率都应以官方文档为准，不要替别人的项目做安全承诺。

---

## License

MIT © OpenLoop MAS contributors
