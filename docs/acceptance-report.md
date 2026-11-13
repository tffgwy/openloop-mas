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
├─ skills/              mas-loop / mas-review / mas-scaffold 三个 Agent Skill（单一真源，随 wheel 分发）
├─ tests/               213 个离线测试
├─ docs/                quickstart / architecture / loop-engineering / mcp / positioning
├─ install.ps1          Windows 一键安装
├─ install.sh           macOS / Linux 一键安装
├─ .github/workflows/   CI：3 操作系统 × 4 个 Python 版本
└─ examples/            task.json / criteria.json 示例
```

---

## v0.7.1 追加验收（2026-11-13）

### 触发原因

上一轮交接时本机 `~/.codex/skills` / `~/.claude/skills` 里装的是 **0.6.0 时期的旧
skill 副本**，与仓库真源 `mas/skills/` 的哈希不一致（DRIFT）。这件事**没有任何工具
发现** —— `mas doctor` 只检查「skill 在不在」，从不检查「是不是这一版」。这暴露了
三个真实缺陷，本轮全部修掉。

### 修掉的三个真实缺陷

| 缺陷 | 修前行为 | 修后 | 证据 |
| --- | --- | --- | --- |
| **skills 版本漂移没人发现** | `mas install` 只覆盖文件、从不比较内容 | 按**内容指纹**（sha256 遍历文件树）判断漂移；安装前备份旧副本、清掉新版已删除的残留文件 | `tests/test_installer.py::SkillSyncTest`（8 条） |
| **失效的 MCP 注册被当成「已注册」** | 只要配置里有 `mas_loop` 这个键就算通过 | 判断标准改为「能不能真的启动」：解释器存在 **且** 包路径下确实有 `mas` 包；死注册会被刷新 | `tests/test_installer.py::StaleMcpRegistrationTest`（6 条） |
| **目录级备份静默失败** | `_backup()` 只处理文件，skill 目录的备份调用直接返回 `None` | 文件与目录都能备份 | `tests/test_installer.py::test_sync_backs_up_the_replaced_copy` |

另修一处**本轮自己引入的误报**：注册有效性若绑定「当前导入的模块路径」，那么从
源码运行 `mas` 时会把 `~/.mas/app` 里的正常安装判为失效。改为判定「可用性」而非
「是否等于当前构建」（`tests/test_installer.py::UsableRegistrationTest`，3 条）。

### 新增能力

- **`mas sync`** —— 把已安装的 skills 同步回包内版本；`--dry-run` 只报告不写入。
- **`mas doctor` 新增 `skills_sync` 检查项** —— 直接点名是哪个 host 的哪个 skill
  与包内版本不一致。
- **`mas doctor` 的 `mcp_codex` / `mcp_claude` 改为校验可用性**，不再只看键是否存在。
- **release workflow 新增 PyPI Trusted Publishing（OIDC）通道** —— 仓库不需要保存
  任何长期 token；原 token 路径保留为回退，两条路径互斥。

### 本轮实测证据

**内容漂移修复（隔离 HOME，真实端到端）**

```
=== 3. inject drift ===          .codex/mas-loop 与 .claude/mas-review 被手改
=== 4. doctor (after drift) ===
[WARN] skills_sync    内容与包内版本不一致: skills/mas-loop, skills/mas-review（运行 mas install 同步）
=== 5. sync --dry-run ===
[!!] 发现 2 处漂移（已安装内容 != 包内版本）:
       skills/mas-loop  .../_iso71/.codex/skills/mas-loop
       skills/mas-review  .../_iso71/.claude/skills/mas-review
[..] dry-run：未写入任何文件。运行 mas sync 修复。
=== 6. sync ===
[OK ] 已同步 6 处
[..] 旧副本已备份: .../mas-loop.bak-20261113-093121
=== 7. doctor (after sync) ===
[OK  ] skills_sync    6 个已安装 skill 均与包内版本一致
=== 8. content identity vs packaged source ===
  .codex/mas-loop: MATCH     .claude/mas-loop: MATCH
  .codex/mas-review: MATCH   .claude/mas-review: MATCH
  .codex/mas-scaffold: MATCH .claude/mas-scaffold: MATCH
```

**本机真实 HOME 的漂移修复**

修前（实测哈希）：

```
.codex     mas-loop     DRIFT 8b4733177708 75f587abfec3
.codex     mas-review   DRIFT 7b19b3b1aaf3 430461a835be
.codex     mas-scaffold DRIFT 056b85b686d9 a9abcedf7ba6
.claude    同上
```

`mas install` 之后（实测）：

```
.codex  mas-loop: MATCH    .claude  mas-loop: MATCH
.codex  mas-review: MATCH  .claude  mas-review: MATCH
.codex  mas-scaffold: MATCH .claude mas-scaffold: MATCH
```

**本机 doctor（0.7.1，真实 HOME + 真实端点）**

```
[OK  ] package        mas 0.7.1 可导入
[OK  ] skills_sync    9 个已安装 skill 均与包内版本一致
[OK  ] mcp_codex      已在 ~/.codex/config.toml 注册 mcp_servers.mas_loop
[OK  ] mcp_claude     已在 ~/.claude.json 注册 mas_loop
[OK  ] endpoint       端点可用，协议方言 = openai-responses-events
exit: 0
```

**真实模型闭环（`claude-opus-5-5` @ `http://127.0.0.1:16024/v1`，免鉴权）**

```
状态：closed（迭代 1 次，token 4138）
  [PASS] c1 (model) 实现同时使用了 `def dedupe` 和 `set`，满足该验收标准。
  [PASS] c2 (model) 交付物既在代码中按输入顺序遍历并追加元素，也在文字说明中明确解释了如何保持原有顺序。
exit: 0
```

**离线场景与基准**

```
mas demo  -> closure / stagnation / budget 三场景全 [OK]
mas bench -> 9/9 符合预期，闭环率 100%，平均 1.67 迭代 / 3.6ms / 1265.7 token，账本一致性 100%
```

**测试与 lint**

```
unittest discover -s tests  -> Ran 216 tests, OK (skipped=4), 17.5s
ruff check mas tests tools  -> All checks passed!
```

**打包（警告已清零）**

```
$ python -m build
WARNING `project.license` as a TOML table is deprecated      <- 已修
WARNING Package 'mas.skills' is absent from the packages ...  <- 已修
WARNING Package 'mas.teams' is absent from the packages ...   <- 已修
Successfully built openloop_mas-0.7.1.tar.gz and openloop_mas-0.7.1-py3-none-any.whl
```

wheel 内容核验（33 个条目）：

```
mas/skills/mas-loop/SKILL.md
mas/skills/mas-review/SKILL.md
mas/skills/mas-scaffold/SKILL.md
mas/teams/content.json  mas/teams/default.json  mas/teams/review.json
```

**全新 wheel 的一键安装验收**（全新 venv + 全新隔离 HOME + cwd 在仓库外，
确保 `python -m mas` 导入的是 site-packages 而不是仓库副本）

```
resolved package: work/_venv71\Lib\site-packages\mas    0.7.1
mas install    -> skills 6 个 / MCP codex+claude 双注册
mas doctor     -> skills_source [OK]（指向 site-packages/mas/skills）
                  skills_sync [OK] / mcp_codex [OK] / mcp_claude [OK]
mas demo       -> 三场景全 [OK]
mas sync --dry-run -> [OK] 已安装的 skills 与包内版本一致，无需同步
6 个 skill 内容与包内版本：全部 MATCH
```

**CI**

```
release   completed success  v0.7.1  33s
pages     completed success         30s
install   completed success         22s
ci        completed success         ~85s
```

**发布产物**

```
https://github.com/tffgwy/openloop-mas/releases/tag/v0.7.1
  openloop_mas-0.7.1-py3-none-any.whl  98556 B
  openloop_mas-0.7.1.tar.gz           132433 B
```

### 仍然阻塞的一项

**PyPI 尚未发布。** `https://pypi.org/pypi/openloop-mas/json` 实测返回 404，
`gh secret list` 为空。release workflow 的 Trusted Publishing 步骤已就位并
真实执行过（`pypa/gh-action-pypi-publish@release/v1`），但它在 pypi.org 上还没有
对应的 Trusted Publisher 登记，所以拿不到上传凭证。

**需要用户做一次性登记**（步骤见 `docs/publishing-to-pypi.md`）：

<https://pypi.org/manage/project/openloop-mas/settings/publishing/>

Provider = GitHub Actions / Organization = tffgwy / Repository = openloop-mas /
Workflow name = release.yml。登记后直接 `gh workflow run release.yml -f tag=v0.7.1`
补发即可，无需改代码。

替代路径：用户自行 `gh secret set PYPI_API_TOKEN` 写入一个 PyPI API token
（**不要在对话里回显明文**），workflow 会自动走 token 路径。

---

## v0.7.2 追加验收（2026-11-13）

### 触发原因

v0.7.1 修完了三个真实缺陷，但**判定语义本身没有文档契约，也没有测试钉住**。
机检是整个闭环的仲裁者——「模型说可以」永远压不过「规则说不通过」——
可如果 `section` 到底认不认 `#` 一级标题、`regex` 跨不跨行、
`lines_min` 算不算空行，这些都只存在于实现里，那么「写标准的人」就只能靠试。

v0.7.2 的主题是：**把 8 种 check 类型的精确语义公开成契约，并用测试钉死。**

### C.1 审计范围与结论

| 审计项 | 方法 | 结论 |
| --- | --- | --- |
| CLI 子命令一致性 | 枚举真实 subcommand 后全量扫描 README/README.en/全部 SKILL.md/全部 docs | **一致**，3 处命中均为误报（裸词 `mas`、散文里的 `probe`、路径里的 `tests`），无需修 |
| 8 种 check 类型语义 | 直接读 `mas/engine.py::evaluate_check` 源码 + 逐类型实测 | 与文档描述一致，但**精确口径此前只存在于实现里**，见 C.2 |

### C.2 钉死的精确语义（本轮实测确认）

| 类型 | 精确判定 | 容易踩的坑 |
| --- | --- | --- |
| `contains` | 产物含给定字符串；`arg` 可为数组，**全部命中**才算通过 | 数组漏一项整条失败 |
| `not_contains` | 产物**不含**任何给定字符串 | 数组是「一个都不能出现」 |
| `min_chars` / `max_chars` | 产物的**字符数**（含空白与换行） | 不是字数也不是字节数 |
| `regex` | `re.search` 匹配，**自动带 DOTALL**（`.` 能跨行） | 想跨行不必加 `(?s)`；命中的是子串不是全串 |
| `lines_min` | **非空行**数（空行不计） | 段落间空行不影响计数 |
| `section` | 产物里存在 `## <arg>` 这一行 | **必须是二级标题 `##`**，`#`/`###` 都不算 |
| `json_keys` | 产物须是 JSON **对象**且含全部给定键 | 产物不是合法 JSON 直接判不通过 |

另外两条边界：

- **未知类型 → `unjudged`（不通过）**，不会静默放过。
- **参数非法（如 `min_chars:很多`）→ 退回纯描述**，即该标准**没有机检**，由模型判定。
  标准宁可没有机检，也不能声称自己能检查却做不到。

### C.3 本轮改动

| 文件 | 改动 |
| --- | --- |
| `mas/skills/mas-loop/SKILL.md` | 新增「各类型的精确语义」表 + CLI 简写形式的说明 |
| `README.md` | 机检章节补精确语义表 + 简写参数非法时的退回行为 |
| `README.en.md` | 补**整节**简写写法（此前英文版完全缺失，中英不一致）并补精确语义表 |
| `tests/test_engine.py` | 新增 `CheckSemanticsTest`，9 个方法逐条钉死上表口径 |
| `mas/__init__.py` / `pyproject.toml` | 版本 0.7.1 → 0.7.2 |

`README.en.md` 此前没有简写章节，而 `README.md` 有——这是本轮发现的**真实中英文档不一致**。

### C.4 本轮实测证据

| 验收项 | 结果 | 证据 |
| --- | --- | --- |
| 全量测试 | **225 tests OK (skipped=4)**，~18.1 秒 | `unittest discover -s tests`（较 0.7.1 的 216 增 9） |
| 语义测试 | `CheckSemanticsTest` 20 → 9 个新方法全绿 | `unittest discover -s tests -p test_engine.py` → Ran 20, OK |
| lint | All checks passed | `ruff check mas tests tools` |
| 站点构建 | 9 页正常生成 | `tools/build_site.py` |
| 版本号 | `mas.__version__ == 0.7.2` | `python -c "import mas; print(mas.__version__)"` |
| CI | 4 个 workflow 全 success（ci / pages / install / release） | run 43349639396 等 |
| Release 产物 | `openloop_mas-0.7.2-py3-none-any.whl` 99,768 B + sdist | `gh release view v0.7.2` |
| Release wheel 隔离安装 | 全新 venv + 全新 HOME + cwd 在仓库外 → install/doctor/demo 全绿，6 skill 全 MATCH，MCP 双 host 注册 | `work/_e2e_wheel72.py` |
| 本机运行时升级 | `mas --version` → `openloop-mas 0.7.2`；`mas install` 后 9 个 skill 全部 MATCH | `~/.mas/app` 备份至 `~/.mas/backup/app_backup_072` |
| 常驻服务版本 | `/health` → `0.7.2` | Startup 脚本重启后 |
| 真实端点闭环 | `claude-opus-5-5` @ `127.0.0.1:16024/v1` → `closed` / 1 iter / 4619 tokens / c1+c2 双 PASS | `mas run` 实跑 |
| 离线基准 | 9/9 符合预期，闭环率 100%，1.67 迭代 / 4.2 ms / 1265.7 token | `mas bench` |
| HTTPS 鉴权矩阵 | health 200 / 无令牌 401 / 错令牌 401 / Bearer 200 / X-MAS-Token 200 | 本机 8768 真实进程 |
| OpenMausBot MCP 握手 | ok，12 tools | `POST /api/mcp/servers/openloop-mas/test` |
| OpenMausBot 端到端 | `R-d808d9ff5a` / closed / 1 iter / 3457 tokens / c1 pass | `openmausbot run MausBot` |

### C.5 v0.7.2 仍然成立的边界

1. **PyPI 仍未发布**——本轮验证的是 GitHub Release 的正式 wheel，不是 PyPI 包。
2. 真实端点实测只覆盖 `claude-opus-5-5` @ `127.0.0.1:16024/v1`（免鉴权）；Kimi / DeepSeek / Qwen / 原生 OpenAI 本机无端点，**未实测**。
3. 契约测试覆盖协议形状，不保证每厂商接受额外的 `input` 字段。
4. 判定的是结构不是事实；对抗评审发现的 `return list(seen)` 恒空 bug 仍是模型判断。
5. 真实端点下未做多轮崩溃恢复的账本重放校验。
6. 手机接入是 **tailnet only**（`https://win-io6eufe3grh.tail4c219e.ts.net:8768`），非公网 Funnel。
7. `install.ps1` 的 PATH 步骤写 HKCU 注册表，**不受隔离 HOME 影响**；后续隔离测试须加 `-SkipPath`。
8. `mas install` **不写 PATH**（pip 装时 `mas` 已可用；源码装仍需 `install.ps1`/`install.sh`）。
9. `mas uninstall` **不删 `~/.mas`**（账本保留），也不动 pip 包。
10. 不承诺流量 / 签约 / 平台批准 / 检测结果。
11. 星数说法无法核实：`gh search repos` 未找到「四个小 Skills 400+ 星」对应仓库；OpenMausBot 主仓库 `milind-soni/OpenMausBot` 实测 21,590 星。**「400+ 星」不要复述为事实**。
12. `tffgwy/openloop-mas` 当前 stars=0 / forks=0 / open issues=0。
13. OpenMausBot 端口 28799；Windows 无官方 service install，用 Startup 文件夹。
14. 本机 OpenMausBot 配置各实例 `environment` 均为空 → 本机不存在明文 API key。
15. `mas http` 的 `/health` 永远开放（只含版本号）。
16. `mas http` 绑定非 loopback 且无令牌 → **拒绝启动，退出码 2**（fail-closed）。
17. `RunResult` 字段是 `tokens_used`（不是 `tokens`）。
18. `mas/util.py` 的 `parse_criterion_shorthand` 返回 `{"desc": ...}`（无 check）表示「不是简写」。


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

## 11. OpenMausBot 集成验收（本轮实测证据）

目标：把本项目作为自定义 MCP server 挂进 [OpenMausBot](https://github.com/milind-soni/OpenMausBot)
（开源的 Grok Bot 平替，聊天界面 + 多智能体团队），让 bot 在聊天里直接调用闭环运行时。

### 11.1 已验证事实

| 项 | 证据 |
| --- | --- |
| OpenMausBot 0.1.154 安装 | `npm install -g openmausbot` → `openmausbot --version` 输出 `0.1.154` |
| Node 版本满足要求 | `node --version` → `v26.13.1`（要求 24+） |
| setup 向导完成 | 交互式向导走通：选 Claude Code 引擎 → 复用本机既有登录态 → 选 Claude Opus 5.5 → 跳过手机接入 → 落盘 `~/.openmausbot/config.json` |
| 服务器启动 | `openmausbot serve --port 28799 --no-open --no-pair` → 日志 `openmausbot server on http://127.0.0.1:28799` |
| doctor 全绿 | `openmausbot doctor` → `ready`；引擎 grok/kimi/cursor/copilot/kiro/claude/codex/chatgpt/opencodeGo/pi 可用；浏览器引擎 `agent-browser 0.36.0-omb.1` 已装 |
| MCP 握手 | `POST /api/mcp/servers/openloop-mas/test` → `{"ok":true}` + 12 个工具（mas_run / mas_status / mas_ledger / mas_tickets / mas_ticket_resolve / mas_resume / mas_report / mas_replay / mas_export / mas_teams / mas_doctor / mas_config） |
| 端到端闭环（无 check） | bot 调 `mas_run` → 返回 `status: "escalated"`, `iterations: 5`；bot 准确指出原因是 `unjudged: 该验收标准没有机检规则` —— 与修复后的机检语义一致 |
| 端到端闭环（有 check） | bot 调 `mas_run`（criteria 带 `check.type=regex`）→ 返回 `status: "closed"`, `closed: true`, `iterations: 1`，c1 判 `pass` |
| 账本一致性 | 末条账本记录 `decision: "close"`、`closed: true`、`scores[0].source = "rule"`、`note = "regex /[一-龥]{4,}/ matched"` |
| 本机 MAS 升级到 v0.2.0 | `install.ps1` 实跑 → `mas --version` = `openloop-mas 0.2.0`；`mas doctor` 10 项全 OK；`mas bench` 闭环率 100%；`mas verify` 通过；`~/.mas/config.json` 逐字节未变 |
| 开机自启（Windows） | `openmausbot service install` 在 Windows 不支持（官方仅 systemd/launchd）；改用 Startup 文件夹，`OpenMausBot-Server.cmd` 实测能独立拉起服务器（doctor ready） |
| 集成脚本 | `tools/install_openmausbot_mcp.py` 在隔离目录实跑：写入 `mcpServers`、生成 `.bak` 备份、握手测试返回 12 工具 |
| 回归测试 | `python -m unittest discover -s tests` → `Ran 76 tests ... OK` |
| release 工作流修复 | `if: ${{ secrets.* }}` 违反上下文规则导致整文件失效（0 jobs 失败 run）；改用 `env:` + `continue-on-error` 后，run `43247470908` 8 步全 success |
| Pages 实访 | `https://tffgwy.github.io/openloop-mas/` → HTTP 200，`<title>首页 Home · OpenLoop MAS</title>` |

### 11.2 修掉的真实缺陷

1. **release.yml 用 `secrets` 上下文做 `if:` 判定** → GitHub 校验器把整个工作流文件判为无效，任何 push 都生成一个 0-jobs 失败 run，且工作流名退化为文件路径。改用 `env:` 传 token、步骤内自行判空、`continue-on-error` 兜底。
2. **`--criteria` 的 JSON 字符串被当成纯文本描述** → 机检规则完全不生效，标准永远 `unjudged`，循环无法闭合。修复后实跑从 `escalated` 变 `closed`。
3. **配置文件写在 `state_dir/config.json`** → 换状态目录后配置「丢失」。固定为 `~/.mas/config.json`，`state_dir` 只管账本；新增 `MAS_CONFIG_PATH` 覆盖。
4. **向导测试写用户真实配置** → 测试改掉了用户 `~/.mas/config.json`。测试改用 `MAS_CONFIG_PATH` 隔离。

### 11.3 待验证 / 边界

1. **v0.2.0 新链路尚未在真实模型端点跑过** —— 并行候选、故障转移、对抗评审三条链路只有 mock + 注入式测试覆盖。本轮 OpenMausBot 端到端闭环用的是 **mock provider**（`config.json` 里 `provider = "mock"`），交付物正文是占位草稿，属预期行为。
2. **PyPI 未真实发布** —— `https://pypi.org/pypi/openloop-mas/json` 返回 404。工作流已就绪，需仓库 secret `PYPI_API_TOKEN`。
3. **`openmausbot service install` 不支持 Windows** —— 官方仅 systemd/launchd；本机用 Startup 回退。任务计划程序注册在本机被拒（Access is denied，与本机历史行为一致）。
4. **端口 8799 被本机 dsh-tauri 占用** —— 实际使用 28799。`openmausbot doctor` 默认会依次探测 8799 / 18799 / 28799。
5. **手机接入未配置** —— 本机无 HTTPS 入口（未启用 Tailscale / 隧道 / 反代），setup 选了「Skip for now」。
6. **CLI 安装的 OpenMausBot 不加密凭据** —— `config.json` 里的 API key 是明文；桌面版用系统钥匙串（Windows 为 DPAPI）加密，但 `config.json` 中的部分 key 仍是明文。
7. **判定的是结构不是事实**；**GitHub 星数无法预测**，不承诺流量或曝光结果。

---

## 10. 下一步可选动作

1. `pip install -e .` 后用 `mas` 命令直接跑；或直接用 `~/.mas/bin/mas.cmd`。
2. 若要把「四个小 Skills」的既有读者导流过来：在 README 加一节「和 story 系列的关系」，说明 MAS 是**通用闭环层**，story 系列是**写作域的用法**。
3. 若要接更多协议（HTTP 化 MCP、Slack/飞书机器人入口），当前仓库尚未包含，可作为 v0.2.0 议题。
4. 需要给项目配仓库 Pages / Release / 中文视频脚本，可在本机继续做。

---

## 附录 A：v0.4.8 增量验收（2026-11-13，本轮实测）

> 本文档主体是 **v0.2.0 时期**的历史验收记录，保留原文作为演进轨迹。下面列出到 v0.4.8 为止**已被推翻或已补齐**的旧结论。

### A.1 旧结论的更正

| 旧结论（v0.2.0 原文） | v0.4.8 现状 | 证据 |
| --- | --- | --- |
| 「本轮 OpenMausBot 端到端闭环用的是 **mock provider**（`config.json` 里 `provider = "mock"`），交付物正文是占位草稿」 | **已推翻**。本机 `~/.mas/config.json` 现为 `provider=openai_compat` / `model=claude-opus-5-5` @ `http://127.0.0.1:16024/v1`；OpenMausBot 端到端闭环 `R-1ee0025b0f` 是**真实端点**产出，`artifact_sha=d0f3091cb554`，`tokens_used=4009`，`iterations=1`，`status=closed` | `mas run` 输出 JSON + `openmausbot run MausBot` 事件流 |
| 「若要接更多协议（**HTTP 化 MCP**、Slack/飞书机器人入口），当前仓库尚未包含，可作为 v0.2.0 议题」 | **已推翻**。HTTP 化 MCP 自 v0.2.0 起就存在（`mas/http_server.py`，`/health` `/tools` `/call` `/events`）；**v0.4.8 起还内置了鉴权** | `mas/http_server.py`、`tests/test_http_auth.py` |
| 「**手机接入未配置** —— 本机无 HTTPS 入口（未启用 Tailscale / 隧道 / 反代）」 | **已推翻**。v0.4.8 用真实 Tailscale HTTPS 入口 `https://win-io6eufe3grh.tail4c219e.ts.net:8768` 实测过鉴权链路（health 200 / 无令牌 401 / 错令牌 401 / 正确令牌 200）。**但未留常驻配置**，验证后已 `tailscale serve --https=8768 off` 清理 | Tailscale serve 真实进程探针输出 |
| 「v0.2.0 新链路尚未在真实模型端点跑过」（并行候选、故障转移、对抗评审） | **部分更正**。真实端点已验证**主闭环链路**（`mas run` + OpenMausBot 端到端）；并行候选 / 故障转移 / 对抗评审三条链路仍**只有 mock 与注入式测试**覆盖，**未在真实端点跑过** | v0.4.8 真实端点闭环 `R-aea1a8ba15` / `R-1ee0025b0f`；`tests/` 179 条（v0.6.0） |
| 「PyPI 未真实发布」 | **仍然成立**。`https://pypi.org/pypi/openloop-mas/json` 返回 404；`gh secret list` 为空，缺 `PYPI_API_TOKEN` | 本轮 `gh secret list` + PyPI JSON 接口 |

### A.2 v0.4.8 新增的安全承诺

**`mas http` 从「无鉴权」升级为「内置鉴权 + fail-closed」** —— 这是本版本最重要的改动。修复前，绑定非本机地址即等于公开远程控制台（可开任务、可读账本）。修复后：

- 除 `/health` 外所有路由需 `Authorization: Bearer <tok>` 或 `X-MAS-Token: <tok>`；
- 用 `hmac.compare_digest` 常量时间比较；
- 绑定非 loopback 且无令牌 → **拒绝启动，退出码 2**；
- 附带修掉鉴权拒绝发生在读请求体之前导致的 keep-alive 解析错位（客户端卡死）；
- 开启鉴权后 CORS 从 `*` 钉到 `http://127.0.0.1`。

### A.3 v0.4.8 验收证据汇总（本轮工具输出）

| 项 | 结果 |
| --- | --- |
| `unittest discover -s tests` | **Ran 139 tests OK**（连跑 5 次全绿） |
| `pytest tests -q` | **139 passed, 8 subtests passed** |
| `ruff check mas tests tools` | **All checks passed!**（新增 `[tool.ruff]` 配置 + CI `lint` job） |
| 隔离 HOME 全新安装 + `tools/verify_install.py` | 7 项全 OK（断言 / MCP 12 工具 / version / doctor / demo / bench） |
| 真实进程 HTTP 鉴权探针 | health 200 / 无令牌 401 / 错令牌 401 / 正确令牌 200 |
| fail-closed 探针 | `mas http --host 0.0.0.0` 无令牌 → **exit 2** |
| 真实 Tailscale HTTPS 路径 | 401/401/200 三段对照全部符合预期 |
| CI `ci` workflow（run 43307161987） | **success** —— `lint` + `site` + `build` + 15 个 test 矩阵 |
| CI `install` workflow（run 43307161900） | **success** —— windows / macos / ubuntu 三平台 |
| CI `release` workflow（run 43307190818） | **success** —— wheel 80,422 B + sdist 104,481 B |
| CI `pages` workflow（run 43307161819） | **success** |
| Release wheel 用户路径 | 全新 venv → `openloop-mas 0.4.8`，doctor provider/endpoint 全 OK，方言 `openai-responses-events` |
| 真实端点 `mas run` | `R-aea1a8ba15` / closed / iter 1 / 3934 tokens |
| OpenMausBot 端到端 | `R-1ee0025b0f` / closed / iter 1 / 4009 tokens，MCP 握手 12 工具 |

### A.4 仍然成立的边界（v0.4.8）

1. **真实模型实测只覆盖一个端点**（`claude-opus-5-5` @ Codex Responses 桥接 `http://127.0.0.1:16024/v1`，免鉴权）。Kimi / DeepSeek / Qwen / 原生 OpenAI 本机无端点，**未实测**；方言风险由契约测试覆盖（非真实端点）。
2. **契约测试覆盖协议形状**，不保证每个厂商接受额外的 `input` 字段（严格厂商可能 400，可用 `extra_headers` / `fallbacks` 切换）。
3. **判定的是结构不是事实**；对抗评审发现的 `return list(seen)` 恒空 bug 仍是模型判断，未做语义级自动校验。
4. **真实端点下未做多轮崩溃恢复的账本重放校验** —— 账本一致性 100% 是 mock 结论。
5. **PyPI 仍未发布**：缺 secret `PYPI_API_TOKEN`。
6. **手机接入未常驻配置** —— 链路已验证可通（见 A.1），但 `mas http` 未常驻、Tailscale serve 已关闭。
7. `install.ps1` 只在本机 Windows 跑过；`install.sh` 的原生 Linux/macOS 验证依赖 install CI（v0.4.8 三平台全绿）。
8. **不承诺流量、签约、平台批准或检测结果**。

---

## 附录 B：v0.4.9 增量验收（2026-11-13，本轮实测）

> v0.4.9 的主题不是加功能，而是**修两个会让"绿灯"失去意义的缺陷**。

### B.1 修掉的两个真实缺陷

| # | 缺陷 | 严重度 | 发现方式 | 证据 |
| --- | --- | --- | --- | --- |
| 1 | **测试隔离从未生效**：`unittest discover -s tests` 下 `tests/__init__.py` 不执行，套件读真实 `~/.mas/config.json`、打真实端点 | 高（CI 绿灯不代表沙箱） | 本机 6 跑 3 败（40 秒超时） | `docs/live-model-verification.md` v0.4.9 章节；耗时 40s → 16.5s |
| 2 | **POSIX launcher 未声明绑定地址**：systemd unit / launchd plist 只指向 wrapper，绑定地址藏在脚本里 | 中（运维看不到 fail-closed 承诺） | **CI 在 ubuntu 3.10/3.13 + macos 3.10 上失败** | `be87b99` 的 ci run 失败日志 |

第 2 个缺陷是 CI 抓出来的，本机 Windows 跑不出来——这正是「跨平台项目必须让 CI 说话」的例证。

### B.2 v0.4.9 验收证据汇总（本轮工具输出）

| 验收项 | 结果 | 证据 |
| --- | --- | --- |
| 本地全量测试 | 161 tests OK (skipped=4)，~16.5–18.5 秒 | `unittest discover -s tests` |
| 连跑稳定性 | 5 跑 5 全绿，16.3–16.7 秒 | 修复前 6 跑 3 败 |
| pytest 路径 | 155 passed / 2 skipped / 8 subtests | `pytest tests -q` |
| lint | All checks passed | `ruff check mas tests tools` |
| CI | 4 个 workflow 全 success（ci / pages / install / release） | run 43318210169 等 |
| Release 产物 | wheel 81,026 B + sdist 108,894 B | `gh release view v0.4.9` |
| Release wheel 真实闭环 | `R-69722fd5e3` / closed / 1 iter / 4284 tokens | 全新 venv + `mas run` |
| OpenMausBot 端到端 | `R-9657aa76a0` / closed / 1 iter / 3713 tokens | `openmausbot run MausBot` |
| MCP 握手 | ok，12 tools | `install_openmausbot_mcp.py --verify` |
| 常驻服务版本 | `/health` → `0.4.9` | 本机 Startup 服务重启后 |
| HTTPS 鉴权矩阵 | 7 条探针全部符合预期（401/200 对照） | Tailscale HTTPS 真实进程 |
| 防回归测试 | 新增 `tests/test_isolation.py`（6 条）+ daemon 4 条 | 测试文件本身 |

### B.3 仍然成立的边界（v0.4.9）

1. 真实模型实测只覆盖一个端点；Kimi / DeepSeek / Qwen / 原生 OpenAI 未实测。
2. 契约测试覆盖协议形状，不保证每厂商接受额外的 `input` 字段。
3. 判定的是结构不是事实；`return list(seen)` 恒空 bug 仍是模型判断。
4. 真实端点下未做多轮崩溃恢复的账本重放校验。
5. **PyPI 仍未发布**（缺 `PYPI_API_TOKEN`）。
6. 手机接入是 tailnet only，非公网 Funnel。
7. `install.ps1` 只在本机 Windows 跑过。
8. `Ledger.append` 每次全量重读账本 → O(n²) 写入路径，已定位未修。
9. 不承诺流量 / 签约 / 平台批准 / 检测结果。
