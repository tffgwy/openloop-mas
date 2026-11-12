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
