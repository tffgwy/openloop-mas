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
- **参数非法（如 `min_chars:很多`）→ 直接拒绝**（v0.7.5 起）：开跑前退出码 1、零 token。
  标准宁可不开跑，也不能声称自己能检查却做不到。

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

## v0.7.3 追加验收（2026-11-13）

### 触发原因

v0.7.2 把 8 种机检类型的语义公开成了契约。**契约写完就该用契约反过来审实现**——
本轮就是这么做的：拿 v0.7.2 写下的口径逐条对照 `evaluate_check` 的源码，
立刻发现 `section` 的实际行为和它自己在文档里声明的口径对不上。

### D.1 修掉的真实缺陷

| # | 缺陷 | 严重度 | 发现方式 | 证据 |
| --- | --- | --- | --- | --- |
| 1 | **`section` 是子串匹配，会假阳性通过** —— 判据是 `"## 结论" in artifact`，于是三件事都被误判为「有这一节」：正文里随口提到 `## 结论`、`## 结论分析` 满足了检查 `## 结论`、`### 结论`（三级标题）也算数 | 高（机检是闭环仲裁器，假阳性 = 假闭合） | 拿 v0.7.2 文档口径逐条反查实现 | 见 D.2 前后对照 |

这不是「文档写得不准」，而是**实现比承诺宽松**。机检一旦假阳性，
闭环就会在产物根本不满足标准时宣告 `closed` —— 恰好是这个项目要消灭的那种假成功。

### D.2 语义修正前后对照（本轮实测）

| 产物 | 修前 | 修后 | 说明 |
| --- | --- | --- | --- |
| `## 结论` | pass | **pass** | 本来就正确 |
| `# 结论` | fail | fail | 一级标题不算 |
| `### 结论` | **pass**（错） | **fail** | 三级标题不算 |
| `#### 结论` | **pass**（错） | **fail** | 四级标题不算 |
| `正文里提到 ## 结论这个词` | **pass**（错） | **fail** | 正文提及不算一节 |
| `## 结论分析` | **pass**（错） | **fail** | 更长标题不满足更短要求 |
| `## 结论   `（行尾空格） | pass | **pass** | 允许行尾空白 |

修后实现：判定的是**整行恰好等于 `## <标题>`**（`line.strip() == "## " + arg`）。

### D.3 这是行为变更，已在文档里明说

`section` 此前可以当「标题前缀匹配」用。现在必须写完整标题。
判定更严 → 假阳性减少；代价是**以前靠巧合通过的标准会开始失败**。
README（中英）与 SKILL.md 都加了显式变更说明，不做静默语义漂移。

### D.4 本轮改动

| 文件 | 改动 |
| --- | --- |
| `mas/engine.py` | `section` 判定改为整行精确匹配 |
| `tests/test_engine.py` | 新增 `test_section_matches_a_heading_line_not_any_substring`（3 个断言） |
| `mas/skills/mas-loop/SKILL.md` | 语义表更新为新口径 |
| `README.md` / `README.en.md` | 语义表更新 + 变更说明段落（中英各一份） |

### D.5 本轮实测证据

| 验收项 | 结果 | 证据 |
| --- | --- | --- |
| 全量测试 | **226 tests OK (skipped=4)**，~20.0 秒 | `unittest discover -s tests`（较 0.7.2 的 225 增 1） |
| engine 测试 | `CheckSemanticsTest` 21 全绿 | `unittest discover -s tests -p test_engine.py` → Ran 21, OK |
| lint | All checks passed | `ruff check mas tests tools` |
| 离线基准 | 9/9 符合预期，闭环率 100%，1.67 迭代 / 4.2 ms / 1265.7 token | `mas bench`（structured 场景用的正是 `section`，未回归） |
| 离线演示 | closure/stagnation/budget 三场景全 OK | `mas demo` |
| 语义对照 | D.2 表中 7 个用例逐条实测 | 直接调用 `evaluate_check` |
| CI | 4 个 workflow 全 success（ci / pages / install / release） | run 43352079157 等 |
| Release 产物 | `openloop_mas-0.7.3-py3-none-any.whl` 100,455 B + sdist | `gh release view v0.7.3` |
| Release wheel 隔离安装 | 全新 venv + 全新 HOME + cwd 在仓库外 → install/doctor/demo 全绿，6 skill 全 MATCH，MCP 双 host 注册 | `work/_e2e_wheel73.py` |
| 本机运行时升级 | `mas --version` → `openloop-mas 0.7.3`；9 个 skill 全部 MATCH | 备份至 `~/.mas/backup/app_backup_073` |
| 常驻服务版本 | `/health` → `0.7.3` | Startup 脚本重启后 |
| **真实端点正例** | 模型产出 `## 结论` → `closed` / 1 iter / 4031 tokens / c1 `section '## 结论' found` PASS | `mas run --criteria 'section:结论'` |
| **真实端点负例** | 模型只产出 `### 结论` → **`budget_exhausted`**（8 iter / 52967 tokens），c1 `section '## 结论' missing` **FAIL** | 同上，改目标要求三级标题 |
| 鉴权矩阵 | health 200 / 无令牌 401 / 错令牌 401 / Bearer 200 / X-MAS-Token 200 | 本机 8768 真实进程 |

**真实端点负例是本轮最关键的一条证据**：修复前，同一份产物（三级标题）
会因为子串匹配被判 PASS，循环直接 `closed`——一个**假闭合**。修复后它判 FAIL，
循环反复修不好直到预算触顶，诚实退出为 `budget_exhausted`。
这正是本项目主张的「宁可升级到人工，不假装成功」在真实模型上的体现。

### D.6 v0.7.3 仍然成立的边界

1. **PyPI 仍未发布**——验证的是 GitHub Release 的正式 wheel，不是 PyPI 包。
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
19. **`section` 语义在 v0.7.3 变更过**：更严格（整行精确匹配），旧标准若依赖前缀匹配需改写。

---

## v0.7.4 追加验收（2026-11-13）

### 触发原因

v0.7.3 的教训是：**实现比承诺宽松**。于是本轮把审计方法反过来做了一件事——
不是看「实现对不对」，而是看「**标准写漏了会怎样**」。
也就是去攻击机检规则的**参数边界**，而不是规则本身。

### E.1 修掉的真实缺陷

| # | 缺陷 | 严重度 | 发现方式 | 证据 |
| --- | --- | --- | --- | --- |
| 1 | **机检参数缺失时静默退化成恒真判断** —— 空 `regex` 匹配一切、空 `contains` 列表要求为零、缺失的 `min_chars` 退化成「至少 0 个字」 | **高**（标准根本没写，闭环却报绿灯） | 枚举 8 种类型 × 缺失/空/空列表/负数 四类边界参数实测 | 见 E.2 |
| 2 | 同上，但方向相反：`max_chars` 缺失 → 0 → **恒 FAIL** | 中（假阴性，浪费预算反复撞墙） | 同上 | 见 E.2 |

修复前后的实测对照（产物固定为 `'some artifact text'`，18 字符）：

| 类型 | 参数 | 修前 | 修后 |
| --- | --- | --- | --- |
| `contains` | 缺失 / `[]` / `""` | **pass**（假阳性） | `unjudged`（不通过） |
| `not_contains` | 缺失 / `[]` | **pass**（假阳性） | `unjudged` |
| `min_chars` | 缺失 | **pass**（假阳性，退化成 ≥0） | `unjudged` |
| `min_chars` | `-1` | **pass**（假阳性） | `unjudged`（提示需非负） |
| `max_chars` | 缺失 | fail（假阴性，退化成 ≤0） | `unjudged` |
| `lines_min` | 缺失 | **pass**（假阳性） | `unjudged` |
| `regex` | 缺失 / `""` | **pass**（假阳性，空正则匹配一切） | `unjudged` |
| `regex` | `"([未闭合"` | 抛 `re.error`（未捕获） | `unjudged`（提示无法编译） |
| `section` | 缺失 / `""` | fail（假阴性） | `unjudged` |
| `json_keys` | 缺失 / `[]` | fail（假阴性） | `unjudged` |

### E.2 修复方式：两段式，而不是只堵一个洞

1. **判定层**（`evaluate_check`）：参数不可用时返回 `pass=None`（`source: "invalid"`），
   附一句人能看懂的说明（「regex needs a regular expression」）。**永远不会**返回通过。
2. **入口层**（`LoopEngine.run` 开头）：新增 `validate_criteria()`，**在开跑前**把
   不可执行的标准一次性列出来并抛错。CLI 退出码 1、**零 token 消耗**、账本零污染。

只做第 1 步的话，配置错误仍会烧掉整个预算（v0.7.3 那个负例实测烧了 52967 token
才退出 `budget_exhausted`）。只做第 2 步的话，绕过入口的调用路径（账本重放、
内部构造的 criterion）仍会假通过。两段都做才封死。

### E.3 本轮改动

| 文件 | 改动 |
| --- | --- |
| `mas/engine.py` | 新增 `_ARG_REQUIRED` / `_check_arg_problem()` / `validate_criteria()`；`evaluate_check` 前置校验；`run()` 入口快速失败 |
| `tests/test_engine.py` | 新增 `DegenerateArgumentTest`（8 个方法，覆盖 8 种类型 × 缺失/空/空列表/负数/非法正则）；修正 1 条与新语义冲突的旧断言 |
| `mas/skills/mas-loop/SKILL.md` | 新增「参数写漏了 = 标准作废」表 |
| `README.md` / `README.en.md` | 同表 + 变更说明段落（中英各一份） |

### E.4 本轮实测证据

| 验收项 | 结果 | 证据 |
| --- | --- | --- |
| 全量测试 | **235 tests OK (skipped=4)**，~19.4 秒 | `unittest discover -s tests`（较 0.7.3 的 226 增 9） |
| engine 测试 | 30 全绿 | `unittest discover -s tests -p test_engine.py` → Ran 30, OK |
| lint | All checks passed | `ruff check mas tests tools` |
| 离线基准 | 9/9 符合预期，闭环率 100% | `mas bench` |
| 离线演示 | closure/stagnation/budget 三场景全 OK | `mas demo` |
| 参数边界实测 | E.1 表中 10 类边界逐条实测 | 直接调用 `evaluate_check` |
| CLI 快速失败 | `错误：验收标准里有无法执行的机检规则: - c1: missing argument: regex needs a regular expression`，退出码 1 | `mas run --criteria '{"check":{"type":"regex","arg":""}}'` |
| 账本不污染 | 快速失败后账本条目数 = 0 | `engine.ledger.entries()` 实测 |

### E.5 v0.7.4 仍然成立的边界

1. **PyPI 仍未发布**——验证的是 GitHub Release 的正式 wheel，不是 PyPI 包。
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
19. **`section` 语义在 v0.7.3 变更过**：更严格（整行精确匹配），旧标准若依赖前缀匹配需改写。
20. **机检参数校验在 v0.7.4 加入**：缺参数的标准会在开跑前报错，不再静默恒真通过。

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
8. ~~`Ledger.append` 每次全量重读账本 → O(n²) 写入路径，已定位未修。~~ **已更正：该缺陷早已在 `62d04a4` 修好**（按文件身份缓存条目数，2000 条从 46.5s 降到 1.38s），本条为过时记录，现保留原样并标注更正。
9. 不承诺流量 / 签约 / 平台批准 / 检测结果。

## F. v0.7.5 — 简写形式的坏参数不再静默降级

### F.1 问题

v0.7.4 让 **JSON 形式**的坏参数在开跑前快速失败，但**简写形式**（`--criteria 'regex:'`）
仍然静默降级成纯描述。于是同一份标准有两种待遇：

- `--criteria '{"desc":"x","check":{"type":"regex"}}'` → 退出码 1、零 token、账本零污染
- `--criteria 'regex:'` → 退回纯描述 → **没有机检** → 交给模型判定 → 烧满 5 轮迭代 → 升级人工工单

实测（v0.7.4 wheel，隔离环境，mock provider）：

| 简写 | v0.7.4 行为 | v0.7.5 行为 |
| --- | --- | --- |
| `regex:` | escalated（5 迭代 / 2884 token） | **exit 1 / 0 token / 账本 0 行** |
| `contains:` | escalated（5 迭代 / 2925 token） | **exit 1 / 0 token / 账本 0 行** |
| `min_chars:` | escalated（5 迭代 / 2943 token） | **exit 1 / 0 token / 账本 0 行** |
| `min_chars:many` | 退回纯描述 | **exit 1（invalid argument: ... got 'many'）** |
| `min_chars:-1` | 退回纯描述 | **exit 1（non-negative count, got -1）** |
| `regex:([unclosed` | 退回纯描述 | **exit 1（cannot compile /([unclosed/）** |
| `unknown_type:abc` | 退回纯描述 | 退回纯描述（**行为不变**，见下） |

### F.2 修复

- `mas/util.py`：**已知类型**的简写，即使参数不可用也保留 `check`（不再退回纯描述）；
  只有**类型名本身未知**才退回纯描述——那不是参数写漏，而是压根没打算写机检。
- `mas/engine.py`：`_check_arg_problem` 把「空字符串参数」判为 *missing* 而非 *invalid*，
  人话提示更准。

### F.3 契约

简写与 JSON 两种写法**待遇一致**：参数写漏或写错 → 开跑前退出码 1、零 token、账本零污染。
未知类型仍退回纯描述（该标准没有机检，由模型判定）。

### F.4 验证

- `unittest discover -s tests` → **241 tests OK (skipped=4)**
- `ruff check mas tests tools` → All checks passed
- 隔离环境 CLI 实测：上表 6 类坏简写全部 exit=1、`runs.jsonl` 0 行

### F.5 发布产物与真实端点复验（v0.7.5）

**Release 产物**（`gh release view v0.7.5`）：

- `openloop_mas-0.7.5-py3-none-any.whl`（103167 B）
- `openloop_mas-0.7.5.tar.gz`（153481 B）
- CI 四个 workflow 全绿：`ci` / `pages` / `install` / `release`

**全新 wheel 隔离安装 E2E**（新 venv + 新 HOME + 仓库外 cwd）：

| 检查 | 结果 |
| --- | --- |
| `pip install --no-index <wheel>` | `Successfully installed openloop-mas-0.7.5` |
| 解析到的包路径 | `_venv75\Lib\site-packages\mas`（不是仓库源码） |
| `mas --version` | `0.7.5` |
| `mas install` | exit 0，skills 装到 6 个，codex/claude 双端 MCP 注册成功 |
| `mas doctor --no-network` | exit 0，全项 OK（含 `skills_sync 6 个已安装 skill 均与包内版本一致`） |
| `mas demo` | exit 0，closure / stagnation / budget 三场景全 OK |
| skill 指纹（6 组 codex/claude × 3 skill） | 全部 **MATCH** |
| MCP 注册 | codex `True` / claude `True` |

**v0.7.5 契约在发布产物上复验**（隔离环境，坏简写）：

| 简写 | exit | 账本行数 | 提示 |
| --- | --- | --- | --- |
| `regex:` | 1 | 0 | `missing argument: regex needs a regular expression` |
| `contains:` | 1 | 0 | `missing argument: contains needs a string or a list of strings to look for` |
| `min_chars:` | 1 | 0 | `missing argument: min_chars needs a minimum character count` |
| `min_chars:many` | 1 | 0 | `invalid argument: ... got 'many'` |
| `min_chars:-1` | 1 | 0 | `invalid argument: ... needs a non-negative count, got -1` |
| `regex:([unclosed` | 1 | 0 | `invalid argument: regex cannot compile /([unclosed/: ...` |

**真实端点正例**（`claude-opus-5-5` @ `http://127.0.0.1:16024/v1`，免鉴权）：

```
mas run --goal "写一个 Python 去重函数 dedupe，保留首次出现顺序" \
  --criteria "regex:def dedupe.*set" --criteria "min_chars:120"
-> exit 0，closed，迭代 1 次，token 5214
-> [PASS] c1 (rule) regex /def dedupe.*set/ matched
-> [PASS] c2 (rule) length 489 >= 120
```

**真实端点负例**（同一端点，坏标准）：

```
mas run --goal "随便写点东西" --criteria "regex:"
-> exit 1，错误：验收标准里有无法执行的机检规则:
     - c1: missing argument: regex needs a regular expression
-> 账本行数 before=100 / after=100，delta=0（零污染、零 token）
```

**常驻 HTTP 服务**（重启后）：`/health` → 200，`version: 0.7.5`

**鉴权矩阵**（v0.7.5）：

| 场景 | 结果 |
| --- | --- |
| `/health` 无令牌 | 200（只含版本号） |
| `/tools` 无令牌 | 401 |
| `/tools` 错令牌 | 401 |
| `/tools` `Authorization: Bearer <token>` | 200 |
| `/tools` `X-MAS-Token: <token>` | 200 |

**OpenMausBot 集成**（端口 28799）：

- MCP 握手：`ok: True`，**12 个工具**
  （`mas_run` / `mas_status` / `mas_ledger` / `mas_tickets` / `mas_ticket_resolve` /
  `mas_resume` / `mas_report` / `mas_replay` / `mas_export` / `mas_teams` /
  `mas_doctor` / `mas_config`）
- 端到端闭环：`exit 0`，`status: closed`，iterations 1，tokens 3497，
  c1 regex `[一-龥]{4,}` 命中 → pass

**本机运行时升级**：备份 `~/.mas/backup/app_backup_075`，`mas install` 后
`mas doctor` 全绿（`package mas 0.7.5 可导入`、`skills_sync 9 个已安装 skill 均与包内版本一致`）。

## G. v0.7.6 — 未知机检类型不再被模型"顺手闭合"

### G.1 问题

v0.7.4 引入 `source="invalid"` 来表示"机检存在但判不了"，但**合并层没认它**：

- `evaluate_check` 对未知类型返回 `pass=None, source="none"`；
- `_merge_verdict_scores` 用 `check.get("pass") is not None` 判断"这条标准有机检"；
- 于是 `pass=None` 被当成"没有机检" → **落回模型判定** → 模型说"看起来没问题"就闭合。

这与文档承诺的「未知类型 → `unjudged`（不通过）」直接矛盾——判定层是 unjudged，
合并层却把它放行了。

复现（v0.7.5 代码，本机直接调用）：

```
evaluate_check(未知类型) -> {'pass': None, 'note': 'unknown check type: mystery', 'source': 'none'}
_merge_verdict_scores(模型说 pass=True) -> {'pass': True, 'source': 'model'}
=> 该标准被判为: PASS（假闭合）
```

### G.2 修复

- `mas/engine.py` / `evaluate_check`：未知类型返回 `source="invalid"`（与参数非法同一类）；
- `mas/engine.py` / `_merge_verdict_scores`：合并层显式处理 `source=="invalid"`
  → `pass=False`、`source="invalid"`，**模型永远不能覆盖一条判不了的机检**；
- `mas/engine.py` / `validate_criteria`：JSON 形式的未知类型名在开跑前被拒绝，
  并列出所有已知类型。

### G.3 契约

| 情况 | 判定层 | 合并层 | 开跑前 |
| --- | --- | --- | --- |
| 有机检、参数合法 | `pass=True/False`, `source=rule` | 由规则决定 | 正常跑 |
| 有机检、参数非法 | `pass=None`, `source=invalid` | **不通过**（模型无权覆盖） | **拒绝，exit 1** |
| 有机检、类型名未知（JSON） | `pass=None`, `source=invalid` | **不通过** | **拒绝，exit 1** |
| 无机检 | `pass=None`, `source=none` | 由模型判定 | 正常跑 |
| 简写、类型名未知 | 无 check | 由模型判定 | 正常跑（压根没打算写机检） |

### G.4 验证

- `unittest discover -s tests` → **245 tests OK (skipped=4)**（新增 `UnknownCheckTypeCannotFakeClosureTest`，4 个方法）
- `ruff check mas tests tools` → All checks passed
- `mas bench` → 9/9、闭环率 100%、账本一致性 100%
- `mas demo` → 三场景全 OK
- 隔离环境实测（真实 provider）：

| 输入 | exit | 账本 |
| --- | --- | --- |
| JSON `{"check":{"type":"mystery","arg":"x"}}` | 1 | 0 行 |
| JSON `{"check":{"type":"mystery"}}` | 1 | 0 行 |
| 简写 `mystery:abc` | 0 | 2 行（**契约不变**：压根没打算写机检） |
| JSON `{"check":{"type":"contains","arg":"循环"}}` | 0 | 4 行（正常闭环，rule 命中） |

### G.5 发布产物与真实端点复验（v0.7.6）

**Release 产物**（`gh release view v0.7.6`）：

- `openloop_mas-0.7.6-py3-none-any.whl`（103673 B）
- `openloop_mas-0.7.6.tar.gz`（154889 B）
- CI 四个 workflow 全绿：`ci` / `pages` / `install` / `release`

**全新 wheel 隔离安装 E2E**（新 venv + 新 HOME + 仓库外 cwd）：

| 检查 | 结果 |
| --- | --- |
| `pip install --no-index <wheel>` | `Successfully installed openloop-mas-0.7.6` |
| 解析到的包路径 | `_venv76\Lib\site-packages\mas`（不是仓库源码） |
| `mas --version` | `0.7.6` |
| `mas install` | exit 0，skills 装到 6 个，codex/claude 双端 MCP 注册成功 |
| `mas doctor --no-network` | exit 0，全项 OK（含 `skills_sync 6 个已安装 skill 均与包内版本一致`） |
| `mas demo` | exit 0，closure / stagnation / budget 三场景全 OK |
| skill 指纹（6 组） | 全部 **MATCH** |
| MCP 注册 | codex `True` / claude `True` |

**契约在发布产物上复验**：

| 输入 | exit | 账本行数 | 提示 |
| --- | --- | --- | --- |
| `regex:` | 1 | 0 | `missing argument: regex needs a regular expression` |
| `contains:` | 1 | 0 | `missing argument: contains needs a string or a list of strings to look for` |
| `min_chars:many` | 1 | 0 | `invalid argument: ... got 'many'` |
| JSON `{"check":{"type":"mystery","arg":"x"}}` | 1 | 0 | `unknown check type: 'mystery' (known types: contains, json_keys, ...)` |

**真实端点正例**（`claude-opus-5-5` @ `http://127.0.0.1:16024/v1`，免鉴权）：

```
mas run --goal "写一个 Python 去重函数 dedupe，保留首次出现顺序" \
  --criteria "regex:def dedupe.*set" --criteria "min_chars:120"
-> exit 0，closed，迭代 1 次，token 4711
-> [PASS] c1 (rule) regex /def dedupe.*set/ matched
-> [PASS] c2 (rule) length 645 >= 120
```

**真实端点负例**（同一端点，未知类型机检）：

```
mas run --goal "随便写点东西" \
  --criteria '{"id":"c1","desc":"产物含关键词","check":{"type":"mystery","arg":"x"}}'
-> exit 1，错误：验收标准里有无法执行的机检规则:
     - c1: unknown check type: 'mystery' (known types: contains, json_keys, lines_min, max_chars, min_chars, not_contains, regex, section)
-> 账本 delta = 0（零 token、零污染）
```

**常驻 HTTP 服务**（重启后）：`/health` → 200，`version: 0.7.6`

**鉴权矩阵**（v0.7.6）：health 200 / 无令牌 401 / 错令牌 401 / Bearer 200 / X-MAS-Token 200

**本机运行时升级**：备份 `~/.mas/backup/app_backup_076`，`mas install` 后
`mas doctor` 全绿（`package mas 0.7.6 可导入`、`skills_sync 9 个已安装 skill 均与包内版本一致`）。

## H. v0.7.7 — 模型的判定不再靠真值转换

### H.1 问题

合并层用 `bool(review.get("pass"))` 读取模型判定。Python 里 `bool("false")` 是 **True**：

```
merge 直接消费未归一化 verdict -> [{'pass': True, 'source': 'model'}]   # 模型说了 false
merge 消费归一化后 verdict     -> [{'pass': False, 'source': 'model'}]
```

主路径靠 `normalize_verdict` 的 `_as_bool` 挡住了，但**合并层没有最后一道防线**——
任何绕过归一化的调用方（MCP/HTTP 直传判定的扩展路径、未来新增的评审角色）都会踩到它。

顺带发现 `_as_bool` 不认数字字符串：模型回 `"pass": "1"` 会被当成读不懂 →
标准变成 `model-missing` → 白烧一轮迭代再追问。

### H.2 修复

- `mas/engine.py` / `_merge_verdict_scores`：改用 `_as_bool` 读取判定含义；
  读不懂的值按 `model-missing`（未判定）处理，绝不猜。
- `mas/util.py` / `_as_bool`：数字字符串 `"0"` / `"1"` 归入 false / true。

### H.3 实测

| 模型回的 `pass` | 修前 | 修后 | source |
| --- | --- | --- | --- |
| `"false"` | **True（假闭合）** | False | model |
| `"true"` | True | True | model |
| `"no"` / `"yes"` | — | False / True | model |
| `"0"` / `"1"` | 未判定 | False / True | model |
| `"maybe"` / `""` / `None` | 未判定 | 未判定 | model-missing |
| `False` / `True` | — | False / True | model |

### H.4 验证

- `unittest discover -s tests` → **252 tests OK (skipped=4)**
  （新增 `ModelVerdictTruthinessTest`，6 个方法；`UnknownCheckTypeCannotFakeClosureTest` 补多评审路径 1 个方法）
- `ruff check mas tests tools` → All checks passed
- `mas bench` → 9/9、闭环率 100%、账本一致性 100%
- `mas demo` → 三场景全 OK

### H.5 发布产物与真实端点复验（v0.7.7）

**Release 产物**（`gh release view v0.7.7`）：

- `openloop_mas-0.7.7-py3-none-any.whl`（104317 B）
- `openloop_mas-0.7.7.tar.gz`（156453 B）
- CI 四个 workflow 全绿：`ci` / `pages` / `install` / `release`

**全新 wheel 隔离安装 E2E**（新 venv + 新 HOME + 仓库外 cwd）：

| 检查 | 结果 |
| --- | --- |
| `pip install --no-index <wheel>` | `Successfully installed openloop-mas-0.7.7` |
| 解析到的包路径 | `_venv77\Lib\site-packages\mas`（不是仓库源码） |
| `mas --version` | `0.7.7` |
| `mas install` | exit 0，双端 MCP 注册成功 |
| `mas doctor --no-network` | exit 0，全项 OK（含 `skills_sync 6 个已安装 skill 均与包内版本一致`） |
| `mas demo` | exit 0，closure / stagnation / budget 三场景全 OK |
| skill 指纹（6 组） | 全部 **MATCH** |
| MCP 注册 | codex `True` / claude `True` |

**契约在发布产物上复验**：

| 输入 | exit | 账本行数 |
| --- | --- | --- |
| `regex:` | 1 | 0 |
| `min_chars:many` | 1 | 0 |
| JSON `{"check":{"type":"mystery","arg":"x"}}` | 1 | 0 |

**真实端点正例**（`claude-opus-5-5` @ `http://127.0.0.1:16024/v1`，免鉴权）：

```
-> exit 0，closed，迭代 1 次，token 4978
-> [PASS] c1 (rule) regex /def dedupe.*set/ matched
-> [PASS] c2 (rule) length 427 >= 120
```

**真实端点负例**（同一端点，JSON 未知类型机检）：

```
-> exit 1，错误：验收标准里有无法执行的机检规则:
     - c1: unknown check type: 'mystery' (known types: contains, json_keys, ...)
-> 账本 delta = 0
```

**常驻 HTTP 服务**（重启后）：`/health` → 200，`version: 0.7.7`

**鉴权矩阵**（v0.7.7）：health 200 / 无令牌 401 / 错令牌 401 / Bearer 200 / X-MAS-Token 200

**本机运行时升级**：备份 `~/.mas/backup/app_backup_077`，`mas install` 后
`mas doctor` 全绿（`package mas 0.7.7 可导入`、`skills_sync 9 个已安装 skill 均与包内版本一致`）。

**OpenMausBot 集成**（v0.7.7，端口 28799）：

- MCP 握手：`ok: True`，**12 个工具**
  （`mas_run` / `mas_status` / `mas_ledger` / `mas_tickets` / `mas_ticket_resolve` /
  `mas_resume` / `mas_report` / `mas_replay` / `mas_export` / `mas_teams` /
  `mas_doctor` / `mas_config`）
- 端到端闭环：`exit 0`，`status: closed`，iterations 1，tokens 3485，
  c1 regex `[一-龥]{4,}` 命中 → pass

## I. v0.7.8 — 可选标准不再谎报通过

### I.1 问题

`required: false` 的标准在合并层被**硬编码**成通过：

```python
if not criterion.required:
    merged.append({"id": criterion.id, "pass": True, "note": "optional criterion",
                   "source": "skipped"})
```

文档承诺的是「只做记录」，实际做的是**丢弃**：一条带机检规则的可选标准，
机检结果被完全扔掉，报告里显示 `PASS / optional criterion`。
读者看到的是绿灯，而规则其实没通过——这与本项目一路修掉的
「假闭合」是同一类缺陷，只是发生在可选标准上。

更糟的是它本来就没法修：合并层一旦把机检的真实结果写进去
（`pass: False`），闭合判定 `all(bool(s["pass"]) for s in scores)`
根本不区分 optional 与 required，**一条可选标准失败就会让循环永远闭合不了**。

### I.2 修复

- `mas/engine.py` / `_merge_verdict_scores`：
  - 可选标准有机检 → 保留机检真实结果（`source: rule`），不再谎报通过；
  - 可选标准机检本身坏了（`source: invalid`）→ 记为 `invalid`，仍然不阻塞；
  - 可选标准没有机检 → 采纳模型判定并记录（原来整条丢弃）；
  - 模型判定读不懂 → 记为 `skipped`（可选标准**不触发**二次追问，
    否则一条可有可无的标准会让循环反复回头问评审）；
  - 所有 score 新增显式 `blocking` 字段：必选为 `True`，可选为 `False`。
- `mas/engine.py` / `run()`：闭合判定只看 `blocking` 的标准——
  `passed = bool(scores) and all(bool(s["pass"]) for s in blocking)`；
  `issues` 也只收必选标准的失败（可选失败是信息，不是返工理由）。
- `mas/report.py`：报告新增 `optional_failed` 字段；Markdown 里可选标准标注
  「（可选，不阻塞闭合）」，失败的可见但不污染 `failed`。
- `mas/cli.py` / `mas/report.py`：输出里给可选标准打上「，可选」标记。

### I.3 实测

| 场景 | 修前 | 修后 |
| --- | --- | --- |
| 可选标准 + 机检通过 | `PASS / optional criterion`（来源被抹掉） | `PASS / rule`，note 为机检原文 |
| 可选标准 + 机检失败 | `PASS / optional criterion`（**谎报**） | `FAIL / rule`，note 为机检原文，**不阻塞闭合** |
| 可选标准无机检 | `PASS / optional criterion`（模型判定被丢弃） | 记录模型判定结果；读不懂则 `skipped` |
| 可选标准机检参数坏了 | `PASS / optional criterion` | `FAIL / invalid`，不阻塞闭合 |
| 必选标准失败 | 不闭合 | 不闭合（`blocking: true`） |

### I.4 验证

- `unittest discover -s tests` → **257 tests OK (skipped=4)**
  （新增 5 个用例：可选标准保留真实机检结果、记录模型判定、
  必选标准 `blocking=true`、可选失败不阻塞闭合、必选失败仍阻塞闭合）
- `ruff check mas tests tools` → All checks passed
- `mas bench` → 9/9 符合预期、闭环率 100%、平均迭代 1.67、账本一致性 100%
- `mas demo` → closure / stagnation / budget 三场景全 OK


### I.5 发布产物与真实端点复验（v0.7.8）

**Release 产物**（`gh release view v0.7.8`）：

- `openloop_mas-0.7.8-py3-none-any.whl`（105198 B）
- `openloop_mas-0.7.8.tar.gz`（158096 B）
- CI 四个 workflow 全绿：`ci` / `pages` / `install` / `release`

**全新 wheel 隔离安装 E2E**（新 venv + 新 HOME + 仓库外 cwd）：

| 检查 | 结果 |
| --- | --- |
| `pip install --no-index <wheel>` | `Successfully installed openloop-mas-0.7.8` |
| 解析到的包路径 | `_venv78\Lib\site-packages\mas`（不是仓库源码） |
| `mas --version` | `0.7.8` |
| `mas install` | exit 0，双端 MCP 注册成功 |
| `mas doctor --no-network` | exit 0，全项 OK（含 `skills_sync 6 个已安装 skill 均与包内版本一致`） |
| `mas demo` | exit 0，closure / stagnation / budget 三场景全 OK |
| skill 指纹（6 组） | 全部 **MATCH** |
| MCP 注册 | codex `True` / claude `True` |

**契约在发布产物上复验**：

| 输入 | exit | 账本行数 |
| --- | --- | --- |
| `regex:` | 1 | 0 |
| `min_chars:many` | 1 | 0 |
| JSON `{"check":{"type":"mystery","arg":"x"}}` | 1 | 0 |

**真实端点实测**（`claude-opus-5-5` @ `http://127.0.0.1:16024/v1`，免鉴权）：

任务：写一个 Python 去重函数 `dedupe`，返回 set；
c1 = `regex:def dedupe.*set`（必选）、c2 = `min_chars:120`（必选）、
c3 = `min_chars:999999`（**可选**，模型不可能满足）。

```
-> exit 0，closed，迭代 1 次，token 4602
-> [PASS] c1 (rule) regex /def dedupe.*set/ matched
-> [PASS] c2 (rule) length 282 >= 120
-> [FAIL] c3 (rule) length 282 < 999999   ← 可选标准真实失败，可见
-> issues: []                            ← 但不阻塞闭合，也没有触发返工
```

**报告渲染**（同一份账本）：

```
- 验收判定：
  - [PASS] `c1` regex /def dedupe.*set/ matched
  - [PASS] `c2` length 282 >= 120
  - [FAIL] `c3` length 282 < 999999（可选，不阻塞闭合）
```

`failed: []`（必选失败清单为空）、`optional_failed: [c3]`（可选失败单独列出）。

**常驻 HTTP 服务**（重启后）：`/health` → 200，`version: 0.7.8`

**鉴权矩阵**（v0.7.8）：health 200 / 无令牌 401 / 错令牌 401 / Bearer 200 / X-MAS-Token 200

**本机运行时升级**：备份 `~/.mas/backup/app_backup_078`，`mas install` 后
`mas doctor` 全绿（`package mas 0.7.8 可导入`、`skills_sync 9 个已安装 skill 均与包内版本一致`）。

**OpenMausBot 集成**（v0.7.8，端口 28799）：

- MCP 握手：`ok: True`，**12 个工具**
- 端到端闭环：`exit 0`，`status: closed`，iterations 1，tokens 3539，
  c1 regex `[一-龥]{4,}` 命中 → pass

### I.6 PyPI 发布状态（本轮唯一未完成项）

`release.yml` 的 PyPI 发布步骤**真实执行过**，失败原因为 PyPI 返回：

```
invalid-publisher: valid token, but no corresponding publisher
(Publisher with matching claims was not found)
```

即 **pypi.org 上还没有为 `openloop-mas` 登记 Trusted Publisher**。
`https://pypi.org/pypi/openloop-mas/json` 仍返回 404（名称未被占用，可注册）。

需要用户一次性登记（唯一需要人做的步骤）：

| 字段 | 值 |
| --- | --- |
| Provider | GitHub Actions |
| Organization | tffgwy |
| Repository | openloop-mas |
| Workflow name | release.yml |
| Environment name | 留空 |

地址：<https://pypi.org/manage/project/openloop-mas/settings/publishing/>

登记后补发：

```bash
gh workflow run release.yml -f tag=v0.7.8
```

替代路径：`gh secret set PYPI_API_TOKEN`（**不要在对话或日志里回显明文**）。

**顺带修掉的一个真实缺陷**（commit `4e23b2d`）：原来的状态上报步骤只看
secret 是否存在，于是两种情况都会说错——Trusted Publishing 失败（非致命）
仍然打印「Published to PyPI.」，而 Trusted Publishing 成功（无 token 路径）
反而打印「未发布」。现在改为直接查 pypi.org 的版本 JSON 判断真实结果，
并把结论写进 job summary；失败时给出具体的登记地址与参数。

### I.7 边界说明

- 可选标准的机检**照常执行**，只是不参与闭合判定；不因为可选就跳过检查。
- `blocking` 字段是新增的公开字段，旧账本记录没有该字段时按 `True` 处理
  （历史记录语义不变）。

## J. v0.7.9 — 标志读含义，JSON 数组不再整体退化

### J.1 问题（两个）

**问题一：`required` 标志被真值转换。**
`Criterion.from_raw` 用 `bool(raw.get("required", True))`。Python 里 `bool("false")` 是 `True`，
所以 `{"required": "false"}`（**字符串**）被当成必选标准。标准经 JSON / MCP / shell 传递时
带上引号是极常见的写法，用户写的是"这条是可选的"，引擎执行的是"这条必须过"——
循环于是永远闭合不了。这与 v0.7.7 修的模型判定真值转换同源：**同一个值，读含义而不是读真值**。

**问题二：`--criteria` 的 JSON 数组形式整体退化。**
`mas/cli.py` 的 `_parse_criterion` 只判断 `stripped.startswith("{")`，数组以 `[` 开头，
于是落到 `parse_criterion_shorthand` → 无 check → **原样返回字符串**。
结果：一整个数组被当成**一条纯文本描述**，机检一次都没跑，闭合其实来自模型说"看起来没问题"。

实测（修前，真实端点）：

```
exit: 0  status: closed
  [PASS] c1 (model) 原文同时包含 "def dedupe" 和 "set"...
--- 交付物预览 ---
## 本轮修复
- 已处理：c1: no machine check          ← 机检根本没跑
```

### J.2 修复

- `mas/engine.py`：新增 `_required_flag(value)`——
  `bool` 直接取用；否则走 `_as_bool` **读含义**
  （`"false"` / `"0"` / `"no"` → 可选；`"true"` / `"1"` / `"yes"` → 必选）；
  **读不懂的值（`None`、缺失、`"maybe"`、空容器）一律按必选处理**——
  严格解读是安全的一侧，读不懂的标志绝不能悄悄放宽标准。
  `Criterion.from_raw` 的两处（字符串简写路径 + 字典路径）都改用它。
- `mas/cli.py`：`_parse_criterion` 现在接受 `{` 与 `[` 两种开头，数组 `json.loads` 后
  **返回 list**；`_load_criteria` 遇到 list 时 `extend` 而非 `append`（否则数组会被当成一条标准）。

### J.3 实测

`_required_flag` 的读值结果（本轮实测）：

| 输入 | 结果 | 输入 | 结果 |
| --- | --- | --- | --- |
| `"false"` | `False` | `"true"` | `True` |
| `"no"` | `False` | `"yes"` | `True` |
| `"0"` | `False` | `"1"` | `True` |
| `False` / `0` | `False` | `True` / `1` | `True` |
| `None` / 缺失 | `True`（必选） | `"maybe"` / `[]` | `True`（必选） |

**CLI 真实端点复验**（`claude-opus-5-5` @ `http://127.0.0.1:16024/v1`），
JSON 数组形式 + `"required": "false"` 字符串标志：

```
exit: 0
状态：closed（迭代 1 次，token 4310）
  [PASS] c1 (rule) all required strings present
  [FAIL] c2 (rule)，可选 length 249 < 999999
```

账本里的 score（`blocking` 字段如实区分）：

```json
[{"id": "c1", "pass": true,  "note": "all required strings present", "source": "rule", "blocking": true},
 {"id": "c2", "pass": false, "note": "length 249 < 999999", "source": "rule",
  "optional": true, "blocking": false}]
```

即：**JSON 数组里的机检真的跑了**（`source: rule`，修前是 `model`），
字符串 `"false"` 真的被读成可选（`blocking: false`），可选失败可见但不阻塞闭合。

**必选标准失败的负例**（同一端点，c1 = `min_chars:999999` 必选）：

```
exit: 2
状态：escalated（迭代 2 次，token 102601）
  [FAIL] c1 (rule) length 336218 < 999999
已升级为人工工单：T-20261113-131408-5a77
```

必选标准失败 → 不闭合 → 升级人工工单 → 退出码 2，行为正确。

### J.4 验证

- `unittest discover -s tests` → **268 tests OK (skipped=4)**
  （新增 11 个用例：`RequiredFlagTest` 6 个 + `CriterionJsonArrayTest` 5 个）
- `ruff check mas tests tools` → All checks passed
- `mas bench` → 9/9 符合预期、闭环率 100%、平均迭代 1.67、账本一致性 100%
- `mas demo` → closure / stagnation / budget 三场景全 OK

### J.5 发布产物与真实端点复验（v0.7.9）

**Release 产物**（`gh release view v0.7.9`）：

- `openloop_mas-0.7.9-py3-none-any.whl`（106059 B）
- `openloop_mas-0.7.9.tar.gz`（161080 B）
- CI 四个 workflow 全绿：`ci` / `pages` / `install` / `release`

**全新 wheel 隔离安装 E2E**（新 venv + 新 HOME + 仓库外 cwd）：

| 检查 | 结果 |
| --- | --- |
| `pip install --no-index <wheel>` | `Successfully installed openloop-mas-0.7.9` |
| 解析到的包路径 | `_venv79\Lib\site-packages\mas`（不是仓库源码） |
| `mas --version` | `0.7.9` |
| `mas install` | exit 0，双端 MCP 注册成功 |
| `mas doctor --no-network` | exit 0，全项 OK（含 `skills_sync 6 个已安装 skill 均与包内版本一致`） |
| `mas demo` | exit 0，closure / stagnation / budget 三场景全 OK |
| skill 指纹（6 组） | 全部 **MATCH** |
| MCP 注册 | codex `True` / claude `True` |

**契约在发布产物上复验**（账本零污染）：

| 输入 | exit | 账本行数 |
| --- | --- | --- |
| `regex:` | 1 | 0 |
| `min_chars:many` | 1 | 0 |
| JSON `{"check":{"type":"mystery","arg":"x"}}` | 1 | 0 |

**两个新修复在发布产物上复验**：

```
== JSON 数组 + "required":"false" ==
  exit 0，closed，迭代 1
  [PASS] c1 (rule) all required strings present        ← 机检真的跑了（修前是 model）
  [FAIL] c2 (rule)，可选 length 71 < 999999            ← 字符串 "false" 读成可选
== 数组里的必选标准失败 ==
  exit 2，escalated，迭代 7
  [FAIL] c1 (rule) length 1108 < 999999
  已升级为人工工单：T-20261113-132500-7182
```

**本机运行时升级**：备份 `~/.mas/backup/app_backup_079`，`mas install` 后
`mas doctor` 全绿（`package mas 0.7.9 可导入`、`skills_sync 9 个已安装 skill 均与包内版本一致`）。

**常驻 HTTP 服务**（重启后）：`/health` → 200，`version: 0.7.9`

**鉴权矩阵**（v0.7.9）：health 200 / 无令牌 401 / 错令牌 401 / Bearer 200 / X-MAS-Token 200

**OpenMausBot 集成**（v0.7.9，端口 28799）：

- MCP 握手：`ok: True`，**12 个工具**
- 端到端闭环：`exit 0`，`status: closed`，iterations 1，tokens 3456，
  c1 regex `[一-龥]{4,}` 命中 → pass

### J.6 PyPI 发布状态（仍未完成）

与 v0.7.8 相同：Trusted Publishing 步骤真实执行过，但 PyPI 返回
`invalid-publisher: valid token, but no corresponding publisher`，
即 pypi.org 上还没有为 `openloop-mas` 登记 Trusted Publisher。
`https://pypi.org/pypi/openloop-mas/json` 仍为 404（名称未被占用，可注册）。

一次性登记后补发：

```bash
gh workflow run release.yml -f tag=v0.7.9
```

### J.7 边界说明

- `required` 读不懂的值一律按**必选**处理；这是刻意的保守选择——
  读不懂的标志绝不能悄悄放宽标准。
- JSON 数组形式只影响 `--criteria`；`--criteria-file` 的数组形式本就一直有效。
- 本轮真实端点实测仍只覆盖 `claude-opus-5-5` @ `http://127.0.0.1:16024/v1`（免鉴权）。

## K. v0.7.10 — resume 失败不再伪装成已解决

### K.1 问题（本轮真实端点发现，非推演）

升级工单是给人看的队列：它说「机器自己合不了，需要人拍板」。用不可满足的正则
`a^` 触发升级后，`mas resume` 的行为暴露了两个缺陷：

1. **失败的 resume 把工单标成 `resolved`** —— resume 跑完**仍然闭合不了**
   （status=escalated / budget_exhausted），工单却已经是 `resolved`。
   升级事项从 `mas tickets` 人工队列里消失，而它本该解决的那个问题一个都没解决。
   队列少报问题、循环「假闭合」。
2. **失败的 resume 多开一张重复工单** —— 一次失败被记成两个待办，
   人工队列因此谎报「有几个问题在等」。

### K.2 修复

- `mas/engine.py::LoopEngine.resume()`：只有 `result.closed` 为真才
  `tickets.resolve(...)`；否则 `tickets.reopen(...)`，把失败现场
  （status / error / 失败的标准 / run_id）写进工单的 `resume_attempts`。
- `mas/tickets.py::TicketStore.reopen()`（新增）：状态回 `waiting_human`，
  `decision=None`、`resolved_at=None`，并追加 `resume_attempts` 列表。
- `mas/tickets.py::TicketStore.append_attempt()`（新增）：把新一轮的
  产物、失败标准、迭代历史合并到**已有**工单上，不再另起一张。
- `mas/engine.py::LoopEngine.run()` 新增 `parent_ticket_id`：resume 路径
  把父工单传下去，升级时复用它。
- `mas/cli.py::cmd_resume()`：未闭合时打印
  「工单 X 仍未解决，仍在人工队列里：mas tickets」。

### K.3 真实端点复验（`claude-opus-5-5` @ `http://127.0.0.1:16024/v1`）

**失败分支**（标准 `regex:"a^"`，token 预算 3000 触发升级）：

```
== run exit: 2
    已升级为人工工单：T-20261113-144731-1a66
== resume exit: 2
状态：escalated（迭代 1 次）
  [FAIL] c1 regex /a^/ not found
工单 T-20261113-144731-1a66 仍未解决，仍在人工队列里：mas tickets
== tickets exit: 0
T-20261113-144731-1a66  waiting_human  loop_escalation  用一段话介绍循环工程

A ticket state: waiting_human | decision: None | resolved_at: None
A resume_attempts: [{"at": "2026-11-13T06:48:09+00:00",
  "reason": "resume did not close the loop: status=escalated, budget: tokens 4834 >= 3000, issues: c1: regex /a^/ not found",
  "status": "escalated", "iterations": 1,
  "issues": ["c1: regex /a^/ not found"], "run_id": "R-230711b82e"}]
```

**修前对比**：同一场景修前队列里出现 **两张** 工单（`...144240-8d53` 与
`...144324-9641`），原工单被标 `resolved`；修后**只有一张**且为
`waiting_human`。

**成功分支**（人工把标准放宽成可满足的 `min_chars:30`）：

```
run exit: 2  TICKET: T-20261113-144924-9dc2
== resume exit: 0
状态：closed（迭代 1 次）
  [PASS] c1 length 82 >= 30
== tickets exit: 0
T-20261113-144924-9dc2  resolved       loop_escalation  用一段话介绍循环工程
== replay exit: 0
回放 4 条账本记录
- #1 iter=1 planner
- #2 iter=1 closure escalate c1: regex /a^/ not found
- #3 iter=1 planner
* #4 iter=1 closure escalate
```

两条分支都验证到位：真闭合才关单，没闭合就留在队列并记下原因。

### K.4 本机门禁

| 检查 | 结果 |
| --- | --- |
| `python -m unittest discover -s tests` | **271 tests OK (skipped=4)**（v0.7.9 为 268） |
| `ruff check mas tests tools` | All checks passed! |
| `mas bench` | 9/9，闭环率 100%，平均迭代 1.67，账本一致性 100% |
| `mas demo` | closure / stagnation / budget 三场景全 OK |

新增测试 3 项：

- `test_a_failed_resume_keeps_the_ticket_in_the_human_queue`
- `test_a_successful_resume_resolves_the_ticket`
- `test_a_failed_resume_does_not_open_a_second_ticket`

### K.5 发布产物与本机运行时复验（v0.7.10）

**CI 四绿**（tag `v0.7.10` 推送后）：`ci` #53 / `pages` #49 / `install` #35 /
`release` #29 全部 success。

**Release 产物**（`gh release view v0.7.10`）：

- `openloop_mas-0.7.10-py3-none-any.whl`（108392 B，
  sha256 `9160b9abcf8fda80f2a70f21024b0565d6204c660f85d5f66ba84b3c1df7ffad`）
- `openloop_mas-0.7.10.tar.gz`（166353 B，
  sha256 `450d1ea16ebcb261a0b763a18a7b90df7d0930de9edecf70658c4bfc1f01ab9e`）

**全新 wheel 隔离安装 E2E**（新 venv `_venv710` + 新 HOME `_iso710` + 仓库外 cwd）：

| 检查 | 结果 |
| --- | --- |
| `pip install --no-index <wheel>` | `Successfully installed openloop-mas-0.7.10` |
| 解析路径 | `_venv710\Lib\site-packages\mas` |
| `mas --version` | `0.7.10` |
| `mas install` / `mas doctor --no-network` / `mas demo` | 全部 exit 0 |
| skill 指纹（6 组） | 全部 MATCH |
| MCP 双端 | True / True |
| 契约：`regex:` / `min_chars:many` / JSON 未知类型 | 均 exit 1、账本 0 行 |

**发布产物上复验 v0.7.10 的新修复**（mock provider，`min_chars:9999` 不可满足）：

```
run exit: 2  TICKET: T-20261113-150605-2c36
== resume exit: 2
状态：escalated（迭代 5 次）
  [FAIL] c1 length 1117 < 9999
工单 T-20261113-150605-2c36 仍未解决，仍在人工队列里：mas tickets
== tickets exit: 0
T-20261113-150605-2c36  waiting_human  loop_escalation  停滞任务

ticket count: 1
   T-20261113-150605-2c36 waiting_human attempts_total= 1
   resume_attempts= [{"reason": "resume did not close the loop: status=escalated,
                     issues: c1: length 1117 < 9999", "status": "escalated",
                     "iterations": 5, "run_id": "R-b561d1fc78"}]
```

**本机运行时升级**：备份 `~/.mas/backup/app_backup_0710`，`mas install` 后
`mas doctor` 全绿（`package mas 0.7.10 可导入`、`skills_sync 9 个已安装 skill
均与包内版本一致`、provider `claude-opus-5-5` 端点可用）。

**常驻 HTTP 服务**（重启后）：`/health` → 200，`version: 0.7.10`

**鉴权矩阵**（v0.7.10）：health 200 / 无令牌 401 / 错令牌 401 / Bearer 200 /
X-MAS-Token 200

**OpenMausBot 集成**（v0.7.10，端口 28799）：

- MCP 握手：`ok: True`，**12 个工具**
- 端到端闭环：`exit 0`，`status: closed`，iterations 1，tokens 3597，
  c1 regex `[一-龥]{4,}` 命中 → pass

### K.6 边界说明

- 本轮真实端点实测仍只覆盖 `claude-opus-5-5` @ `http://127.0.0.1:16024/v1`（免鉴权）。
- `resume_attempts` 是新增的公开字段，旧工单没有该字段时不影响读取。
- 触发升级在真实端点上依赖 token 预算（`max_tokens`）而非停滞判定：
  真实模型每轮都会改写产物，哈希不变几乎不会发生。
- PyPI 发布状态见下节（仍未完成）。

## L. v0.7.11 — 判定读含义：假闭合的最后一类

### L.1 问题（本轮代码审计 + 复现，非推演）

v0.7.9 修好了「标志读含义」（`"required": "false"` 被当成必选），
但**判定值本身**还有同类的漏洞没被覆盖。这一类漏洞的方向恰恰相反——
它们**把否决变成通过**，是本项目最危险的一类假闭合。

复现证据（修前）：

1. **多评审聚合把字符串 `"false"` 算成通过票** ——
   `mas/orchestration.py::aggregate_reviews` 用 `bool(票)` 读判定值，
   而 `bool("false")` 是 `True`。两位评审都回 `{"pass": "false"}` 时，
   聚合结果是 `pass: True, votes: "2/2", verdict: "pass"`。
   `mode="any"` 下更严重：只要**有一张字符串假票**，整条标准就通过。
2. **`normalize_verdict` 的 canonical 路径把读不懂的判定推成 `pass`** ——
   `{"verdict": "", "scores": [{"id": "c1", "pass": "maybe"}]}` 修前得到
   `verdict: pass`。原因是原字符串留在 `score["pass"]` 里，
   `all(s.get("pass"))` 直接真值化，`"maybe"` 也算通过。
3. **mock 后端把同一条标准报两次** —— 没有机检规则的标准，
   mock 评审先从 checks 列表追加一条（`pass: False, note: "no machine check"`），
   又从 unjudged 列表追加一条；`setdefault` 取第一条，
   于是**真正有用的「mock 无法判定」说明被覆盖掉了**。
4. **「未判定」在评审提示里显示成 FAIL** ——
   `mas/roles.py` 的机检结果区用 `check.get("pass")` 二值化，
   `None` → `FAIL`。评审模型于是读成「机检失败了」，
   而不是「这条根本没有被判定过」。

### L.2 修复

- `mas/orchestration.py::aggregate_reviews`：判定统一走 `mas.util._as_bool()`。
  **读不懂的票一律不算通过票**（保守侧），并新增 `unreadable_votes` 计数字段。
- `mas/util.py::normalize_verdict`：canonical 路径下读不懂 → `pass: False` +
  `unreadable: True`；verdict 判定改为 `all(s.get("pass") is True)`，
  与 `criteria` 路径语义对齐。
- `mas/providers.py::MockProvider`：`check["pass"] is None` 时不再从
  checks 分支追加，只由 unjudged 分支报告一次。
- `mas/roles.py`：机检结果区改为三态显示 `UNDECIDED` / `PASS` / `FAIL`。

### L.3 判定语义实测（`_as_bool` 矩阵）

```json
{"true(bool)": true, "false(bool)": false, "'false'": false, "'FALSE'": false,
 "'no'": false, "'0'": false, "'true'": true, "'yes'": true, "'1'": true,
 "'maybe'": null, "None": null, "''": null, "0(int)": false, "1(int)": true}
```

### L.4 聚合层实测（两位评审都回 `{"pass": "false"}`）

| mode | 修前 | 修后 |
| --- | --- | --- |
| `all` | `pass: true, votes: 2/2, verdict: pass` | `pass: false, votes: 0/2, verdict: fail` |
| `any` | `pass: true, votes: 2/2, verdict: pass` | `pass: false, votes: 0/2, verdict: fail` |
| `majority` | `pass: true, votes: 2/2, verdict: pass` | `pass: false, votes: 0/2, verdict: fail` |

**混合票**（一张布尔 `false` + 一张布尔 `true`）：

| mode | 修后结果 |
| --- | --- |
| `all` | `pass: false, votes: 1/2`（一票否决，符合严格语义） |
| `any` | `pass: true, votes: 1/2` |
| `majority` | `pass: false, votes: 1/2` |

**只有一张读不懂的票**（`"maybe"`）：

```json
{"verdict": "fail", "scores": [{"id": "c1", "pass": false,
  "note": "?; 1 reviewer(s) gave an unreadable verdict",
  "source": "review", "votes": "0/1", "unreadable_votes": 1}]}
```

### L.5 `normalize_verdict` canonical 路径实测

| 输入 | 修后输出 |
| --- | --- |
| `{"verdict": "", "scores": [{"id": "c1", "pass": "maybe"}]}` | `verdict: fail`，`pass: false`，`unreadable: true` |
| `{"verdict": "", "scores": [{"id": "c1", "pass": "false"}]}` | `verdict: fail`，`pass: false` |
| `{"verdict": "", "scores": [{"id": "c1", "pass": true}]}` | `verdict: pass`，`pass: true` |

### L.6 mock 评审「每条标准恰好报一次」实测

输入：c1 带机检（`pass: true`）、c2 无机检（`pass: null`）。

```json
{"scores": [
  {"id": "c1", "pass": true,  "note": "all required strings present"},
  {"id": "c2", "pass": false, "note": "unjudged: 该验收标准没有机检规则，mock 模式无法判定"}
]}
```

修前 c2 会出现两条（一条 `no machine check`、一条 `unjudged: ...`），
且 `setdefault` 让前者胜出——有用的说明被覆盖。

### L.7 真实端点复验（`claude-opus-5-5` @ `http://127.0.0.1:16024/v1`，免鉴权）

**完整闭环无回归**（标准 c1 `section:"结论"` + c2 `min_chars:120`）：

```
EXIT: 0
状态：closed（迭代 1 次，token 3936）
  [PASS] c1 (model) 虽然机检结果为"UNDECIDED - no machine check"，但该标准明确要求产物包含标题行"## 结论"，候选产物原文已满足。
  [PASS] c2 (rule) length 210 >= 120
```

真实评审模型**原话**把未判定的机检读成了「UNDECIDED - no machine check」，
而不是「FAIL」——修复 4 的三态语义在真实模型上确实生效，
模型没有把「没判定」误读成「机检失败」。

**真实端点的判定形状**：模型返回标准布尔 `pass: false`，
`normalize_verdict` 读出 `verdict: fail`。

### L.8 回归门禁

| 项 | 结果 |
| --- | --- |
| `python -m unittest discover -s tests` | **283 tests OK (skipped=4)**（v0.7.10 为 271，新增 12） |
| `ruff check mas tests tools` | All checks passed! |
| `mas bench` | 9/9 符合预期，闭环率 **100%**，账本一致性 **100%** |
| `mas demo` | closure / stagnation / budget 三场景全 OK |

**新增测试**（12 个）：

- `tests/test_orchestration.py`：`test_a_string_false_vote_is_not_a_passing_vote`、
  `test_an_unreadable_vote_is_not_a_passing_vote`、
  `test_an_unreadable_vote_alone_never_passes`
- `tests/test_config_providers.py`：`test_mock_verifier_reports_each_criterion_once`
- `tests/test_config_providers.py`（同一文件内新增）：
  `test_an_unreadable_verdict_never_produces_a_pass`、
  `test_both_verdict_shapes_agree_on_an_unreadable_answer`
- `tests/test_roles.py`（**新增文件**）：三态显示的 6 个测试
  （PASS / FAIL / UNDECIDED / 无效规则 / 三态共存 / 缺 `pass` 键）

### L.9 发布产物与本机运行时复验（v0.7.11）

**CI 四绿**（tag `v0.7.11` 推送后）：

| workflow | run id | 结论 |
| --- | --- | --- |
| `ci` | 43389388580 | success |
| `pages` | 43389388663 | success |
| `install` | 43389388678 | success |
| `release` | 43389396693 | success |

**Release 产物**（`gh release view v0.7.11`）：

- `openloop_mas-0.7.11-py3-none-any.whl`（110340 B）
- `openloop_mas-0.7.11.tar.gz`（171294 B）
- <https://github.com/tffgwy/openloop-mas/releases/tag/v0.7.11>

**全新 wheel 的隔离安装 E2E**（`pip install --no-index <wheel>`）：

| 项 | 结果 |
| --- | --- |
| 隔离安装 | `Successfully installed openloop-mas-0.7.11` |
| `mas --version` | `openloop-mas 0.7.11` |
| `mas install` | exit 0，MCP 注册 codex + claude，9 个 skill 落盘 |
| `mas doctor --no-network` | exit 0，`skills_sync 6 个已安装 skill 均与包内版本一致` |
| `mas demo` | closure / stagnation / budget 三场景全 OK |
| skill 指纹 | `.codex` / `.claude` × `mas-loop` / `mas-review` / `mas-scaffold` 全部 **MATCH** |
| MCP | `[mcp_servers.mas_loop]` 存在 + `.claude.json` 有 `mas_loop` |

**发布产物上的契约复验**（缺参数必须当场拒绝、零 token、账本零污染）：

| 输入 | exit | 账本条数 | 报错 |
| --- | --- | --- | --- |
| `regex:` | 1 | 0 | `missing argument: regex needs a regular expression` |
| `min_chars:many` | 1 | 0 | `invalid argument: min_chars needs a minimum character count, got 'many'` |
| `{"type":"mystery"}` | 1 | 0 | `unknown check type: 'mystery'` |

**本机运行时升级**：备份 `~/.mas/backup/app_backup_0711`，
用发布 wheel 的包内容替换 `~/.mas/app/mas` → `mas 0.7.11 可导入`。
`mas install` 同步 9 个 skill（旧副本备份为 `mas-loop.bak-20261113-162631`），
`mas doctor` 全绿：
`package mas 0.7.11 可导入`、`skills_sync 9 个已安装 skill 均与包内版本一致`、
`provider openai_compat / claude-opus-5-5 @ http://127.0.0.1:16024/v1（免鉴权端点）`、
`endpoint 端点可用，协议方言 = openai-responses-events`。

**常驻 HTTP 服务**（重启后）：`/health` → 200，`version: 0.7.11`

**鉴权矩阵**（v0.7.11）：health 200 / 无令牌 401 / 错令牌 401 / Bearer 200 /
X-MAS-Token 200

**OpenMausBot 集成**（v0.7.11，端口 28799）：

- MCP 握手：`ok: True`，**12 个工具**
  （`mas_run`, `mas_status`, `mas_ledger`, `mas_tickets`, `mas_ticket_resolve`,
  `mas_resume`, `mas_report`, `mas_replay`, `mas_export`, `mas_teams`,
  `mas_doctor`, `mas_config`）
- 端到端闭环：`exit 0`，`status: closed`，iterations 1，tokens 3466，
  c1 regex `[一-龥]{4,}` 命中 → pass

### L.10 边界说明

- 本轮真实端点实测仍只覆盖 `claude-opus-5-5` @ `http://127.0.0.1:16024/v1`（免鉴权）。
- 修前行为是**代码审计 + 离线复现**得到的，不是真实端点现场发现的：
  真实模型目前都返回标准布尔 `pass`。字符串假票路径需要人工构造的输入才会触发。
- `_as_bool` 把 `1` / `0` 视为 `true` / `false`（模型偶尔这么写）；
  这与 v0.7.9 的标志读法一致。
- 判定读的是**结构**，不是事实：模型说「不通过」不等于产物真的不合格，
  只是引擎不再把「说不通过」误读成「通过」。
- PyPI 发布状态见下节（仍未完成）。

## M. v0.7.12 — 总结不许和逐条判定打架

### M.1 问题

v0.7.11 把「判定值怎么读」彻底修好了，但留了一个尾巴：`normalize_verdict`
的 canonical 路径只归一化逐条 `scores`，**原样保留**模型写的顶层 `verdict`。

于是模型自己前后矛盾时，引擎把矛盾原样传下去：

```
输入   {"verdict": "pass", "scores": [{"id": "c1", "pass": "false"}]}
修前   {"verdict": "pass",  "scores": [{"id": "c1", "pass": false}]}
       ↑ 顶层说「通过」，逐条说「不通过」
```

**闭合判定本身不受影响**——`engine.py` 的 `passed` 只由逐条 `scores` 算出
（`all(bool(s["pass"]) for s in blocking)`），顶层 `verdict` 从不参与闭合。
但每一份报告、每一次回放、每一条账本条目都显示着这个矛盾，
读者无从判断引擎到底信了哪一半。这属于「机器做对了、机器说出来的话是错的」，
同样要修。

### M.2 修复

`mas/util.py::normalize_verdict` 的 canonical 路径：

- 有**任何一条**标准的判定读得懂 → 顶层 `verdict` 由这些判定重新算出
  （全部通过才算 `pass`）；
- 只有**一条都读不懂**（全部 `unreadable`）→ 模型自己的总结被保留，
  因为那时它是唯一的信号。

落点是「报告说的和引擎做的一致」，**不是**「更容易通过」。

### M.3 实测

| 输入 | 修前 | 修后 |
| --- | --- | --- |
| `{"verdict":"pass","scores":[{"id":"c1","pass":"false"}]}` | `verdict: pass` | `verdict: fail` |
| `{"verdict":"fail","scores":[{"id":"c1","pass":true}]}` | `verdict: fail` | `verdict: pass` |
| `{"verdict":"pass","scores":[{"id":"c1","pass":true},{"id":"c2","pass":false}]}` | `verdict: pass` | `verdict: fail` |
| `{"verdict":"pass","scores":[{"id":"c1","pass":"maybe"}]}` | `verdict: pass` | `verdict: pass`（全不可读，保留模型总结） |

### M.4 回归门禁

| 项 | 结果 |
| --- | --- |
| `python -m unittest discover -s tests` | **287 tests OK (skipped=4)**（v0.7.11 为 283，新增 4） |
| `ruff check mas tests tools` | All checks passed! |
| `mas bench` | 9/9，闭环率 **100%**，账本一致性 **100%** |
| `mas demo` | closure / stagnation / budget 三场景全 OK |
| 真实端点 `claude-opus-5-5` @ `127.0.0.1:16024/v1` | 完整闭环 `exit 0`，`closed`，迭代 1，token 3966 |

**新增测试**（4 个，`tests/test_config_providers.py`）：

- `test_a_pass_summary_cannot_contradict_a_failed_score`
- `test_a_fail_summary_cannot_contradict_a_passing_score`
- `test_a_mixed_payload_summarises_as_fail`
- `test_the_summary_survives_when_no_score_is_readable`

### M.5 发布产物与本机运行时复验（v0.7.12）

**CI 四绿**（tag `v0.7.12` 推送后）：

| workflow | run id | 结论 |
| --- | --- | --- |
| `ci` | 43397479360 | success |
| `pages` | 43397479374 | success |
| `install` | 43397479421 | success |
| `release` | 43397493651 | success |

**Release 产物**（`gh release view v0.7.12`）：

- `openloop_mas-0.7.12-py3-none-any.whl`（111117 B）
- `openloop_mas-0.7.12.tar.gz`（173180 B）
- <https://github.com/tffgwy/openloop-mas/releases/tag/v0.7.12>

**全新 wheel 的隔离安装 E2E**：

| 项 | 结果 |
| --- | --- |
| 隔离安装 | `Successfully installed openloop-mas-0.7.12` |
| `mas --version` | `openloop-mas 0.7.12` |
| `mas install` | exit 0，MCP 注册 codex + claude，9 个 skill 落盘 |
| `mas doctor --no-network` | exit 0，`skills_sync 6 个已安装 skill 均与包内版本一致` |
| `mas demo` | closure / stagnation / budget 三场景全 OK |
| skill 指纹 | `.codex` / `.claude` × `mas-loop` / `mas-review` / `mas-scaffold` 全部 **MATCH** |
| MCP | `[mcp_servers.mas_loop]` 存在 + `.claude.json` 有 `mas_loop` |

**发布产物上的契约复验**（缺参数当场拒绝、零 token、账本零污染）：
`regex:` / `min_chars:many` / `{"type":"mystery"}` 三种非法输入全部
`exit 1`、账本 0 条。

**本机运行时升级**：备份 `~/.mas/backup/app_backup_0712`，
用发布 wheel 的包内容替换 `~/.mas/app/mas` → `mas 0.7.12 可导入`。
`mas install` 同步 9 个 skill（旧副本备份为 `mas-loop.bak-20261113-171233`），
`mas doctor` 全绿：
`package mas 0.7.12 可导入`、`skills_sync 9 个已安装 skill 均与包内版本一致`、
`provider openai_compat / claude-opus-5-5 @ http://127.0.0.1:16024/v1（免鉴权端点）`、
`endpoint 端点可用，协议方言 = openai-responses-events`。

**常驻 HTTP 服务**（重启后）：`/health` → 200，`version: 0.7.12`

**鉴权矩阵**（v0.7.12）：health 200 / 无令牌 401 / 错令牌 401 / Bearer 200 /
X-MAS-Token 200

**OpenMausBot 集成**（v0.7.12，端口 28799）：

- MCP 握手：`ok: True`，**12 个工具**
- 端到端闭环：`exit 0`，`status: closed`，iterations 1，tokens 3481，
  c1 regex `[一-龥]{4,}` 命中 → pass，账本无未结票据

### M.6 边界说明

- 本轮真实端点实测仍只覆盖 `claude-opus-5-5` @ `http://127.0.0.1:16024/v1`（免鉴权）。
- 这条修复**不影响闭合判定**，只影响载荷归一化后的顶层 `verdict` 字段。
  闭合一直只看逐条 `scores`。
- 真实模型目前很少自相矛盾；这个场景需要人工构造的输入才会触发，
  属于**代码审计 + 离线复现**得到的缺陷，不是真实端点现场发现的。
- PyPI 发布状态见下节（仍未完成）。
