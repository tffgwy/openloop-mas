# 把 OpenLoop MAS 挂进 OpenMausBot

[OpenMausBot](https://github.com/milind-soni/OpenMausBot) 是开源的 Grok Bot 平替：一个聊天界面里，
每个联系人都是一个真的智能体（Claude Code / Codex 在本地跑），各有自己的模型、电脑和接的应用。

[OpenLoop MAS](../README.md) 负责另一半：**让「做完没有」变成可被机器判定的事实**。

两者接在一起是自然的：

- OpenMausBot 提供**入口**——聊天、审批、多智能体团队、1700+ 应用连接；
- OpenLoop MAS 提供**判定**——机检规则、闭环账本、停滞检测、人工升级工单。

接完之后，一个 bot 在聊天里收到任务，可以直接调用 `mas_run`，拿回一个
`closed / escalated / budget_exhausted` 的确定结论，而不是一句「我完成了」。

## 架构

```
   你 ──聊天──> OpenMausBot (127.0.0.1:8799)
                   │  挂载自定义 MCP server
                   ▼
              OpenLoop MAS (stdio MCP)
                   │
                   ▼
        机检规则 · 闭环账本 · 停滞检测 · 人工工单
```

## 一、安装 OpenMausBot

需要 Node.js 24 或更高版本。

```sh
npm install -g openmausbot
openmausbot
```

首次启动是交互式向导：选 AI 接入方式（Codex / Claude Code / Kimi / Gemini /
DeepSeek / API key）→ 选模型 → 可选配手机。之后每次 `openmausbot` 直接复用已保存的配置。

> Windows 桌面包直接下载 `OpenMausBot-setup.exe`；Linux 用 `.deb` 或 `.AppImage`；
> macOS 用 `.dmg`（已签名公证）。

## 二、注册 OpenLoop MAS 作为 MCP server

OpenMausBot 的 `config.json`（默认在 `~/.openmausbot/config.json`）支持
`mcpServers` 块，命令型 server 就是标准 stdio MCP：

```json
{
  "mcpServers": {
    "openloop-mas": {
      "command": "python",
      "args": ["-X", "utf8", "-m", "mas", "serve"],
      "env": {
        "PYTHONPATH": "~/.mas/app",
        "PYTHONUTF8": "1",
        "MAS_STATE_DIR": "~/.mas",
        "MAS_CONFIG_PATH": "~/.mas/config.json"
      },
      "enabled": true
    }
  }
}
```

用一键脚本（Windows / macOS / Linux 通用）：

```sh
python tools/install_openmausbot_mcp.py
```

脚本会自己找 `mas` 的安装位置、写好配置、备份旧文件并做一次握手测试。

改完 `config.json` 要**重启 OpenMausBot** 才会生效——文档里写明这一点，
因为 server 是进程启动时挂载的。

## 三、验证

握手测试（会真的启动 server 并列出工具）：

```sh
curl -X POST http://127.0.0.1:8799/api/mcp/servers/openloop-mas/test
```

期望返回 12 个工具：

```
mas_run  mas_status  mas_ledger  mas_tickets  mas_ticket_resolve  mas_resume
mas_report  mas_replay  mas_export  mas_teams  mas_doctor  mas_config
```

端到端跑一个闭环：

```sh
openmausbot run MausBot "调用 openloop-mas 的 mas_run，goal 为「写一句中文说明什么是循环工程」，
criteria 为 [{\"id\":\"c1\",\"desc\":\"回答含中文\",\"check\":{\"type\":\"regex\",\"arg\":\"[\\u4e00-\\u9fa5]{4,}\"}}]"
```

## 四、给 bot 的任务模板

### 让 bot 用机检规则判定

机检规则是闭环能不能闭合的关键。**没有 `check` 的标准会判为 `unjudged`**，
在 mock provider 下永远无法闭合——这是刻意设计：宁可升级到人工，不假装成功。

```json
{
  "goal": "把 README 的安装章节改写成三步",
  "criteria": [
    {"id": "c1", "desc": "包含三个编号步骤",
     "check": {"type": "regex", "arg": "1[\\.、].*2[\\.、].*3[\\.、]"}},
    {"id": "c2", "desc": "不含旧命令",
     "check": {"type": "not_contains", "arg": "pip install mas"}}
  ]
}
```

可用的检查类型：`contains`、`not_contains`、`min_chars`、`regex`。

### 让 bot 处理需要升级的情况

当闭环停在 `escalated`，会生成一张人工工单。bot 可以列出工单：

```
mas_tickets          # 列出待处理工单
mas_ticket_resolve   # 批准或驳回一张工单
mas_resume           # 带着人工意见继续被升级的循环
```

## 五、权限与安全

OpenMausBot 的自定义 MCP server **默认不预批准**：

- 在 Claude 引擎上，工具调用走权限代理，变成 Allow / Deny 卡片；
- 在 Codex 引擎上，保持按需审批策略；
- 自定义 server 名字不能占用 OpenMausBot 的保留名（`browser`、`computer`、`agents` 等）；
- `OMB_*` / `OGB_*` 前缀的环境变量名会被拒绝。

凭据只写在环境变量里，不进 argv。`MAS_CONFIG_PATH` 指向的 `config.json` 里如果存了
API key，它在本机是**明文**——按官方文档的说法，CLI 安装的版本不加密。
桌面版会用系统钥匙串（Windows 上是 DPAPI）加密，但 `config.json` 里的
OpenCode provider key、单引擎 key 和 MCP header 值仍是明文。

## 六、开机自启（Windows）

`openmausbot service install` 只支持 Linux（systemd）和 macOS（launchd）。
Windows 上用任务计划程序或 Startup 文件夹：

```powershell
# Startup 回退（本机实测可用；任务计划程序在部分环境下会 Access is denied）
$startup = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup"
Copy-Item "$env:USERPROFILE\.openmausbot\start-openmausbot.cmd" "$startup\OpenMausBot-Server.cmd"
```

## 七、边界

- OpenMausBot 的 MCP 挂载要求重启服务器才生效，不能热加载自定义 server；
- OpenMausBot 目前**不提供**远程 API 的自助注册入口，MCP 配置要么走 UI（Plugins → MCP servers），
  要么手改 `config.json`；
- OpenMausBot 的开源版不含企业层功能，`OMB_LICENSE_KEY` 未设置时以开源版运行；
- 本项目（OpenLoop MAS）的并行编排、对抗评审、故障转移三条链路目前只有
  mock + 注入式测试覆盖，尚未在真实模型端点上跑过。

## 相关

- [MCP 接入](./mcp.md) —— OpenLoop MAS 自己的 MCP 工具清单
- [架构](./architecture.md)
- [循环工程](./loop-engineering.md)
