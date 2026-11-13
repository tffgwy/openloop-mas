# OpenLoop MAS v0.4.0 · 真实模型端点验证报告

- 生成时间：2026-11-13（Asia/Shanghai）
- 版本：v0.4.0
- 验证端点：`http://127.0.0.1:16024/v1`（Codex Responses 桥接，`claude-opus-5-5`）
- 仓库：https://github.com/tffgwy/openloop-mas

这一份报告记录 **v0.4.0 之前从未被真实流量验证过的链路**，以及本轮实测中挖出并修掉的 5 个真缺陷。

---

## 1. 为什么要做这次验证

v0.2 / v0.3 引入了三条新链路：并行候选、对抗评审、故障转移。它们的测试全部基于 mock provider 与注入式假 provider，只能证明「代码路径没崩」，不能证明「真模型跑得通」。

本轮把 `~/.mas/config.json` 指向真实端点，把三条链路全部跑了一遍。结果：**三条链路全部存在真缺陷，其中两条会让闭环根本无法闭合。**

---

## 2. 实测挖出并修掉的 5 个缺陷

### 缺陷 1 · 提示词把任务埋在 JSON 里，真实模型直接答非所问

**症状**：`mas run` 连续 8 轮全部 `revise`，最终 `budget_exhausted`。交付物内容是：

> I'd be happy to continue — but I don't have the preceding context or task in this thread.

**根因**：`mas/roles.py` 把任务状态以 JSON dump 的形式塞在提示词末尾：

```
Produce the deliverable now. Output only the deliverable text.
按下面 JSON 状态里的信息完成任务。

## TASK STATE
<<MAS_STATE>>
{ "goal": "...", "criteria": [...] }
<<END>>
```

`<<MAS_STATE>>` / `<<END>>` 是 mock provider 的私有解析协议。真实模型看到的是一段结构化 JSON，理解为「一段上下文数据」，而不是「请交付这个东西」。

**修法**：`Roles._brief()` 新增人类可读的任务简述，排在 JSON 之前：目标、轮次、验收标准（含机检规则类型与参数）、已有计划、上一轮未通过的问题、人类补充说明；verifier 角色额外拿到待评审交付物原文与机检结果。JSON 状态块保留在末尾，mock provider 的解析不受影响。

**证据**：修完后同一端点、同一任务，1 轮闭环 `closed`，产出正确的 `dedupe` 实现。

### 缺陷 2 · OpenAI 兼容端点忽略 `messages`，只读 `input`

**症状**：提示词修好后，模型依然回答「请把需要继续的内容贴过来」。

**根因**：桥接端点的 `/v1/chat/completions` 路由**静默丢弃 `messages` 字段**，只读 Responses API 的 `input` 字段。四组对照实验：

| 请求 | 模型回答 |
| --- | --- |
| 只有 `messages` | 「Sure—what should I continue? Please paste the previous text…」 |
| `messages` + `input` | 正确产出 `dedupe` 代码 |
| 只有 `input` | 「我是 Claude，一款由 Anthropic 开发的 AI 助手…」 |
| `stream=false` + `messages` | 空响应 |

**修法**：`OpenAICompatProvider` 同时发送 `messages` 与 `input`。严格的 OpenAI 端点读 `messages`、忽略未知键；Responses 风格桥接读 `input`。两边都正常。

**证据**：`tests/test_config_providers.py::ProviderPayloadTest::test_input_field_is_sent_alongside_messages` 固化该行为。

### 缺陷 3 · 评审结果被整体丢弃（`usable 0 / skipped 3`）

**症状**：并行 3 + 对抗评审 3 跑通后，账本里 `review` 记录显示 `usable: 0, skipped: 3` —— 三个评审全部「失败」，实际等于没评审。

**根因**：评审模型自发返回了另一套 JSON 结构：

```json
{"overall_pass": false,
 "criteria": [{"id": "c1", "result": "FAIL", "reason": "缺少遍历 items…"}]}
```

而引擎只认：

```json
{"verdict": "pass|fail", "scores": [{"id": "c1", "pass": true, "note": "..."}]}
```

`aggregate_reviews` 按 `scores` 取值，取不到就把该评审计为 skipped。**评审模型工作得很好（甚至准确识别出我测试代码里的真 bug：`return list(seen)` 恒为空列表），但结论被静默丢弃了。**

**修法**：`mas/util.py` 新增 `normalize_verdict()`，把常见变体（`criteria` / `results` / `checks` / `items` 列表，`pass` / `passed` / `result` / `status` / `outcome` / `satisfied` 布尔字段，`note` / `reason` / `explanation` / `analysis` 说明字段）统一归一化为规范结构。引擎对 `verifier` 角色自动调用。同时强化 verifier 的 system prompt，明确要求使用 `verdict` / `scores` / `id` / `pass` / `note` 五个键。

**证据**：修完后同一任务 `usable: 3, skipped: 0`，c1 与 c2 各得 3/3 票通过，且每条 note 都带具体原文证据。

### 缺陷 4 · 故障转移不回退，回退端点被迫沿用主端超时

**症状**：主端点指向不可达端口，回退端点指向真实端点，`mas run` 直接 `error`，一次都没回退。

**根因**：`build_provider_chain()` 构造回退 provider 时只复制了 `provider` / `model` / `base_url` / `api_key` / `api_key_env`，**`timeout_sec`、`max_retries` 等连接参数被丢弃**，回退后端被迫继承主端的超时与重试设置。

**修法**：回退规格现在可以逐项覆盖 `provider` / `model` / `base_url` / `api_key` / `api_key_env` / `timeout_sec` / `max_retries` / `extra_headers` / `temperature`。

**证据**：主端 `127.0.0.1:9`（不可达，超时 3 秒）→ 回退 `127.0.0.1:16024`（超时 60 秒），闭环 `closed`，1 轮，4215 token。

### 缺陷 5 · 环境变量值被冻结进用户配置文件 + 测试读真实用户配置

这是本轮发现的一组关联缺陷，分三层：

**(a) 环境值被写回磁盘。** `load_config()` 会把 `MAS_STATE_DIR`、`MAS_API_KEY` 等环境覆盖值合并进返回的 config，而 `mas init`、MCP 的 `mas_config set`、setup 向导都直接把这份合并结果 `save_config()` 回磁盘。结果：临时的 `state_dir` 和来自环境的 API key 被永久写进 `~/.mas/config.json`。本机实测 `state_dir` 已被污染成 `C:\...\Temp\tmpqovwuxxd` —— 系统临时目录会被清理，账本随时可能消失。

**修法**：
- 新增 `load_persisted_config()`：只读磁盘文件，不合并环境。
- 新增 `persistable_config()`：落盘前剥离环境来源的值；API key 一律清空并只保留 `api_key_env` 变量名（向导早就承诺「配置里不会存明文密钥」，现在由代码强制执行）。
- `mas init` / MCP `mas_config set` / setup 向导改用持久化视图写入。
- 额外规则：指向 `tempfile.mkdtemp()` 自动生成目录（`tmp` + 8 位随机字符）的 `state_dir` 视为运行期注入，不予持久化；用户自命名的临时目录照常保留。

**(b) 测试读真实用户配置。** `tests/test_http_report.py` 只设了 `MAS_STATE_DIR`，没设 `MAS_CONFIG_PATH`，因此一直读真实的 `~/.mas/config.json`。之前真实配置恰好是 mock 所以没暴露；本轮切到真实端点后，HTTP 测试开始发起真实网络调用并超时。

**修法**：新增 `tests/conftest.py`，会话级隔离 `MAS_CONFIG_PATH` 与 `MAS_STATE_DIR`，退出时还原。

**(c) 旧的测试直接写真实用户配置。** `test_defaults_and_persistence` 调用 `save_config(config)` 没传路径，直接覆盖 `~/.mas/config.json`。

**修法**：改为写入隔离路径；新增 `ConfigPersistenceHygieneTest` 覆盖 (a) 的四条规则。

**修法小结**：本机 `~/.mas/config.json` 的 `state_dir` 已从被污染的临时目录恢复为 `~/.mas`，账本回到稳定位置。

---

## 3. 真实模型闭环验证结果

全部使用 `claude-opus-5-5`，任务为「写一个 Python 函数 `dedupe(items)`，返回去重且保持原顺序的列表，并给出简短说明」，验收标准为两条正则机检规则。

| 场景 | 命令 | 结果 |
| --- | --- | --- |
| 单 worker 单评审 | `mas run --criteria-file …` | `closed`，迭代 1，token 4348 |
| 并行候选 + 对抗评审 | `mas run --parallel 3 --reviewers 3 --review-mode majority` | `closed`，迭代 1，token 13273 |
| 故障转移 | 主端不可达 + 回退端真实 | `closed`，迭代 1，token 4215 |

并行场景的账本明细：

```
candidates: direct(passed=2, chars=297, selected=True)
            thorough(passed=2, chars=292)
            concise(passed=2, chars=236)
review:    mode=majority personas=[strict, skeptic, completeness]
            usable=3 skipped=0
            c1 pass votes=3/3  「交付物给出了完整且可运行的 Python 函数，使用 set 记录…」
            c2 pass votes=3/3  「交付物包含明确的中文说明…」
closure:   closed=True stagnation=0
```

三个候选全部通过机检，按「通过数 → 长度 → 索引」的确定性规则选中 `direct`。

---

## 4. 回归测试

```
91 passed in 1.80s
```

新增覆盖：

- `ConfigPersistenceHygieneTest`（4 条）：env 的 state_dir 不落盘、env 的 API key 不落盘、持久化视图无视环境、环境变量名被保留
- `VerdictNormalizationTest`（4 条）：规范结构保持不变、criteria 结构被转换、布尔字段名被识别、无关负载不被改写
- `ProviderPayloadTest`（2 条）：请求同时携带 `messages` 与 `input`、回退端可覆盖连接参数
- `test_an_explicit_config_path_is_authoritative`：显式配置文件路径不被其它位置覆盖

mock 模式无回归：`mas demo` 3/3 通过，`mas bench` 9/9 通过，闭环率 100%，账本一致性 100%。

---

## 5. 仍然诚实的边界

1. **验证只覆盖一个端点。** `claude-opus-5-5` 经 Codex Responses 桥接。Kimi / DeepSeek / Qwen / 原生 OpenAI 端点未实测 —— 缺陷 2 的修复正是因为「OpenAI 兼容端点只读 `input`」这种方言差异，所以其他方言仍可能有同类差异。
2. **判定的是结构，不是事实。** 机检规则能确认交付物包含某个正则，不能确认逻辑正确。对抗评审能发现机检漏掉的语义问题（本次确实发现了 `return list(seen)` 的真 bug），但它仍是模型判断。
3. **账本一致性 100% 是 mock 场景下的结论。** 真实端点下未做多轮崩溃恢复的账本重放校验。
4. **不承诺流量、签约、平台批准。** 判定的是结构与机检结果，不是市场结果。


---

## 6. v0.4.1 / v0.4.2：来自 OpenMausBot 端到端的实测

前面的验证都从命令行直接跑 `mas run`。这一轮改从 OpenMausBot 里让 bot 通过 MCP 工具
`mas_run` 发任务，结果暴露了两个命令行视角看不到的缺陷。

### 缺陷 6 · v0.4.0 发布包的 `mas doctor` 在干净安装时崩溃

三个平台的 CI 都复现了同一个错误：

```
错误：name 'config_path' is not defined
```

根因：`mas/doctor.py` 在「配置文件不存在」的回退分支里调用 `config_path()`，
但模块级 import 漏掉了它。本地开发树因为 `~/.mas/config.json` 总是存在，
永远走不到那个分支 —— 只有全新安装才会触发。

修复：`mas/doctor.py` 补上 `config_path` 导入；新增 `tests/test_doctor.py`（3 条），
并把该文件临时改回缺陷版本验证测试确实会失败（复现出同样的 `NameError`）。

### 缺陷 7 · `--criteria 'regex:...'` 简写从未生成机检规则

这是端到端跑出来的账本暴露的：前三轮的 `source` 是 `model-missing`，
而不是 `rule`。

根因：`mas/cli.py::_parse_criterion` 只认 JSON 形式，其它一律当纯文本描述。
`regex:(?s)def dedupe.*set` 于是变成一句描述，`check=None`，机检规则完全没生效。
**此前文档里「regex 机检通过」的说法其实经不起推敲 —— 那些 pass 是模型评审给的，
不是规则判的。**

修复：

- `mas/util.py` 新增 `parse_criterion_shorthand()`，解析 `<类型>:<参数>`，
  参数保留首个冒号之后的所有内容（正则里可以有冒号）；未知类型与非法参数保持为普通描述。
- 解析下沉到 `mas/engine.py::Criterion.from_raw`，因此 CLI、MCP、HTTP、账本重放
  走同一条路径。
- `mas/cli.py` 复用同一个解析器。

修复后的实测（同一任务、同一验收标准）：

```
c1 pass=true  note=regex /(?s)def dedupe.*set/ matched   source=rule
c2 pass=true  note=regex /[一-龥]{4,}/ matched            source=rule
status=closed  iterations=1  tokens=4617
```

`source` 从 `model` 变成 `rule`，说明判定确实来自确定性检查器。

### 缺陷 8 · 验证者漏判会白烧一整轮迭代

修复前的端到端账本（`run_id=R-f241456619`）：

```
iter=1 verdict=fail decision=revise  c1 pass=False source=model-missing
iter=2 verdict=fail decision=revise  c1 pass=False source=model-missing
iter=3 verdict=fail decision=revise  c1 pass=False source=model-missing
iter=4 verdict=pass decision=close   c1 pass=True  source=model
```

验证者连续三轮没给出判定，引擎却把它当成「交付物不合格」，
于是重新生成交付物、重新评审 —— 前三轮的工作全部作废。

修复：`mas/engine.py` 新增 `_ask_missing_verdicts()`。当合并后的判定里存在
`source == "model-missing"` 时，引擎在**同一轮内**用一份定向指令就缺失的标准
追问验证者（`extra` 指令 + `missing_criteria` 状态），把新答案并回原有判定。
追问后仍无答案才按 `unjudged` 记为不通过。

修复后的实测（无任何机检规则的标准，验证者第一次沉默）：

```
status=closed  iterations=1  tokens=3571
verifier 调用次数=2（第一次沉默，第二次补齐）
```

一轮闭合。

### 回归测试

```
103 passed
```

新增覆盖：

- `CriterionShorthandTest`（7 条）：正则简写生成机检、引擎路径同样生效、
  数字简写转整数、正则含冒号、未知类型保持描述、非法数字参数保持描述、空参数保持描述
- `MissingVerdictRecoveryTest`（2 条）：漏判时一轮闭合、追问得到的判定被正确记账
- `tests/test_doctor.py`（3 条）：缺失配置时不抛异常、配置存在时报 ok、所有检查都有状态

两条新测试都做过「故意改坏 → 测试失败 → 恢复」的验证，确认它们真的能抓住回归。

mock 模式无回归：`mas demo` 3/3 通过，`mas bench` 9/9 通过，闭环率 100%，账本一致性 100%。

### v0.4.1 / v0.4.2 的干净安装验证

从 Release 产物安装到全新 venv：

```
pip install openloop_mas-0.4.1-py3-none-any.whl
mas --version   -> openloop-mas 0.4.1
mas doctor      -> 全部通过，退出码 0
mas demo        -> 3/3
mas bench       -> 9/9，闭环率 100%
mas init        -> 写出的配置 state_dir=~/.mas（未被环境变量污染）
```

同一干净 venv 里接真实模型端点跑闭环：`status=closed`，迭代 1。

---

## v0.4.3：方言差异从「未覆盖」变成「有保障」

### 为什么要做这件事

上一轮的真实模型实测只覆盖了一个端点（`claude-opus-5-5` @ Codex Responses 桥接）。
本机没有 Kimi / DeepSeek / Qwen / 原生 OpenAI 端点，因此「换一个厂商会不会炸」
这个问题在 v0.4.2 之前**没有任何保障** —— 只靠一句「OpenAI 兼容」。

v0.4.3 的做法：不假装能测到不存在的端点，而是把**协议方言本身**变成可执行契约。

### 协议级方言契约测试（`tests/test_provider_dialects.py`，16 条）

用本地 `ThreadingHTTPServer` 起假网关，逐条断言请求负载与响应解析：

| # | 方言 | 断言要点 |
|---|---|---|
| a | 标准 OpenAI chat-completions | `messages` 结构、`Authorization` 头、usage 三元组 |
| b | 只读 `input` 的 Responses 桥接 | `input` 与 `messages` 同时下发，两端都工作 |
| c | OpenAI SSE 流 | `choices[].delta.content` 折叠、usage 统计 |
| d | Responses 事件流 | `response.output_text.delta` + `response.completed`、usage 统计 |
| e | 不支持 `response_format` 的网关 | 首次 400 → 去掉该字段重试一次成功 |
| f | Anthropic `/v1/messages` | `content[].type=="text"`、`input_tokens`/`output_tokens` |

另外覆盖：`response.failed` 事件必须抛 `ProviderError`；`total_tokens` 缺失时按
prompt+completion 回填；`message.content` 为内容块列表时必须拼成字符串。

### 契约测试当场抓出的两个真实缺陷（已修复）

**缺陷 9 · `usage.total_tokens` 缺失时被记成 0**

部分网关只返回 `prompt_tokens` / `completion_tokens`。修复前账本会把这次调用的
token 数记成 0，token 预算形同虚设。

修复：`mas/providers.py` 新增 `_normalize_usage()`，兼容
`prompt_tokens`/`input_tokens` 两套字段名，并在 `total_tokens` 缺失时回填。

**缺陷 10 · `message.content` 是内容块列表时被当成文本**

多模态形状的网关即使只回纯文本，也可能用内容块数组。修复前 provider 把整个
list 原样返回，下游 JSON 解析必然失败。

修复：`mas/providers.py` 新增 `_flatten_content()`，把字符串 / 内容块列表 / `None`
统一成纯文本。

两个 helper 被上移到 `_HttpProvider` 基类，OpenAI 与 Anthropic 两条路径共用同一套
归一化规则。

### 全链路方言闭环测试（`tests/test_dialect_loop.py`，3 条）

provider 单元测试只证明「一次 HTTP 往返能解析」。全链路测试用假网关跑完整的
`LoopEngine` 回路 —— 计划、产出、评审、机检、账本记账 —— 断言：

- SSE 方言：闭环 1 次迭代完成，token 统计大于 0
- Anthropic 方言：闭环 1 次迭代完成，token 统计大于 0
- 账本里确实记下了 Anthropic 方言归一化后的 token 数（≥ 160）

### 端点握手探测（`mas/probe.py` + `mas doctor`）

新增 `mas probe` 能力：`mas doctor` 在配置了真实端点后会发一次最小请求，
然后告诉用户**你的网关说的是哪种方言**：

```
[OK  ] endpoint       端点可用，协议方言 = openai-responses-events
```

本机 Codex 桥接实测（`http://127.0.0.1:16024/v1`）：

```json
{"status": "ok", "dialect": "openai-responses-events"}
```

识别规则：`choices` → openai-chat-completions；`content[]` → anthropic-messages；
`output` → openai-responses-events；SSE 中出现 `choices` → openai-sse-stream；
SSE 中出现 `response.*` → openai-responses-events。

探测失败不会让 `mas doctor` 崩：未配置 base_url 是 `warn`，HTTP 4xx/5xx 是 `fail`，
连接不通是 `fail` 并带原因。

### 回归测试

```
127 passed
```

新增覆盖：provider 方言契约 16 条、全链路方言闭环 3 条、端点探测 5 条。

真实端点复测（`claude-opus-5-5` @ `http://127.0.0.1:16024/v1`）：

```
status=closed  iterations=1  tokens_used=3732
c1 pass=true source=rule note=regex /(?s)def dedupe.*set/ matched
```

### 诚实的边界

- 契约测试覆盖的是**协议形状**，不是**真实厂商端点**。Kimi / DeepSeek / Qwen /
  原生 OpenAI 在本机没有端点，**未做真实调用实测**。
- 契约测试保证的是「请求和响应能被正确理解和组装」，不保证每个厂商都接受
  `input` 这个额外字段（严格 OpenAI 会忽略它，但个别厂商可能拒绝未知字段）。
  若某个厂商因此报 400，可用 `extra_headers` 或改用 `fallbacks` 切换后端。

## v0.4.4：让 CI 矩阵和运行时说同一种话

v0.4.3 的 Release 产物本身是好的（构建、twine 检查、GitHub Release 全部成功），但**CI 矩阵是全红的**。

**根因**：v0.4.3 新增的 `tests/test_provider_dialects.py` 与 `tests/test_probe.py` 使用了 pytest 风格断言与参数化装饰器，而 CI 矩阵执行的是 `python -m unittest discover -s tests`。项目定位是「零第三方依赖」，CI 环境并未安装 pytest，于是收集阶段直接 `ModuleNotFoundError: No module named 'pytest'`。

**修法**：把这两个文件全部改写为标准库 `unittest` 写法 —— `pytest.raises` → `assertRaises`，`pytest.mark.parametrize` → `subTest`。运行时代码一行未改，`mas/` 目录与 v0.4.3 完全一致。

**验证（两套 runner 同源）**：

- `python -m unittest discover -s tests` → `Ran 123 tests ... OK`
- `python -m pytest tests -q` → `123 passed, 8 subtests passed`
- `ruff check mas tests tools --select F821,F401,F811` → `All checks passed`
- GitHub Actions 15 任务矩阵（build / site / 3 OS × 5 Python 版本）全部 `success`

**意义**：这不只是「让 CI 变绿」。它把「零依赖」这条产品承诺真正落到了测试层 —— 任何人 clone 下来 `pip install -e .` 就能跑全套测试，不需要先猜该装 pytest 还是 unittest。

## v0.4.5：不要把「免鉴权端点」当成「配置不完整」

实际在本机装 v0.4.4 时发现的一个诊断误报。

**现象**：本机 Codex Responses 桥接在 `http://127.0.0.1:16024/v1`，不需要 API key（`api_key` 为空）。`mas doctor` 却报出：

```
[WARN] provider  openai_compat 缺少: 密钥(env MAS_API_KEY)（离线 mock 模式仍可用）
[WARN] endpoint  端点未配置完整，跳过握手探测
```

**根因**：`run_doctor` 把「有 API key」当作揢测的前提（`if provider != "mock" and key and base_url`）。对任何免鉴权端点，握手探测都被跳过——而保护结果依然是真实的，只是无法提前告诉用户他的网关说的是哪种方言。

**修法**：

1. 揢测条件改为 `if provider != "mock" and base_url` —— 只要配了端点就发请求，让服务端的回答（200 / 401 / 400）本身弱我们。
2. `provider` 检查项新增一个分支：无密钥但有 `base_url` → 判为 `ok`，描述为「免鉴权端点，由握手探测确认」；两者都缺才是 `warn`。

**修后实测**：

```
[OK  ] provider  openai_compat / claude-opus-5-5 @ http://127.0.0.1:16024/v1（免鉴权端点，由握手探测确认）
[OK  ] endpoint  端点可用，协议方言 = openai-responses-events
```

**回归测试**：`tests/test_probe.py` 新增 3 条（免密钥揢测、无端点误报、无密钥本地端点接受），全套 126 条通过（unittest 与 pytest 两套 runner 同源全绿）。

## v0.4.6：离线环境不能因为网络而报错

`mas doctor` 的端点握手探测是有意为之的，但它也意味着**无网环境下不可用**。

**修法**：`mas doctor --no-network`，跳过握手探测并明确报出「已按 --no-network 跳过握手探测」。适用于内网隔离、审核环境、或任何不允许出站请求的场景。

**验证**：

```
$ mas doctor              # 带网络
[OK  ] endpoint  端点可用，协议方言 = openai-responses-events
$ mas doctor --no-network
[WARN] endpoint  已按 --no-network 跳过握手探测
```

回归测试新增 1 条（`--no-network` 不发起揢测），全套 127 条通过。

## v0.4.7：「一键安装」不能只是一句口号

### 定位到的缺陷

在本机装 v0.4.6 的过程里使用了一个**隔离的 HOME**（没有 `~/.codex`、没有 `.claude.json`）来模拟全新机器，安装器返回退出码 `0`，但实际结果是：

```
[!!] ~/.codex/config.toml not found, skipped Codex MCP registration
[WARN] skill_codex  未安装: ...\.codex\skills\mas-loop\SKILL.md
[WARN] skill_claude  未安装: ...\.claude\skills\mas-loop\SKILL.md
```

**退出码是 0，但 MCP 和 skills 全部丢失了。** 这就是「安装成功」最危险的失败形式：用户看不出任何异常。

根因在 `install.sh` 与 `install.ps1` 里是同一个模式：

```bash
if [ -f "$codex_config" ]; then
  ... register ...
fi          # 不存在时静默跳过
```

### 修法

1. **配置文件不存在时创建它**，并写入 MCP 注册块；
2. **skills 目录总是为 Codex / Claude Code 创建**（这两个是 MAS 明确支持的宿主），`.agents` / `.gemini` 仍是可选；
3. `~/.claude.json` 不存在时创建空对象再写入。

### 验证

隔离 HOME 重跑安装器后：

```
[OK] 6 skill folder(s) installed
[OK] created ~/.codex/config.toml and registered the MCP server
[OK] registered in ~/.claude.json (backup: ...)
[OK  ] skill_codex  ...\.codex\skills\mas-loop\SKILL.md
[OK  ] skill_claude ...\.claude\skills\mas-loop\SKILL.md
[OK  ] mcp_codex  已在 ~/.codex/config.toml 注册 mcp_servers.mas_loop
```

### 为什么还要新增 CI

修好一个脚本不等于它不会再回归。**新增 `install` workflow**：在 ubuntu / windows / macos 三个平台的**干净 HOME**里执行真实安装器，然后跑 `tools/verify_install.py`。该脚本断言三件事：

1. `~/.codex/config.toml` 与 `~/.claude.json` 均存在且包含 `mas_loop`；
2. 三个 skill（`mas-loop` / `mas-review` / `mas-scaffold`）在 Codex 与 Claude 两边都落盘；
3. **按注册配置启动的 MCP 服务器真的能回答**（`initialize` + `tools/list` 返回 12 个工具），且安装后的 `mas --version` / `doctor --no-network` / `demo` / `bench` 全部退出码 0。

本机实测（隔离 HOME）：

```
[OK] install assertions passed
[OK] MCP server answered with 12 tools: ['mas_run', ..., 'mas_config']
[OK] mas --version / mas doctor --no-network / mas demo / mas bench --repeats 1
[OK] fresh-install verification passed
```
[OK] fresh-install verification passed
```

### CI 三平台实跑（run 43299517751，head `ed5b179`）

`install` workflow 在 ubuntu / windows / macos 三平台全部 `success`，日志里的关键行：

| 平台 | 安装器输出 | 断言 |
| --- | --- | --- |
| ubuntu-latest | `[OK] 6 skill folder(s) installed`、`[OK] created ~/.codex/config.toml and registered the MCP server`、`[OK] registered in ~/.claude.json` | pass |
| windows-latest | 同上（`created ~/.codex/config.toml and registered the MCP server`） | pass |
| macos-latest | 同上 | pass |

这是 `install.sh` 在**原生 Linux / macOS** 上的第一次真机执行 —— 此前只在 Windows 与 Git Bash 下验证过。

第一次推送时该 workflow 是红的，但**红的不是安装器**：断言步骤写成 `shell: python`，GitHub 会把 `run:` 里的命令行当成 Python **源码**交给解释器，于是报 `SyntaxError: invalid syntax`。改成 `shell: bash` 后三平台全绿。

### 从 GitHub Release 下载 wheel 的用户路径（v0.4.7）

Release 产物：`openloop_mas-0.4.7-py3-none-any.whl`（77,723 B）+ `openloop_mas-0.4.7.tar.gz`（99,179 B）。全新 venv 安装后：

```
$ mas --version
openloop-mas 0.4.7
$ mas doctor
[OK  ] provider       openai_compat / claude-opus-5-5 @ http://127.0.0.1:16024/v1（免鉴权端点，由握手探测确认）
[OK  ] endpoint       端点可用，协议方言 = openai-responses-events
$ mas demo
[OK ] closure     期望=closed             实际=closed             迭代=2 票=-
[OK ] stagnation  期望=escalated          实际=escalated          迭代=4 票=T-...
[OK ] budget      期望=budget_exhausted   实际=budget_exhausted   迭代=1 票=-
$ mas bench --repeats 1
符合预期 : 3/3      闭环率 : 100%      账本一致性 : 100%
```

真实端点闭环（`mas run`）：`task_id R-8853f87812-1`，`status closed`，`iterations 1`，`tokens_used 3987`，两条机检标准全部 `pass`。

### OpenMausBot 端到端（重启后重跑）

本机安装同步到 0.4.7 并重启 OpenMausBot（端口 28799）后重跑同一任务：

```json
{"event":"job","state":"completed","reason":"MausBot finished"}
```

- **run_id**：`R-d09530e7a2`（task_id `R-d09530e7a2-1`）
- **status**：`closed`，**iterations**：1
- 两条验收标准 c1 / c2 均 `pass`，`issues` 为空
- `artifact_sha`：`d0f3091cb554`，`tokens_used` 3940

---

## v0.4.8 收口：HTTP 鉴权缺陷定位与修复 + CI lint 收口

### 发现的真实安全缺陷：`mas http` 此前完全没有鉴权

`mas/http_server.py` 原本对所有路由开放：

| 路由 | 原本行为 | 风险 |
| --- | --- | --- |
| `GET /health` | 开放 | 仅返回版本号，无害（保留开放） |
| `GET /tools` | 开放 | 暴露工具清单 |
| `POST /call` | 开放 | **可执行任务**（`mas_run`） |
| `GET /events` | 开放 | **SSE 持续推送账本内容** |

原来的默认启动方式是 `mas http --host 127.0.0.1`，但 `--host` 是自由参数：一旦改成 `0.0.0.0` 并套一层反向代理或隧道，它就变成一台**任何人都能开任务、读你账本的远程控制台**。README 里旧那句「本项目不内置认证」等于把这个坑写进了文档。

### 修法（4 层）

1. **令牌来源**：`MAS_HTTP_TOKEN` 环境变量，或 `mas http --token <tok>` 参数。环境变量优先于命令行。
2. **鉴权头**：除 `/health` 外，所有路由接受 `Authorization: Bearer <token>` 或 `X-MAS-Token: <token>` 两种形式。
3. **常量时间比较**：用 `hmac.compare_digest`，避免时序侧信道。
4. **fail-closed**：绑定到**非 loopback** 地址且没有令牌时，**拒绝启动**并退出码 2 —— 不是「警告后继续」。

附带修掉一个真实的 keep-alive 缺陷：鉴权拒绝原本发生在**读取请求体之前**，残留字节会让同一连接上的下一个请求解析错位，客户端表现为卡死。现在改成**先读请求体、后鉴权**。

同时，开启鉴权后 CORS 从通配 `*` 钉到 `http://127.0.0.1`。

### 新增测试（`tests/test_http_auth.py`，12 条）

覆盖：loopback 判定（`127.0.0.1` / `::1` / `localhost` / `0.0.0.0` / `192.168.x.x`）、带令牌时无凭证 401、错误令牌 401、Bearer 与 `X-MAS-Token` 两种头都放行、CORS 被钉住、`/health` 保持开放、fail-closed 拒绝启动。

### 真实进程探针（非 mock，非单测桩）

用 Python 起真实的 `mas http` 子进程 + `urllib` 打真实 socket：

```
health              -> 200
tools  (no token)   -> 401
tools  (bad token)  -> 401
tools  (good token) -> 200
call   (no token)   -> 401
call   (good token) -> 200
```

fail-closed 探针：

- `mas http --host 0.0.0.0` 无令牌 → **exit 2** + 中文拒绝提示
- `mas http`（loopback）无令牌 → `/tools` 200（本地开发不受影响）

### 真实 Tailscale HTTPS 路径

起 `tailscale serve --bg --https=8768 http://127.0.0.1:8768`，从 tailnet 入口 `https://win-io6eufe3grh.tail4c219e.ts.net:8768` 打：

```
health              -> 200
tools  (no token)   -> 401
tools  (bad token)  -> 401
tools  (good token) -> 200
call   (no token)   -> 401
call   (good token) -> 200
```

即：**走真实 HTTPS + 反代 + 公网可信域名，鉴权依然生效**。验证完已用 `tailscale serve --https=8768 off` 清理，未留常驻配置。

### 顺带修掉的测试隔离漏洞

`tests/conftest.py` **只被 pytest 自动加载**；CI 用的 `python -m unittest discover -s tests` 根本没加载它 → 测试读到了真实用户的 `~/.mas/config.json`（真实端点），表现为间歇性 timeout —— 这正是「CI 偶发红、本地全绿」的根因。

修法：隔离逻辑移到 `tests/_isolation.py`（强制 `MAS_PROVIDER=mock`），新增 `tests/__init__.py` 触发导入，`conftest.py` 仅 re-export pytest hook。结果：unittest 耗时从 60s 降到 ~30s，连续 5 次全绿不再 flake。

### 引入可复现的 lint 配置

此前仓库**没有任何 ruff 配置**，CI 也不跑 lint —— 之前那句「All checks passed」只是本地默认规则的假象（`ruff check .` 实际报 429 errors）。

新增 `[tool.ruff]` / `[tool.ruff.lint]` 到 `pyproject.toml`：`target-version=py310`、`line-length=100`、`select=["E","F","W","I","B","SIM","C4","RET","ARG","PIE","RUF"]`。

ignore 理由：`E501`（中文诊断行超长）、`BLE001`（边界层故意捕获）、`ARG001/ARG002`（CLI 入口参数）、`RUF001/002/003`（中文全角标点被误判为混淆字符）、`RUF100`（代码里存在针对未启用规则的 noqa 标记）。per-file-ignores：`tests/*` = `["ARG","SLF","PLR2004","F841","RUF015"]`，`tools/*` = `["ARG","PLR2004","SIM105"]`。

CI 新增独立 `lint` job（ubuntu + Python 3.12 + `ruff check mas tests tools`）。

### ruff 揪出的真实代码问题（全部已修）

| 文件 | 问题 | 修法 |
| --- | --- | --- |
| `mas/providers.py` | B904 `raise exc` 丢失因果链 | `raise exc from retry_exc` |
| `mas/setup.py` | F841 未使用的 `backend_ids` | 删除 |
| `mas/util.py` | SIM105 `try/except/pass` | 改用 `contextlib.suppress(ValueError, OSError)` |
| `mas/probe.py` | PIE810 `startswith(a) or startswith(b)` | `startswith((a, b))` |
| `mas/engine.py` | PIE807 无用 lambda | 改 `describe()` 条件调用 |
| `mas/engine.py` | B905 `zip` 无长度校验 | 加 `strict=True`（checks 与 crits 一一对应） |
| `mas/config.py` | SIM102 嵌套 if | 合并 |
| `mas/orchestration.py` | SIM108 if/else 三元 | 改三元 |

import 排序由 `ruff check --fix` 自动处理。

### 本轮本地验证

```
unittest discover -s tests  ->  Ran 139 tests OK   （连跑 5 次全绿）
pytest tests -q             ->  139 passed, 8 subtests passed
ruff check mas tests tools  ->  All checks passed!
```

隔离 HOME 全新安装（`install.ps1 -InstallDir work/_iso048/.mas -SkipDemo`）+ `tools/verify_install.py`：

```
[OK] install assertions passed
[OK] MCP server answered with 12 tools
[OK] mas --version -> openloop-mas 0.4.8
[OK] mas doctor --no-network
[OK] mas demo
[OK] mas bench --repeats 1
[OK] fresh-install verification passed
```

`tools/verify_install.py` 同时新增**安装版本断言** —— 从已安装 runtime 的 `mas/__init__.py` 用正则读 `__version__`，而不是走父进程 import path（否则 `ModuleNotFoundError`）。

### v0.4.8 CI 与 Release 证据

提交 `d568804` 触发的四个 workflow **全部 success**：

| workflow | run id | 结论 |
| --- | --- | --- |
| `release` | 43307190818 | success |
| `pages` | 43307161819 | success |
| `ci` | 43307161987 | success（`lint` + `site` + `build` + 15 个 test 矩阵） |
| `install` | 43307161900 | success（windows / macos / ubuntu 三平台） |

`ci` 的 job 明细：

```
lint                             success
site                             success
build                            success
test (ubuntu-latest, 3.10~3.13)  4 x success
test (windows-latest, 3.10~3.13) 4 x success
test (macos-latest, 3.10~3.13)  4 x success
```

Release 产物：`openloop_mas-0.4.8-py3-none-any.whl`（80,422 B）+ `openloop_mas-0.4.8.tar.gz`（104,481 B）。从 `gh release download v0.4.8` 下载 wheel → 全新 venv 安装 → `mas --version` = `openloop-mas 0.4.8`；`mas doctor` 的 provider / endpoint 全 OK，方言 = `openai-responses-events`。

### v0.4.8 真实端点闭环

`mas run`（v0.4.8 独立 venv，真实端点）：

```json
{"run_id": "R-aea1a8ba15", "task_id": "R-aea1a8ba15-1", "status": "closed",
 "closed": true, "iterations": 1, "tokens_used": 3934,
 "artifact_sha": "d0f3091cb554"}
```

两条机检标准 c1 / c2 均 `pass`，`issues` 为空。

本机安装同步到 0.4.8 后，OpenMausBot 端到端（端口 28799）：

```json
{"event":"job","state":"completed","reason":"MausBot finished"}
```

- **run_id**：`R-1ee0025b0f`（task_id `R-1ee0025b0f-1`）
- **status**：`closed`，**iterations**：1
- **artifact_sha**：`d0f3091cb554`，`tokens_used` 4009
- MCP 握手：`handshake: ok, 12 tools`（`mas_run`, `mas_status`, `mas_ledger`, `mas_tickets`, `mas_ticket_resolve`, `mas_resume`, `mas_report`, `mas_replay`, `mas_export`, `mas_teams`, `mas_doctor`, `mas_config`）

### 常驻配置：手机接入（tailnet 内 HTTPS）

v0.4.8 收口后，把「手机接入」从一次性探针升级为**可长期使用的常驻配置**：

| 组件 | 落地方式 | 状态 |
| --- | --- | --- |
| 鉴权令牌 | 生成 43 位 `secrets.token_urlsafe(32)`，写入 `%USERPROFILE%\.mas\http_token.txt`（**不写进仓库、不写进 Startup 脚本**） | 已生成 |
| 常驻服务 | Startup 文件夹 `OpenLoop-MAS-Http.cmd` -> `mas http --host 127.0.0.1 --port 8768`，脚本启动时从令牌文件读 `MAS_HTTP_TOKEN` | 已落盘并运行中 |
| HTTPS 入口 | `tailscale serve --bg --https=8768 http://127.0.0.1:8768` -> `https://win-io6eufe3grh.tail4c219e.ts.net:8768` | 已生效 |
| 暴露范围 | **tailnet only**（不是 Funnel 公网）。本机 8443 的 Funnel 是 dsh-tauri 占用，未改动 | tailnet 内 |

设计要点：

- 服务只绑 **127.0.0.1**，外部访问必须经过 Tailscale 的 TLS 反代，避免直接暴露在局域网。
- 令牌与脚本分离：脚本里**不含明文令牌**，令牌文件缺失时脚本直接报错退出，不会退化成无鉴权启动。
- Startup 脚本等价于开机自启；如需停止，删掉该 `.cmd` 或结束对应进程。

真实 HTTPS 路径复测（走 Tailscale TLS + 反代，非本机直连）：

```
health              -> 200  {"ok": true, "name": "openloop-mas", "version": "0.4.8"}
tools  (no token)   -> 401  {"error": "unauthorized: missing or invalid bearer token"}
tools  (bad token)  -> 401  {"error": "unauthorized: missing or invalid bearer token"}
tools  (good token) -> 200  {"tools": [{"name": "mas_run", ...}]}
tools  (X-MAS-Token)-> 200  {"tools": [...]}
call   (no token)   -> 401  {"error": "unauthorized: missing or invalid bearer token"}
call   (good token) -> 200  {"tool": "mas_status", "result": {"config": {"provider": "openai_compat", "model": "claude-opus-5..."}}}
```

两种令牌头（`Authorization: Bearer` 与 `X-MAS-Token`）都验证过；`/health` 仍开放且只含版本号。

**手机上的用法**：浏览器打开 `https://win-io6eufe3grh.tail4c219e.ts.net:8768/tools`，带 `Authorization: Bearer <令牌>` 头即可。令牌通过密码管理器同步，不在聊天里明文传递。

**边界**：该入口是 **tailnet 内**可达（需设备加入同一 tailnet 并已授权），**不是公网 Funnel**。如需公网暴露，需显式改 `tailscale serve --https=8768 funnel` 并另做速率限制与令牌轮换，那属于扩大暴露范围，需另行确认。

---

## v0.4.9：测试隔离失效的根因与修复

v0.4.9 修复的不是一个 flake，而是一个**会随开发者本地配置漂移的严重缺陷**：整个测试套件在 `unittest discover -s tests` 下从未真正隔离，读的是开发者真实的 `~/.mas/config.json`，因此会打到真实端点。实测 6 次跑有 3 次因 40 秒超时失败——这掩盖了它更大的危害：**CI 绿灯并不代表套件跑在沙箱里**。

### 根因（三层，逐层叠加）

| 层 | 位置 | 现象 |
| --- | --- | --- |
| L1 | `tests/__init__.py` 的 `from . import _isolation` | `unittest discover -s tests` 把模块加载成顶层 `test_x`，**包 `__init__.py` 根本不执行**，隔离模块从未被加载 |
| L2 | `tests/test_config_providers.py::TestConfig::test_env_overrides` 的 `finally` | 无条件 `os.environ.pop("MAS_PROVIDER")`，**摧毁隔离** |
| L3 | `tests/test_mcp.py::TestMcpServer::tearDown` | 同样无条件 `os.environ.pop("MAS_PROVIDER", None)`，**再次摧毁隔离** |

L1 是根本原因：隔离只挂在一个不会执行的钩子上。L2/L3 是放大器——即使 L1 修好，任何一个测试的 `pop` 都会让隔离在后续测试中静默消失。

探针证实：两种 discover 方式下，测试内看到的 `MAS_PROVIDER` 都是 `None`（而非隔离后的 `mock`）。

### 修法

1. **`tests/_isolation.py`**：加 `ISOLATION_ACTIVE` 标志；加 `MAS_TEST_ISOLATION=1` 环境哨兵 + `_ALREADY_INSTALLED` 守卫，防止 `tests._isolation` 与顶层 `_isolation` 被当成两个模块各加载一次、彼此清空。
2. **全部 12 个 `tests/test_*.py`**：模块顶部直接导入隔离（不再依赖包钩子）。双形态兼容：

   ```python
   try:  # pragma: no cover - the two import styles cover two runners
       from ._isolation import ISOLATION_ACTIVE  # noqa: F401
   except ImportError:  # loaded as a top-level module (unittest -s tests)
       import os
       import sys
       sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
       from _isolation import ISOLATION_ACTIVE  # noqa: F401
   ```

   banner 插在 `from __future__ import annotations` **之后**（`test_dialect_loop.py` / `test_probe.py` / `test_provider_dialects.py` 三个文件），否则 Python 3.14 会报 "from __future__ imports must occur at the beginning of the file"。
3. **恢复而非销毁**：`test_env_overrides`、`test_persisted_view_ignores_the_environment`、`TestMcpServer` 的 `setUp`/`tearDown` 全部改为**保存-恢复**，不再 `pop`。
4. **语义修正**：`test_partial_file_keeps_defaults` 原先期望「文件值胜过环境」，这与 `load_config` 的文档语义（**环境变量覆盖文件**）直接冲突，它在隔离从未生效时才碰巧通过。改为用 `load_persisted_config()` 断言「部分文件保留默认值」，并另行断言有效视图中环境确实胜出——两条语义各归其位。
5. **新增 `tests/test_isolation.py`**（6 条）锁定隔离不变量，防止未来重构再次静默关掉隔离。

### 验证证据（本轮实测）

| 项 | 修复前 | 修复后 |
| --- | --- | --- |
| `unittest discover -s tests` 用例数 | 151 | **157**（+6 条防回归） |
| 单次耗时 | 40 秒超时（命中真实端点） | **~16.5 秒**（全程 mock） |
| 连跑 5 次 | 6 跑 3 败 | **5 跑 5 全绿**，耗时稳定 16.3–16.7 秒 |
| `pytest tests -q` | 未验证 | **155 passed, 2 skipped, 8 subtests passed**，17.04 秒 |
| `ruff check mas tests tools` | — | **All checks passed** |

隔离生效的直接证据：套件耗时从 40 秒（真实端点超时）降到 16.5 秒且完全稳定——不再有「取决于端点有多慢」的随机性。

### 边界

- 修复的是**测试套件自身**的隔离，不改变 `mas` 在生产中的任何行为。
- `pytest` 与 `unittest` 两条路径都验证过；CI 用的仍是 `unittest discover -s tests`。

---

## v0.4.9 收口：Release 产物 + 真实端点 + OpenMausBot 端到端复测

### CI 与 Release

`be87b99`（测试隔离修复）触发的 `ci` workflow **首次失败**，暴露了第二个真实缺陷（见下）。修复后 `e1b171a` 的 4 个 workflow 全部 success：

| workflow | run id | 结论 |
| --- | --- | --- |
| ci | 43318210169 | success（lint + site + build + 15 个 test 矩阵） |
| pages | 43318210060 | success |
| install | 43318210211 | success（windows / macos / ubuntu 三平台） |
| release | 43318569086 | success |

Release `v0.4.9`：`openloop_mas-0.4.9-py3-none-any.whl`（81,026 B）+ `openloop_mas-0.4.9.tar.gz`（108,894 B）。

### CI 暴露的第二个缺陷：POSIX launcher 未声明绑定地址

`be87b99` 的 ci 在 ubuntu-latest 3.10 / 3.13 与 macos-latest 3.10 上失败：

```
FAIL: test_launcher_binds_loopback_only (test_serve_daemon.ServeDaemonTest)
AssertionError: '127.0.0.1' not found in '[Unit]\nDescription=OpenLoop MAS HTTP/SSE transport\n...
```

根因不是测试写错，而是**设计缺陷**：systemd unit 与 launchd plist 只写 `ExecStart=/bin/sh <wrapper>`，绑定地址 `127.0.0.1` 只存在于 wrapper 脚本里。launcher 本身——也就是交给服务管理器的那份声明——完全没有表达「只绑 loopback」。这意味着一个运维只看 unit 文件时，无法从声明中确认服务不会监听 LAN 地址。

修法（`e1b171a`）：

- systemd unit 增加 `Environment="MAS_HTTP_HOST=127.0.0.1"` 与 `Environment="MAS_HTTP_PORT=<port>"`，并附注释说明只绑 loopback。
- launchd plist 增加同样的 `EnvironmentVariables` 字典。
- wrapper 改为读取 `MAS_HTTP_HOST` / `MAS_HTTP_PORT`，带 loopback 默认值。
- 新增 4 条测试：`每个 launcher 面都必须声明 loopback 且不得出现通配地址` / `POSIX launcher 必须把 host/port 传给服务` / `wrapper 必须尊重传入的 host/port` / `POSIX 路径必须用正斜杠`。

这是本轮第二次由 CI 抓出真实问题——第一次是测试隔离，第二次是 launcher 声明。两者的共同点是：**「本机跑通」不等于「跨平台正确」**。

### Release wheel 真实端点闭环

从 `gh release download v0.4.9` 下载 wheel → 全新 venv 安装 → `mas --version` = `openloop-mas 0.4.9` → `mas doctor` 全部 OK：

```
[OK  ] provider  openai_compat / claude-opus-5-5 @ http://127.0.0.1:16024/v1（免鉴权端点，由握手探测确认）
[OK  ] endpoint  端点可用，协议方言 = openai-responses-events
```

`mas run`（真实端点）：

```json
{"run_id": "R-69722fd5e3", "status": "closed", "iterations": 1, "tokens": 4284}
```

两条机检标准 c1 / c2 均 `pass`，`issues` 为空。

### OpenMausBot 端到端（v0.4.9）

本机安装同步到 0.4.9（`install.ps1 -SkipDemo` 全部自检 OK），重启 OpenMausBot（端口 28799）后：

- MCP 握手：`handshake: ok, 12 tools`
- `openmausbot run MausBot <prompt>` 真实任务：

```json
{"run_id": "R-9657aa76a0", "task_id": "R-9657aa76a0-1",
 "iterations": 1, "artifact_sha": "29fc6fafa39f", "tokens": 3713,
 "verdict": "pass", "decision": "close", "stagnation": 0}
```

账本明细：planner 3 步计划，closure `verdict=pass` / `decision=close`，c1 规则机检 `regex /(?s)def dedupe.*set/ matched`，issues 为空。

### 常驻服务与 HTTPS 入口复测（v0.4.9）

常驻服务重启后版本已升：`/health` 返回 `"version": "0.4.9"`。走 Tailscale HTTPS（`https://win-io6eufe3grh.tail4c219e.ts.net:8768`）实测：

```
health                   -> 200  {"ok": true, "name": "openloop-mas", "version": "0.4.9"}
tools(no token)          -> 401  {"error": "unauthorized: missing or invalid bearer token"}
tools(bad token)         -> 401  {"error": "unauthorized: missing or invalid bearer token"}
tools(bearer)            -> 200  {"tools": [...]}
tools(x-mas-token)       -> 200  {"tools": [...]}
call(no token) POST      -> 401  {"error": "unauthorized: missing or invalid bearer token"}
call(bad token) POST     -> 401  {"error": "unauthorized: missing or invalid bearer token"}
call(good token) POST    -> 200  {"tool": "mas_status", "result": {"config": {"provider": "openai_compat", ...}}}
```

### 本轮测试证据

| 项 | 数值 |
| --- | --- |
| `unittest discover -s tests` | 161 tests，**OK (skipped=4)**，~16.5–18.5 秒 |
| 连跑 5 次（隔离修复后） | 5 跑 5 全绿，耗时 16.3–16.7 秒 |
| `pytest tests -q` | 155 passed / 2 skipped / 8 subtests（隔离修复后），17.04 秒 |
| `ruff check mas tests tools` | All checks passed |
| CI test 矩阵 | 15 个 job 全 success（lint + site + build + 12 组 test） |

### 边界（v0.4.9 仍然成立）

1. 真实模型实测只覆盖一个端点（`claude-opus-5-5` @ Codex Responses 桥接，免鉴权）。Kimi / DeepSeek / Qwen / 原生 OpenAI 本机无端点，**未实测**。
2. 契约测试覆盖协议形状，不保证每厂商接受额外的 `input` 字段。
3. 判定的是结构不是事实；对抗评审发现的 `return list(seen)` 恒空 bug 仍是模型判断。
4. 真实端点下未做多轮崩溃恢复的账本重放校验。
5. **PyPI 仍未发布**：缺 secret `PYPI_API_TOKEN`。
6. 手机接入是 **tailnet only**，非公网 Funnel。
7. `install.ps1` 只在本机 Windows 跑过；`install.sh` 依赖 install CI（三平台全绿）。
8. 不承诺流量 / 签约 / 平台批准 / 检测结果。

---

## v0.5.0：账本写入路径去 O(n²)

### 缺陷

`Ledger.append()` 每写一条都调用 `self.entries()` **全量重读整个账本文件**再取长度作为 `seq`。一次运行写 N 条 → 总读取量 O(N²)。账本越长，**每一次运行都更慢**，而且是持续劣化而非一次性成本。

### 修法

给 `Ledger` 加一个带失效检测的计数缓存：

- 缓存键是 `(st_size, st_mtime_ns)`——文件身份。
- `append` 前比对键；键没变就直接用缓存计数，**不再重读文件**。
- 自己的写入会改变文件，因此写完后更新缓存计数与新键，让下一次追加仍然免扫描。
- 文件在背后被改写（另一个进程追加、轮转、截断）时键会变化，缓存自动失效并重新扫描——**序号语义与修复前完全一致**。

### 量化证据（本轮实测，同一台机器同一进程内对比）

| 实现 | 2000 条追加耗时 | 平均每条 |
| --- | --- | --- |
| 修复前（全量重读） | **46.533 s** | 23.266 ms |
| 修复后（缓存计数） | **1.380 s** | 0.690 ms |

**33.7 倍提速。** 序号仍为 1..2000 无空洞。

### 新增测试（`tests/test_ledger.py`，5 条）

| 测试 | 锁定的不变量 |
| --- | --- |
| `test_seq_is_gapless_and_starts_at_one` | 序号从 1 起、无空洞 |
| `test_cache_does_not_break_when_the_file_is_truncated` | 文件被外部截断后缓存失效，序号重新从 1 开始 |
| `test_two_writers_on_the_same_file_get_distinct_sequences` | 两个 `Ledger` 实例共享同一文件时序号不重复 |
| `test_entries_are_still_appended_never_rewritten` | 账本仍是只追加语义，旧条目不被改写 |
| `test_ts_is_filled_in` | 时间戳仍自动补齐 |

### 影响面

- 只改 `mas/ledger.py` 的 `append` 路径，不改变账本格式、不改变任何一条已写记录。
- 本机真实账本 `C:\Users\Administrator\.mas\ledgers\runs.jsonl` 不受影响（新代码只影响新写入的条目序号计算方式，且序号值与旧实现一致）。
