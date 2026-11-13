# `mas relay` —— 给「非要 API Key」的客户端用的私有端点中继

## 1. 它解决什么死结

现在很多 agent 宿主在**协议层面**就强制要求一个 API key：

- DeepSeek Harness 的 `dsh-llm-pi-ai` 适配器，`openai-completions` 和
  `openai-responses` 两种协议都**坚持要 `apiKeyEnv` 或 `Authorization` 头**，
  没给就直接不发请求；
- 而私有端点（自己机器上的桥、公司内网网关、本地推理服务）常常**根本不校验凭据**，
  甚至会因为一个占位 key 直接回 `401`。

两头一撞就是死结：

```
客户端：我非要给 key            上游：你给的 key 我不认 → 401
```

这不是配置问题，是**协议契约和端点契约不一致**。本项目实测到的具体现场：

- 宿主用占位 key `client-placeholder` 打本机桥 → `401 无效的API Key`；
- 同一个宿主、**不带**任何 `Authorization` 头打同一个桥 → `200`。

`mas relay` 就是插在中间的那一层：

```
DSH (非要 key)  ──►  mas relay  ──►  私有桥 (不要 key，或要另一把 key)
                    丢弃 Authorization
                    按需换成上游真 key
```

## 2. 快速上手

```powershell
# 默认：监听 127.0.0.1:8766，转发到 http://127.0.0.1:16024
mas relay

# 指定上游与端口
mas relay --upstream http://127.0.0.1:16024 --port 8766 --verbose

# 上游需要真凭据时：客户端的 key 丢掉，换成上游那把
$env:MY_UPSTREAM_KEY = "sk-..."
mas relay --upstream https://api.example.com --upstream-key-env MY_UPSTREAM_KEY
```

支持的路径：

| 路径 | 作用 |
| --- | --- |
| `GET  /v1/models` | 模型目录（无 `/v1` 前缀的 `/models` 同样支持） |
| `POST /v1/responses` | OpenAI Responses 协议（支持 SSE 流式） |
| `POST /v1/chat/completions` | OpenAI Chat 协议（支持 SSE 流式） |
| `GET  /health` | 存活探测，**永远开放**，不泄露任何配置 |

> **为什么要两种前缀都支持**：不同宿主对版本段的位置有分歧，而且有的私有桥
> 只在 `/models` 上有实现、对 `/v1/models` 直接挂住。relay 会在 `/v1/X` 失败时
> 自动改试兄弟路径 `/X`——**但只对 GET 生效**，避免一次生成被重复请求两遍。

## 3. 两种鉴权模式

| 模式 | 客户端的 `Authorization` | 上游收到什么 | 适用 |
| --- | --- | --- | --- |
| **strip**（默认） | 丢弃 | 不带凭据 | 上游不校验凭据的私有端点 |
| **replace** | 丢弃 | `--upstream-key-env` 指定的那把 | 上游要真凭据、但客户端拿不到 |

无论哪种模式，客户端发来的 `Authorization`、`x-api-key`、`cookie` 都会被**丢弃**，
不会原样透传给上游。上游密钥只从环境变量读，**不写盘、不回显、不进日志**。

## 4. 安全边界

- **默认只监听回环**。绑到非回环地址且没有令牌时，relay **拒绝启动**（退出码 2），
  理由和 `mas http` 一样：这东西是一扇通往你模型账户的门。
- 想对外暴露必须给令牌：
  ```powershell
  $env:MAS_RELAY_TOKEN = "..."
  mas relay --host 0.0.0.0
  ```
  令牌通过 `Authorization: Bearer <token>` 或 `X-MAS-Token` 头传入，
  `/health` 除外。
- 上游密钥**不会**通过任何响应头、错误消息或日志泄露。
- relay **不缓存**任何请求体或响应体。

## 5. 配合 DeepSeek Harness 一键装

`tools/setup_dsh_bridge.py` 把「profile + 插件 + relay 路由」一次生成好：

```powershell
python tools/setup_dsh_bridge.py --profile masbridge --relay-port 8766
```

它会：

1. 在 `~/.dsh/profiles/<profile>/` 写出 `cordis.patch.yml` / `cordis.yml` / `package.json`；
2. 自动执行 `dsh plugin --profile <profile> add dsh-openloop-mas@file:<repo>/integrations/dsh-openloop-mas`；
3. 打印「起 relay」和「跑 headless」两条现成命令。

然后：

```powershell
# 终端 1
mas relay --upstream http://127.0.0.1:16024 --port 8766

# 终端 2 —— pi-ai 适配器坚持要 key，给个占位值即可（relay 会丢弃它）
$env:MAS_BRIDGE_API_KEY = "placeholder"
dsh --profile masbridge headless "用 mas_run 跑一个闭环：目标是写一份发布说明，验收标准用 file_exists 要求 out/report.md 存在"
```

## 6. 已验证 / 未验证

**已验证（本机实测）**：

- `/health` 返回 `{"ok": true, "name": "openloop-mas-relay", ...}`，HTTP 200；
- `GET /v1/models` 透传上游目录（HTTP 200），并在 `/v1/models` 挂住时自动回退到 `/models`；
- `POST /v1/responses` 透传成功：客户端带占位 key，relay 丢弃后上游仍返回 200；
- SSE 流式响应按 chunked 正确转发；
- 非回环 + 无令牌 → 拒绝启动，退出码 2；
- 单元测试 `tests/test_relay.py` **12 个全过**；
- **端到端**：DSH 宿主内 `mas_run` 真实跑出 `status=closed`、`closed=true`、
  判定 `source="file"` 读的是真实磁盘，产物文件确实落盘。

**未验证**：

- 只在本机桥（`claude-opus-5-5` @ `127.0.0.1:16024/v1`，免鉴权）上跑通过；
  **Kimi / DeepSeek / Qwen / 原生 OpenAI 端点未实测**；
- **replace 模式未在本机实测**（本机桥不需要凭据），只有单元测试覆盖；
- 长时间运行的连接稳定性、并发上限未做压力测试。
