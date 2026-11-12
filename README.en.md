# OpenLoop MAS · A Multi-Agent System That Actually Closes Its Loops

> A **zero-dependency, standard-library-only** multi-agent loop runtime. It turns
> "plan → work → verify → reflect" into an **engineering loop with acceptance
> criteria, exit conditions and an auditable ledger**. One command to install;
> beginners can run it too.

[English](README.md) | [中文](README.md)

[![ci](https://github.com/tffgwy/openloop-mas/actions/workflows/ci.yml/badge.svg)](https://github.com/tffgwy/openloop-mas/actions/workflows/ci.yml)
[![license](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![deps](https://img.shields.io/badge/dependencies-0-orange.svg)]()

**What v0.2.0 adds**: parallel candidate competition, adversarial cross-review,
multi-backend failover, HTTP/SSE remote access, closure reports and replay,
an interactive setup wizard, an offline benchmark and a real closure self-check.

---

## Why

The most common failure in multi-agent collaboration is not "the model is not
smart enough" - it is that **the loop never closes**:

- No verifiable acceptance criteria → everyone says "close enough" and nobody can be convinced;
- No exit condition → rounds keep going until the tokens run out;
- No process record → failures cannot be reviewed, handed over or audited;
- No stop-loss → an obviously doomed task retries until it times out.

Loop Engineering addresses exactly this: **turning "is it done?" into a fact a
machine can decide.**

OpenLoop MAS writes the closure into code constraints instead of hoping a
prompt will do it:

| Stage | Role | Output | Decided by |
| --- | --- | --- | --- |
| Plan | `planner` | steps and risks | — |
| Work | `worker` | **the deliverable itself** | — |
| Verify | `verifier` | per-criterion pass/fail plus evidence | engine (machine checks win) |
| Reflect | `reflector` | why it failed, what to change | — |
| Close | `lead` | `close` / `revise` / `escalate` | engine |

A loop is closed only when **artifact + per-criterion verdicts + decision +
ledger entry** all exist. Otherwise the status is one of exactly three:
`escalated` (handed to a human), `budget_exhausted` (stopped by budget) or
`error`. **There is no "almost done" state.**

---

## 5-minute quickstart

> The configuration always lives in `~/.mas/config.json` (Windows:
> `%USERPROFILE%\.mas\config.json`). The run ledger lives under `state_dir`
> instead - the two are separate, so moving the state directory never loses
> your setup. In CI, use `MAS_CONFIG_PATH` for the config file and
> `MAS_STATE_DIR` for the state directory.

```bash
git clone https://github.com/tffgwy/openloop-mas.git
cd openloop-mas

# Windows
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
# macOS / Linux
bash install.sh

# No API key needed - watch it close a loop on its own
mas demo
```

`mas demo` runs three offline scenarios and prints the real results:

```
[OK ] closure     期望=closed             实际=closed             迭代=2 票=-
[OK ] stagnation  期望=escalated          实际=escalated          迭代=4 票=T-20261111-085346-4396
[OK ] budget      期望=budget_exhausted   实际=budget_exhausted   迭代=1 票=-
```

> In `mock` mode, pass/fail is decided by the engine's deterministic machine
> checks - not by a model claiming "I am done". The mock only simulates the
> worker producing a draft; the verdict always belongs to the checker.

---

## Run a real task

```bash
mas run --goal "Write a v0.1.0 release note" \
  --criteria '{"id":"c1","desc":"contains the version","check":{"type":"contains","arg":"v0.1.0"}}' \
  --criteria '{"id":"c2","desc":"at least 200 chars","check":{"type":"min_chars","arg":200}}'
```

Output:

```
状态：closed（迭代 2 次，token 2140）
账本：/home/you/.mas/ledgers/runs.jsonl
  [PASS] c1 (rule) all required strings present
  [PASS] c2 (rule) length 418 >= 200
--- 交付物预览 ---
...
```

### Machine check types (decided by the engine, fully reproducible)

| type | meaning | argument |
| --- | --- | --- |
| `contains` / `not_contains` | must contain / must not contain | `arg` (string or array) |
| `min_chars` / `max_chars` | length bounds | `arg` (int) |
| `regex` | regex match | `arg` (pattern) |
| `lines_min` | minimum non-empty lines | `arg` (int) |
| `section` | must contain a `## heading` | `arg` (heading) |
| `json_keys` | required keys of a JSON object | `arg` (array) |

Criteria without a machine check fall back to the model verifier - but **if the
model gives no verdict, the default is fail** (`unjudged`). A criterion marked
`"required": false` is recorded but never blocks closure.

---

## Parallel multi-agent: let candidates compete

One worker produces exactly one kind of solution. v0.2 lets several workers
produce candidates **in parallel, from different angles**, and lets the engine
pick the winner by machine-check results:

```bash
# Raise the effort for a single task without touching the config
mas run --goal "..." --criteria-file criteria.json \
  --parallel 3 --perspective thorough \
  --reviewers 3 --review-mode majority

mas init --wizard    # q7 worker count, q8 reviewer count, q11 fallback chain
```

| flag | effect |
| --- | --- |
| `--parallel N` | number of parallel candidates (N>1 races several angles; the engine picks the winner by machine-check score) |
| `--perspective` | anchor angle: `direct` / `thorough` / `concise` / `structured` / `example_first` / `risk_first` |
| `--reviewers N` | number of independent reviewers (N>1 enables adversarial aggregation) |
| `--review-mode` | aggregation rule: `all` (unanimous) / `majority` / `any` |

Or edit the config directly:

```json
{
  "orchestration": {
    "parallel_workers": 3,
    "reviewers": 3,
    "review_mode": "all"
  }
}
```

| setting | behaviour |
| --- | --- |
| `parallel_workers` | candidates produced in parallel. Angles cycle through direct / thorough / concise / structured / example_first / risk_first - **not N samples of one prompt**. |
| `reviewers` | independent verifiers. Personas cycle through strict / skeptic / completeness / reader. |
| `review_mode` | `all` (unanimous), `majority` or `any`. |

**Selection is deterministic**: machine-check wins first, then artifact length,
then index. The same config selects the same candidate twice - which is what
makes replay trustworthy.

**Machine checks always beat model judgement**: a criterion with a `check` is
decided by the rule. Only criteria without one fall to the reviewer consensus.
A reviewer saying "pass" can never override a rule saying "fail".

**Defaults reproduce v0.1 exactly**: with `parallel_workers: 1` and
`reviewers: 1`, the code path is identical to v0.1.

---

## Multi-backend failover

If one endpoint returns 502 and kills the whole loop, that is not a closed
loop - it is an outage. Configure fallbacks and the primary is tried first:

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

Each response records which backend actually served it (`provider_used` /
`model_used`). Only when every backend fails does the run error out.

---

## Observability: reports and replay

```bash
mas report                 # Markdown closure report
mas report --out r.md      # write to a file
mas replay                 # replay the run trace from the ledger
mas export                 # export the raw ledger as a Markdown table
```

Every number in the report comes from the ledger. **Nothing is invented.**

---

## Remote access: HTTP + SSE

stdio serves local agents. HTTP/SSE lets the runtime be reached by a remote
client, a browser dashboard or a CI job:

```bash
mas http --host 127.0.0.1 --port 8765
```

| route | method | purpose |
| --- | --- | --- |
| `/health` | GET | liveness + version |
| `/tools` | GET | tool catalogue |
| `/call` | POST | call any tool, JSON in / JSON out |
| `/events` | GET | SSE stream of ledger growth |

### Authentication (required when exposing it)

`/call` executes tools and reads the ledger; `/events` streams the ledger
continuously. Since **v0.4.8** the HTTP transport therefore ships with
built-in authentication:

```bash
export MAS_HTTP_TOKEN=$(python -c "import secrets;print(secrets.token_urlsafe(32))")
mas http --host 0.0.0.0 --port 8765
```

With `MAS_HTTP_TOKEN` set, every route except `/health` requires the token,
either as `Authorization: Bearer <token>` or as `X-MAS-Token: <token>`.

**Fail-closed**: binding to a non-loopback address (`0.0.0.0`, a LAN IP)
*without* a token makes the server **refuse to start** (exit code `2`) - a
typo in an environment variable must never turn into an open, world-reachable
control plane. With authentication on, CORS is pinned to
`http://127.0.0.1` instead of `*`, so no arbitrary web page can call it
cross-site.

---

## Four stop-loss mechanisms

1. **Iteration budget**: `budget.max_iterations` → `budget_exhausted`.
2. **Wall-clock budget**: `budget.deadline_sec`.
3. **Token budget**: `budget.max_tokens`.
4. **Stagnation detection**: the same artifact repeated N times in a row
   (default 3, `stagnation_limit`) → **escalate to a human ticket** instead of
   spinning.

An escalated ticket carries the full context (goal, criteria, last artifact,
failure reasons, iteration history), so a human can pick it up with one command:

```bash
mas tickets
mas resume T-20261111-085346-4396 --note "rewrite the conclusion section"
```

---

## The closure ledger

Every cycle appends one entry to `~/.mas/ledgers/runs.jsonl` (append-only):

```json
{"seq":3,"ts":"2026-11-11T08:53:46+00:00","run_id":"R-3f1c...","task_id":"R-3f1c...-1",
 "iteration":2,"role":"closure","provider":"mock","model":"mock-1",
 "artifact_sha":"9d1f...","verdict":"pass","decision":"close",
 "scores":[{"id":"c1","pass":true,"note":"all required strings present","source":"rule"}],
 "closed":true}
```

The ledger is the **evidence**: reviewable, reproducible, handable over, and
usable as a CI gate.

---

## Bring your own model

The runtime only uses the standard library and `urllib` for HTTP, so any
OpenAI-compatible endpoint works:

```bash
# Kimi / Moonshot
mas init --provider openai_compat --model kimi-k2 --base-url https://api.moonshot.cn/v1
export MAS_API_KEY=sk-...

# Claude
mas init --provider anthropic --model claude-sonnet-4-5
export MAS_API_KEY=sk-ant-...

# GPT / Grok / DeepSeek / Qwen / self-hosted vLLM - anything OpenAI-compatible
mas init --provider openai_compat --model gpt-4o-mini --base-url https://api.openai.com/v1
```

Three built-in tolerances cover most "OpenAI-compatible" gateways:

- `response_format` is dropped and the call retried once when unsupported, with
  JSON then extracted from the answer body;
- SSE event streams (many relays and local bridges answer this way) are folded
  back into the standard chat shape;
- `<think>` scratchpads emitted before the answer are stripped, so reasoning
  never reaches the artifact, the verdict or the ledger.

Environment variables work too, with no config file edit:

```bash
export MAS_PROVIDER=openai_compat MAS_MODEL=your-model MAS_BASE_URL=https://... MAS_API_KEY=...
```

---

## Plug into MCP (Codex / Claude Code / any MCP client)

The installer writes this into `~/.codex/config.toml`:

```toml
[mcp_servers.mas_loop]
command = "python"
args = ["-m", "mas", "serve"]
startup_timeout_sec = 30.0
tool_timeout_sec = 300.0
```

Tools exposed:

| tool | purpose |
| --- | --- |
| `mas_run` | run a closure task; returns status and per-criterion verdicts |
| `mas_status` | config, ledger size, open tickets |
| `mas_ledger` | read the closure ledger (filterable by `run_id`) |
| `mas_tickets` / `mas_ticket_resolve` / `mas_resume` | human intervention |
| `mas_report` / `mas_replay` / `mas_export` | reporting and replay |
| `mas_teams` | list team templates |
| `mas_doctor` | installation self-check |
| `mas_config` | read / update configuration |

The protocol is a pure-standard-library JSON-RPC 2.0 over stdio -
**no `pip install mcp` required**.

---

## Bundled Agent Skills

| Skill | purpose |
| --- | --- |
| `mas-loop` | run closure tasks, write acceptance criteria, read ledger and tickets |
| `mas-review` | multi-perspective adversarial review (worker / verifier / reflector separation) |
| `mas-scaffold` | set up team templates and CI gates for a new project |

The installer places them in every skill directory it finds (`~/.codex/skills`,
`~/.claude/skills`, `~/.agents/skills`, `~/.gemini/skills`).

---

## Command cheat sheet

```bash
mas init          # write/update config
mas init --wizard # interactive setup
mas run           # run a closure task
mas demo          # three offline scenarios
mas verify        # real closure self-check
mas bench         # offline benchmark
mas status        # local state
mas ledger        # closure ledger
mas report        # Markdown closure report
mas replay        # replay a run trace
mas export        # export the ledger as Markdown
mas tickets       # human tickets
mas resume ID     # continue with a human note
mas teams         # team templates
mas doctor        # installation self-check
mas serve         # run as an MCP stdio server
mas http          # run as an HTTP + SSE server
```

`mas run` exit codes: `0` = closed, `2` = not closed. Use it directly as a CI
gate.

---

## Development and tests

```bash
python -m unittest discover -s tests -v   # 76 offline tests, no network needed
python -m mas demo                        # three closure scenarios
python -m mas bench                       # offline benchmark
python -m mas verify                      # real closure self-check
```

Covered: machine checks, the three termination paths (closure / stagnation /
budget), rules overriding model verdicts, unjudged criteria never passing, MCP
handshake and tool calls, config merging and env overrides, SSE folding,
scratchpad stripping, UTF-8 safety on legacy code pages, parallel candidate
selection, adversarial review aggregation (all/majority/any), backend failover,
real HTTP/SSE requests, reports and replay, wizard and self-check.

---

## Docs

| doc | contents |
| --- | --- |
| [docs/quickstart.md](docs/quickstart.md) | 5-minute quickstart: install → offline → real model → human in the loop |
| [docs/architecture.md](docs/architecture.md) | runtime architecture and data flow |
| [docs/loop-engineering.md](docs/loop-engineering.md) | methodology: why the loop is designed this way |
| [docs/mcp.md](docs/mcp.md) | MCP integration, handshake, tools, protocol details, troubleshooting |
| [docs/acceptance-report.md](docs/acceptance-report.md) | installation and acceptance report: measured evidence, CI results, known limits |

---

## Positioning

OpenLoop MAS is **not** a clone of "private AI agent team" products. It
supplies a different layer:

- those products connect Claude / GPT / Kimi into a chatty agent team that runs
  locally or in the cloud, with MCP and lots of app integrations;
- OpenLoop MAS answers "**after the team finishes, how do I know it actually
  finished, and can I replay, stop and hand over the process?**"

They stack: the former is the execution layer, the latter is the closure layer.

---

## License

MIT © OpenLoop MAS contributors
