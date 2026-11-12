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
