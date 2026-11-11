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

安装器做四件事：

1. 定位 Python ≥ 3.10；
2. 把运行时拷到 `~/.mas/app`，在 `~/.mas/bin/mas.cmd`（Windows）生成启动器并加入用户 PATH；
3. 把 `mas-loop` / `mas-review` / `mas-scaffold` 三个 Skill 装进本机找到的所有 skill 目录；
4. 把 MCP 服务注册进 `~/.codex/config.toml` 与 `~/.claude.json`（注册前自动备份）。

可选开关：`-SkipPath` / `-SkipMcp` / `-SkipSkills` / `-SkipDemo`；`-Python` 指定解释器；`-InstallDir` 指定安装根。

验证：

```bash
mas doctor      # 9 项自检
mas demo        # 离线跑三个闭环场景
```

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

---

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

**Q：任务一直不闭合？**
看 `mas ledger` 里的 `verdict` 与 `note`；若是 `unjudged`，说明该标准既没有机检规则，模型也没给判定——补一条 `check` 规则最省事。

**Q：想让它少烧 token？**
调小 `budget.max_iterations` / `budget.max_tokens`，或调小 `stagnation_limit` 让卡死更早被拦下。
