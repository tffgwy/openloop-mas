# Quickstart · 5 分钟跑通一个真闭环

> 目标：从零到「一个有验收标准、有退出条件、有账本」的多智能体循环。
> 全程零第三方依赖，不需要 API Key 也能先跑通。

---

## 0. 前置条件

| 项目 | 要求 | 检查命令 |
| --- | --- | --- |
| Python | 3.10 或更高 | `python -V` |
| 网络 | 只在接真实模型时需要 | — |
| 磁盘 | < 20 MB | — |

运行时只用标准库（HTTP 走 `urllib`），**不需要 `pip install` 任何东西**。

---

## 1. 安装

### 1.1 只下载一个脚本（初学者推荐）

**不需要 git、不需要 pip、不需要编译工具**——把一个脚本文件下载到任意目录，
执行它就行。脚本会自己从 GitHub Release 拉取已发布的 wheel 并装好。

Windows：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

macOS / Linux：

```bash
bash install.sh
```

固定版本（默认装最新版）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Version 0.7.89
```

```bash
MAS_VERSION=0.7.89 bash install.sh
```

> 下载路径是「标准库优先、`curl` 兜底」，并会读 `HTTPS_PROXY` 与
> Windows 系统代理设置——所以在公司网络或需要代理的机器上也能直接跑。

> **机器上还没有 Python？** Windows 上脚本会下载官方安装包，
> **按当前用户静默安装**（不要管理员权限、不改 `PATH`），
> 然后在自己放的位置把解释器找回来；
> macOS / Linux 上会调用系统包管理器（Homebrew / apt / dnf / yum），
> **装不上会如实告诉你**，并给出可以直接复制的那条命令。
> 不想让它动你的机器？加 `-NoBootstrap`（Windows）或
> `MAS_NO_BOOTSTRAP=1`（shell）。

### 1.2 从源码安装（适合开发）

```bash
git clone https://github.com/tffgwy/openloop-mas.git
cd openloop-mas
```

Windows：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

macOS / Linux：

```bash
bash install.sh
```

### 1.3 安装器做了什么

1. 定位 Python ≥ 3.10，并把解释器路径归一成绝对路径再写进注册；
2. 把运行时放到 `~/.mas/app`——有源码就拷源码，没有源码就下载并解开已发布的
   wheel；并在 `~/.mas/bin/`（Windows 是 `mas.cmd`）生成启动器、加入用户 PATH；
3. 把 `mas-loop` / `mas-review` / `mas-scaffold` 三个 Skill 装进本机找到的所有
   skill 目录；
4. 把 MCP 服务注册进 `~/.codex/config.toml` 与 `~/.claude.json`（注册前自动备份），
   注册里同时写明**运行时路径**和**配置文件位置**，宿主进程不会去读别人的配置。

可选开关：

| 开关 | PowerShell | shell |
| --- | --- | --- |
| 不动 PATH | `-SkipPath` | `MAS_SKIP_PATH=1` |
| 不注册 MCP | `-SkipMcp` | — |
| 不装 Skill | `-SkipSkills` | — |
| 跳过演示与自检 | `-SkipDemo` | — |
| 指定解释器 | `-Python <路径>` | `MAS_PYTHON=<路径>` |
| 指定安装根 | `-InstallDir <路径>` | `MAS_HOME=<路径>` |
| 指定版本 | `-Version <版本>` | `MAS_VERSION=<版本>` |
| 用本地 wheel | `-Wheel <路径>` | `MAS_WHEEL=<路径>` |

验证：

```bash
mas doctor      # 安装自检：运行时 / 配置 / 账本 / skills / MCP / 端点 / 指令是否生效
mas demo        # 离线跑三个闭环场景
```

### 1.4 以后想换新版本：先升级运行时，再刷新注册

**这两个命令干的事不一样，顺序不能反。**

| 命令 | 动的是什么 | 不动什么 |
| --- | --- | --- |
| `mas upgrade` | 机器上装的那份**运行时本体** | skills、MCP 注册 |
| `mas install` | skills 和 **MCP 注册** | **运行时本体** |

也就是说：如果你只跑了 `mas install`，真正在跑的那份代码**还是旧的**——
自检会显示「skills installed」「MCP registration refreshed」，一切正常，
但 agent 调到的行为是旧版本。`mas doctor` 现在有一项专门的检查会告诉你这件事：

```
[注意] runtime（可选）
       本机装的运行时比你现在跑的这版旧：C:\Users\...\app\mas（0.7.79）；运行 mas upgrade 升级
```

换新版的完整顺序：

```bash
mas upgrade     # 把旧的运行时换成你现在这版（旧的备份在 <state_dir>/backup/runtime/）
mas install     # 让 skills 和 MCP 注册指向新版本
mas doctor      # 复查
```

只想看看差多少版本：

```bash
mas runtime     # 只读：机器上装的是哪一版，和当前跑的差多少
```

`mas upgrade` 升级后会**当场核对磁盘上的版本号**，对不上就报失败，不报成功。

### 1.5 另一份 mas：你敲 mas 时究竟跑的是哪一份

**PATH 可以同时存多份 `mas`。** 一键安装把自己的那份放在 `~/.mas/bin`，
但如果你曾经跑过 `pip install openloop-mas`，那路径下还有一份。
PATH 是**按顺序搜**的，所以哪一份在前面，就哪一份赢。

这会造成一个很险巅的情况：

```
[OK ] package（必需）        mas 0.7.81 可导入
[OK ] runtime（可选）        本机装的运行时是 0.7.81，不用升级
[OK ] mcp_codex（可选）      已在 ~/.codex/config.toml 注册
```

自检全绿，但你敲 `mas` 跑的是 **0.7.16**。安装器自检的是它刚装好的
那份，而你手里敲的是另一份。

现在自检会把这件事报出来（`shadow_runtime` 检查）：

```
[注意] shadow_runtime（可选）
       你敲 mas 时跑的是 C:\...\Scripts\mas.exe（0.7.16），不是现在这一版（0.7.81）
```

先看看 PATH 上到底有几份：

```bash
mas shadow      # 列出 PATH 上每一份 mas 及其版本，标出哪一份赢
```

处理方法：

```bash
mas upgrade --fix-shadow   # 把 PATH 上的旧副本一并处理掉
```

这条命令对两类副本的做法不同，且是有意为的：

| 副本怎么来的 | `mas upgrade --fix-shadow` 怎么处 | 为什么 |
| --- | --- | --- |
| **pip 装的** | 只报出命令，不动它的文件 | 直接改代码会让 `pip list` 开始撒谎（版本号对不上） |
| **其他副本** | 备份后换成现在这一版，换完当场核对版本号 | 不是 pip 管的，换了就真的换了 |

pip 那一份的正确处理是把它消掉，让安装器那一份接管这个名字：

```bash
pip uninstall openloop-mas
```

安装器现在会把自己的启动器放在 PATH **最前面**（不是最后面），
并且当 PATH 前面还有别的 `mas` 时会**当场告警**。

---

## 2. 先看它自己跑通（不需要 Key）

```bash
mas demo
```

预期输出：

```
[OK ] closure     期望=closed             实际=closed             迭代=2 票=-
[OK ] stagnation  期望=escalated          实际=escalated          迭代=4 票=T-...
[OK ] budget      期望=budget_exhausted   实际=budget_exhausted   迭代=1 票=-
```

三个场景分别是：

- **closure**：两轮内满足全部验收标准 → `closed`；
- **stagnation**：产物连续多轮完全相同 → 判定卡死 → `escalated` 并生成人工工单；
- **budget**：超出迭代预算 → `budget_exhausted`。

> `mock` 模式只模拟**执行者产出草稿**，**通过与否始终由引擎的机检规则判定**。模型说「我完成了」不算数。

---

## 3. 跑一个真实任务（先离线）

```bash
mas run \
  --goal "写一份 v0.1.0 的发布说明" \
  --criteria '{"id":"c1","desc":"包含版本号","check":{"type":"contains","arg":"v0.1.0"}}' \
  --criteria '{"id":"c2","desc":"不少于 200 字","check":{"type":"min_chars","arg":200}}'
```

判定规则表见 README「支持的机检规则」。要点：

- 有 `check` 的标准 → 引擎机检，**模型的 verdict 覆盖不了**；
- 没有 `check` 的标准 → 交给模型验证者；模型没给判定 → `unjudged`，**按不通过处理**；
- `"required": false` → 只记录，不阻塞闭合。

退出码：`0` = 闭合，`2` = 未闭合（升级 / 预算耗尽）。CI 里可以直接当门禁。

---

## 4. 接上真实模型

### 4.1 写配置

```bash
mas init --provider openai_compat --model kimi-k2 --base-url https://api.moonshot.cn/v1
```

或者用 Anthropic 兼容端点：

```bash
mas init --provider anthropic --model claude-sonnet-4-5
```

### 4.2 给密钥

```bash
# Windows PowerShell
$env:MAS_API_KEY = "sk-..."

# macOS / Linux
export MAS_API_KEY="sk-..."
```

> 配置文件里只存 `api_key_env`（默认 `MAS_API_KEY`），密钥本体建议只放环境变量。

### 4.3 不用改文件的临时写法

```bash
export MAS_PROVIDER=openai_compat MAS_MODEL=your-model MAS_BASE_URL=https://... MAS_API_KEY=...
```

环境变量优先级高于配置文件。

### 4.4 换完先自检

```bash
mas doctor     # 会检查 provider / base_url / 密钥是否就位
```

---

## 5. 人机协同：工单与续跑

```bash
mas tickets                                  # 看所有未处理工单
mas resume T-20261111-085346-4396 --note "按用户口径重写结论段"
```

工单里带齐了：目标、验收标准、最后一版产物、失败原因、迭代历史。人工给一句话，循环从断点接着跑，而不是从头再来。

`mas tickets` 现在把每张工单摊开给你看，不用再去翻 JSON：

```
待处理工单：1 个

T-20261115-050450-d643  [waiting_human]  不可能达成的目标
  运行：R-9f2fe97e69
  卡在：c1: regex /^NEVER$/ not found
  原因：stagnation: the deliverable did not change for 3 consecutive attempts
  已尝试：5 轮
  最后产物：85 字
  接着干：mas resume T-20261115-050450-d643 --note "你的意见"
```

**「卡在哪」和「为什么放弃」是两回事**——前者是验收标准没过，
后者是循环为什么停手（停滞、超预算、升级）。两条都在，
你才知道该补内容还是该改标准。

刚装好、还没跑过时，`mas ledger` 和 `mas tickets` 也不会一声不吭：
它们会说自己还是空的，并告诉你敲哪条命令能填上。

**没闭环的运行一定会给你留一张票**——不管是撞了迭代上限、超了时间预算、
烧光了 token，还是后端直接挂了。票会写清楚是哪一类：

| 工单类型 | 含义 | 你该做的 |
| --- | --- | --- |
| `loop_escalation` | 循环自己判定改不动了（停滞） | 给点人工意见，或者放宽标准 |
| `budget_exhausted` | 预算用完了（迭代/时间/token） | 加预算：`mas init --max-iterations 12` |
| `run_error` | 后端挂了（provider 报错） | 修 endpoint 或换模型 |

### 5.4 闭环还成立吗：`mas seal`

循环闭合时会给出一个结论。**结论应该能被重算，而不是只能被相信** ——
所以每次闭合都会把「判定时依据的东西」封进账本：完整的验收标准、
交付物的 sha256、每一个写出的文件的 sha256、逐条判定。

```bash
mas seal                 # 复核本机账本里的每一条闭环
mas seal --run-id R-xxx  # 只看某一次运行
mas seal --json          # 给别的程序读
```

复核会**重读磁盘、重跑机检、拿结果和封存的判定比对**：

| 复核结果 | 意思 |
| --- | --- |
| `holds` | 结论仍然成立 |
| `drifted` | 文件或交付物在闭环之后被改过 |
| `tampered` | 封存本身被改过 |
| `missing` | 这条闭环没有封存（跑在封存这一格补上之前） |
| `unrecomputable` | 交付物已经不在，结论**无法重算** —— 不算通过 |
| `unknown-version` | 这份封存的格式这个版本读不懂 |

**不能重算的结论不算通过。** 只由模型判定、没有机检规则的标准会被标成
`model-judged`，如实说清哪一部分是机器算的、哪一部分是模型说的 ——
而不是把两者一起算成「通过」。

封存是一份**记录**，不是**公证**：同时改掉封存和账本的人仍然能造出一份
能通过复核的封存。它换来的是**一次普通的事后小改动会变得可见**。

### 5.5 报告也会看见漂移

不只有 `mas seal` 能看。`mas report` 是你跑完任务后真正会打开的那个屏幕，
**它也会重算每一条封存**，并把漂移直接写在摘要表里：

```bash
mas report
```

```
问题概览

| 问题              | 状态         | 尝试 | 轮数 | 工单     | token | 来源         |
|---------------------|--------------|-------|------|----------|-------|--------------|
| 写一份结论        | **结论已漂移**   | 1     | 1    | `-`      | 30    | ⚠ 结论已漂移 |
```

报告顶部也会先行给出总数：

```
封存复核：1 条封存中，1 条结论已漂移（判定之后有东西被改过）
```

每个运行的详情里会写明**哪个文件被改过**。

如果你在 CI 里读报告，不用去拆 Markdown —— JSON 载荷里就有复核结果：

```bash
mas report --json
```

```json
{
  "seal": { "sealed": 1, "holds": 0, "drifted": 1, "ok": false }
}
```

**没有封存的运行不被报成漂移**。封存是 v0.7.88 才加的，
就算旧闭环没有封存，它也**不是被改过的**。

### 5.0 一个问题就是一条线

续跑不会开第二张票——**同一个问题永远只有一张票**，
但账本上会多出一次运行。这时候要分清两个数：

- **运行次数（runs）**：为了这个问题一共试了几轮循环；
- **问题数（problems）**：还有几个问题没解决。

`mas status` 和 `mas report` 现在**两个都给你**：

```
账本：14 条  运行 2 次  已闭环 0  未闭环 2
待解决问题：1 个（共 1 个问题）
```

**「未闭环 2」不等于「有两件事要做」**——那可能是**同一个问题试了两次**。
状态页和报告会把这两个数分开报，**人工队列里也只有一张票**，
两边说的是同一件事。

工单上记的也是**这个问题的总账**，不是最后一次的账：

```
T-20261115-050450-d643  [waiting_human]  不可能达成的目标
  运行：R-9f2fe97e69
  关联运行：2 次尝试（R-9f2fe97e69 … R-c31a08f5d2）
  已尝试：5 轮（本次）
  累计已烧：10 轮（含历次续跑）
  最后产物：85 字
  接着干：mas resume T-20261115-050450-d643 --note "你的意见"
```

**累计烧了多少轮，是这张票真正的价格**。

---

## 5.1 找回你的交付物

```bash
mas status                                  # 一页人话：跑了多少、闭环多少、交付物在哪、谁在等你
mas artifacts                                # 列出本机产出的全部交付物
mas artifacts --run-id R-20261115-033129    # 只看某一次运行
mas artifacts --json                        # 结构化输出
```

每次成功运行都会把最终成果写到 `<state_dir>/artifacts/<运行ID>.md`，
`mas run` / `mas resume` 结束时会直接打印那行路径。
`mas artifacts` 把历次成果列成一张表：**运行 ID、状态、字数、轮数、路径、文件还在不在**。
文件被人删了或挪走时，那一行会标 **「缺失」**，不会假装一切正常。

---

## 5.3 账本放在哪：state_dir 的语义

账本（`ledgers/runs.jsonl`）、工单和交付物都写在 **state_dir** 里，
默认是 `~/.mas`。这个位置值得说清楚，因为它决定了你干完活之后
**东西还在不在**。

| 你怎么设的 | 会不会写进配置文件 | 说明 |
| --- | --- | --- |
| 什么都不设 | 写，默认 `~/.mas` | 常规用法，账本长期跟着你 |
| `mas init --state-dir <目录>` | **写，一定保留** | 你亲自指定的路径就是决定 |
| 环境变量 `MAS_STATE_DIR` | **不写** | 只决定这一次跑写哪儿 |

三条规则的实际含义：

* **你亲自指定的目录一定保留**，哪怕它就落在系统临时目录里。
  人打出来的路径是决定，守卫只拦注入，不替你做主。

* **系统临时目录里的「一次性目录」不落盘**。判据是形状：
  临时目录下、分隔符后跟着八位随机字符的目录（`mkdtemp` 的产物）
  一律不写进配置——因为系统清理临时目录时会把账本一起带走，
  活儿明明干过，工具却报告「空历史」。

* **你自己命名的目录不会被误伤**。`%TEMP%\myproject` 这种名字
  不是 `mkdtemp` 的产物，就是你的，照样保留。

```bash
# 想换个地方放账本（长期生效）
mas init --state-dir D:\mas-ledger

# 只是这一次跑写到别处（不改变配置）
MAS_STATE_DIR=D:\tmp-run mas run --goal "..." --criteria "..."
```

---

## 5.2 回放也要说人话

`mas report` 讲的是**问题**，`mas replay` 讲的是**过程**——
但过程也得让人看出**问题**在哪。

```bash
mas replay
```

```
回放 14 条账本记录

── 运行 R-b59585accd（budget_exhausted，1 轮，14252 token）
   任务：写一份循环工程上手指南
  - #1 iter=1 planner
  - #2 iter=1 write
  ...
  - #7 iter=1 run_end revise status=budget_exhausted decision=revise
   → 接着干：mas resume T-20261115-070909-a094 --note "你的意见"

── 运行 R-bc72d41c8d（budget_exhausted，1 轮，13103 token）
   任务：写一份循环工程上手指南
  ...

按问题分组：
  [待处理] 写一份循环工程上手指南　2 次尝试　2 轮
```

三件事：

- **每次运行一块** —— 状态、轮数、token 花费、这次在干什么；
- **没闭环的运行给出接手命令** —— 不用再去翻工单队列找是哪一张；
- **末尾一行一个问题** —— 「待处理 / 已解决、几次尝试、几轮花费」。

`mas report` 的开头也多了一张**「问题概览」表**，
**排在「运行概览」之前**——因为运行表会把同一个问题列成两行，
**先读运行表的人会以为有两件事要做**。

机器可读的版本也一样：

```bash
mas replay --json
```

```json
{
  "runs": [{"run_id": "R-...", "status": "budget_exhausted", "closed": false, "ticket_id": "T-..."}],
  "threads": [{"thread_id": "T-...", "run_count": 2, "closed": false, "iterations": 2}],
  "problems_open": 1
}
```

## 6. 接入 MCP 客户端

安装器已自动注册。手动注册（任意 MCP 客户端通用）：

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

然后在 Codex / Claude Code 里说：「用 mas_loop 跑一个闭环任务，目标是……，验收标准是……」。

工具清单见 [mcp.md](mcp.md)。

---

## 7. 卸载

Windows：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\uninstall.ps1
```

手动清理也可以：删 `~/.mas`、删三个 skill 目录、从 `~/.codex/config.toml` 与 `~/.claude.json` 里移除 `mas_loop` 条目、移除 PATH 里的 `~/.mas/bin`。

---

## 常见问题

**Q：`mas` 提示不是内部或外部命令？**
新开一个终端（PATH 改动只对新进程生效），或直接用 `~/.mas/bin/mas.cmd`。

**Q：`mas doctor` 报 provider 不可用？**
默认是 `mock` 离线模式，永远可用。要用真实模型先 `mas init` 并设置 `MAS_API_KEY`。

**Q：`mas doctor` 报 `instruction` 是 warn？**
这条检查在问一件更要紧的事：*你发的角色指令，模型到底采纳了没有*。
它让模型复述一个只出现在 system 指令里的标记词。端点能应答、却没复述，
说明你的网关读的是 `input` 而不是 `messages`（或反过来），于是 worker / verifier 的
角色契约没有生效——**闭环可能"闭合"得毫无意义**。这时先确认网关方言，
再升级到 v0.7.19 及以上（该版本起 `input` 会携带整段对话）。

**Q：任务一直不闭合？**
看 `mas ledger` 里的 `verdict` 与 `note`；若是 `unjudged`，说明该标准既没有机检规则，模型也没给判定——补一条 `check` 规则最省事。

**Q：想让它少烧 token？**
调小 `budget.max_iterations` / `budget.max_tokens`，或调小 `stagnation_limit` 让卡死更早被拦下。
