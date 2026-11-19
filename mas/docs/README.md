# OpenLoop MAS · 会闭合的多智能体系统

> 一个 **零依赖、纯标准库** 的多智能体闭环运行时：把「规划 → 执行 → 验证 → 复盘」写成**有验收标准、有退出条件、有过程账本**的工程循环。
> **下载一个脚本就能装**，初学者也能跑起来。


[![ci](https://github.com/tffgwy/openloop-mas/actions/workflows/ci.yml/badge.svg)](https://github.com/tffgwy/openloop-mas/actions/workflows/ci.yml)
[![license](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![deps](https://img.shields.io/badge/dependencies-0-orange.svg)]()

**第一次来？** 直接看 [5 分钟上手](#5-分钟上手) —— 一条脚本装好，
不用 API Key 就能跑通一个闭环。

**想知道它凭什么敢叫「会闭合」？** 看 [为什么做这个](#为什么做这个)。

**想看它踩过的坑？** 最后一节 [门禁考古](#门禁考古) 记着每一道门禁的来历。

---

## 5 分钟上手

### 方式一：一条脚本（跨平台，不需要 Python 环境准备）

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/tffgwy/openloop-mas/main/install.sh | bash

# Windows（PowerShell）
irm https://raw.githubusercontent.com/tffgwy/openloop-mas/main/install.ps1 | iex
```

脚本会自己建好虚拟环境、装好包、注册 MCP 服务、跑一遍自检。

### 方式二：pip 安装（需要 Python 3.10+）

```bash
pip install openloop-mas     # 见下方说明：这个包名目前还没上 PyPI
mas install        # 装 skills + 注册 MCP 服务器
mas demo           # 不需要任何 API Key，先看它自己跑通闭环
mas doctor         # 自检（包含 skills 漂移、运行时版本落后）
mas sync           # 把已安装的 skills 同步回包内版本
mas runtime        # 看本机装的是哪一版运行时
mas upgrade        # 把落后的旧版运行时换成当前这版
```

> **升级过、想换新版本？** 先 `mas upgrade`（换掉真正在跑的那份运行时），
> 再 `mas install`（让 MCP 注册指向新版本）。
> 只做后者的话，skills 和注册都刷新了，**但真正在跑的还是旧代码**——
> 这正是 `mas doctor` 的 `runtime` 检查现在会指出来的事。

> **关于 `pip install openloop-mas`**：这个包名**目前还没有在 PyPI 上建立**，
> 直接 pip 会得到 `No matching distribution found`。现在请用上面的**安装脚本**，
> 或从 [Releases](https://github.com/tffgwy/openloop-mas/releases/latest) 下载 wheel。
> `mas doctor` 会**实测**这个包名在 PyPI 上的状态并如实告诉你——
> 包名一旦发布，这段说明和 doctor 的检查都会自动对上。

`mas doctor` 说的是人话：每一项都解释「这是什么」，标清楚是**必需 / 建议 / 可选**，
最后给一句**结论**和一条**下一步命令**：

```
[OK  ] python（必需）
       3.14.10
       是什么：Python 版本够不够新（3.10 以上才能跑）
[注意] skill_codex（可选）
       未安装: ...\.codex\skills\mas-loop\SKILL.md（运行 mas install）
       是什么：给 Codex 装的 skill：让 Codex 会用这个闭环工具
...
小结：8 项正常，6 项注意，0 项坏了
结论：闭环能跑，但智能体接不上：skill_codex、skill_claude、skills_sync、mcp_codex、mcp_claude 还没装。
下一步：运行 mas install，一条命令装上 skill 并注册 MCP 服务。
```

三种结论的区别很重要：**闭环跑不起来**（必需项坏了）、**闭环能跑但智能体接不上**
（skill / MCP 没装）、**一切就绪**。退出码只在第一种情况下非零。

`mas install` 做的是**装 agent skills 到 Codex / Claude Code 的 skill 目录**、
**把 MCP 服务器注册进 `~/.codex/config.toml` 和 `~/.claude.json`**，
然后你可以直接开一个新终端，Codex 和 Claude Code 就认识这套闭环工作流了。

- 幂等：重复跑不会重复注册，也不会覆盖你已有的配置。
- 安全：任何被修改的配置文件都会先备份成 `*.bak-<时间戳>`。
- 不含密钥：安装过程不写入任何 API Key。

> 想看它装了什么，加 `--json`。

### 方式三：只下载一个脚本（零依赖，最适合初学者）

不用 clone、不用 pip、不用自己装任何东西——**下载一个脚本文件**，双击/执行它就行：

```powershell
# Windows：下载 install.ps1 到任意目录，然后执行
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

```bash
# macOS / Linux：下载 install.sh 到任意目录，然后执行
bash install.sh
```

脚本会自己从 GitHub Release 拉取已发布的 wheel、用标准库解开、装到
`~/.mas/`，然后注册 MCP 服务器和 skills，最后跑一遍离线自检。
**不需要 git，不需要 pip，不需要编译工具。**

**机器上还没有 Python？脚本会自己装一个。**

- Windows：脚本会下载官方 Python 安装包，**按当前用户静默安装**
  （不需要管理员权限，不改 `PATH`，不装 py 启动器），
  然后在它自己放的位置把解释器找回来继续装。
- macOS / Linux：脚本会调用系统自带的包管理器
  （Homebrew / apt / dnf / yum）装一个 Python 3.12。
  如果装不上（比如没有 `sudo` 权限），脚本会**如实告诉你**
  并给出可以直接复制的那条命令。

> 不想让它动你的机器？加 `-NoBootstrap`（Windows）或
> `MAS_NO_BOOTSTRAP=1`（shell），它就只检查、自己不去装。

想装固定版本（而不是"最新版"）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Version 0.7.90
```

```bash
MAS_VERSION=0.7.90 bash install.sh
```

> 脚本默认会往 `PATH` 里加一条 `~/.mas/bin`（Windows 写当前用户的注册表）。
> 不想动 `PATH` 就加 `-SkipPath`（Windows）或 `MAS_SKIP_PATH=1`（shell）。

### 方式四：从源码安装（适合开发）

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

> `install.ps1` / `install.sh` 与 `mas install` 做的是同一件事，
> 前者适合从源码装（含开发环境）或「只下一个脚本」，
> 后者适合 pip 安装后的机器。

> 配置文件固定在 `~/.mas/config.json`（Windows：`%USERPROFILE%\.mas\config.json`）。
> 运行账本另存在 `state_dir` 下，两者分开——换状态目录不会把配置弄丢。
> CI 里想隔离，用环境变量 `MAS_CONFIG_PATH` 指定配置文件、`MAS_STATE_DIR` 指定状态目录。

`mas demo` 会离线跑三个场景，并把真实结果打出来：

```
[OK ] closure     期望=closed             实际=closed             迭代=2 票=-
[OK ] stagnation  期望=escalated          实际=escalated          迭代=4 票=T-20261111-085346-4396
[OK ] budget      期望=budget_exhausted   实际=budget_exhausted   迭代=1 票=-
```

> 注意：`mock` 模式里，**通过与否由引擎的机检规则判定**，不是模型自己说「我完成了」。
> mock 只是模拟执行者产出草稿，验证权始终在确定性检查器手里。

---


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
| 闭合 | `lead` | **只判没人判的标准**：`close` / `revise` / `escalate` | 引擎（**最终否决权**） |

一个循环只有同时满足 **产物 + 逐条判定 + 决策 + 账本条目** 才算闭合；否则状态只有三种：`escalated`（升级人工）、`budget_exhausted`（预算止损）、`error`。**没有「差不多完成」这个状态。**

`lead` 是**顾问，不是法官**：**机检失败是事实**，这种情况**不咨询 lead**；
咨询了它也**只能填空**（机检已定的标准原样保留）。
**停滞、预算、死线**由引擎**最终否决**，lead 关不掉一个超预算的运行；
**lead 自己判不了**（`met` 读不出布尔值）就**保持未判定、继续追问**。


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

判定口径（实测确认，写标准时按这个来）：

| 类型 | 精确判定 | 容易踩的坑 |
| --- | --- | --- |
| `contains` | 产物含给定字符串；`arg` 可为数组，**全部命中**才算通过 | 数组漏一项整条失败 |
| `not_contains` | 产物**不含**任何给定字符串 | 数组是「一个都不能出现」 |
| `min_chars` / `max_chars` | 产物的**字符数**（含空白与换行） | 不是字数也不是字节数 |
| `regex` | `re.search` 匹配，**自动带 DOTALL**（`.` 能跨行） | 想跨行不必加 `(?s)`；命中的是子串不是全串 |
| `lines_min` | **非空行**数（空行不计） | 段落间空行不影响计数 |
| `section` | 产物里**整行**恰好是 `## <arg>`（允许行尾空白） | **必须是二级标题 `##`**，`#`/`###` 都不算；正文里提到 `## 结论` 也**不算**；`## 结论分析` **不满足** `## 结论` |
| `json_keys` | 产物须是 JSON **对象**且含全部给定键 | 产物不是合法 JSON 直接判不通过 |

未知类型会被判为 `unjudged`（**不通过**），不会静默放过。

### 文件级机检（v0.7.15 起）：判定跟的是磁盘，不是文案

上面所有规则搜的都是**产物文本**。下面这十个搜的是**真实文件系统**——
「模型说写了」和「文件真的在」从此是两回事：

| type | 含义 | 参数 |
| --- | --- | --- |
| `file_exists` | 文件存在且是普通文件 | `path` |
| `file_missing` | 文件不存在（清理类验收） | `path` |
| `file_contains` | 文件含给定字符串 | `path` + `value` |
| `file_not_contains` | 文件不含给定字符串 | `path` + `value` |
| `file_regex` | 文件内容命中正则 | `path` + `value` |
| `file_lines_min` | 非空行数下限 | `path` + `value` |
| `file_min_bytes` / `file_max_bytes` | 字节数上下限 | `path` + `value` |
| `file_sha256` | sha256 等于给定摘要 | `path` + `value` |
| `file_mtime_newer` | 修改时间晚于给定 ISO 时间戳 | `path` + `value` |

### 内容级机检（v0.7.70 起）：读的是文件**里面**，不是它的字节

上面十个搜的是**字节**。对**正文不是明文的格式**它们是瞎的：
PDF 的文字在**压缩流**里，`.docx` 本身是 **zip 包**，图片里没有文字。
下面六个**先把内容取出来再判**（只用标准库，不执行文件里的任何东西）：

| type | 含义 | 参数 |
| --- | --- | --- |
| `content_contains` | 文件**内容**含给定字符串 | `path` + `value` |
| `content_not_contains` | 文件**内容**不含给定字符串 | `path` + `value` |
| `content_regex` | 文件**内容**命中正则 | `path` + `value` |
| `content_lines_min` | 内容**非空行**数下限 | `path` + `value` |
| `content_min_chars` | 内容**字符数**下限 | `path` + `value` |
| `content_format_is` | 文件**格式**等于给定格式 | `path` + `value` |

```bash
mas run --workspace ./my-project --goal "出一份 PDF 报告" \
  --criteria 'content_format_is:out/report.pdf::pdf' \
  --criteria 'content_contains:out/report.pdf::结论'
```

**格式按 magic bytes 判定**，扩展名只做兜底 —— 扩展名会撒谎，
`.txt` 里装的是 PDF，那它就是 PDF。支持的格式：`pdf` / `docx` / `xlsx` /
`pptx` / `zip` / `png` / `jpeg` / `gif` / `bmp` / `tiff` / `webp` /
`wav` / 文本类。

**读不出内容 = 不通过**（不是「无法判定」）。这是这一版最要紧的一条：
判「无法判定」的话，这条标准会**回到模型手里**，而模型说一句
「looks fine」就把它关上了 —— **没人查过的标准被关上，比不闭合更糟**。

### 统一资源索引与多格式预览（v0.7.71 起）：一个文件夹就是一份索引

```bash
mas files                  # 索引这台机器上所有工作区
mas files ./out            # 索引一个文件夹
mas preview ./out/report.pdf   # 预览一个文件
```

```bash
$ mas files ./out

[./out]  5 个文件  263.7 KB
    pdf        1 个
    image      2 个
    model      1 个
      836.0 B  pdf        report.pdf  pages 2、text chars 57
      327.0 B  image      shot.png  width 12、height 7
      117.0 B  image      liar.txt  width 4、height 4
      213.0 B  model      ascii.stl  triangles 2
      251.0 B  zip        bundle.zip  members 2
```

**格式按文件自己的头判定，不看名字** —— 上面的 `liar.txt` 其实是一张 PNG，
所以它报 `image`。**读不开的文件会列出来并写明原因**（那一行 ⚠ 是信息，
不是噪声：交付物中间有一个读不开的文件，恰恰是读者最该看见的东西）。

`mas preview` 把 PDF 的字从**压缩流**里取出来、把 Word 的字从 **zip 成员**里
剥掉标记取出来；图片没有正文时**明确说「没有可预览的正文」**，而不是给你一个空白屏幕。
文件不存在或传了目录 → **退出码 2**。

详见 **[docs/resources.md](docs/resources.md)**（含与
[MOKI 资料库](https://github.com/JKZeng-cell/moki-library-for-dsh) 的分工对照）。

### 草稿层（v0.7.72 起）：改动先进草稿，人确认后才写回原文件

```bash
mas drafts                              # 看有哪些草稿在等
mas drafts show D-1a2b3c4d              # 看这一份的 diff
mas drafts approve D-1a2b3c4d           # 批准：写回原文件
mas drafts reject  D-1a2b3c4d           # 驳回：原样不动
mas drafts token                        # 拿人类令牌（agent 批不了自己的草稿）
mas drafts ticket D-1a2b3c4d --token <人类令牌>   # 签一张只管这一个草稿、用一次就作废的票据
mas drafts conflict                           # 查哪些草稿的文件在等待期间被改过
mas drafts approve D-1a2b3c4d --force           # 明知文件被改过，仍要写回
```

一次运行想改一个**已经存在**的文件时，它**不再直接覆盖**：
改动先进草稿，**原文件一个字节都不动**，草稿带一份**可读的 diff**，
你说「批准」才写回，你说「驳回」就**一个字都不改**。

```bash
$ mas drafts

工作区 ~/mas/artifacts：1 个草稿
  D-1a2b3c4d  pending  notes.txt  有改动
        理由：the loop proposed a change to an existing file
```

**新建文件照常直接写** —— 一个还不存在的文件，没有东西可以毁掉。
想回到旧行为，把配置里的 `edit_policy` 改成 `"overwrite"`（默认 `"draft"`）。

**机检读的是草稿里的新字节**，不是磁盘上的旧文件 —— 否则循环会陷入
「提改动 → 读旧文件 → 判不通过 → 再提改动」的死结，**永远闭不上**。
草稿和它改的文件**住在同一个工作区**（`.mas-drafts/`），文件夹被复制时
草稿跟着走 —— 丢掉的草稿，是一份**永远没法被人审阅**的改动。

### agent 经 MCP 提草稿（v0.7.73 起）：提议和批准分成两个动作

`mas drafts` 是**人**的那一半。MCP 上还有**一半给 agent**，
但两半**不是同一件事**：

```bash
# 人在自己的机器上拿令牌（一次就够）
mas drafts token
```

agent 侧的 MCP 工具：

| 工具 | 做什么 | 能不能改文件 |
|---|---|---|
| `mas_draft_stage` | **提**一份改动（带 diff 和理由） | **不能** —— 原文件一个字节都不动 |
| `mas_drafts` | 看有哪些草稿、看某一份的 diff | 不能 |
| `mas_draft_resolve` | 批准或驳回 | **要人类令牌** |
| `mas_files` | 索引工作区 | 只读 |
| `mas_preview` | 预览一个文件 | 只读 |

**agent 提的草稿，agent 自己批不了。** 令牌不对、空令牌、没令牌，
一律拒绝，而且**原文件不动**。
令牌的作用**不是保密，是分权** —— 提议的人不确认自己提的东西。

```bash
# agent 提了一个改动（文件没变）
mas_draft_stage {path: "notes.txt", content: "改过的正文", reason: "补一句结论"}

# agent 想自己批 —— 被拒
mas_draft_resolve {action: "approve", draft_id: "D-1a2b3c4d"}
-> {error: "human_token_required"}

# 人批 —— 才写回
mas_draft_resolve {action: "approve", draft_id: "D-1a2b3c4d", token: "<人类令牌>"}
```

详见 **[docs/drafts.md](docs/drafts.md)**（含与
[MOKI 资料库](https://github.com/JKZeng-cell/moki-library-for-dsh) 的分工对照：
MOKI 管**交互层**（`Alt+D`），本项目管**引擎层**（循环跑出来的东西），
**规则是同一条**）。

### 写二进制交付物（v0.7.70 起）：worker 怎么产出那个 PDF

内容级机检要读得进 PDF，**前提是有一个 PDF 存在**。写文件协议
（v0.7.15）原本只能带**文字**，所以交付物是 PDF 的任务**没法产出 PDF**。

现在写块可以带 **`encoding`** 属性：

```
<<<MAS:FILE path="out/report.pdf" encoding="base64">>>
JVBERi0xLjQK...
<<<MAS:END FILE>>>
```

引擎把块里的内容**解码成真正的二进制**再落盘，之后内容级机检就能读它。

| 情况 | 结果 |
| --- | --- |
| `encoding="base64"` | **解码成二进制**再写 |
| 不写 `encoding` | **按 UTF-8 文本写**（旧行为不变） |
| `encoding="rot13"` 等**不认识**的编码 | **拒绝**，并说明原因 |
| base64 **解不出来** | **拒绝**，并说明原因（不留半截文件） |

**base64 严格解码。** 宽松解码会把**不在字母表里的字符全丢掉**，
于是一段纯胡话被解成**一个空文件**、一声不吭 —— worker 要一份 PDF，
工作区里躺着一个**零字节的 PDF**。宁可拒绝。

**模板块不是请求。** 二进制模板里写着 `out/report.pdf`，看着像个真路径；
靠**路径**认不出模板（任务真可能就要写这个文件），所以改用**正文**认。

```bash
mas run --workspace ./my-project --goal "写一份调研报告" \
  --criteria 'file_exists:out/report.md' \
  --criteria 'file_contains:out/report.md::结论'
```

判定口径：**文件缺失 = 不通过**（不是「无法判定」）；路径越界 = 开跑前拒绝；
参数写漏 = 开跑前拒绝；路径合法但读不出结论 = `unjudged`。
单文件读取上限 8 MiB。**内容级机检读不出内容时判「不通过」**，理由同上。worker 用**写文件协议**（`<<<MAS:FILE path="...">>>` 围栏块）
把内容真的写进工作区，写入进账本可查。完整说明见 [`docs/file-checks.md`](docs/file-checks.md)。



**参数写漏了 = 标准作废，不是标准通过。** 规则缺参数时不可能判出任何结论，
所以引擎会在**开跑前**直接报错（退出码 1，不消耗任何 token），而不是让它恒真通过：

| 类型 | 必须提供 | 非法示例 |
| --- | --- | --- |
| `contains` / `not_contains` | 非空字符串，或非空字符串数组 | `""`、`[]` |
| `min_chars` / `max_chars` / `lines_min` | 非负整数 | 缺失、`-1`、`"很多"` |
| `regex` | 能编译的正则 | `""`、`"([未闭合"` |
| `section` | 标题文本 | `""` |
| `json_keys` | 键名或非空键数组 | `[]`、缺失 |

**简写形式的坏参数同样快速失败。** `--criteria 'regex:'`、`--criteria 'min_chars:many'`
不会退回成纯描述——它们保留机检类型，然后被上面那张表拦下，退出码 1、零 token。
同一份标准，JSON 形式和简写形式待遇一致：写漏了就是写漏了，不会偷偷变成"交给模型判"。

**评审的判定要"读"出来，不是靠真值转换。** 模型如果回 `"pass": "false"`，
`bool("false")` 是 `True`——一条刚被判定为不通过的标准会被当成通过。引擎读的是
这个值的**含义**：`"false"` / `"0"` / `"no"` 是不通过，`"true"` / `"1"` / `"yes"` 是通过，
读不懂（`"maybe"`、空、缺失）就算**未判定**，绝不猜。

**类型名写错也不放过。** JSON 形式里写了一个不存在的类型（如
`{"check":{"type":"mystery"}}`），引擎同样在开跑前拒绝，并列出所有已知类型——
一个写错的类型名，如果放行，就会退化成"交给模型判"，模型说"看起来没问题"就闭合了。

只有**简写形式**里类型名本身不存在（`--criteria 'unknown_type:abc'`）才退回成纯描述——
那是压根没打算写机检，区别于"想写机检但写错了"。

### 简写写法（v0.4.2 起）

机检规则不必每次都写成 JSON。命令行可以直接用 `<类型>:<参数>`：

```bash
mas run --goal "写一个去重函数" \
  --criteria 'regex:(?s)def dedupe.*set' \
  --criteria 'min_chars:200' \
  --criteria 'contains:关键结论'
```

**一次写多条标准**，直接给 JSON 数组——数组里的每一条都带上自己的机检规则跑，
不会被当成一条纯文本描述（那样机检就一次都没跑过，闭合其实来自模型的"看起来没问题"）：

```bash
mas run --goal "写一份 v0.1.0 的发布说明" \
  --criteria '[{"id":"c1","desc":"包含版本号","check":{"type":"contains","arg":"v0.1.0"}},
               {"id":"c2","desc":"不少于 200 字","check":{"type":"min_chars","arg":200}}]'
```

简写与 JSON 形式完全等价，判定同样来自引擎的确定性检查器（账本里记为 `source: rule`）。
简写解析在引擎层生效，所以从 MCP、HTTP 或账本重放进来的标准也会被同样处理。
未知的类型前缀（例如 `写清楚就好`）仍然按普通描述处理，不会被误当成机检规则。
参数不是整数的数字类前缀（例如 `min_chars:很多`）同样退回普通描述——标准宁可没有机检，也不能声称自己能检查却做不到。

没有机检规则的标准会交给模型验证者判定；但**如果模型没给出判定，默认判不通过**（`unjudged`），不会因为一句「看起来可以」就放行。
`"required": false` 的标准**只记录、不阻塞闭合**：它有机检规则时照常跑机检，结果原样写进报告（过了是 `PASS`，没过是 `FAIL`），但不会让循环一直不闭合；它没有机检规则时由模型判定，判定结果同样只作记录。

**`required` 标志读的是含义，不是真值。** 写 `"required": "false"`（字符串）**确实**表示可选——
`bool("false")` 是 `True`，而这条标准一旦通过 JSON、MCP 或 shell 传进来，很容易就带上引号；
引擎读的是这个值的含义：`"false"` / `"0"` / `"no"` 是可选，`"true"` / `"1"` / `"yes"` 是必选，
读不懂（`"maybe"`、缺失、`null`）一律按**必选**处理——读不懂的标志绝不能悄悄把标准放宽。

> v0.4.2 起，验证者漏判某条标准时，引擎会在**同一轮内**就缺失的标准追问一次，
> 而不是重新生成整份交付物、白白消耗一整轮迭代。追问仍无答案才按 `unjudged` 记为不通过。

---

## 并行多智能体：让候选互相竞争

一个 worker 只会产出**一种**解法。v0.2 让多个 worker 带着**不同视角**并行产出候选，由引擎按机检结果选优：

```bash
# 单次任务临时加力，不改配置
mas run --goal "..." --criteria-file criteria.json \
  --parallel 3 --perspective thorough \
  --reviewers 3 --review-mode majority

mas init --wizard    # 交互式配置，第 7 题设并行数、第 8 题设评审数、第 11 题设回退链
```

| 旗标 | 作用 |
| --- | --- |
| `--parallel N` | 并行候选数（N>1 时并发产出多个视角，引擎按机检通过数选优） |
| `--perspective` | 候选视角锚点：`direct` / `thorough` / `concise` / `structured` / `example_first` / `risk_first` |
| `--reviewers N` | 独立评审者数量（N>1 时启用对抗评审聚合） |
| `--review-mode` | 多评审聚合规则：`all`（全票通过）/ `majority`（过半）/ `any`（任一） |

或者直接改配置：

```json
{
  "orchestration": {
    "parallel_workers": 3,
    "reviewers": 3,
    "review_mode": "all"
  }
}
```

| 配置 | 行为 |
| --- | --- |
| `parallel_workers` | 并行产出的候选数。视角依次为 direct / thorough / concise / structured / example_first / risk_first，**不是同一段提示词的 N 次采样**。 |
| `reviewers` | 独立验证者数量。视角依次为 strict / skeptic / completeness / reader。 |
| `review_mode` | `all` 一票否决（全通过才算通过）/ `majority` 多数通过 / `any` 任一通过。 |

**选优规则是确定性的**：先看机检通过项数，再看产物体积，最后按下标定序。同一份配置跑两次，选中的一定是同一个候选 —— 这让回放可信。

**机检永远压过模型判断**：有 `check` 的标准由规则决定，只有没有机检的标准才交给评审共识。评审说「通过」不能覆盖规则说「不通过」。

**默认就是 v0.1 的行为**：`parallel_workers: 1`、`reviewers: 1` 时，路径与 v0.1 完全一致。

---

## 多后端故障转移

一个端点返回 502 就让整条循环死掉，那不叫闭环，叫宕机。配置多个后备端点，主端点失败自动切换：

```json
{
  "provider": "openai_compat",
  "model": "gpt-4o-mini",
  "base_url": "https://api.openai.com/v1",
  "fallbacks": [
    { "provider": "anthropic", "model": "claude-sonnet-4-5", "base_url": "https://api.anthropic.com" },
    { "provider": "openai_compat", "model": "kimi-k2", "base_url": "https://api.moonshot.cn/v1" }
  ]
}
```

每个后备端点都会记录自己实际接了哪一次调用（`provider_used` / `model_used`）。全部失败才会报错。

---

## 观测：复盘报告与回放

```bash
mas report                 # 生成 Markdown 复盘报告（逐条判定、规划、候选、评审）
mas report --out r.md      # 写入文件
mas replay                 # 回放账本里的运行轨迹
mas export                 # 把原始账本导出成 Markdown 表格
```

`mas report` 的输出长这样：

```
# OpenLoop MAS 闭环复盘报告

运行总数：12　已闭环：10　未闭环：2　闭环率：83%

## 运行 `R-912928b0af`

- 规划：拆解目标：输出一份结论
- 并行候选（3 个）：
  - ★ `thorough` - 机检通过 3 项
  -   `concise` - 机检通过 1 项
- 对抗评审：模式 all，可用评审 3，跳过 0
- 验收判定：
  - [PASS] `c1` all required strings present
```

报告里的每个数字都来自账本，**没有任何一处是编出来的**。

---

## 远程接入：HTTP + SSE

stdio 传输给本地 Agent 用；HTTP/SSE 传输让运行时能被远程客户端、浏览器面板或 CI 任务访问：

```bash
mas http --host 127.0.0.1 --port 8765
```

| 路由 | 方法 | 用途 |
| --- | --- | --- |
| `/health` | GET | 存活检查 + 版本 |
| `/tools` | GET | 工具清单 |
| `/call` | POST | 调用任一工具，JSON 进 JSON 出 |
| `/events` | GET | SSE 事件流，实时推送账本增长 |

```bash
# 免鉴权（仅限本机访问）
curl http://127.0.0.1:8765/health
curl -X POST http://127.0.0.1:8765/call \
  -H 'Content-Type: application/json' \
  -d '{"tool":"mas_run","arguments":{"goal":"写一份发布说明","criteria":[]}}'
curl -N http://127.0.0.1:8765/events
```

### 鉴权（对外暴露时必做）

`/call` 能执行任务、读账本，`/events` 能持续推送账本内容 —— 所以从 **v0.4.8** 起，HTTP 传输**内置鉴权**：

```bash
export MAS_HTTP_TOKEN=$(python -c "import secrets;print(secrets.token_urlsafe(32))")
mas http --host 0.0.0.0 --port 8765
```

设了 `MAS_HTTP_TOKEN` 之后，除 `/health` 外的所有路由都需要令牌：

```bash
curl -H "Authorization: Bearer $MAS_HTTP_TOKEN" http://127.0.0.1:8765/tools
curl -X POST http://127.0.0.1:8765/call \
  -H "Authorization: Bearer $MAS_HTTP_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"tool":"mas_status","arguments":{}}'
```

也可以用 `X-MAS-Token: <token>` 请求头代替 `Authorization`。

**fail-closed**：绑定到非本机地址（`0.0.0.0` / 局域网 IP）却**没有**令牌时，服务器**拒绝启动**（退出码 `2`）—— 环境变量写错不应该变成公网裸奔。开启鉴权后 CORS 也不再回 `*`，而是钉到 `http://127.0.0.1`，避免任意网页跨站调用。


### 常驻部署实战（服务 + TLS 反代）

真正要长期给手机 / 其它设备用，推荐这个组合 —— **服务只绑本机，对外只暴露 TLS 入口**：

```bash
# 1. 令牌落在文件里，不进版本库、不进启动脚本
python -c "import secrets,pathlib;pathlib.Path.home().joinpath('.mas/http_token.txt').write_text(secrets.token_urlsafe(32))"

# 2. 服务常驻（示例：Linux systemd / Windows 启动文件夹 / macOS launchd 均可）
export MAS_HTTP_TOKEN=$(cat ~/.mas/http_token.txt)
mas http --host 127.0.0.1 --port 8768

# 3. 只把 TLS 入口反代出去（tailnet 内；--https 需要 tailscale serve 权限）
tailscale serve --bg --https=8768 http://127.0.0.1:8768
```

手机 / 其它设备访问 `https://<your-host>:<port>/tools`，带上 `Authorization: Bearer <令牌>`。

要点：

- 服务绑 `127.0.0.1` + TLS 反代，比直接 `--host 0.0.0.0` 安全得多（不暴露在局域网，也由反代统一处理 TLS）。
- 令牌与启动脚本**分离**：脚本里不写明文，令牌文件缺失就报错退出，绝不退化成无鉴权启动。
- `tailscale serve` 默认是 **tailnet only**；`funnel` 会暴露到公网，属于扩大暴露范围，请自行评估后再开。
- 停用：`tailscale serve --https=8768 off`。


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
- 网关用 Responses 事件流回答（Codex 桥接、OpenMausBot 这类运行时）时同样折叠；
- 网关只认 `input` 不认 `messages` 时也能工作 —— 请求同时带两份，二选一被读取；
- 回答是内容块数组而不是字符串时，自动拼成纯文本；
- usage 字段名在 `prompt_tokens` / `input_tokens` 两套之间自动归一，缺 `total_tokens` 时回填；
- 模型在正文前吐 `<think>` 思维链时自动剥离，思维链不会进入交付物、判定和账本。

装完之后跑一次握手，看你的端点到底说哪种方言：

```bash
mas doctor
# [OK  ] endpoint       端点可用，协议方言 = openai-responses-events
```

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
| `mas_report` | 生成闭环复盘报告 |
| `mas_replay` | 回放运行轨迹 |
| `mas_export` | 导出账本为 Markdown |
| `mas_teams` | 列出团队模板 |
| `mas_doctor` | 安装自检 |
| `mas_config` | 读取 / 更新配置 |

协议实现是纯标准库的 JSON-RPC 2.0 over stdio —— **不需要 `pip install mcp`**。

### 接进 OpenMausBot（开源 Grok Bot 平替）

[OpenMausBot](https://github.com/milind-soni/OpenMausBot) 是开源的 Grok Bot 平替：聊天界面里
每个联系人都是一个真的智能体（Claude Code / Codex 在本地跑）。它提供**入口**，本项目提供**判定**。

```sh
npm install -g openmausbot && openmausbot     # Node 24+
python tools/install_openmausbot_mcp.py         # 把 MAS 注册成它的 MCP server
```

注册后 OpenMausBot 会挂载上表的 12 个工具，bot 在聊天里就能直接调用 `mas_run`
拿回一个 `closed / escalated / budget_exhausted` 的确定结论。

完整说明（含权限模型、开机自启、给 bot 的任务模板）：[docs/openmausbot-integration.md](docs/openmausbot-integration.md)。

---

## 接入 DeepSeek Harness（可选）

仓库里带了一个 **DSH 插件**（`integrations/dsh-openloop-mas`）：它不重写闭环逻辑，
而是通过 **MCP** 调用本地运行时，因此 DSH 对话里的判定与账本，和 Codex / Claude Code
客户端看到的是**同一份事实**。

```powershell
# 可选开关：默认关闭，主线安装仍然零依赖（纯 Python，不需要 Node / DSH / npm）
powershell -ExecutionPolicy Bypass -File .\install.ps1 -InstallDshPlugin
```

装好后 DSH 里多出 6 个工具：`mas_run`、`mas_status`、`mas_ledger`、`mas_tickets`、
`mas_report`、`mas_doctor`。

细节、配置项、实测证据与边界见 **[docs/dsh-integration.md](docs/dsh-integration.md)**。

### 让 DSH 真的能说话：`mas relay`

装上插件不等于模型能用。DSH 的 `dsh-llm-pi-ai` 适配器**坚持要一个 API key**，
而私有端点常常**不要 key**（甚至会因为占位 key 直接 `401`）——这是协议契约和
端点契约打架。`mas relay` 插在中间：丢弃客户端的 `Authorization`，按需换成上游真 key。

```powershell
# 一键生成能跑的 DSH profile（写配置 + 装插件 + 打印命令）
python tools/setup_dsh_bridge.py --profile masbridge --relay-port 8766

# 终端 1
mas relay --upstream http://127.0.0.1:16024 --port 8766

# 终端 2：pi-ai 坚持要 key，给个占位值即可（relay 会丢弃它）
$env:MAS_BRIDGE_API_KEY = "placeholder"
dsh --profile masbridge headless "用 mas_run 跑一个闭环：目标是写一份发布说明，验收标准用 file_exists 要求 out/report.md 存在"
```

> **`headless` 是 profile 名，不是子命令。** 正确形态是 `dsh --profile <name> headless "<task>"`。

中继的两种鉴权模式、安全边界、已验证/未验证清单见 **[docs/relay.md](docs/relay.md)**。

### 同宿主并列插件：MOKI 资料库

[MOKI](https://github.com/JKZeng-cell/moki-library-for-dsh)（`dsh-moki-library`）是
**另一个项目**，别和 OpenMausBot 混：它给 DeepSeek Harness 加一个**本地资源管理器**——
文件/网页链接统一整理、多格式预览、选区 `Alt+D` 唤起 AI 对话、**修改先进草稿、
用户确认后才写回原文件**。

分工很清楚：**MOKI 负责把资料整理成可交付的产物，MAS 负责判定这份产物到底过不过。**

```bash
dsh plugin --profile web add "dsh-moki-library@file:<绝对路径>/dsh-moki-library-0.1.73.tgz" --allow-build=sharp
dsh plugin --profile web add "dsh-openloop-mas@file:<绝对路径>/dsh-openloop-mas-0.7.16.tgz"
```

> MOKI 需要 **DSH >= 0.2.2-rc.1**、**Node >= 22.19**，且是 **web-platform 插件**——
> 在 CLI / headless profile 里会一直 pending（宿主形态不对，不是装坏了）。

## 随包附带的 Agent Skill

| Skill | 作用 |
| --- | --- |
| `mas-loop` | 跑闭环任务、写验收标准、读账本与工单 |
| `mas-review` | 多视角对抗式审查（执行者 / 验证者 / 复盘者分离） |
| `mas-scaffold` | 给新项目搭团队模板与 CI 门禁 |

安装器会把它们装进本机能找到的所有 skill 目录（`~/.codex/skills`、`~/.claude/skills`、`~/.agents/skills`、`~/.gemini/skills`）。

> Skill 的**唯一真源是 `mas/skills/`**——它随 wheel 一起分发，所以 `pip install` 装出来的 skill
> 和从源码装出来的 skill 逐字节相同，不存在两份副本各自漂移的问题。

卸载：

```bash
mas uninstall       # 移除 skills 与 MCP 注册（保留 ~/.mas 里的账本与配置）
pip uninstall openloop-mas
```

---

## 目录结构

```
openloop-mas/
├─ mas/                 运行时（纯标准库）
│  ├─ engine.py         闭环引擎：预算、停滞判定、闭合/升级
│  ├─ orchestration.py  并行候选、对抗评审聚合、后端故障转移
│  ├─ providers.py      mock / OpenAI 兼容 / Anthropic 适配
│  ├─ ledger.py         append-only 账本
│  ├─ tickets.py        人工升级工单
│  ├─ mcp_server.py     MCP stdio 服务
│  ├─ http_server.py    MCP over HTTP + SSE（远程接入）
│  ├─ report.py         复盘报告、回放轨迹、账本导出
│  ├─ setup.py          交互式向导 + 真实闭环自检
│  ├─ bench.py          离线基准测试
│  ├─ cli.py            命令行
│  ├─ drafts.py         草稿层：改动先进草稿，人确认后才写回
│  ├─ doctor.py         安装自检
│  ├─ demo.py           三个离线场景
│  └─ teams/            团队模板（default / review / content）
│  └─ skills/           Agent Skill 包（唯一真源，随 wheel 一起分发）
├─ integrations/        DSH 插件（DeepSeek Harness 集成）
├─ tests/               离线测试
├─ install.ps1          Windows 一键安装
├─ install.sh           macOS / Linux 一键安装
└─ docs/                架构、方法论、MCP、定位说明
```

## 命令速查

```bash
mas install       # 装 skills + 注册 MCP 服务器
mas sync          # 把已安装的 skills 同步回包内版本（修复漂移）
mas uninstall     # 移除 skills + MCP 注册（保留账本）
mas init          # 生成/更新配置
mas init --wizard # 交互式向导，一步步配好
mas run           # 跑一个闭环任务
mas demo          # 离线演示三个闭环场景
mas verify        # 跑一次真实闭环自检
mas bench         # 离线基准测试
mas status        # 本机状态
mas ledger        # 闭环账本
mas ledger --undone   # 只看被撤销过的那几条批准
mas undos        # 哪些批准被撤回了：配对 + 改了多少行
mas report        # 闭环复盘报告（Markdown）
mas replay        # 回放运行轨迹
mas export        # 导出账本为 Markdown 表格
mas tickets       # 人工工单
mas resume ID     # 带人工意见继续
mas teams         # 团队模板
mas doctor        # 安装自检
mas selftest      # 隔失门禁：重复打磈易偶发的路径（离线）
mas files        # 索引工作区：每个文件是什么格式、能读出什么
mas preview FILE # 预览一个文件（PDF / Word / 文本…）
mas drafts       # 待确认的草稿：list / show / approve / reject / undo / token / ticket / conflict
mas drafts token # 拿人类令牌：agent 提的草稿，agent 自己批不了
mas drafts ticket D-ID --token <令牌>  # 签一张只管一个草稿、用一次就作废、15 分钟过期的票据
mas serve         # 以 MCP stdio 服务运行
mas http          # 以 HTTP + SSE 运行（远程接入；对外部署请设 MAS_HTTP_TOKEN）
mas relay         # OpenAI 兼容中继：给「非要 API Key」的客户端用（默认 127.0.0.1:8766）
```

`mas run` 退出码：`0` = 闭合，`2` = 未闭合（升级或预算耗尽）。CI 里可以直接当门禁用。

---

## 开发与测试

```bash
python -m unittest discover -s tests -v   # 213 个离线测试，不需要网络
python -m mas demo                        # 三个闭环场景
python -m mas bench                       # 离线基准测试
python -m mas verify                      # 真实闭环自检
python -m mas selftest --iterations 120     # 隔失门禁（会拒绝绝经不可能的路径）
```

测试覆盖：机检规则、闭合/停滞/预算三条终止路径、规则判定压过模型判定、未判定标准不得通过、MCP 握手与工具调用、配置合并与环境变量覆盖、SSE 流折叠、思维链清洗、遗留代码页下的 UTF-8 输出安全、并行候选选优、对抗评审聚合（all/majority/any）、后端故障转移、HTTP/SSE 真实请求、复盘报告与回放、向导与自检。

---

## 延伸阅读

- [文件级机检与写文件协议](docs/file-checks.md) —— 闭环为什么必须跟磁盘
| [DSH 集成与 relay](docs/dsh-integration.md) —— 让模型在宿主里真的能跑闭环

| 文档 | 内容 |
| --- | --- |
| [docs/quickstart.md](docs/quickstart.md) | 5 分钟上手：安装 → 离线跑通 → 接真实模型 → 人机协同 |
| [docs/architecture.md](docs/architecture.md) | 运行时架构与数据流 |
| [docs/loop-engineering.md](docs/loop-engineering.md) | 方法论：闭环为什么这样设计 |
| [docs/mcp.md](docs/mcp.md) | MCP 接入、握手、18 个工具、协议细节与排障 |
| [docs/dsh-integration.md](docs/dsh-integration.md) | DeepSeek Harness 集成：DSH 插件、relay 一键流程、MOKI 分工 |
| [docs/relay.md](docs/relay.md) | `mas relay`：给「非要 API Key」的客户端用的私有端点中继 |
| [docs/drafts.md](docs/drafts.md) | 草稿层：改动先进草稿，人确认后才写回原文件；与 MOKI 的分工 |
| [docs/resources.md](docs/resources.md) | 统一资源索引与多格式预览；与 MOKI 的分工 |
| [docs/positioning.md](docs/positioning.md) | 定位说明：与「私有化代理团队」类项目的关系与传播口径 |
| [docs/acceptance-report.md](docs/acceptance-report.md) | 安装与验收报告：本机实测证据、CI 结果、已知限制 |
| [README.en.md](README.en.md) | English version of this README |

---

## 定位说明（与 OpenMausBot 等项目的关系）

OpenLoop MAS **不是**「私有化 AI 代理团队」那一类产品的复刻，它补的是**另一层**：

- 那一类产品解决的是「把 Claude / GPT / Kimi 接进一个能聊天的代理团队，跑在本地或云端，能挂 MCP 和一堆应用」；
- OpenLoop MAS 解决的是「**这个团队跑完之后，怎么知道它真的做完了，而且过程可回放、可止损、可交接**」。

两者可以叠加：前者当执行层，后者当闭环层。
传播口径上可以把本项目讲成「让多智能体**真的会结束**的那一层」，而不是又一个聊天壳。

实测接法（见 [docs/openmausbot-integration.md](docs/openmausbot-integration.md)）：
把本项目注册成 OpenMausBot 的自定义 MCP server，bot 在聊天里就能调用 `mas_run`，
拿回 `closed / escalated / budget_exhausted` 的确定结论。

> 视频/介绍里如果需要引用同类项目（如 OpenMausBot 这类主打私有化代理团队、支持 MCP 与大量应用打通的方案），请以其官方仓库的说明为准：能力边界、安全模型、更新频率都应以官方文档为准，不要替别人的项目做安全承诺。

---

## License

MIT © OpenLoop MAS contributors

---

## 门禁考古

这个项目至今有 **30 道门禁**，每一道都是从一个**真实事故**里长出来的。
下面按门禁编号倒序记录它们各自的来历 —— 如果你只想用，直接跳过这一节就好。

- **第 30 项门禁 `report_undo`** —— v0.7.78 已经让“撤销”在账本里留下痕迹，
  v0.7.85 也给了 `mas undos` 能列出“哪些批准被撤回去过”；
  但 **`mas report` 从来没看过它们** ——
  `run_summary()` 根本不读 `draft_approve` / `draft_undo` 那两类行，
  因为一个关于草稿的人工决定不是一次运行。
  结果很具体：你三周前撤回过的那几次错批，
  在账本里醒着，而报告面照透清、只写「已解决 · 机器闭环」。
  **能证明“哪些批准是错的”能力，却没有被主视图消费** ——
  那就等于没有。
  现在报告会把它说出来：
  顶部一行说「撤销回收：N 次批准被撤销」；
  报告里新增一节「被撤销的批准」，
  逐条点名**哪个草稿**、**哪个文件**、被撤了几次、退回多少行；
  JSON 载荷里也带上了这组数字，CI 和脚本不用拆 Markdown。
  还修了一个**说假话**：只有人工决定、没有任何运行的账本，
  原来会被写成「账本为空」 —— 这不是真的；
  现在它会说「这台机器上还没有跑过任何一次闭环，
  但账本里记着人工的决定」，并指向 `mas undos`。
  配对逻辑**不重写**：直接复用 `mas.undos` 的位置式栈配对，
  配不上的撤销**计数而不隐藏**，也不猜。
  这道门禁**自证能失败**：把报告的撤销计数打坏成
  「永远等于合法」，门禁必须发现它已经分不出
  「哪次批准错了」和「哪次批准对了」。

  ```bash
  mas selftest --only report_undo
  ```

- **第 29 项门禁 `report_seal`** —— v0.7.88 给每个闭环加了**封存**，但报告从来不看它；
  验收标准、交付物的 sha256、每一个写出文件的 sha256。
  `mas seal` 能拿这份封存**重新推导**结论，说它还成立不成立。
  但**报告从来不看封存**。
  报告才是人真正会做的那个屏幕 ——
  “这份东西成没成”正是在这里被回答的 ——
  而它继续写“已解决”、“机器闭环”，
  屏掉一个**结论的字节已经被改过**的闭环。
  **能证明漂移的能力，却没有被主视图消费** ——
  那就等于没有。
  现在报告会**重算封存**：
  问题概览里一个被改过的闭环不再叫“已解决”，
  而是“**结论已漂移**”；运行概览里不再给它打绿开灯；
  每一个运行的详情里写明**哪个文件被改过**；
  JSON 载荷里也带上了封存复核结果，
  这样 CI 和脚本不用去拆 Markdown。
  这道门禁**自证能失败**：把报告的封存检查故意打坏成
  “永远成立”，门禁必须发现它已经分不出
  健康的闭环和被改过的闭环。

  ```bash
  mas selftest --only report_seal
  ```

- **第 28 项门禁 `closure_seal`** —— 这个项目一直承诺一件事：
  **同一份产物 + 同一份标准，永远得到同一个结论**。
  但这个承诺只兑现了一半。
  账本记了交付物**正文**的哈希，验收标准却只活在引擎的内存里 ——
  **事后的读者没法独立重算，只能选择相信**；
  而 worker 写出的文件只记了**长度**，没记内容 ——
  事后有人把文件改掉（哪怕改回**同样长度**），账本**一点察觉都没有**，
  循环照样报 `closed / pass`。
  换句话说：**闭环的结论没有被钉在它判定时依据的那份字节上。**
  现在闭环在判定的那一刻，就把**它判定所依据的东西**一起封进账本：
  完整的验收标准、交付物的 sha256、每一个写出的文件的 sha256、逐条判定。
  之后 `mas seal` 只凭这份封存**重新推导**结论：重读磁盘、重跑机检、
  和封存的判定比对 —— 文件被改过就说改过。
  **不能重算的结论不算通过**：只由模型判定、没有机检的标准会被如实标成
  `model-judged`，而不是悄悄算作「通过」。
  封存是一份**记录**，不是**公证** —— 同时改掉封存和账本的人仍然能造出
  一份能通过复核的封存。它换来的是：**一次普通的、事后的小改动会变得可见。**
  这道门禁自己当场抓到**两个真 bug**：两条人工交付物路径（`handback` 与
  `handback_workspace`）在封存时引用了一个**并不存在**的变量 ——
  交付物判完了、文件写好了，运行却在写账本的路上**崩掉**；
  以及封存记的是**运行自己的目录**，而机检读的其实是**人工编辑的那个目录** ——
  于是重算读到的是另一个文件夹的文件，**把没发生过的漂移报成漂移**。
  两个都没有测试钉住，因为**没有一个测试向人工路径要过封存**。
  这道门禁由第 28 项门禁守着：

  ```bash
  mas selftest --only closure_seal
  ```

- **第 27 项门禁 `bootstrap_execution`** —— v0.7.86 把「没有 Python 时
  自己装一个」写进了安装器，也加了第 26 道门禁盯住规则不漂移。
  但那道门禁**只比对文本**，而且 CI 里**每一条**安装任务都先跑
  `actions/setup-python` —— **跑测试的机器全都已经有 Python**。
  于是「这台机器上没有 Python」这条分支，**在任何地方都没被执行过**：
  不在 CI，不在开发者机器上，不在贡献者机器上。
  **没人跑过的路会静悄悄地坏掉**，而且恰好坏在最难排查的地方：
  初学者的新笔记本上，旁边没有人能告诉他那句报错是什么意思。
  现在这条分支**真的被执行**：把 `PATH` 上的 Python **全部藏起来**，
  让机器看起来真的没有解释器；用**演练开关**把解释器放到
  真实自举会放的位置；安装器必须**用生产查找逻辑**把它找回来
  —— 代码不改、路径不改；演练之后的安装必须**是完整的**
  （运行时在位、启动器在位、自检跑通）；**反证**必须能响：
  解释器放在查找逻辑读不到的地方，演练必须**如实说找不到**。
  它同时盯住两件以前没人盯的事：**Microsoft Store 存根**
  （一台全新的 Windows 上，`WindowsApps\python.exe` 和 `python3.exe`
  **本来就在**，即使从没装过 Python —— 它们是别名，执行一个会
  **弹出 Microsoft Store**，不打印任何东西，然后失败；
  安装器如果把它当成「找到 Python 了」，就会报成功，然后**跑不起来**）
  和**等待重试**（官方的安装 exe 是**引导器**，可能装完就返回、
  文件还在写；查找逻辑只试一次就会扑空）。
  顺手还修掉三处真漂移：**Windows 不认 `MAS_NO_BOOTSTRAP`**
  （两个安装器都支持拒绝自举，但一份只认开关、一份只认变量）；
  **演练开关两边拼写不同**（现在两边认同一套环境变量，
  第 26 道门禁会盯住「一个安装器少一个开关 = 那个平台不再被演练」）；
  以及**门禁自己读错了输出**（它读的是演练的输出，
  而不是「拒绝那次运行」的输出）。
  这道门禁自己又抓出**两处真缺陷**，两处都只会在
  **受限环境**里发作（服务账号、计划任务、自建环境的 CI 步骤）：
  **`PATHEXT` 形同虚设**（它可能不是空的，而是 `.CPL` ——
  一个**不含任何可执行后缀**的列表；这时 shell 把 `python.exe`
  当成**文档**，管道里调用直接抛「Cannot run a document in the
  middle of a pipeline」，单独调用则**静默无输出**、`$LASTEXITCODE`
  为空 —— 安装器于是把**真实存在的 Python** 判成不可用，
  告诉用户「这台机器没有 Python」）；
  以及**查找靠的是 shell**（`Get-Command` 走 `PATHEXT`，
  同样会漏掉明明就在 `PATH` 上的解释器）。
  现在安装器**自己走 `PATH`、自己用 .NET 启动解释器**，
  不再把判断权交给 shell；门禁的演练环境也**故意**带着
  `PATHEXT=.CPL`，好让这条路**每次都被走一遍**。

- **第 26 项门禁 `bootstrap_path`** —— 这一版修的是一个**把初学者挡在门外**
  的口子，而且它就写在 README 里。README 一直写着「方式一：一条脚本
  （跨平台，**不需要 Python 环境准备**）」，两个安装器也这么写着 ——
  然后两个安装器的**第一步**都是找 Python 3.10+，找不到就 `exit 1`。
  一台全新的 Windows 笔记本、一台刚装好的 Linux 机器，
  **照着 README 走，第一步就撞墙**，撞出来的还是一句
  「去官网自己装一个 Python」。
  讽刺的是：**这一步之后的所有事情真的都是零依赖** ——
  运行时是纯标准库，wheel 是手工解开的，不用 pip、不用 git、不用编译器。
  **只有自举这一步还在要人力**。
  现在自举规则只有一份，在 `mas/bootstrap.py` 里：
  最低版本 3.10、**钉死的**自举版本 3.12.8（不是 "latest" ——
  一个解析到"当前最新"的自举，是**没人改代码也会变行为**的自举）、
  固定版本的官方下载地址、以及 POSIX 上**可以直接复制**的那几条命令。
  Windows 安装器会下载官方安装包、**按当前用户静默安装**
  （不管理员权限、不改 `PATH`），然后**在自己放的位置**把解释器找回来 ——
  因为 per-user 安装的解释器**不在 `PATH` 上**，不去找就会
  「刚装完就说没装上」。POSIX 安装器走系统包管理器；
  **装不上就如实说装不上**，并给出确切命令，不假装成功。
  用户随时可以拒绝自举（`-NoBootstrap` / `MAS_NO_BOOTSTRAP=1`）。
  规则存在**三份**（模块 + 两个安装器）—— 安装器在需要规则的那一刻
  **可能根本没有 Python**，所以**没法 import 模块**，副本必须存在；
  于是这道门禁**专门盯这三份副本会不会漂移**，
  并用**五个反证**证明它真的会响（改了版本、改了系列、
  把那句「去自己装」放回来、删掉拒绝开关、删掉自举路径 ——
  五个都必须报红）。

- **第 25 项门禁 `undo_visibility`** —— v0.7.77 让**撤销**能把文件逐字节退回去，
  v0.7.78 让它留下一条**可验证的痕迹**。但这两样都还是**流水**：
  账本是一道河，一行一行往下走；**审计需要的是一张清单**。
  坐在屏幕前的人真正想知道的是那句 **「我批错过的那些东西是哪几件？」**
  —— 以前他得自己读完整本账本、挑出撤销行、再用手工判断每次撤销**盖掉的是哪一次批准**。
  工具该干的活，不该甩给人。
  第二处缺口更安静，也更糟：一个草稿**批准 → 撤销 → 再批准**之后，
  它的状态是 `approved` —— **「它曾经是错的」从草稿自己的状态里消失了**。
  草稿文件只存最新状态，链子记得，但没人把这件事**端到读者面前**。
  现在新增 **`mas undos`**：把每一次撤销**配对**到它撤销的那次批准
  （**按栈配对** —— 撤销撤掉的是**当时还站着的最近一次批准**，
  配对是**读出来的**，不是猜的），并给出**改动多大**
  （批准写 `changed_line_count`，撤销写 `reverted_line_count`，
  两端**必须对得上**）。`mas ledger --undone` / `mas ledger --draft <编号>`
  可以筛出专门的那几条；`mas drafts list` 现在会直接标出
  「⚠ 这个草稿的批准被撤销过 N 次」；`mas audit` 末尾附上被撤销批准摘要；
  MCP 多了 **`mas_undos`**，**agent 问的和人问的是同一份答案**。
  诚实边界：**配不上的撤销进 `unmatched`，不硬配邻居**；
  旧记录缺 `reverted_line_count` 就**保留空缺**，**不猜** ——
  「没写」和「零」是两件事，不合并。
  这个缝是用**反证**查出来的：故意把配对改成「永远配最新一次批准」
  （可能是**撤销之后**才发生的批准），门禁必须报红。

- **第 24 项门禁 `packaged_docs`** —— 上一轮把发布出来的 wheel 装进
  一个**干净 home** 里跑，抓到一处真缺陷：`mas selftest --only
  onboarding_path` **自己报红**（exit 2）。
  根因不是门禁太严，是 **wheel 只带包、不带仓库的散文** ——
  门禁去源码树找 README，找不到，`FileNotFoundError`。
  这个失败有两重坏：它说的不是初学者的事，它也不是任何真实缺陷；
  它只是暴露了一件事 —— **初学者拿到手的 wheel 里没有那两份文档**，
  而那正是初学者唯一会读的东西。
  现在入门文档**随 wheel 走**（`mas/docs/`），门禁**读打包副本优先、
  仓库兜底**，并且汇总器多出第三种状态：**跳过** ——
  一道门禁在没有观察对象时不谎报「初学者走不通」，
  也不被悄悄算成「通过」，它单独列出来。
  证据是干净 home 装新 wheel 后门禁**真通过**（exit 0）。

- **第 23 项门禁 `onboarding_path`** —— 前面 22 道门禁全在管**运行时**：
  引擎闭不闭合、账本留不留、草稿挡不挡得住。**没有一道管「门」**——
  而新来的人只看两样东西：README 和 quickstart。
  代价是真实的：这个项目长到 22 道门禁时，README 已经涨到 **2242 行**，
  首屏塞满门禁考古，**「5 分钟上手」压在第 1314 行**；
  两种安装方式**都叫「方式二」**；文档里钉死的版本还停在 **0.7.17**，
  而发布版已经是 0.7.82。
  这些**一条都不影响引擎**，但**全都挡住初学者**——
  而「让初学者一键装上」本来就是这个项目的目标之一。
  现在门禁守着这扇门：上手章节必须在**首屏 40 行内**、
  安装方式编号**不能重复**、文档里出现的每个 `mas` 命令
  **必须真的在解析器里**、文档钉住的版本**必须就是当前版本**、
  第一条路径**不许要 API Key**（新手还没有账号）。
  同样用**反证**查出来：故意把上手章节的定位器打瞎，
  门禁必须报红（另外四个变异分别打在重复编号、版本漂移、
  幽灵命令、首路径要密钥上，也都被捕获）。
  这道门禁盯的是**人读到的第一页**，而不是**代码跑出来的结果**——
  这是它和前 22 道门禁最不一样的地方。

- **第 22 项门禁 `shadow_guard`** —— 这个项目里每一条检查问的都是**正在跑的这份代码**。
  但坐在键盘前的人敲的 `mas`，跑的是 **PATH 先摸到的那一份**。
  真实机器上就是这样：

  ```
  AppData\Roaming\Python\Python314\Scripts\mas.exe   0.7.16
  .mas\bin\mas.cmd                                      0.7.81
  ```

  安装器装的是 0.7.81，对着它跑完 `mas doctor`，报「一切就绪」——
  然后用户敲 `mas`，跑出来的是 0.7.16。**每一条检查都过了，跑的却是错的程序。**
  现在 `mas shadow` 按 **shell 的规矩**走一遍 PATH，指出**赢家**是哪一份、
  它的版本是多少（**从磁盘上的文件读出来**，读不到就说读不到，不猜）；
  `mas doctor` 多了一档 `shadow_runtime`，**不**把它算进「还没装」——
  因为那份旧的**确实在**，只是**是错的那份**，而 `mas install` 根本改不了
  PATH 先摸到谁，该给的是 `mas upgrade`。
  `mas upgrade --fix-shadow` 会把 PATH 上的旧副本换成现在这一版
  （**先备份、换完当场核对版本**，对不上就是报错，不会假装成功）；
  至于 **pip 装的**那一份 —— **只报出 `pip uninstall openloop-mas` 命令，不偷改它的文件**，
  因为偷改会让 `pip list` 开始撒谎。
  两个安装器（`install.ps1` / `install.sh`）现在都把启动器**前置**到 PATH
  （原来是追加，那等于给旧副本让路），并在发现 PATH 前面还有别的 `mas` 时**当场警告**。
  这个缝是用**反证**查出来的：故意让影子检测永远返回「没有影子」，
  门禁必须报红（4 条观测同时变红）。

- **第 21 项门禁 `audit_chain`** —— v0.7.77 让**撤销**能把文件逐字节退回去。
  但撤销当时还只是一个**文件操作**：做完之后，机器自己的记录里看不出
  「这次批准后来被撤回了」——那条线程看起来和一次普通的批准毫无区别。
  现在每一次**批准**和**撤销**都写**两遍**：一份进**链式审计链**
  （`<工作区>/.mas-drafts/audit.jsonl`，每条记录带着上一条的哈希和自己的哈希，
  **改一条、删一条都能被发现**），一份进**主账本**（`mas ledger` 看得见的那本）。
  两份副本**必须一致**，新增的 **`mas audit`** 专门检查这件事 ——
  改了一条、删了一条、只改了一份副本，都会被**指名报出来**
  （「第 N 条的内容被改过」「账本与审计链不一致：某草稿的某字段」）。
  MCP 多了 **`mas_audit`**。`mas report` 不会把「批准改动」「撤销改动」
  误当成一次**运行**。
  诚实边界：一条链**不是公证** —— 有人把整条链重写一遍，它会重新「验证通过」；
  检测的是**单点改动**，不是**两份一起重写**。
  这个缝是用**反证**查出来的：故意把链的校验换成永远通过的桩，门禁必须报红。

- **第 20 项门禁 `undo_guard`** —— v0.7.72 让**批准**成为改动到达文件的
  唯一通道。这是对的，但它也让批准成了**一扇单向门**：人批错了，
  唯一的退路是**手工去改文件**。
  而 v0.7.76 存下来的**基线**就在草稿里，却没人能用它。
  现在 `mas drafts undo <草稿编号>` 把改动**收回来**：文件**逐字节**回到
  草稿建立时的样子（不是近似、不是反向 patch，是把当初的基线原样写回）；
  **草稿不消失** —— 撤销之后还能**再批准一次**。
  撤销**也先问代价**：文件在**批准之后**又被人改过，就**拒绝撤销**，
  并**点名会丢掉哪几行** —— 和批准是**对称**的一条规则：
  批准挡住「覆盖掉别人的更新」，撤销挡住「抹掉批准之后的新工作」。
  MCP 多了 **`mas_draft_undo`**，同样**要人类令牌或一次性票据**。
  `mas_status` 里多了 `undone_drafts` —— 一个被撤销的改动**不是「还没发生」**，
  它说明**有一次批准是错的**，而那份草稿还在原地等着被批准第二次。
  这个缝是用**反证**查出来的：故意让撤销写错字节，门禁必须报红。

- **第 19 项门禁 `force_cost`** —— v0.7.75 让草稿记住基线哈希，
  文件被改过就**拒绝写回**。但「文件被改过」是**事实**，不是**决定** ——
  人没法凭这句话判断那次改动值不值得留，得看见**改了什么**。
  现在草稿**把基线一起存下来**，并把覆盖的代价摆到台面上：
  `mas drafts compare <草稿编号>` 摆出**基线 → 现在 → 草稿**三方对比，
  **点名覆盖会丢掉的那几行**（人自己写的、草稿里没有的行）；
  没被改过的行**不算丢失**。
  MCP 侧多了 **`mas_draft_compare`**，agent 问的是同一个问题。
  哈希**基准**也记进草稿（`digest_basis`）——旧草稿改用基线比对，
  没有基线的**不谎报冲突**；假警报比没有警报更坏，它教会人忽略真警报。
  **二进制文件不被误判**成「已改动」。
  `mas_status` 现在看得见**卡在冲突上的草稿**：
  那是**人还欠的一个决定**，不写进票据队列就等于没人看见。
  这个缝是用**反证**查出来的：故意把「什么都不是丢失」改成
  「什么都是丢失」，门禁必须报红。

- **第 18 项门禁 `conflict_guard`** —— v0.7.72 让改动先进草稿，
  但草稿**从来不核对**它当时对着的那份文件：人等草稿的这段时间里
  改了文件，批准下来会**把人的新内容静默覆盖掉**。
  两个 agent 抢一个文件时，这就是**丢失更新** —— 最高频的事故。
  现在草稿**记住基线哈希**，写回之前对一下：
  对不上就**拒绝写盘**，磁盘上人的新内容**原样不动**，
  草稿**继续等着**。`mas drafts approve --force` 才覆盖 ——
  明知故犯，得是人干的。
  `mas drafts conflict` **不动任何东西**地查「这份草稿还能安全批准吗」。
  冲突时报**期望哈希与实际哈希**，不只是一句「不行」。
  还有一处真缝被补上了：**票据是一次性的**，
  以前兑票在前、冲突在后的话，一次失败的批准会**烧掉人给的那一个决定**。
  现在是**看票（不花）→ 查冲突 → 花票 → 写回**，
  票不对、票过期、文件动过，**票都还在**。
  这个缝是用**反证**查出来的：冲突路径的**十二条变异全部被捕获**
  （其中三条就是**门禁自己的判定被改坏**）。

- **第 17 项门禁 `ticket_approval`** —— v0.7.73 的**人类令牌**是一把
  **常备钥匙**：贴给 agent 一次，它就能批准**所有**草稿，**永久有效**。
  在多智能体团队里这是一条真缝 —— 提议的人拿到了确认的权力，
  **分权就白分了**。
  现在令牌之外多了一条路：**票据**。
  `mas drafts ticket <草稿编号>` 用人类令牌签一张票据，
  它**只对那一个草稿**生效、**用一次就烧掉**、**15 分钟过期**，
  **盘上只存哈希** —— 票据库被复制走也带不走一张能用的票据。
  票据还**只能签一个动作**：给驳回签的票据批不了批准。
  `mas_draft_resolve` 认票据了，票据不对时报错会说明到底卡在哪：
  `ticket_already_used` / `ticket_expired` /
  `ticket_bound_to_another_draft` / `ticket_action_mismatch`。
  这个缝是用**反证**查出来的：票据路径的**十三个变异全部被捕获**
  （其中四个就是**门禁自己的判定被改坏**）；
  而且这道门禁的判定被**独立实现了两遍**，**改坏判定函数自己也会被抓**。

- **第 16 项门禁 `mcp_draft_approval`** —— v0.7.72 把「改动先进草稿」
  落成了**引擎级**规则：循环改你已有的文件，先记草稿，你批准才写回。
  **但那条规则只管住了循环自己** —— 一个会说话的 agent 拿着 MCP 工具，
  仍然能直接改你的文件，**根本用不到草稿这一层**。
  现在**提议**和**批准**分成了两个动作：
  agent 经 MCP **提**一份改动（**原文件一个字节都不动**，草稿带 diff 和理由），
  **批准要人类令牌** —— `mas drafts token` 拿到的那个。
  **agent 提的草稿，agent 自己批不了**：令牌不对、空令牌、没令牌，一律拒绝，
  而且**原文件不动**。
  令牌的作用**不是保密，是分权**：提议的人不确认自己提的东西。
  agent 也能 **`mas_files`** 索引工作区、**`mas_preview`** 预览文件，
  **提改动之前先看清楚要改的是什么**。
  而且这两个工具**认工作区**：agent 说「索引这个工作区」「预览里面的这个文件」，
  工具就真的去读那个文件夹 —— 不是去读进程当前目录，
  也不是回一句「这里什么都没有」。
  这个缝是用**反证**查出来的：MCP 草稿路径的**十二个变异全部被捕获**（其中两个就是**路径解析被改坏**）；
  而且这道门禁的判定被**独立实现了两遍**，**改坏判定函数自己也会被抓**。

- **第 15 项门禁 `draft_approval`** —— 前面每一版都在**读**和**写**上使劲，
  却**没有一版问过「谁来批准这次改动」**。
  一次运行想改一个**已经存在**的文件时，引擎就真的把它覆盖了 ——
  **你原来写的东西没了，而你甚至没看见它被改成了什么**。
  最危险的时刻不是循环跑不动，而是它**跑得太顺**。
  现在改动**先进草稿**：**原文件一个字节都不动**，草稿带一份**可读的 diff**，
  你说「批准」才写回，你说「驳回」就**一个字都不改**。
  **新建文件照常直接写** —— 一个还不存在的文件，没有东西可以毁掉。
  **机检读的是草稿里的新字节**，否则循环会陷入
  「提改动 → 读旧文件 → 判不通过 → 再提改动」的死结，**永远闭不上**。
  草稿住在**工作区里**（`.mas-drafts/`），文件夹被复制时草稿跟着走 ——
  丢掉的草稿，是一份**永远没法被人审阅**的改动。
  草稿路径的**八个变异全部被捕获**。
- **第 14 项门禁 `resource_index`** —— 前面每一版都在**读**和**写**上使劲，
  却**没有一版回答过最朴素的那个问题**：「**这个循环到底产出了什么？**」
  答案是散的：`mas artifacts` 只列**交付物**，`mas status` 只讲**机器**，
  一个既不是交付物、也没有任何验收标准提到的文件 —— 一份佐证 PDF、一个字体、
  一个 3D 模型 —— **根本没有地方列**。
  现在有了 **`mas files`**（一个文件夹就是一份索引）和 **`mas preview`**（读一个文件）。
  索引保证三件事：**每个文件都进**（包括没人检查的辅助文件）、
  **格式按文件自己的头判定**（`.txt` 装的 PNG 报 `image`）、
  **读不开的文件要列出来并写明原因**（不能悄悄丢掉）。
  事实从文件里读出来：PDF **页数**、字体**家族**、音频**时长**、
  模型**三角面**、压缩包**成员数** —— **只报文件头能证明的事实**。
  纯标准库，**只读、不执行任何东西**，所以它在任何装了 Python 的机器上都能跑。
  索引路径的**七个变异全部被捕获**。
- **第 13 项门禁 `binary_write`** —— v0.7.70 让机检**读得进** PDF，
  可是**循环写不出** PDF：写文件协议只能带**文字**，worker 的输出是一个字符串，
  引擎把它写成 UTF-8。交付物是 PDF 的任务**没有办法产出 PDF** ——
  于是**读得进**的那门本事**没有东西可读**。
  现在写文件协议支持 **`encoding="base64"`**：`<<<MAS:FILE path="out/report.pdf" encoding="base64">>>>`，
  引擎把块里的内容**解码成真正的二进制**再落盘。
  不写 `encoding` 的块**仍旧按文本写**，旧协议一点没变。
  第 12 项门禁看**读**，**没有任何门禁看「写」** —— 这个缝是用**反证**查出来的，
  写路径的**七个变异全部被捕获**。
- **第 12 项门禁 `content_check`** —— 前面所有文件级机检搜的都是**文件的字节**，
  但 PDF 把文字放在**压缩流**里、`.docx` 本身是个 **zip 包**、图片里根本没有文字 ——
  于是**交付物是 PDF 的任务，机检根本判不了**，标准落回模型手里，
  模型一句「looks fine」就关上了一条**没人查过的标准**。这是**假闭合**。
  现在机检能**读进文件里**：PDF 解压取字、Word 解包剥标记、格式按 **magic bytes** 判定。
  **读不出内容判「不通过」而不是「未判定」** —— 判「未判定」的话它会回到模型手里被一句话关掉。
  内容路径的**九个变异全部被捕获**。
- **第 11 项门禁 `folder_handoff`** —— v0.7.67 让人写的交付物能闭上环，但**只接受一段文字**。
  而这个循环真正跑的任务写的是**文件**，验收标准也是**读磁盘的文件机检**。
  人改这样的任务，做的是**打开文件改**；旧的文本交回却要求人把文件内容粘进命令行 ——
  这是**第二份工作**，而复制的这一份正好是容易出错的那一份。
  第 9 项门禁看的是**引擎**，第 10 项门禁看的是**报告**，**没有任何门禁看人这条路**。
  这个缝是用**反证**查出来的：文件夹交回的六个变异（机检读错文件夹 / 交付物丢文件正文 /
  被拒时也写交付物 / offer 只取第一个文件 / 交付物落到第二个文件夹 / 票据忘了工作区）
  **一个都没被捕获**。现在第 11 项门禁看着这条路，**六个变异全部捕获**。
- **文件夹才是交付单位** —— 人在**原地方**改文件，交回的是**那个文件夹**，
  交付物由文件自动生成，**不用手工粘贴**。
- **草稿语义** —— 判定用 overlay（盘上现在的字节），**判定前不写盘**；
  **被拒时磁盘原样不动**，不写交付物 —— 你改的工作不会被丢掉。
- **通过了才写回** —— 验收过的交付物落在**人改过的那个文件夹里**，不是第二个文件夹。
- **票据就是入口** —— `mas tickets` 直接印「在这里改：<工作区>」与
  「改完交回：mas handback <id> --workspace "<path>"」。
- **新命令 `mas handback --workspace <dir>`** —— `--file` / `--text` 的旧行为不变。

- **第 10 项门禁 `handback_report`** —— v0.7.67 让人写的交付物能闭上环，
  但**报告里看不见**。第 9 项门禁看的是**引擎**（人写的东西能不能闭合），
  **没有任何门禁看报告层**。这个缝是用**反证**查出来的：报告层的六个变异（
  丢掉 closed、决策改名、不读 handback 分数、回到「所有成员都闭合才算」、
  忘掉是哪一次闭的、来源恒说机器）**一个都没被捕获**。
  现在第 10 项门禁看着这一层，**六个变异全部捕获**。
- **报告里的「来源」列** —— 问题概览与运行概览两张表都多了一列：
  `🤝 人工完成` / `🤖 机器闭环`。
- **人工交付物有自己的验收小节** —— 逐条列出通过/未通过，
  并附上**人的意见**；**被拒的交付物也会被报告出来**（失败不许消失）。
- **`mas replay` 也看得见人** —— 运行头部打印来源与人工意见。

**v0.7.67 加了什么**（**人写的交付物也能把闭环关上**）：

- **第 9 项门禁 `handback_closes`** —— 工单队列以前只有**一个**答案：
  「续跑，让机器再试一次」。但真实世界里最常见的情况恰恰是**人已经把答案
  写好了**，却没有地方放。这一版把这条回路补上，**被门禁当场抓住**。
- **走的是同一套验收标准** —— 标准从工单上取回来，**就是这件事当初写下的
  那套**。带机检的标准由机检说了算，**评审员不能推翻它**。
- **不合格的接回不会假装闭合** —— 失败项写回票上，工单**留在人工队列里**。
- **评审挂了也不会毁掉人写好的交付物** —— 机检在调用评审之前就判完了。
  **这条是被探针当场抓出来的**：原来的捕获范围比主循环窄，provider 抛一个
  普通异常就会让手写的东西消失。
- **新命令 `mas handback`** —— 队列里每个待处理工单都会印出这一行：

  ```
  交回交付物：mas handback T-20261116-075440-8aae --file <你的文件>
  ```

- **MCP 工具 `mas_handback`** —— Codex / Claude 可以直接把交付物交回闭环。

**v0.7.66 加了什么**（**接手的运行看得见上一轮的成果，并且能换一份预算**）：

- **第 8 项门禁 `takeover_handover`** —— 上一版让票据带齐了上下文，但
  **接手的运行根本用不上那份上下文**。这一版把「接手」这件事本身修好，
  并且让它**被门禁当场抓住**。
- **四个真缺陷（先量出来，再修掉）**：
  1. **token 触顶被误报成「循环自己闭不上」** —— 预算触顶的运行被归到
     `loop_escalation` 队列里，人会去查一个卡住的 worker，而真正的原因是
     预算太小。**现在预算触顶就是 `budget_exhausted`。**
  2. **接手的运行看不见上一轮的交付物** —— 续跑把尝试计数重置成 1、
     问题清单清空，于是 worker 被要求「从零写」，写出来又是同样的东西。
     **现在上一轮的交付物会原样交给接手的 worker。**
  3. **预算触顶的票据，续跑会立刻再次触顶** —— **现在三条上限都能按
     这一轮单独给**，且不会漏进同一引擎的下一轮。
  4. **屏幕上那句「接着干」是死路** —— **现在续跑命令会带上更宽的那一条
     上限**（外加一个下限，保证宽出来的预算真的能干活）。
- **续跑命令的旗标写对了** —— 屏幕上的命令必须**能粘贴就能跑**的：
  `--max-tokens` 而不是 `--max_tokens`。这条是被门禁当场抓出来的。
- **门禁被反证过六次** —— 交付物被藏起来、预算覆盖被丢掉、token 触顶又被
  归成升级、放宽逻辑被拆掉、接手的运行拿不到东西、票据丢掉预算，
  **六次全部被捕获**。全程离线：不要端点、不要 key、不要网络。

**v0.7.65 加了什么**（**没闭合的运行必须留下人能接手的东西**）：

- **第 7 项门禁 `handover_ticket`** —— 引擎答应过三件事：跑不出来就升级、
  升级就开票据、票据带够让人接回来的上下文。前两件看得见，**第三件从来
  没人守**。**现在它会被门禁当场抓住。**
- **票据不是留言，是交接单** —— 门禁把票据**真的用起来**：拿票据里的验收
  标准喂回 `engine.resume()`，循环必须真的闭合才算过。
  **verifier 在这个检查里永远说 fail**，所以闭环只能靠票据带回来的机检
  —— 模型糊弄不过去，票据丢了标准就接不回来。
- **当场抓到一个真 bug** —— 票据字段在两条升级路径上不一致（停滞路径少了
  `status`）。**根因修好了**：两条路径现在共用一个 payload 构造器，
  **票据只有一个形状**。
- **门禁被反证过六次** —— 票据丢标准、标准只剩散文、票据丢产物、票据丢失败
  原因、票据被提前标记已处理、票据不落盘，**六次全部被捕获**。

**v0.7.64 加了什么**（**并行选优必须是确定的**）：

- **第 6 项门禁 `parallel_pick`** —— 引擎写着“赢家是确定的”，但这条
  不变量没人守：候选池按**完成顺序**回填、平局反向打破、或者排名把
  **正文长度**排在机检之前，都会换一个交付物交出去，而运行仍然报成功。
  **现在它会被门禁当场抓住。**
- **赛次是排好的，不靠运气** —— 检查用事件链把三个候选的完成顺序钉死
  成逆序，所以结果跟调度器的心情无关。
- **三个候选让选优必须真干活** —— `direct` 与 `thorough` 通过的机检数
  和正文长度**完全相同**，只能靠最小序号分胜负；`concise` 更长但一条
  机检都过不了。**谁赢是排出来的，不是抢出来的。**
- **门禁被反证过四次** —— 候选池按完成顺序回填、平局反向打破、长度压过
  机检、赢家取“先跑完的那个”，**四次全部被捕获**。

**v0.7.63 加了什么**（**装完即自证**）：

- **安装脚本现在会自己跑门禁** —— `install.ps1` 与 `install.sh` 装完后
  自动执行一次 `mas selftest --iterations 20`。**装坏了当场看得见**，
  不用等到第一次真跑任务才发现。
- **两项新门禁**（全程离线：不要端点、不要 key、不要网络）：
  - `mcp_handshake` —— MCP 握手能应答，广告出来的工具都答得上来，
    调不存在的工具会被拒绝，跑完一轮服务还活着；
  - `stream_folds` —— 两种事件流方言（chat 分片 / Responses 事件）
    折叠成同一段正文，且 `total_tokens` 不丢。
- **门禁被反证过四次** —— MCP 不再广告 `mas_run`、MCP 不再回应 `ping`、
  SSE 折叠把用量扔掉、SSE 折叠丢一段正文，**四次全部被捕获**。

**v0.7.62 加了什么**（**隔失门禁**）：

- **两个真实的少见结果进了 CI** —— 中继拒绝路径时没有排空请求体（v0.7.59），
  以及已计费的失败调用丢了用量。两者都是“测一次看不出来”的少见结果 ——
  一次性的测试本来就抹涂不了它们。
- **新增 `mas selftest`** —— 把“把一条路径打上千次看不变量”这个方法变成一条命令：
  ```bash
  mas selftest                    # 打 200 次
  mas selftest --iterations 500   # 打 500 次
  mas selftest --only relay_refusal
  ```
  全程离线：自己在本机起回环表服务器，不要端点、不要 key、不要网络。
- **门禁本身被反证过** —— 三个回归分别把中继、用量计账、工作区放坏打破，
  三次全部被捕获。**一个只终达入不报的门禁不叫门禁。**

**v0.7.61 加了什么**（**产物不再散落**）：

- **跑完的产物，说没就没** —— v0.7.60 之前，一次运行写出来的文件落在系统临时目录里
  （`C:\Users\...\Temp\tmpXXXXX\ws\notes\...`），进程一结束就没了；
  而交付说明又单独存到 `<state_dir>/artifacts/<run_id>.md`。**两样东西分居两地。**
- **现在每次运行有自己的文件夹** —— `<state_dir>/artifacts/<run_id>/`，
  交付说明写作 `deliverable.md`，worker 写的所有文件也在里面。**交付什么、生成什么，
  一眼看完，跑完也留得住**；
- **你指定的工作区照旧优先** —— `mas run --workspace ./my-project` 仍然写进你指定的目录，
  规则只有一条：**你点名的地方，就是这次运行的工作区**；
- **文件检查也用同一个工作区** —— 以前文件级判定读的是「配置里的工作区」，
  而文件写进的是「临时目录」，两边对不上；现在读写同一处，判定终于对得上；
- **账本和报告都记下工作区** —— 账本多 `workspace` 与 `files` 两栏，
  `mas artifacts` 会告诉你「另外生成了哪些文件」。

**v0.7.60 加了什么**（**账本也开始说人话**）：

- `mas ledger` 以前打印的是引擎自己的词：`closure   iter=1 pass close`。
  现在是 **`验收 (closure) 第1轮 通过 (pass) 闭环了 (close)`** —— 人话在前、原名在后：
  人读得懂，脚本也照样 grep 得到。

**v0.7.59 修了什么**（**relay 不再偶发弄断连接**）：

- v0.7.58 之后 CI 抓到一个偶发失败：relay 拒绝一个未知路径时 **没有读掉请求体**。
  HTTP/1.1 保持连接，残留字节留在 socket 里，下一次请求从请求体中间开始读，
  客户端于是收到「连接被中止」，而不是本该拿到的 404。
- 本地压测 200 次能复现约 9 次 —— 这是真 bug，不是机器慢。现在拒绝（401 / 404）
  之前先排空 body，拒绝后显式关闭连接，并补了两条回归测试。

**v0.7.58 加了什么**（**为什么没闭环，也要说人话**）：

- **没闭环的那一行，还是引擎在自说自话** —— v0.7.57 把「判定」换成了人话，
  但对初学者最要紧的那一行 —— **这轮为什么停** —— 还是这样：
  ```
  [FAIL] closure  未闭环（状态 budget_exhausted）：budget: reached max_iterations=1
  - 未闭合原因：budget: reached max_iterations=1
  ```
  `budget: reached max_iterations=1` **只说了「有件事发生了」，没说「发生了什么」**：
  没说是轮数用完了、原本有几轮、现在该怎么办；
  **不认识 `max_iterations` 的人，这一行字等于没写**；
- **现在每种停下来的理由都换成人话，并且点名该调哪个旋钮**：
  ```
  状态：预算用完了（没跑完，已停下来你接手）（budget_exhausted）
  说明：预算用完了：这轮只允许跑 1 轮，已经跑满了。
    要继续加轮数：mas init --max-iterations <更大的数>
  ```
- **理由出现在每一个你会去看它的屏幕上** —— `mas run`、`mas resume`、
  `mas tickets`、`mas report`、`mas replay`；
- **账本仍然存英文** —— **理由是审计记录的一部分，脚本要按它取值**，
  所以翻译只发生在显示层；
- **`mas init` 补上了理由点名的那三个旋钮** —— `--deadline-sec`、
  `--max-tokens`、`--stagnation-limit`；
  **在这之前，理由让你「把上限调高」，而那个上限根本没有开关**；
- **`mas replay` 现在每一步也说人话**，不只是导出的表格；
- **修掉三个真 bug**：
  - **翻译器翻译的是引擎从不写的字符串** —— 它认得 `budget: exceeded
    max_tokens=`，而引擎真正写的是 `budget: tokens {used} >= {limit}`
    和 `budget: wall-clock budget exhausted after {n}s`，
    **最常见的两种停法，恰好是它一个都没认出来的**；
  - **比较方向写反了** —— `length 48 < 999999` 被翻成
    「正文只有 48 字，**超过了** 999999 字」，**和事实完全相反**，
    **读者会找错问题**；字节数那一处也一样；
  - **理由那一行读不成一行** —— 句子和「调高上限」的提示被换行连在一起，
    **一行式的显示（比如工单队列）会被撑断**；
- **配套回归测试 31 个**，**二十八层改动全部单独回退反证**，对应测试均会挂。

**v0.7.57 加了什么**（**判定结果也要说人话**）：

- **判定本身还在用引擎的词汇说话** —— v0.7.56 把「你写的标准」带回来了，
  但判定那一行仍然是引擎在自说自话：
  ```
  [PASS] c1 (rule) all required strings present
        要求：正文里要出现「结论」
  ```
  **两行说的是同一件事，用的还是你没写的那门语言**；
- **现在判定也换成人话，并且说清楚判定来自哪里**：
  ```
  [PASS] c1 机检：所有要求的字符串都在
        要求：正文里要出现「结论」
  [FAIL] c1 文件检查：文件的修改时间 2026-11-15T11:17:06+00:00 不晚于 2099-01-01T00:00:00+00:00
        要求：文件 out/report.md 的修改时间要晚于 2099-01-01T00:00:00Z
  ```
- **引擎写出的每一种 note 都有对应的人话** —— 正文太短、缺了哪个字符串、
  正则没匹配上、sha 对不上、哪一节不见了、哪个文件找不到；
- **看不懂的 note 一律原样保留，绝不瞎猜** —— **一句没见过的 note 可能带着
  没人看得见的信息，替它编一句等于把它扔了**；
- **账本仍然存英文** —— **note 是审计记录的一部分，脚本要按它取值**，
  所以翻译只发生在显示层：**一条存下来的运行，还是它存下去那一刻的意思**；
- **`mas resume` 现在和 `mas run` 说同一种话** —— **同一个人、同一个会话里
  读的两个屏幕，不该对「发生了什么」各说各话**；
- **`mas report` 的状态和决策也换成人话** —— 「已闭环（closed）」
  「预算用完了（budget_exhausted）」，**原始值留在括号里，因为那是你 grep 的东西**；
- **工单队列现在报「你写的哪一条标准卡住了」**，而不是引擎自造的编号：
  `c1: unjudged: ...` 变成「正文里要出现「结论」- 没有人判定」；
- **顺手补了三个词表的真实缺口** —— **引擎在没闭环时真正会写的那几个状态
  （`escalated` / `not_closed` / `incomplete` / `deliverable_ok_but_not_closed`）
  和那几种判定来源（`model-missing` / `skipped` / `model`），原来都没词**；
- **配套回归测试 32 个**，**二十三层改动全部单独回退反证**，对应测试均会挂。

**v0.7.56 加了什么**（**验收判定要说人话**）：

- **你写了三条标准，判定却只回了“c1”“”c2”** —— `c1` **不是你的标准，
  是引擎自己受的号**；这样的输出**无法让人知道自己写的哪一条被判定了**；
- **现在每条判定都把标准原话带回来**：
  ```
    [PASS] c1 (rule) all required strings present
          要求：正文里要出现「结论」
    [FAIL] c2 (file) mtime 2026-11-15T11:14:06+00:00 is not newer than 2099-01-01T00:00:00+00:00
          要求：文件 out/report.md 的修改时间要晚于 2099-01-01T00:00:00Z
  ```
- **三种标准三种说法** —— **因为它们本来就是三种东西**：
  - **你写了说明的**（“必须有结论段”）——**保留你的原话**，
    并把机检规则追加在后面，让你看到**到底会检查什么**；
  - **只写了简写**（`contains:结论`）——**直接换成人话**，
    不再把原文回显一遍（原文根本没说新信息）；
  - **可选的标准** ——**标注出来**，让你分得清「没阻塞」和「没查」；
- **这句话也存进账本** —— `mas report` 和 `mas report --json` 都拿同一句标准原话回来；
- **顺手修了一个真实缺陷** —— 文件类检查曾把参数当成 Python 字典打印出来：
  ```
  文件 {'path': 'out/report.md', 'value': '2099-01-01T00:00:00Z'} 的修改时间要晚于 {...}
  ```
  现在路径和期望值各走各的位置，**不再泄露字典**；
- 配套回归测试 **20 个**，**九层改动全部单独回退反证**，对应测试均会挂。

**v0.7.53 加了什么**（**找不到就说找不到**）：

- **四个命令会把「不存在」说成「账本是空的」** —— **而这恰恰是初学者
  最容易遇到的那句话**；打错一个运行 ID、或者工单指向别的机器上的运行，
  **落到的都是同一句假话**；
- **实测：账本里明明有 22 条记录** —— `mas replay --run-id R-不存在`
  回答「账本为空，没有可回放的运行。」；`mas export --run-id R-不存在`
  **只吐一个表头**；`mas report --run-id R-不存在` 在一份完整的报告里
  写「账本为空，暂无运行记录。」；
- **现在直接告诉你账本里有什么** —— **这样你就能改对，而不是怀疑
  「这活儿到底干过没有」**：
  ```
  没有找到运行 R-does-not-exist。
    账本里有这些运行：R-7e4af659ba
    用 mas status 看一眼有哪些运行，用 mas replay --run-id <运行ID> 回放其中一次。
  ```
- **「找不到」和「空的」从此是两回事** —— **账本真的是空的，它就还叫空；
  账本里有别的运行，它就报别的运行**，**再也不会混为一谈**；
- **`--json` 的「找不到」也说得清楚** —— **`{"error": "run_not_found",
  "found": false}`，退出码 2**，**脚本不用去解析人话**；
- **`mas export --json` 以前是个谎言** —— **它收下 `--json` 却完全不理，
  给脚本的还是 Markdown 表格**；现在给的是真正的数据（行、条数、
  以及 Markdown 渲染），**而且文字版和 JSON 版保证一致**；
- **空账本不再只给你一个表头** —— **刚装完就跑 `mas export`，
  现在会告诉你账本是空的、并且告诉你下一步该干嘛**（`mas run` /
  `mas demo`）；
- **顺手修好了 Linux 构建** —— **v0.7.52 有条新测试断言
  `Ledger` 和 `ledger` 是同一个目录**，**这在 Windows 上成立、
  在 Linux 和 macOS 上不成立**（**它们是两个不同路径**），
  **所以本机全绿、Linux 挂**；**守卫是对的，测试是错的**——
  现在测试改成陈述「平台自己的拼写规则」，**而不是假设某一个平台**；
- 配套回归测试 **13 个**，**八层改动全部单独回退反证**，对应测试均会挂。

**v0.7.52 加了什么**（**你的账本不会自己消失**）：

- **账本是循环工程唯一的证据，而它会自己消失** —— **不报错、不告警，
  就是没了**；工具报告「空历史」，而活儿明明干过；
- **真实复现：你的 `~/.mas/config.json` 指向了系统临时目录** ——
  `C:\Users\ADMINI~1\AppData\Local\Temp\mas-handover-n2d4xr0z`，
  **是某次测试用 `mkdtemp` 建的**；系统清理临时目录时账本跟着一起走；
- **两道守卫同时失守** —— ① 守卫只认 `tmpXXXXXXXX` 这一种形状，
  **而项目自己的测试全用 `mas-handover-` / `mas-state-` / `mas-ledger-`
  这些前缀，全绕过去了**；② 守卫比的是字符串，
  **同一个目录写成 `ADMINI~1` 和 `Administrator` 就不认了**；
- **现在看的是随机尾巴，不是前面那个词** —— 临时目录下、
  **分隔符后跟着八位随机字符**的目录一律当成一次性的，不落盘；
  **而你自己命名的目录（比如 `%TEMP%\myproject`）仍然是你的，保留**；
- **现在比的是路径，不是字符串** —— 短路径、尾斜杠、大小写，
  **同一个目录就是同一个目录**；
- **你亲自指定的目录，一定保留** —— `mas init --state-dir <目录>`
  **哪怕它就在临时目录里也照写**，因为**人打出来的路径就是决定，
  守卫是拦注入，不是替人做主**；
- **环境变量注进来的目录绝不落盘** —— `MAS_STATE_DIR` 只决定
  **这一次**跑哪儿写，不该变成安装的永久事实；
- **测试之间不再互相污染** —— 以前有测试模块把 `MAS_STATE_DIR`
  指向临时目录后忘了还原，后面的测试就把那堆垃圾当成了用户配置；
  **现在守住 `unittest.TestCase.run()` 这个唯一入口**，
  **标准库 runner 和 pytest 都覆盖，子类重写 `setUp` 也拦得住**；
- 配套回归测试 **22 个**，**七层改动全部单独回退反证**，对应测试均会挂。

**v0.7.51 加了什么**（**回放也要说人话**）：

- **回放是最后一块没改口的地方** —— **v0.7.50 让报告开始数「问题」，
  可 `mas replay` 还在平铺账本**；**而回放恰恰是「我接下来该干嘛」
  最该被看懂的那一屏**；
- **以前两次尝试看不出来分界** —— **同一个问题试两次，
  账本上是十四行平铺记录，**「一个问题试了两次」和
  「两个问题各试一次」**在屏幕上长得一模一样**；
- **现在回放按运行分块** —— **每次运行一块，写清状态、轮数、
  token 花费，**以及**它到底在干什么**；
- **没闭环的运行现在直接给出接手入口** —— **以前回放完就结束了，
  明明有张票在等着，却不说从哪儿接着干**；现在直接给命令：
  `→ 接着干：mas resume T-... --note "你的意见"`；
- **回放末尾按问题汇总** —— **一行一个问题**，
  标着「待处理 / 已解决」、几次尝试、几轮花费，
  **而不是一行一次尝试**；
- **报告改成先讲问题，再讲运行** —— **运行表会把一个问题列成两行**，
  **先读运行表的人会以为有两件事要做**；现在报告开头就是「问题概览」表；
- **`mas replay --json` 也说同一套话** —— **新增 `runs` / `threads` /
  `problems_open`，每条 step 还带上自己的 `run_id`**；
- 配套回归测试 **20 个**，**八层改动全部单独回退反证**，对应测试均会挂。

**v0.7.50 加了什么**（**一次问题一条线**）：

- **账本数的是「运行」，人工队列数的是「问题」—— 以前这俩对不上** ——
  **一次运行升级成工单，之后每续跑一次就在同一个问题上多一次运行**，
  **但状态页和报告数的是运行**；
- **这是真机实测出来的** —— **续跑一次之后，状态页写着「未闭环 2」，
  人工队列里却只有一张票**，**报告和队列对不上：人根本分不清
  「一个问题试了两次」和「两个问题各试了一次」**；
- **现在报告和状态页同时数「问题」** —— **两次运行、一个问题**，
  **一眼就看得出来**；`mas status` 里是「待解决问题：1 个（共 1 个问题）」，
  `mas report` 里也是同一套口径；
- **工单记的是这个问题的总账** —— **不是最后一次试了几轮，
  而是历次续跑累计烧了多少轮**：以前一张烧了十五轮的票写着「已尝试 5 轮」；
- **工单点名它覆盖了哪几次运行** —— 续跑两次就列两次，
  **人工队列和账本之间有了一条真正的接缝**；
- **账本把线程记下来了** —— 每次续跑都记下自己属于哪张票，
  **连续跑成功闭合的那次也算**——**它不是无主的运行，它是这个问题的答案**；
- **已解决的工单指向真正闭合它的那次运行** —— **人拿到的那份成果物在票上**，
  **不用翻失败史去找**；
- 配套回归测试 **20 个**，**八层改动全部单独回退反证**，对应测试均会挂。

**v0.7.49 加了什么**（**没闭环的运行必须留下工单**）：

- **以前只有「升级」这一条路会开票** —— **撞了迭代上限的、超了时间预算的、
  烧光了 token 的、后端直接挂掉的**，这四种收尾**全都不留任何东西**；
- **这是真机实测出来的，不是猜的** —— 真模型撞上 `max_iterations`，
  **交付物躺在硬盘上，却一张工单都没有**：状态页写着「未闭环 1」，
  **人工队列是空的**——**循环报告了一件没干完的活，却没给任何人接手的入口**，
  **这是闭环最关键的最后一环断了**；
- **现在任何没闭环的收尾都开一张票** —— **预算耗尽有票、后端报错有票**；
- **票会说明是哪一类失败** —— 「撞上限」和「后端挂了」要的是两种人的反应：
  **一个是加预算，一个是修 endpoint**，**一张把它们混为一谈的票没法告诉人到底出了啥**；
- **票里带着原因、目标、验收标准、最后那版产物** —— **不用重跑一遍就能决定要不要续**；
- **闭环了的运行照样不留票**，**续跑也不会开第二张**——**一个问题不重复上队列**；
- 配套回归测试 **14 个**，**七层改动全部单独回退反证**，对应测试均会挂。

**v0.7.48 加了什么**（让**工单自己解释自己**）：

- **工单以前只是一串编号** —— `mas tickets` 以前每张只给一行
  「T-2026...  waiting_human  loop_escalation  标题」，
  **人根本看不出卡在哪、为什么放弃、接着该敲哪条命令**；
- **现在每张工单自带说明书** —— **任务目标、对应运行、没过的检查项、
  放弃的原因、已经试了几轮、最后产物多大、人工意见、以及那条
  `mas resume` 续跑命令**，**全在一张工单里**；
- **空命令不再装死** —— `mas ledger` 在空账本上**以前一个字符都不打**，
  看起来像坏了而不是像空的；现在它**说账本是空的、账本文件在哪、
  该敲哪条命令把它填上**；`mas tickets` 同理，**并且指向 `mas demo`**；
- **账本末尾告诉你账本在哪**、总共多少条；
- **已解决的工单单独列出** —— **带着人工的决定和留言**，
  而且**不再提示「接着干」**——**一张已解决的工单不该催你再跑一遍**；
- **状态页点名的是问题，不是编号** —— 三张在等的工单，
  **光看编号分不出来**，现在工单行直接带上任务标题；
- 配套回归测试 **23 个**，**九层改动全部单独回退反证**，对应测试均会挂。

**v0.7.47 加了什么**（让 **`mas status` 说人话**）：

- **状态页以前是一坨 JSON** —— 对机器没问题，**对这个项目要照顾的初学者
  完全没用**：JSON 只说「entries: 27」「waiting_human: [T-...]」，
  **从不说「你有 2 份交付物、1 个工单在等你」**。
  **人读不懂的状态页不是状态页**；
- **现在是一页人话** —— provider 和模型、状态目录、账本大小、
  **跑过几次 / 闭环几次 / 没闭环几次**、**交付物清单（标题、字数、路径）**、
  **在等你的工单**、还有预算；
- **状态页会数活儿** —— **「entries: 27」说的是日志有多少行，
  「3 次运行、2 次闭环」说的是活儿干得怎么样**，**这是两回事**；
- **状态页点名待办工单** —— **每张工单直接给出该敲的那条
  `mas resume` 命令**，**找不到的升级就永远不会被处理**；
- **交付物文件被删了会明说**，**不假装一切正常**；
- `--json` 照样给机器读的结构，**而且多了 `runs` 和 `artifacts` 两段**；
- 配套回归测试 **11 个**，**七层改动全部单独回退反证**，对应测试均会挂。

**v0.7.46 加了什么**（让**账本知道这次运行在干什么**）：

- **账本记了运行的一切，唯独没记「在干什么」** —— 这是**真机实测出来的**，
  **不是猜的**：真模型跑完，**账本里根本没有任务目标的痕迹**。
  回放只能说出「某次运行闭环了、241 字、哈希 fe8848013b」，
  **读者无从知道这是上手指南还是第 12 周周报**；
  **mock 后端掩盖了这一点**，因为它的规划摘要**恰好复述了目标**，
  **只有接真模型才露馅**；
- **结束条目现在记 `goal` 和 `title`** —— **连自己的任务都叫不出來的账本，
  只是一份调用日志，不是工作记录**；
- **报告直接说「这次在干什么」** —— 概览表**多了「任务」列**，
  每次运行的段落**开头就是「- 任务：…」**；
  **一张只有运行 ID 和哈希的表是审计日志，**同时写着任务的表**才是人能读的报告**；
- **`mas artifacts` 也说人话** —— 列表**先给任务、再给路径**，
  JSON 每行带 `title` 和 `goal`；
  **一串路径不是能用的清单**，**用户得逐个打开才知道那是什么**；
- **任务里带竖线不会撑坏表格** —— 概览单元格**转义 `|`**，
  「写 A | B」这种目标**不会把表格行劈开**；
- 配套回归测试 **11 个**，**七层改动全部单独回退反证**，对应测试均会挂。

**v0.7.45 加了什么**（让**用户找得到交付物**）：

- **成果物存下来了，却没人找得到** —— v0.7.44 让交付物**活过进程**，
  但 CLI **只打 1500 字的预览和账本路径**，**从不说那个能打开的文件在哪**，
  而且**根本没有任何命令能列出以前的交付物**。
  初学者跑完一轮、关掉终端、一周后再回来，
  **连「我到底拿到了什么」都问不出来**；
- **`mas run` 和 `mas resume` 直接点名文件** —— 输出里多一行
  **「交付物：路径」**。**resume 尤其关键**：**被升级的运行本来就该走 resume**，
  **它的结果恰恰是最容易丢的那一个**；
- **新增 `mas artifacts`：列出本机产出的全部交付物** ——
  **一行一份**，**运行 ID、状态、字数、轮数、路径、文件还在不在**；
  `--run-id` 可以只看某一次运行；
- **`--json` 给出结构化版本** —— `{"artifacts": [...], "count": N}`，
  每行带 `run_id`/`status`/`path`/`exists`/`chars`/`tokens`/`iterations`/`finished_at`；
- **文件被删了会明说** —— 那一行标 **「缺失」**，底部再给一句警告，
  **而不是把打不开的行悄悄藏起来**；
- **什么都没有时告诉你下一步** —— 直接说
  **「还没有任何交付物。跑一次 `mas run` 就会生成。」**；
- 配套回归测试 **15 个**，**六层改动全部单独回退反证**，对应测试均会挂。

**v0.7.44 加了什么**（让**闭环的成果物真的留下来**）：

- **闭环成功了，成果物却没了** —— 这是**比报告措辞更致命的问题**。
  交付物以前**只活在引擎的内存里**：账本记它的哈希，报告打它的哈希，
  **进程一退出，活儿就没了**。初学者跑完 `mas run` 看到绿灯、
  关掉终端，**拿到的是一句结论，不是一份成果**。
  现在每次运行**都把最终交付物写到磁盘**：
  `<state_dir>/artifacts/<运行ID>.md`，**和账本放在一起**，
  **一次运行一个文件**，**用运行 ID 命名**；
- **路径进账本** —— 结束条目现在记 `artifact_path` 和 `artifact_chars`，
  **回放时能直接找到那份成果**，不用猜；`mas run --json` **也带上路径**；
- **报告直接点名文件** —— 以前报告只打一串哈希，
  **哈希是指纹，不是能打开的东西**。现在写：
  **「交付物已保存：`路径`（N 字，可直接打开取用）」**；
- **写文件失败不会把运行带崩** —— 活儿已经干完、东西在内存里，
  **因为权限问题丢了文件却报成闭环崩溃**，那是更糟的谎。
  **写入是尽力而为**，**运行本身不受影响**；
- 配套回归测试 **9 个**，**六层改动全部单独回退反证**，对应测试均会挂。

**v0.7.43 加了什么**（让**报告讲出整个循环**，而不只是最后一轮）：

- **报告以前只显示最后一轮** —— 引擎**每轮判定都写一条账本**，
  每轮失败**还写一条反思**。但报告**只读了最后一条判定**，
  于是**改了三轮才成功的运行，在报告里只剩最后一个「PASS」**，
  **前面两轮怎么败的、怎么改的，全看不见**。
  现在报告有**「循环过程（共 N 轮）」**：**一轮一行**，
  **判了什么、决策是什么、过了几条、哪条没人判、
  这一轮交付物的哈希**，**读者能看着循环跑，而不是只看结果**；
  **一次就过的运行会直说「1 轮即定」，不假装有过程**；
- **反思终于看得见了** —— reflector 是**唯一说得出「下一轮必须改什么」的角色**，
  以前报告**一个字都没提**。现在**每一轮都带着把它推出来的那条反思**，
  **因果挨着摆**；JSON 报告也新增 `reflections` 列表，
  **带着每条反思的摘要、问题和下一步**；
- **回放里那一行不再是空的** —— 账本回放的 reflector 行
  以前**是空白**（`_short_detail` 根本没有这个分支），
  现在**把反思摘要、问题和下一步都写出来**；
- 配套回归测试 **12 个**，**七层改动全部单独回退反证**，对应测试均会挂。

**v0.7.42 加了什么**（让**「没判定过」不再被写成「没通过」**）：

- **没人判定过的标准，被报告写成了 `[FAIL]`** —— 引擎对每条验收标准
  其实有**三种状态**：**通过了**、**没通过**、**没人能判定**
  （没有机检、评审没给出结论、lead 也判不了）。引擎对第三种状态
  **特别小心**：它**永远不会算通过**，也**不会让它自己闭合闭环**。
  但报告在最后一步**把这三种压成了两种** —— 判失败的列表用
  `not pass` 筛选，**`None` 也被算进去了**。于是**从没被检查过的标准，
  在报告里成了「检查过且被否决」**。现在判失败的列表**只认
  `pass is False`**，那才是「判了，而且没判过」的准确含义；
- **「交付物不合格」这句话本身就是假的** —— 以前只要交付物没通过，
  报告就写「❌ 未通过验收标准」，**哪怕根本没查完**。
  现在**有标准从未被判定时，报告会直说**，并**把标准号点出来**：
  **不是被否决，是压根没查完**；
- **Markdown 里终于有了第三种标记** —— 验收表以前只有 `PASS` 和
  `FAIL`，现在**没人判定的是 `UNDECIDED`**，并注明**不能算通过**；
  报告还会**单列一节**，把判定来源（`rule` / `file` / `model` /
  `lead` / `model-missing`）写出来，**让人看到"为什么没人能判"**；
- **JSON 报告也分得清了** —— `run_summary` 新增 `unjudged` 键，
  和 `failed` / `optional_failed` 并列，**读 JSON 的人不用去猜**；
- 配套回归测试 **8 个**，**每一层改动都做过反证**：单独回退后确认对应测试会挂。

**v0.7.41 加了什么**（让**闭环的最后一环真的接上**）：

- **工单号从来没进账本** —— 运行升级时，引擎会**开一张工单**，
  里面装着交付物、验收标准和历史，`mas resume` 就是**从工单接着跑**。
  但**工单号根本没写进账本**，于是报告除了「escalated」**什么也说不出来**，
  人拿到报告**没法找到那张工单**。现在**工单号入账**；
- **报告能指到工单，也能告诉你下一步** —— 报告现在**报出工单号**，
  并且**说清拿它怎么办**：`mas tickets` 查看，`mas resume <工单号>` 继续。
  升级的运行现在**告诉人去哪动手**，**而不只是说"出事了"**；
- 配套回归测试 **4 个**，**每一层改动都做过反证**：单独回退后确认对应测试会挂。

**v0.7.40 加了什么**（让**「为什么没闭合」不再消失**）：

- **报告从来不说为什么没闭合** —— 引擎**把理由记在结束条目里**，
  v0.7.37 起还专门把停滞的理由写具体了
  （"交付物连续 N 次没有变化"），**但报告只读了状态、把理由扔了**。
  现在报告**直接把理由写出来**；账本里没记理由的升级，
  报告**会说明"未记录具体原因"，而不是闷着**；
- **停滞次数被扔了** —— 引擎**一直在数**「连续几次产出同一个交付物」，
  这个数字**正是区分「worker 卡住了」和「worker 还差一轮」的关键**。
  现在报告**报出来、说清楚、并且在概览表里列出**，
  **扫一眼表格就知道哪次运行卡住了**；
- 配套回归测试 **6 个**，**每一层改动都做过反证**：单独回退后确认对应测试会挂。
  （**概览表那一列一开始根本没测试覆盖**，反证时把这个洞抓出来了。）

**v0.7.39 加了什么**（让**「没闭合」不再是一句糊涂话**）：

- **交付物明明合格，报告却说「没闭合」** —— 引擎其实**分别知道两件事**：
  **交付物过没过验收标准**、**这次运行闭没闭合**，而且**把第一件事记在了结束条目里**，
  但**报告把它扔了**。真机实测过：交付物**通过了全部验收标准**，
  运行因为停滞升级而没闭合 —— 旧报告只写「not closed」，
  **看起来像活儿做砸了**，用户**只能靠猜**是哪一半出了问题。
  现在这种运行**单独报成 `deliverable_ok_but_not_closed`**：
  **这是一个能区分的状态**，不是「没做完」的第三种说法；
- **Markdown 里也没说清是哪一半坏了** —— 现在**直接用话讲**：
  **交付物通过了全部验收标准，但本次运行没有闭合（预算或死线到了）——
  交付物可用，直接取用即可**；
- **「运行没来得及说」不等于「交付物是坏的」** —— 没有结束条目的运行，
  引擎**根本不知道**交付物如何，报告现在报 `deliverable_ok: None`，
  **而不是自信地报 `False`**；
- 配套回归测试 **5 个**，**每一层改动都做过反证**：单独回退后确认对应测试会挂。

**v0.7.38 加了什么**（让**「负责拍板的那个角色」真的上线**）：

- **`lead` 角色一直只有合同，没有接线** —— `roles.py` 写清了 lead 的职责，
  `providers.py` 有它的 mock 分支，README 和 Skill 都把「闭合」写在它名下，
  **但引擎从头到尾没调用过它**：结局是代码里那句
  `decision = "close" if passed else "revise"`。现在**该问的时候真的会问**：
  **有验收标准既没有机检、评审也没判定**时，lead 拿到这些标准和交付物，
  回答 `{"decision", "reason", "judged": [{"id", "met", "note"}]}`；
- **lead 是顾问，不是事实的法官** —— **机检失败是事实**，这种情况
  **根本不咨询 lead**；咨询了也**只能填空**，机检已经定了的标准
  **原样保留**，lead 说什么都不算。**停滞、预算、死线**这些硬约束，
  **引擎保留最终否决权**，lead 关不掉一个已经超预算的运行；
- **lead 说不清楚就什么也关不掉** —— `met` 读不出布尔值时，
  标准**保持未判定**，循环**继续追问**（和「读不懂的评审答复」同一套规矩）。
  lead **坏了**（端点不通、答复是乱码）也只是**不判定**，
  **不会削弱引擎自己的判断**；
- **lead 的调用没进账** —— lead 也是**要花钱的**，但它的角色
  **不在账本的计费名单里**，于是**凡是问了 lead 的运行，报账都少算**。
  现在**入账**，**账本合计 == 运行报告的 token 用量**；
- 配套回归测试 **5 个**，**每一层改动都做过反证**：单独回退后确认对应测试会挂。

**v0.7.37 加了什么**（让**「读不懂的答复」不再能蒙混过关**）：

- **分数不是判定** —— 模型答 `"pass": 0.5`（意思是「这条标准满足了一半」），
  旧代码用 `bool(value)`，**0.5 直接变成 True**：这条标准被记成**完全通过**，
  **循环就这么闭合了**。现在**数字只有正好是 0 或 1 才算判定**，
  其余的**一律算「读不懂」**，而**读不懂就不是答复**；
- **「读不懂」曾被悄悄记成「不通过」** —— 归一化把读不懂的判定折成
  `pass: False`，引擎**分不出**「评审说不行」和「评审没说清」，
  于是这条标准**看起来已经判定过了**，引擎**再也不追问评审**，
  白白烧掉一轮迭代去改一个根本改不动的地方。
  现在**读不懂的标记在入口就被读出来**：标准**保持未判定**，
  **追问一次**，**以回来的那个答复为准**；
- **多评审和单评审对同一份乱码答复处置不一致** —— panel 聚合把标记丢了，
  **配 1 个评审会追问，配 3 个评审却记成「已判定为不通过」**。
  现在聚合**带上标记**，**两条路行为一致**；
- **另一条 JSON 形状一直没人管** —— 归一化有两条路：
  `{"scores": [...]}` 和 `{"criteria": [...]}`。v0.7.12 只在第一条路上
  让顶层总结服从逐条判定，**第二条路漏了**：模型**逐条答 FAIL、顶层答 pass**，
  顶层总结**还是 pass**。现在**两条路规矩一样**；
- **总结不能替读不懂的评审开绿灯** —— 所有判定都读不懂时，
  模型的顶层总结会被保留，于是
  `{"verdict": "pass", "scores": [{"pass": "maybe"}]}` **报的是通过**。
  引擎本来就是按逐条判定闭合的，**报告现在也这么说**：
  **没人判定过，就没人通过**；
- **报告里的迭代次数有时是 `null`** —— 旧表达式 `a or b if rows else 0`
  实际解析成 `(a or b) if rows else 0`，行里没有 `iteration` 字段时
  报的是 **null**，而**写着「null 次迭代」的报告，脚本读不了**。
  现在按**运行自己的结束条目 → closure 行 → 所有行里最大的迭代号**取，**永远是个数字**；
- **升级处理不说是为什么** —— 交付物不再变化导致的升级，
  理由写的是「循环无法自行闭合」，**会让人去找错的东西**。
  现在**点名原因**：交付物连续 N 次没有变化；
- 配套回归测试 **11 个**，**每一层改动都做过反证**：单独回退后确认对应测试会挂。

**v0.7.36 加了什么**（让**死线真的是死线**，让**自检不再沉默几分钟**）：

- **`deadline_sec` 不是天花板，它只是一句说明** —— 墙钟预算**只在两轮迭代之间**被检查。
  **一次 provider 调用超了预算**（端点慢、回答长）**是被允许跑完的**，
  **而运行依然会报「闭环成功」**。实测：死线 1s 配 3s 的慢 provider，
  **跑了 9.0s 仍然报成功**；真机死线 180s，**耗时 185.26s 仍然报成功**。
  **配置里写的那个数字，当时只是文档，不是约束**。
  现在**每一次 provider 调用都带着剩余墙钟预算这个硬天花板**：HTTP 层取
  **「配置超时」与「剩余预算」里更小的那个**，**每次重试重新计算**，
  预算耗尽时抛出一个**专门的 `BudgetExceeded`** —— 那是**主动止损**，不是**端点坏了**，
  报告里也**分开说**。**闭合判定本身也要过一遍钟**：交付物**通过了全部机检**，
  **但到得比死线晚**，那就**升级处理（escalate）而不是闭合**，
  **并且把原因写清楚**。顺带修掉一个更隐蔽的 bug：**判定顺序**原来是
  「检查通过就无条件收工」，于是引擎**判出来的 escalate 会被自己丢掉**；
- **自检会沉默好几分钟** —— 实测真机一整轮 **460.5s 一个字都没有**，
  **看起来和卡死完全一样**。现在 `mas verify` **每一步慢操作之前都先报一声**：
  端点握手、指令校验、真实闭环，**闭环结束时报用时**。进度走 **stderr**，
  **`--json` 的 stdout 契约不受影响**；
- **自检判的是一份用户根本没跑过的配置** —— verify 运行时把用户自己配置的墙钟预算
  **覆盖成了一个写死的 180 秒**。真机实测一轮 **342 秒**、而用户自己的预算是 **900 秒**，
  结果**报「未闭环」** —— **自检把一个本来能跑通的配置判成了失败**。现在自检
  **用用户真正配的那个预算**（上限 900 秒，**免得沉默一刻钟**）；
- **「未闭环」把「哪一半坏了」给藏住了** —— 交付物**通过了全部机检**、只是**超了预算**的运行，
  和**交付物一塌糊涂**的运行，**报的是同一句话**。用户于是**去找一个根本不存在的坏交付物**，
  **真正的问题——那个钟——却没被点名**。现在引擎**把两件事分开记**：
  `deliverable_ok` 说**交付物过没过机检**，`status` 说**运行闭没闭合**；
  自检会**点名是哪一种**：**「交付物已通过全部机检，但本次运行超出时间预算」**，
  **直接告诉你该调哪个旋钮**；
- 天花板通过**线程局部上下文**传给 provider，**不给 `Provider.complete` 加新参数** ——
  **所有既有自定义 provider 和插件一行都不用改**。并行工作线程**显式继承**天花板，
  因为**线程池不继承线程局部状态**；
- 配套回归测试 **5 个**。

**v0.7.35 加了什么**（让自检**诊断的是你自己的配置**，而不是一个假象）：

- **端点握手**没带密钥**，却报 401** —— v0.7.34 让 provider 按 `api_key_env`
  从环境变量取密钥（向导只把**变量名**写进配置、不存明文，这个承诺是对的），
  **但握手没跟上**：`probe_from_config` 仍然只读**字面 `api_key` 字段**，
  而那个字段**是故意留空的**。于是对着需要鉴权的端点，握手**揣着一个空的
  Authorization 头**去问，**拿回 401** —— 而**同一次运行里的每一个真实请求**
  **都带着密钥、都成功了**。**用户被告知要去修一个本来就没错的配置**，
  而且 `mas doctor` 和 `mas verify` **对同一台机器给出了不一致的结论**。
  现在握手**和运行时用同一套方式解析凭据**：provider 检查、指令检查、
  端点检查**说的是同一种方言、带着同一份凭据**，
  **诊断终于诊断的是用户自己的配置**；
- 已实测（对着一个拒绝无鉴权请求的本地服务器）：**修之前** endpoint 行报
  `HTTP 401`、而服务器日志显示**其余每个请求都带着密钥**；**修之后**
  endpoint 行报**端点可达**、**闭环真的闭合**。

**v0.7.34 加了什么**（让自检说真话 —— 并且**把你设好的密钥真的发给模型**）：

- **你设好的密钥，从来没被发出去过** —— 初始化向导让你把密钥放进
  `OPENAI_API_KEY`（或 `ANTHROPIC_API_KEY`，或你自己取的名字），
  配置里**只记变量名**、**不存明文**，这是对的。但**建 provider 的那一步
  只读配置里的字面 `api_key` 字段**，而那个字段**是故意留空的**。
  于是**完全按文档配置好的用户**，**拿着空的 Authorization 头**去调端点，
  **拿到的是 401**。现在密钥按配置承诺的方式从环境变量解析，并有回归测试盯着；
- **回退后端会继承主后端的密钥** —— 声明了自己密钥变量的回退后端，
  **照样拿到了主后端的密钥**。**把主后端的凭据发给另一个端点**，
  **既是错的，也是泄露**。现在回退后端声明了自己的凭据就**不再继承**，
  没声明的才继续沿用；
- **`mas verify` 的 provider 那一行是橡皮图章** —— 它**只把配置里的字符串打印出来**，
  只要**看着像那么回事**就报 ok：**不发一次网络请求，也根本不查密钥**。
  于是端点挂了，用户看到的是**一行绿的 provider + 一行红的 closure**，
  **中间什么都没有告诉你真正的原因**。现在 provider 行会**说清端点地址、密钥找没找到**，
  并新增两项**真检查**：**端点握手**（和 `mas doctor` 用的是同一套，
  两个命令对「可达」的定义从此一致）、**指令生效检查**（验证 system 提示**真的到了模型手里**）。
  **端点挂了，现在会直接点名**；
- mock 模式**依然完全离线**：**不发握手、不发指令探测、一次网络请求都没有**；
- 配套回归测试 **10 个**。已实测：`mas verify` 指向本机真实端点 →
  **握手 ok、指令生效 ok、闭环 ok、账本 5 条**；指向一个死端口 →
  **endpoint 行直接点名「连接失败」**。

**v0.7.33 加了什么**（让回放真的能看懂 —— 以及修掉默认配置下的「None 之谜」）：

- **闭合运行的回放，closure 那一行是空白的** —— closure 行恰恰是**唯一说明
  「判了什么、判成什么、为什么」的那一行**，可它的摘要**只取 issues**，
  而**闭合运行的 issues 恒为空**。于是**成功的案例**（也就是最值得回放给别人看的案例）
  **在轨迹里是一个空格子**。现在摘要**说清楚判定、决策、通过了几条、哪几条没过、为什么**；
- **单评审者模式（默认配置）报的是「可用 None、跳过 None、缺席 None」** ——
  单评审者路径**直接返回原始判定、根本没走面板聚合**，于是账本和报告**根本说不清**
  那唯一一位评审**到底答了没有**、**是沉默还是压根没跑**。
  **一个人也算面板**，现在**数字齐了**；
- **沉默的评审者没有被记成「缺席票」** —— 单评审者模式下，**没给出任何可判定内容的评审**
  **不留任何痕迹**。现在**记为跳过 + 缺席**，**和多人面板完全一致**；
- 面板描述**不改变闭合判定** —— **有机检的标准永远由机检说了算**，
  评审说不说、说没说完，**都不影响**；
- 配套回归测试 **14 个**。已实测：逐个去掉修复 → **2 处回退全部被测试抓住**。
  真机跑通一次闭环：**回放每一行都有内容**，**没有空行**，
  单评审者显示「可用=1 跳过=0 缺席=0」。

**v0.7.32 加了什么**（让报告说的状态 == 运行真实的状态）：

- **报告分不清「停了」「炸了」「没干完」** —— 引擎**从不把最终状态写进账本**，
  报告只能**靠 closure 行去猜**，于是**凡是没走完决策就结束的运行，猜的全是错的**；
- **撞预算上限停下的运行，被报告成「未闭环」** —— 那读起来像「**还没做完**」，
  可它其实是**主动停的、有明确原因的停**。现在报告直接写 `budget_exhausted`；
- **provider 报错挂掉的运行，被报告成「incomplete」** —— **错误原因一个字都没有**。
  **失败的运行**，不能被当成**从未完成的运行**；
- **失败运行的产物是隐形的** —— 产物哈希挂在 closure 行上，而**在评分之前就挂掉的运行
  根本没有 closure 行**，于是报告显示空产物，读者**根本查不到这次到底做出了什么**；
- **账本现在自己交代「这次是怎么结束的」** —— 每次运行末尾新增一条 `run_end` 条目，
  带上**状态、决策、错误、迭代数、产物哈希、未解决 issues、总花费**；
  报告**直接读它，不再猜**，原始账本表格也**逐行交代结局**；
- 配套回归测试 **15 个**。已实测：逐个去掉修复 → **5 处回退全部被测试抓住**。
  真机跑通一次闭环，**运行状态 == 报告状态**，**引擎记账 == 账本求和 == 报告花费**，
  **三者分毫不差**。

**v0.7.31 加了什么**（让账本能自己回答「这次运行花了多少」）：

- **账本根本回答不了「花了多少钱」** —— 花费数字**只活在引擎的内存里**，
  账本里没有，**复盘报告里也没有**。现在**每次计费都在账本里记一次**，
  复盘报告直接显示**每次运行花了多少 token**、**总共花了多少**；
- **单 worker 路径**一条账本都不写 —— 并行候选池**记得账**，**单 worker 不记得**，
  于是**worker 花的钱在账本里凭空消失**。现在**同角色记账**；
- **reflector 从来没被记过账** —— 它**每次失败后都要跑**，**是要花钱的调用**，
  账本里却**一个字都没有**：一次反复自我修正的运行，**从不说复盘花了多少钱**。
  现在**记账**；
- **只记了最后一轮评审** —— 首轮评审和**追问**是**两次独立的计费调用**，
  现在**分开记账**，并且**一个 token 不会被记两次**；
- 配套回归测试 **14 个**。已实测：逐个去掉修复 → **每一处都有测试失败**。
  不变式扫描覆盖 **45 种池子形状**，现在同时校验
  **端点实收 == 引擎记账 == 账本求和**，**三者分毫不差**。

**v0.7.30 加了什么**（修掉「追问轮」的两个缺陷 —— 面板模式与单评审者模式**行为不一致**）：

- **靠追问答案闭合的运行，账本却记成「评审什么都没判」** —— 评审者漏判的标准，
  引擎会**追问一次**（一次便宜的小问题，而不是重来一整轮）。
  但**单评审者模式**在追问之后**账本里留的还是旧面板** ——
  于是账本显示「评审没有给出任何判定」，**旁边却是一次已经闭合的运行**。
  审计者看着像白纸黑字写着「没评审过」。**面板模式本来就把追问结果带下去了**，
  现在**单评审者模式也带** —— 账本记的，**就是引擎真正据以决策的那个面板**；
- **追问失败 = 整个运行直接报错结束** —— 追问是**补充提问**，它失败了，
  正确处理是**该标准保持「未判定」**，交给循环正常往下走。
  **面板模式本来就是这么降级的**，**单评审者模式却让异常逃了出去** ——
  **同一个漏判，在一个配置里让运行崩溃，在另一个配置里只是变慢**。
  现在**两种模式行为一致**。失败的调用**照样记账**（端点收了钱），
  并且**写进账本**；
- 配套回归测试 6 个。已实测：逐个去掉修复 → **3 个 / 2 个测试失败**。

**v0.7.29 加了什么**（修掉「失败的调用没被记账」的三个缺陷）：

- **失败的候选，只有「恰好排在赢家前面」才被记账** —— 并行池里失败成员的开销，
  是在**扫描到赢家时**才被冲进账本的。于是**排在赢家后面**的那个失败候选，
  **这笔钱永远消失**。同一个池子，**花费取决于哪个成员先跑完** ——
  而**取决于赛跑结果的预算不是预算**。现在开销在**和截断同一个 pre-pass 里统一收齐**，
  排完名之后**一次性入账**，**顺序不再影响任何数字**；
- **被整池丢掉的池子，失败的那个成员没被记账** —— 截断让整池作废时，
  被截断的、被丢弃后已完成的**都记账了**，唯独**直接失败的那个成员漏了**。
  被丢掉的每个成员**端点都收了钱**，现在**全都记账**；
- **被丢弃的评审轮，失败的那个评审者没被记账** —— 同样的毛病，发生在评审面板上：
  截断让这一轮作废时，**直接失败的那个评审者**被排除在账单之外。
  现在**第一轮评审**和**追问轮**都把它记上；
- **一个被计费的失败调用不会说自己花了多少钱** —— `ProviderError` 现在**带着这次失败调用的用量**，
  于是**响应形状坏了**、**流在半路死了**、**降级链倒到下一个后端**这三种情况，
  花费都能**跟着异常一起走到引擎**。以前**死在 provider 错误上的运行**，
  会为**已经付过钱的活儿**报告**零开销**；
- 配套回归测试 5 个。已实测：逐个去掉修复 → **每个对应测试都会失败**。
  不变式扫描现在覆盖 **50 种池子形状**（1~4 个 worker × 1~3 个评审者，
  跨越干净运行、截断、失败与混合），**每一种都分毫不差地记下端点收的账**。

**v0.7.28 加了什么**（修掉三个「预算账本说谎」的缺陷 + 一个「永远闭合不了」的缺陷）：

- **被整个丢掉的候选池，账本当成免费** —— 并行候选池里只要**有一个**被输出上限截断，
  **整池作废重试**；但引擎**只给被截断的那个记账**，
  **已经跑完的那几个候选等于白送**。实测：账本记 **6,000** token，
  端点实收 **16,010**。**token 上限因此系统性地放水**，预算根本不是预算。
  现在**整池花费先收齐**再抛截断 —— 账本上的数字**就是端点开的账单**；
- **planner 和 reflector 干脆一次都没记账** —— 这两个都是**要花钱的调用**：
  planner 在第一轮、reflector 在每一次失败后。实测：计划→写→失败→复盘→评审，
  账本记 **922**，端点实收 **1,033**。现在**两个都记账**，
  账本里也**看得见 planner 这一次的开销**；
- **被截断的评审者被当成「缺席的投票人」** —— 评审者**用完输出额度**，
  **不是「不同意」**，是**根本没写完**。严格模式把它算成**缺席票**，
  于是循环**永远闭合不了**：每一轮烧掉整个预算，最后**为一个「再试一次就能过」的理由升级**。
  现在评审面板里的截断**是可重试的失败**，下一轮**给更大的输出空间** ——
  和 worker 路径**早就是这么做的**保持一致。**追问轮**（re-ask）同样适用；
- 配套回归测试 10 个。已实测：逐个去掉修复 → **每个对应测试都会失败**。

**v0.7.27 加了什么**（修掉三个「循环闭合得不对」的缺陷）：

- **列表形式的文件机检让循环永远闭合不了** —— 引擎把「要写哪些文件」交给 worker 时，
  **自己解析了一遍**机检参数，而机检**用的是另一套解析器**。两套解析器在**列表写法**上不一致：
  机检看到的是 `notes/a.txt`，引擎看到的是字面量 `['notes/a.txt']`。
  于是 worker 被要求去写一个**名字里带方括号**的文件，而机检去看的是**另一个路径** ——
  循环**跑多少轮都闭合不了**。现在引擎**用机检自己的解析器**读路径，两边永远一致；
- **跑通了的运行却报告着上一次的错误** —— 一次截断重试后成功闭合的运行，
  `status` 是 `closed`，`error` 却还留着 `truncated answer: ...`。
  CLI 于是打出「说明：truncated answer」，**读起来像一次失败的运行**。
  现在**瞬时故障只属于它发生的那一轮**：闭合即清零，
  但**账本里那条 `truncated` 记录照旧保留** —— 审计线索一条不少；
- **token 上限不是上限** —— 预算只在**一轮结束后**检查，而**截断重试是在那之前发生的**。
  一个不停撞输出上限的运行**可以花掉远超上限的 token**（实测上限 20 万、实花 24 万）。
  现在**每轮开头也检查上限**，它才真的是上限；
- 配套回归测试 7 个。已实测：逐个去掉修复 → **2 + 2 + 1 个测试失败**。

**v0.7.26 加了什么**（修掉一个「出错时账本没有记录」的缺陷）：

- **意外异常会抹掉审计线索** —— 引擎只捕获 `ProviderError`，**其他任何异常**都会直接冲出 `run()`。
  结果是：**账本里只剩一条「开始了」的记录，没有结局** —— 没有 error 条目、没有 closure、没有工单。
  那次运行的**闭环状态就此无从查证**，而这恰恰是本项目存在的意义；
- **引擎不是一个安全的库入口** —— CLI、MCP 工具、HTTP 路由**各自都有异常边界**，
  所以命令行用户看到的是干净报错。但引擎**也被直接调用**（库用户、脚本、嵌入式运行时），
  在那里它抛的是**裸 traceback**；
- 现在循环**捕获所有非 `ProviderError` 的异常**，**记进账本**（标为 `internal`），
  然后**返回正常的 `error` 结果**。而**区分被保留**：
  provider 故障仍然读作 `provider error`，
  **验收标准的参数校验仍然照旧抛 `ValueError`** —— 因为**坏掉的规则绝不能看起来像闭合过**；
- 配套回归测试 7 个。已实测：去掉兜底 → **6 个测试失败**。

**v0.7.25 加了什么**（修掉一个「预算账本说谎」的缺陷）：

- **被截断的那次调用是「免费」的** —— 端点**已经生成了 token、已经计费**，然后才停。
  但异常只带走了答案、**没带走用量**，所以引擎**没扣这笔账**。
  截断五次的运行**看起来比实际便宜五倍**，**预算上限实际上宽松了五倍**；
- **并行候选池少算了 N 倍** —— 三个 worker 就是**三次截断、三份 token**，
  但**只有被重新抛出的那一个**被计入，**另外两个候选的 token 直接消失了**。
  现在**整个池的花费先收齐**再抛截断，**账本上的数字就是端点开的账单**；
- `TruncatedAnswer` 现在**带着那次调用的用量**，引擎**照实扣进预算**，
  账本里的 `truncated` 条目**同时报告花掉的 token**；
- 配套回归测试 2 个。已实测：去掉计费 → 并行测试报 **0 token**（应为 2400）。

**v0.7.24 加了什么**（修掉一个「回答被截断了却照样闭合」的缺陷）：

- **被截断的回答被当成完整回答交付** —— 端点返回的 `finish_reason` 被收进了原始数据，
  然后**再也没被读过**。于是唯一那句「这个回答不完整」的信号被静默丢弃了；
- **截断的回答能闭合整个循环** —— 模型用完输出 token 时产出的是**残缺**回答，
  而残缺回答**可能碰巧满足验收标准**：要找的关键词可能就在第一段里，
  片段也可能已经够 `min_chars`。于是循环**在半截交付物上闭合**、报告 `closed`、
  把**没写完的东西交给用户**。这正是本项目要防的那一类假闭合，而且**实践中最容易发生**；
- **输出预算从来没被放大过** —— 每次调用都用 provider 默认的 **4000 输出 token**，
  且**永不增加**。写不进 4000 token 的交付物**必然被截断**，并且**必然被静默接受**；
- 现在：**截断的回答变成「可重试的迭代失败」** —— provider 抛出 `TruncatedAnswer`
  而不是返回残缺答案，引擎**把截断记进账本**并**重试这一轮**。
  残缺回答被**故意丢弃**：碰巧满足检查的片段**绝不能**成为交付物；
- **重试时给更多空间** —— 输出预算**随尝试次数翻倍**（8000 → 16000 → 32000，有上限），
  循环**真的有机会写完**，而不是用同样的方式失败两次。预算可用 `output_max_tokens` 配置；
- **并行路径行为一致** —— 被截断的候选**不算「丢失的角度」**，而是**整个候选池一起重试**；
  否则循环会**在一堆碎片里排名**，然后挑出**最长的那个**；
- 配套回归测试 13 个（provider 8 + 引擎 5）。已实测：去掉检测 → 1 个 provider 测试失败；
  去掉引擎重试 → **5 个引擎测试全部失败**。

**v0.7.23 加了什么**（修掉两个「推理块又漏出来了」的缺陷）：

- **未闭合的推理块被整段交付** —— 模型在思考草稿写到一半时**用完了 token**，
  闭合标签 `</think>` 根本不会到达，回答就是一个**永远不闭合的 `<think>`**。
  旧逻辑只匹配 `<think>...</think>`，于是**整段截断的草稿**被当成交付物、
  被计入长度 tie-break、还被**写进了工作区的文件**。
  现在：**开了标签而没闭合，就说明后面全是草稿** —— 从开标签处往后的内容整体丢弃；
- **不同网关的标签拼写不一样** —— `<think>` 和 `<thinking>` 早就认了，
  但 `<reasoning>`、`<scratchpad>`、`<reflection>` 没认，
  在那些端点上**草稿原样进了交付物**。现在**五种拼写全部支持**（闭合与未闭合都支持）；
- 闭合的情况**仍然先处理**，所以闭合块**不会被误判成开标签**，它后面的正文**完好保留**；
- 配套回归测试 9 个，覆盖未闭合块、每种拼写、闭合与未闭合的交互、以及纯文本。
  已实测：去掉未闭合规则 → 2 个测试失败；去掉扩展拼写 → 再 3 个失败。

**v0.7.22 加了什么**（修掉一个「并行模式交付的是模型的推理草稿」的缺陷）：

- **并行路径忘了剥离推理块** —— 模型把自己的思考草稿包在 `<think>...</think>` 里，
  单 worker 路径**一直都会剥掉**它，**并行路径却没有**。结果是：
  **推理草稿被当成交付物交付出去**、被计入**长度 tie-break**（
  推理草稿更长的候选因此胜出）、还被**写进了工作区的文件**里；
- **现在两条路径交付的内容完全一致** —— 并行路径现在在**排序之前**就剥离推理块，
  很很重要：剥得**早**一步，推理长度就不会污染 tie-break，文本级机检（`min_chars` / `contains`）
  也不会被草稿污染；
- 配套回归测试：断言**并行路径的交付物里一个推理块都没有**、**单个与并行交付的内容完全相同**、
  以及**推理长度不能决定 tie-break**。已实测：去掉剥离，候选字符数从 34 变成 369，测试立刻失败。

**v0.7.21 加了什么**（修掉一个「并行模式选出错误候选」的缺陷）：

- **并行候选排序时，文件级机检读到的是磁盘旧内容** —— 文件级机检读的是**真实磁盘**，
  而排序那一刻，候选**还没把自己的文件写上去**。于是**每个候选在文件规则上都得 0 分**，
  排序退化成「按长度挑」——**可能选出一个写了违规文件的候选**，甚至选中一个
  别的候选才写对的候选。单 worker 路径没这个问题：它是**先写后检**，顺序本来就对；
  只有并行路径**顺序反了**；
- **现在每个候选按「它自己会写出的文件」来评分** —— 候选的写文件块先被解析成
  **写入计划**（不碰磁盘），文件级机检通过一个**覆盖层**读取这个计划。
  **胜出者照旧被真正写入磁盘，而且只有胜出者**；排序期间磁盘状态**完全不变**，
  所以候选**看不到彼此的输出**；
- 新增两个积木：`mas.filewrite.plan_writes` 与
  `mas.filechecks.evaluate_file_check` 的 `overlay` 参数。覆盖层就是
  「绝对路径 → 字节」的普通映射，**只改变机检读到什么，别的一概不动**；
- 配套回归测试：三个候选写同一个文件、错误那两个**故意写得更长**，
  断言赢家必须是**文件满足规则**的那个、且磁盘上是赢家的内容。
  已实测：去掉覆盖层，测试立刻失败——长的错误候选胜出、正确候选被丢弃。

**v0.7.20 加了什么**（修掉一个决定「哪个候选能赢」的缺陷）：

- **并行候选排序跑错了目录** —— 排序时调用机检**漏传了配置**，而文件级机检
  （`file_contains` / `file_exists` 等）是按**配置里的工作区**解析路径的；
  没有配置就退回**当前工作目录**。于是并行模式下每一个文件级机检都在
  「这次运行根本没用到的目录」里找文件，**所有候选在文件规则上全部得 0 分**，
  排序退化成「按长度随便挑一个」——**可能选出一个违反了文件规则的那个候选**，
  文件规则对选优**完全没有影响力**。单 worker 路径和最终判定一直都传了配置，
  只有排序这一处漏了。现在三条路径解析同一个工作区；
- **最终判定其实没被影响** —— 选出赢家之后会用配置**重算**判定，所以受损的只是
  「选谁」，而「选谁」恰恰是这场竞争存在的意义；
- 配套回归测试把工作区放在**与当前目录不同**的位置、三个候选给出不同答案，
  断言赢家必须是**能通过文件规则**的那个。已实测：把修复回退，测试立刻失败
  （三个候选全是 0 分）——这个测试是真能抓住这个缺陷的，不只是「跑得过」。

**v0.7.19 加了什么**（修掉一个会毁掉闭环判定真假的缺陷）：

- **角色指令在 Responses 型网关上被整个丢掉了** —— 这是本项目至今最严重的一个缺陷，
  而且是**真机实测**才发现的（对着本机的 Codex Responses 桥实测，不是猜的）。
  运行时同时发 `messages`（chat-completions 形状）和 `input`（Responses 形状）；
  而 Responses 型网关**只读 `input`、完全无视 `messages`**，偏偏 `input` 里以前只装了
  用户提示。于是 **system 指令被丢在地上**：worker 的「直接输出交付物」、verifier 的
  JSON 结构、reflector 和 lead 的决策契约，**全部没有生效**。
  症状极具迷惑性——端点照常应答、`mas doctor` 照常报「端点可用」、闭环照常显示「已闭合」，
  但那些判定全是猜的。**建立在被丢弃的契约上的闭环，正是本项目要消灭的假闭环。**
  现在 `input` 携带**整段对话（含 system 条目）**，`messages` 保持 chat-completions 形状，
  两种方言下指令都活着，**不需要用户配置任何开关**；
- **`mas doctor` 不再把坏掉的部署报成健康的** —— 握手只能证明「端点能应答」，
  证明不了「你发的指令被采纳了」。新增 `instruction` 检查：让模型复述一个**只出现在
  system 指令里**的标记词，若端点正常应答却没复述，就明确报 warn 并直言
  「角色契约可能未生效，闭环判定不可信」。探针现在也使用与运行时**完全相同**的载荷形状，
  握手测的就是运行时真正发出的那个请求。

**v0.7.18 加了什么**（把「一键安装」这个承诺修成真的）：

- **以前下载一个脚本是跑不通的** —— 安装器在没有源码检出时直接失败。现在它会自己从
  GitHub Release 拉取已发布的 wheel、用标准库解开、装到 `~/.mas/`。**只需要机器上有
  Python 3.10+：不用 git，不用 pip，不用编译工具，不用 clone。**
- **以前装的可能不是你下的那个** —— 运行时是用 `python -m mas` 启动的，而 `python -m`
  会把**当前工作目录**放在 `sys.path` 前面。在源码检出里那个目录就放着 `mas/` 包，
  于是 MCP 注册和 agent skills 全来自检出目录，**刚装好的运行时压根没被用上**。
  现在运行时一律按**包路径**注册和启动；
- **以前注册里没有说配置在哪** —— 宿主进程能启动运行时，却可能读到别人的
  `config.json`。现在两处注册都写明**运行时路径**和**配置文件位置**；
- **两个真机环境的坑** —— Git Bash 下 `python` 解析成不带 `.exe` 的 POSIX 路径，
  Windows 宿主根本执行不了；`install.sh` 用冒号分隔 `PYTHONPATH`，而它调用的
  Windows Python 只认平台分隔符。两条都修了；
- **CI 站起来了** —— 新增的 CI 腿**只摆脚本、不摆源码**，逼安装器走真实的下载路径；
  另一条腿装「从当前 checkout 构建的 wheel」并断言装出来的版本与仓库一致；
- 补上 `CHANGELOG.md`，并新增 `MAS_SKIP_PATH` / `MAS_PYTHON`，两个安装器选项现在完全对称。

**v0.7.16 加了什么**（让模型在宿主里真的能跑闭环）：

- **`mas relay`** —— 一个纯标准库的 OpenAI 兼容中继。它解决一个真实的死结：
  DeepSeek Harness 的 `dsh-llm-pi-ai` **坚持要一个 API key**，而私有端点常常
  **不要 key**（甚至会因为占位 key 直接 `401`）。中继丢弃客户端的 `Authorization`，
  按需换成上游真 key。
- **`tools/setup_dsh_bridge.py`** —— 一键生成能跑的 DSH profile（写配置 + 装插件 +
  打印命令）。
- **实测**：DSH 宿主内 `mas_run` 真实跑出 `status=closed`、`closed=true`、
  判定 `source="file"` 读的是真实磁盘，产物文件确实落盘。
- **MOKI 分工写清楚了**（MOKI ≠ OpenMausBot）：MOKI 整理资料，MAS 判定过不过。

**v0.7.15 加了什么**

- **「已写入 out/report.md」不再是一句可以白说的话** —— v0.7.14 为止，MAS 的**全部**
  机检都只搜**模型输出的文本**。于是最要命的那类假闭环成立了：worker 说「已把结论
  写入 `out/report.md`」，产出一段很像样的说明；如果期望的字符串碰巧也出现在那段说明里，
  引擎甚至会**判定通过**——尽管磁盘上根本没有那个文件。**闭环建立在承诺上，不是事实上。**
  现在新增十种**文件级机检**（`file_exists` / `file_missing` / `file_contains` /
  `file_not_contains` / `file_regex` / `file_lines_min` / `file_min_bytes` /
  `file_max_bytes` / `file_sha256` / `file_mtime_newer`）：判定只认真实文件系统上的字节，
  报告里的 `source` 写 `"file"`，一眼能看出结论是从磁盘来的还是从文本来的；
- **文件缺失 = 不通过，不是「无法判定」** —— `file_exists` 找不到文件时判 `pass: false`
  （而不是三态里的 `unjudged`）。路径越界（`../` 或工作区外的绝对路径）在**开跑前**直接
  拒绝；参数缺失同样开跑前拒绝、零 token 消耗。工作区内的相对路径从**任何**目录启动都
  解析到同一个文件（`mas run --workspace <dir>`）；
- **给 worker 一个真的能写文件的途径** —— 加了读侧才暴露写侧也缺：worker 的输出只是
  一个字符串，它根本没有写文件的办法。新增**写文件协议**：worker 在交付文本里嵌
  `<<<MAS:FILE path="...">>>` 围栏块，引擎把内容**原样写进工作区**并把块从产物里剥掉，
  产物仍然是可读的交付说明；写入进账本（`role="write"`），谁写了哪个文件都能查；
  越出工作区的路径拒绝写入，单文件上限 4 MiB，模板块被照抄时跳过（不让占位内容骗过检查）；
- **并行候选只写胜出的那一个** —— 否则工作区里会同时留下好几个版本的同名文件，
  文件检查就变成「谁最后写谁算数」，不再描述真正交付的那份产物；
- **这层是真实端点现场发现的，不是审计推测** —— 第一轮实测就抓到了「模型声称写了、
  磁盘上没有」这个教科书级假闭环：判定如实报 `pass=false`、`note="file not found"`、
  循环**不闭合**；加上写协议后文件真的落盘，判定才转 `pass=true`、`status=closed`。
  同一轮还修了 gateway 控制标记污染（`]<]…[>[` 之类）混入产物文本的问题。

**v0.7.14 加了什么**（接入 DeepSeek Harness，闭环能力跨客户端共用）：

- **多一个客户端，就多一份真相？** —— Codex、Claude Code、OpenMausBot 都是通过 MCP
  接进来的，判定与账本本来就只有一份。现在 **DeepSeek Harness** 也能接：仓库里
  新增了 DSH 插件 `integrations/dsh-openloop-mas`，它**不重写闭环逻辑**，而是用
  MCP 调用本地运行时——因此 DSH 对话里的判定、票数、账本，和其他客户端看到的
  **是同一份事实**；
- **一键安装多了个可选开关** —— `install.ps1 -InstallDshPlugin`。**默认关闭**：
  主线安装仍然是**零依赖的纯 Python**，初学者不需要装 Node、DSH 或 npm。
  开启后脚本会 `npm pack` 打包插件并装进指定 profile；**DSH 命令失败时优雅降级**
  （打印 tarball 路径与手动安装命令），不会让整个安装崩掉；
- **插件在真实 DSH 宿主里跑通，并抓出两个离线测不出的坑** —— 一是 DSH 认的挂载
  字段是 `dsh.bundle.patch`（指向 `cordis.patch.yml`），不是 `dsh.profile.bundles`，
  放错会被装成普通依赖、**根本不挂载**；二是工具参数 schema 必须满足 DSH 的
  JSON Schema 子集，`items.additionalProperties` 要**显式**为 `true`/`false`，
  否则 `defineTool` 直接抛 `JsonSchemaError`、整个插件 entry 不激活。
  这两个 bug 只有**真跑一次宿主**才会暴露；
- 插件的 MCP 客户端（`lib/mcp-client.js`）**零依赖**，可脱离 DSH 独立做单元测试。

**v0.7.13 修了什么**（缺席的评审不能再冒充弃权票）：

- **三位评审只答了两位，报告却写「全票通过」** —— 对抗评审跑真实端点时，评审面板会**间歇性地少答**：某位评审调用失败或超时后，这次评审结果里就没有他。聚合层此前只统计「答了的人」（`usable`），缺席者既不计入通过票也不计入失败票，于是一块 **3 票的面板只收到 2 票**时，账本写出 `votes: "2/2"`、`pass: true`，报告宣称「全票通过」——**3 位评审里 1 位根本没表态，引擎却当作一致通过收口**。这与 v0.7.11/v0.7.12 修的是同一类假闭合，但这一版是**真实端点现场发现**的，不是离线审计发现的；
- 现在聚合层知道**面板应该到几个人**（`expected`）：缺席票在 `all` 模式下一律判**不通过**（不闭合），票数显示成 `2/3` 而不是 `2/2`，结果里新增 `absent_votes` 与 `judged_by` 字段，聚合 issues 里明确写「N 位评审没有给出判定」；`majority` / `any` 两种弱保证模式**按设计容忍缺席**（用户主动选了弱保证），但缺席同样被记录和展示，不再隐形；
- **重问路径曾把整个评审团换掉** —— 面板缺票时，引擎会重问缺席的成员。此前这条路径只问了一个**没有人格**的普通 verifier，然后用它**替换**整个评审团：票数从 `3/3` 塌成 `1/1`，一次「重问」反而把评审强度降到了零。现在重问走**完整面板**（同一批人格），**每个成员只补自己漏掉的那条标准、只保留自己的答案**，不会把一个人的意见广播给全团；评审调用失败也不再让面板静默塌缩——失败者的槽位被保留为「缺席」；
- 评审日志的**时序**也修好了：此前账本里写的是**第一次聚合**（重问之前）的结果，重问后的最终状态从未落盘。现在账本写的是**最终聚合**（含 `missing`），报告与账本不再互相打架。

**v0.7.12 修了什么**（总结不许和逐条判定打架）：

- **模型自己前后矛盾，报告跟着矛盾** —— `normalize_verdict` 此前只归一化逐条 `scores`，却**原样保留**模型写的顶层 `verdict`。于是 `{"verdict": "pass", "scores": [{"id": "c1", "pass": "false"}]}` 归一化之后，顶层说「通过」、逐条说「不通过」。闭合判定本身不受影响（它只看逐条 `scores`），但**每一份报告、每一次回放、每一条账本条目都显示着这个矛盾**，读者无从判断引擎到底信了哪一半；
- 现在顶层总结**服从逐条判定**：只要有**任何一条**标准的判定读得懂，顶层 `verdict` 就由这些判定算出（全部通过才算通过）；只有**一条都读不懂**时，模型自己的总结才被保留（那时它是唯一的信号）。这条修复的落点是「报告说的和引擎做的一致」，而不是「更容易通过」。

**v0.7.11 修了什么**（判定读含义：假闭合的最后一类）：

- **多位评审都说「不通过」，聚合成「通过」** —— 聚合层此前用 `bool(票)` 读判定值，而 `bool("false")` 是 `True`。一位评审回 `"pass": "false"`（字符串而不是布尔）在聚合时就被算成**一票通过**：两位评审都说「不通过」，引擎聚合成 `pass: true, votes: 2/2`，循环据此闭合。这是本项目最危险的一类假闭合——**机器明明在说不行，账本却写着过了**。现在判定按**含义**读取（`"false"` / `"0"` / `"no"` 才是不通过），**读不懂的票一律不算通过票**（保守侧），并在结果里给出 `unreadable_votes` 计数；`mode="any"` 同样不再被字符串假票骗过；
- **mock 后端把同一条标准报两次** —— 没有机检规则的标准，mock 评审会先从「标准列表」追加一条（`pass: False`，`no machine check`），又从「未判定列表」追加一条；`setdefault` 只留第一条，于是**真正有用的那条说明被覆盖掉了**。现在「无机检」只由未判定分支报告一次，每条标准恰好出现一次；
- **`normalize_verdict` 把读不懂的判定推成 `pass`** —— canonical 路径（`{"verdict": "", "scores": [...]}`）此前保留了原始字符串，`all(s.get("pass"))` 直接真值化，`"maybe"` 也算通过。现在读不懂 → `pass: False` + `unreadable: True`，判定改为 `all(s.get("pass") is True)`，与 `criteria` 路径语义对齐；
- **「没判定」在评审提示里显示成 FAIL** —— 机检结果区此前只有 PASS / FAIL 两态，`pass: None`（无规则、规则写坏、规则类型不认识）被渲染成 `FAIL`，评审模型于是读成「机检失败了」，而不是「这条根本没被判定」。现在三态显示：`UNDECIDED` / `PASS` / `FAIL`。

**v0.7.10 修了什么**（resume 失败不再伪装成已解决）：

- **一次失败的 resume 会把工单标成「已解决」** —— 升级工单是给人看的队列：它说「机器自己合不了，需要人拍板」。`mas resume` 跑完如果**仍然闭合不了**，此前却照样把工单写成 `resolved`：升级事项从 `mas tickets` 里消失了，而它本该解决的那个问题一个都没解决——队列因此少报问题、循环因此「假闭合」。现在只有**真的闭合**才关单；没闭合就留在队列里，并把这次失败的原因（状态、错误、失败的标准）写进工单的 `resume_attempts`；
- **resume 失败会多开一张重复工单** —— 一次失败被记成两个待办，人工队列开始谎报「有几个问题在等」。同一个问题现在只有一张工单，新的现场（最新产物、失败标准、迭代历史）追加到它身上，而不是另起一张；
- CLI 现在明说「工单 X 仍未解决，仍在人工队列里」，不再让一次 `exit 0` 看起来像办完了。

**v0.7.9 修了什么**（标志读含义，JSON 数组不再整体退化）：

- **`"required": "false"` 被当成必选** —— `bool("false")` 是 `True`，而标准经 JSON / MCP / shell 传递时带上引号是极常见的写法。用户写的是「这条可选」，引擎执行的是「这条必须过」，循环于是永远闭合不了。现在标志按**含义**读取（`"false"` / `"0"` / `"no"` 才是可选），**读不懂的值一律按必选处理**——读不懂的标志绝不能悄悄放宽标准；
- **`--criteria '[{...},{...}]'` 的 JSON 数组形式整体退化** —— 数组以 `[` 开头，没被识别成 JSON，就被当成**一条纯文本描述**：机检一次都没跑，闭合其实来自模型说「看起来没问题」。现在数组里的每一条都带上自己的机检规则跑，报告里的 `source` 如实写着 `rule`；
- 同一轮还补上了 CLI 层的回归测试：数组形式解析、机检真的执行、字符串标志读成可选、坏 JSON 数组当场拒绝。

**v0.7.8 修了什么**（可选标准不再谎报通过）：

- `required: false` 的标准此前在合并层被**硬编码**成通过——一条带机检规则的可选标准，机检结果被整个丢弃，报告显示绿灯而规则其实没过；
- 现在有机检就照常跑机检、真实结果原样写进报告（过了是 `PASS`，没过是 `FAIL`），但**不阻塞闭合**；没有机检才由模型判定，判定结果同样只作记录；
- 所有判定新增显式 `blocking` 字段：必选为 `true`，可选为 `false`。闭合只看 `blocking` 的标准。

**v0.7.1 修了什么**（升级不再悄悄留下旧版本）：

- **升级后 skills 会漂移，而且没人发现** —— 之前 `mas install` 只是把文件覆盖过去，从不比较内容；0.6.0 时代装下的旧 skill 副本可以一路留到 0.7.0，而 `mas doctor` 只检查「在不在」，不检查「是不是这一版」；
- 现在安装器按**内容指纹**判断漂移：装之前先备份旧副本，换成包内版本，并清掉新版已删除的残留文件；
- `mas doctor` 新增 **`skills_sync`** 检查项，直接告诉你哪几个 host 的哪个 skill 与包内版本不一致；
- **新增 `mas sync`** —— 一条命令把已安装的 skills 同步回包内版本（`--dry-run` 只报告不写入）；
- **失效的 MCP 注册不再被当成「已注册」** —— 以前只要配置里有 `mas_loop` 这个键就算通过；但如果它指向的 Python 已被删除、或指向一份已卸载的包，MCP 服务器根本起不来，而自检全绿；现在这类注册会被识别、刷新，`mas doctor` 也会报出来；
- **目录级备份修好了** —— `_backup()` 原本只处理文件，skill 目录的备份调用直接静默失败，等于没有备份；
- **发布到 PyPI 不再需要长期 token** —— release workflow 新增 **Trusted Publishing（OIDC）** 通道，仓库不需要保存任何密钥。首次发布需要在 pypi.org 登记一次，见 [docs/publishing-to-pypi.md](docs/publishing-to-pypi.md)。

**v0.7.3 修了什么**（`section` 机检此前会假阳性通过）：

- `section` 判定此前是**子串匹配**（`"## 结论" in artifact`），所以三件事都会**误判为通过**：正文里随口提到「`## 结论`」这个词、`## 结论分析` 满足了检查 `## 结论`、以及 `### 结论`（三级标题）也算数；
- 现在判定的是**整行恰好等于 `## <标题>`**（允许行尾空白），`#` / `###` / `####` 都不再算，正文提及不算，更长的标题也不满足更短的要求；
- 这是**行为变更**：如果你此前用 `section` 检查一个「标题前缀」，现在需要写完整标题。判定更严意味着假阳性减少，代价是以前靠巧合通过的标准会开始失败——这正是闭环该有的方向。

**v0.7.4 修了什么**（标准写漏参数，曾被当成「通过」）：

- 机检规则**缺参数**时会被静默退化成一个恒真判断 —— 空正则匹配一切、空 `contains` 列表要求为零、缺失的 `min_chars` 变成「至少 0 个字」。结果就是：**标准根本没写，闭环却报绿灯**；
- 现在这类规则一律判为**不可判定**（不通过），而且引擎会在**开跑前**就报错退出（退出码 1，**零 token 消耗**）—— 不再让一次笔误烧掉整个预算；
- 这是**行为变更**：如果你此前靠一个空参数「白嫖」通过，现在会立刻被拦下并告诉你缺什么。

**v0.2.0 加入了什么**：并行多候选竞争选优、对抗式交叉评审、多后端故障转移、HTTP/SSE 远程接入、闭环复盘报告与回放、交互式初始化向导、离线基准测试、真实闭环自检。

**v0.4.8 修了什么**（远程接入不再是一扇没锁的门）：

- **`mas http` 之前完全没有鉴权** —— `/call` 能执行任务、读账本，`/events` 能持续推送账本内容；只要把 `--host` 改成 `0.0.0.0`，再套一层反代或隧道，它就是一台**任何人都能开任务、读你账本的远程控制台**；
- 现在设了 `MAS_HTTP_TOKEN`（或 `mas http --token ...`）后，除 `/health` 外的所有路由都需要 `Authorization: Bearer <token>`（或 `X-MAS-Token`），比较用 `hmac.compare_digest` 常量时间；
- **绑定到非本机地址却没有令牌时，服务器直接拒绝启动**（退出码 2）—— 环境变量打错字不应该变成「公网裸奔」，这是 fail-closed；
- 开启鉴权后 CORS 不再回 `*`，而是钉到 `http://127.0.0.1`，避免任意网页跨站调用；
- 顺带修掉一个真实的 keep-alive 缺陷：鉴权拒绝发生在读取请求体之前，残留字节会让同一条连接的下一次请求解析错位、客户端直接卡死；现在请求体先读、后鉴权。

**v0.4.7 修了什么**（「一键安装」现在有 CI 守护）：

- **新机器上安装会静默丢掉 MCP 与 skills** —— 以前只有 `~/.codex/config.toml` **已存在**时才注册 MCP，全新机器上这一步被跳过，用户拿到的是「安装成功」却一个 MCP 工具都没有；
- 现在配置文件不存在时会**创建**它并登记；skills 目录也总是为 Codex / Claude Code 创建（其他宿主仍是可选）；
- **新增 `install` CI workflow** —— 在三个平台的**干净 HOME**（没有 `~/.codex`、没有 `.claude.json`）里跑真实安装器，再断言 MCP 注册、skills 落盘、以及**注册后的 MCP 服务器真的能启动并回答**（`tools/verify_install.py`）。

**v0.4.6 修了什么**（离线环境也能跑自检）：

- `mas doctor` 新增 `--no-network` 选项 —— 无网或强制禁网的环境下可以跳过握手探测，不会因为连不上网关而报错；

**v0.4.5 修了什么**（`mas doctor` 不再误报免鉴权端点）：

- 之前只要配置了 `base_url` 而没配密钥，`mas doctor` 就会报「端点未配置完整」并跳过握手探测 —— 但本机 Codex 响应桥接这类**免鉴权本地端点**就是这种情况，用户会被误导「你的配置不完整」；
- 现在只要配置了 `base_url` 就会真发一次握手请求，由服务端自己的回答（200 / 401 / 400）告诉你结果，并报出实际协议方言；
- 同时修掉 `provider` 检查项的误报（无密钥但有端点 → 判为 `ok`，并注明「免鉴权端点」），并补上回归测试。

**v0.4.4 修了什么**（把 CI 矩阵修回全绿，运行时行为与 v0.4.3 完全一致）：

- v0.4.3 的新增测试用了 pytest 风格（`pytest.raises` / `parametrize`），但 CI 矩阵跑的是标准库 `unittest`，pytest 又不在依赖列表里 —— 结果 Release 产物是好的，CI 却是全红；
- v0.4.4 把这批测试全部改写成纯 `unittest`（`assertRaises` / `subTest`），恢复「零第三方依赖」的项目定位；
- 同一份测试现在两套 runner 都通过：`python -m unittest discover -s tests` 与 `python -m pytest tests -q` 各 123 条全绿。

**v0.4.3 修了什么**（协议方言保障 + 端点握手，详见 [真实模型验证报告](docs/live-model-verification.md)）：

- **协议方言变成可执行契约** —— 6 种真实世界的接入形状（标准 OpenAI、只读 `input` 的桥接、SSE 流、Responses 事件流、不支持 `response_format` 的网关、Anthropic Messages）各有契约测试，连带全链路闭环测试；
- **`usage.total_tokens` 缺失不再记成 0** —— 此前 token 预算对部分网关形同虚设；
- **`message.content` 是内容块列表时也能正确解析** —— 多模态形状的网关不再让下游 JSON 解析崩掉；
- **`mas doctor` 会真的握手一次端点**，直接告诉你「你的网关说的是哪种方言」。

**v0.4.2 修了什么**（来自 OpenMausBot 端到端实测，详见 [真实模型验证报告](docs/live-model-verification.md)）：

- `--criteria 'regex:...'` 简写现在真的生成机检规则 —— 此前它被当成普通描述，判定其实来自模型而非规则；
- 验证者漏判时在同一轮内追问一次，而不是重新生成整份交付物、白烧一整轮迭代；
- `mas doctor` 补上遗漏的导入 —— 干净安装时不再崩溃（v0.4.0 的发布包带上过这个缺陷）。

**v0.4.0 修了什么**（全部来自真实模型端点的实测，详见 [真实模型验证报告](docs/live-model-verification.md)）：

- 提示词不再把任务埋在 JSON 里 —— 真实模型终于知道自己要交付什么；
- 请求同时携带 `messages` 与 `input` —— 兼容只读 `input` 的 OpenAI 兼容端点；
- 评审结论归一化 —— 模型换一套 JSON 写法，结论不再被静默丢弃；
- 回退端点可独立配置超时与重试 —— 故障转移真的能回退；
- 环境变量不再被冻结进配置文件，API key 不落盘，测试不再读你的真实配置。

