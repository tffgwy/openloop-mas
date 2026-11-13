# OpenLoop MAS · 会闭合的多智能体系统

> 一个 **零依赖、纯标准库** 的多智能体闭环运行时：把「规划 → 执行 → 验证 → 复盘」写成**有验收标准、有退出条件、有过程账本**的工程循环。
> 一条命令安装，初学者也能跑起来。

**v0.7.1 修了什么**（升级不再悄悄留下旧版本）：

- **升级后 skills 会漂移，而且没人发现** —— 之前 `mas install` 只是把文件覆盖过去，从不比较内容；0.6.0 时代装下的旧 skill 副本可以一路留到 0.7.0，而 `mas doctor` 只检查「在不在」，不检查「是不是这一版」；
- 现在安装器按**内容指纹**判断漂移：装之前先备份旧副本，换成包内版本，并清掉新版已删除的残留文件；
- `mas doctor` 新增 **`skills_sync`** 检查项，直接告诉你哪几个 host 的哪个 skill 与包内版本不一致；
- **新增 `mas sync`** —— 一条命令把已安装的 skills 同步回包内版本（`--dry-run` 只报告不写入）；
- **失效的 MCP 注册不再被当成「已注册」** —— 以前只要配置里有 `mas_loop` 这个键就算通过；但如果它指向的 Python 已被删除、或指向一份已卸载的包，MCP 服务器根本起不来，而自检全绿；现在这类注册会被识别、刷新，`mas doctor` 也会报出来；
- **目录级备份修好了** —— `_backup()` 原本只处理文件，skill 目录的备份调用直接静默失败，等于没有备份；
- **发布到 PyPI 不再需要长期 token** —— release workflow 新增 **Trusted Publishing（OIDC）** 通道，仓库不需要保存任何密钥。首次发布需要在 pypi.org 登记一次，见 [docs/publishing-to-pypi.md](docs/publishing-to-pypi.md)。

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

[![ci](https://github.com/tffgwy/openloop-mas/actions/workflows/ci.yml/badge.svg)](https://github.com/tffgwy/openloop-mas/actions/workflows/ci.yml)
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

### 方式一：pip 一条命令（推荐，跨平台）

```bash
pip install openloop-mas
mas install        # 装 skills + 注册 MCP 服务器
mas demo           # 不需要任何 API Key，先看它自己跑通闭环
mas doctor         # 自检（包含 skills 版本是否漂移）
mas sync           # 把已安装的 skills 同步回包内版本
```

`mas install` 做的是**装 agent skills 到 Codex / Claude Code 的 skill 目录**、
**把 MCP 服务器注册进 `~/.codex/config.toml` 和 `~/.claude.json`**，
然后你可以直接开一个新终端，Codex 和 Claude Code 就认识这套闭环工作流了。

- 幂等：重复跑不会重复注册，也不会覆盖你已有的配置。
- 安全：任何被修改的配置文件都会先备份成 `*.bak-<时间戳>`。
- 不含密钥：安装过程不写入任何 API Key。

> 想看它装了什么，加 `--json`。

### 方式二：从源码安装

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
> 前者适合从源码装（含开发环境），后者适合 pip 安装后的机器。

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
| `section` | 产物里存在 `## <arg>` 这一行 | **必须是二级标题 `##`**，`#`/`###` 都不算 |
| `json_keys` | 产物须是 JSON **对象**且含全部给定键 | 产物不是合法 JSON 直接判不通过 |

未知类型会被判为 `unjudged`（**不通过**），不会静默放过。

### 简写写法（v0.4.2 起）

机检规则不必每次都写成 JSON。命令行可以直接用 `<类型>:<参数>`：

```bash
mas run --goal "写一个去重函数" \
  --criteria 'regex:(?s)def dedupe.*set' \
  --criteria 'min_chars:200' \
  --criteria 'contains:关键结论'
```

简写与 JSON 形式完全等价，判定同样来自引擎的确定性检查器（账本里记为 `source: rule`）。
简写解析在引擎层生效，所以从 MCP、HTTP 或账本重放进来的标准也会被同样处理。
未知的类型前缀（例如 `写清楚就好`）仍然按普通描述处理，不会被误当成机检规则。
参数不是整数的数字类前缀（例如 `min_chars:很多`）同样退回普通描述——标准宁可没有机检，也不能声称自己能检查却做不到。

没有机检规则的标准会交给模型验证者判定；但**如果模型没给出判定，默认判不通过**（`unjudged`），不会因为一句「看起来可以」就放行。
`"required": false` 的标准只记录、不阻塞闭合。

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
│  ├─ doctor.py         安装自检
│  ├─ demo.py           三个离线场景
│  └─ teams/            团队模板（default / review / content）
│  └─ skills/           Agent Skill 包（唯一真源，随 wheel 一起分发）
├─ tests/               离线测试
├─ install.ps1          Windows 一键安装
├─ install.sh           macOS / Linux 一键安装
└─ docs/                架构、方法论、MCP、定位说明
```

---

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
mas report        # 闭环复盘报告（Markdown）
mas replay        # 回放运行轨迹
mas export        # 导出账本为 Markdown 表格
mas tickets       # 人工工单
mas resume ID     # 带人工意见继续
mas teams         # 团队模板
mas doctor        # 安装自检
mas serve         # 以 MCP stdio 服务运行
mas http          # 以 HTTP + SSE 运行（远程接入；对外部署请设 MAS_HTTP_TOKEN）
```

`mas run` 退出码：`0` = 闭合，`2` = 未闭合（升级或预算耗尽）。CI 里可以直接当门禁用。

---

## 开发与测试

```bash
python -m unittest discover -s tests -v   # 213 个离线测试，不需要网络
python -m mas demo                        # 三个闭环场景
python -m mas bench                       # 离线基准测试
python -m mas verify                      # 真实闭环自检
```

测试覆盖：机检规则、闭合/停滞/预算三条终止路径、规则判定压过模型判定、未判定标准不得通过、MCP 握手与工具调用、配置合并与环境变量覆盖、SSE 流折叠、思维链清洗、遗留代码页下的 UTF-8 输出安全、并行候选选优、对抗评审聚合（all/majority/any）、后端故障转移、HTTP/SSE 真实请求、复盘报告与回放、向导与自检。

---

## 延伸阅读

| 文档 | 内容 |
| --- | --- |
| [docs/quickstart.md](docs/quickstart.md) | 5 分钟上手：安装 → 离线跑通 → 接真实模型 → 人机协同 |
| [docs/architecture.md](docs/architecture.md) | 运行时架构与数据流 |
| [docs/loop-engineering.md](docs/loop-engineering.md) | 方法论：闭环为什么这样设计 |
| [docs/mcp.md](docs/mcp.md) | MCP 接入、握手、9 个工具、协议细节与排障 |
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
