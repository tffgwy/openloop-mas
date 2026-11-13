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

**What v0.7.1 fixes** (an upgrade no longer silently leaves old copies behind):

- **The skills drifted after an upgrade and nobody noticed** - `mas install`
  overwrote the files without ever comparing them. A 0.6.0-era copy could
  survive all the way to 0.7.0, and `mas doctor` only checked *presence*, not
  *version*;
- The installer now compares **content fingerprints**: it backs the old copy
  up first, installs the packaged version, and removes files the new version
  no longer has;
- `mas doctor` gained a **`skills_sync`** check that names the host and the
  skill whose content no longer matches the packaged copy;
- **New `mas sync`** repairs the drift with one command (`--dry-run` reports
  without writing anything);
- **A dead MCP registration is no longer reported as "registered"** - the key
  merely having to exist used to be enough, but an entry pointing at a deleted
  Python or an uninstalled package cannot start, while every self-check stayed
  green. Such entries are now detected and refreshed;
- **Directory backups actually work now** - `_backup()` only handled files, so
  backing up a skill folder silently failed and there was no backup at all;
- **Publishing to PyPI no longer needs a long-lived token** - the release
  workflow gained a **Trusted Publishing (OIDC)** path, so the repository
  stores no credential. The first publish needs a one-time registration on
  pypi.org, see [docs/publishing-to-pypi.md](docs/publishing-to-pypi.md).

**What v0.7.3 fixes** (the `section` machine check used to pass falsely):

- `section` used to be a plain substring test (`"## conclusion" in artifact`), so three
  different things were wrongly accepted as a section: a body sentence that merely
  mentions `## conclusion`, a `## conclusion analysis` heading satisfying a check for
  `## conclusion`, and a third-level heading `### conclusion`;
- the check now requires a **line that is exactly `## <heading>`** (trailing whitespace
  allowed), so `#` / `###` / `####` no longer count, a mention in prose no longer counts,
  and a longer heading no longer satisfies a shorter one;
- this is a **behaviour change**: if you relied on `section` matching a heading *prefix*,
  write the full heading now. A stricter verdict means fewer false positives, at the cost
  of criteria that previously passed by accident now failing - which is the direction a
  closed loop should move in.

**What v0.7.4 fixes** (a criterion with a missing argument used to look like a pass):

- A machine check **missing its argument** used to degrade into a constant true verdict -
  an empty regex matched everything, an empty `contains` list required nothing, a missing
  `min_chars` became "at least 0 characters". The result: **the criterion was never
  written, yet the loop reported a green light**;
- such a check is now judged **unjudged** (never passed), and the engine refuses to start
  at all (exit code 1, **zero tokens burned**) - a typo no longer drains the whole budget;
- this is a **behaviour change**: if you relied on an empty argument to pass for free,
  the run is now stopped up front and told exactly what is missing.

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

### Option 1: one pip command (recommended, cross-platform)

```bash
pip install openloop-mas
mas install        # install skills + register the MCP server
mas doctor         # self-check (including skill version drift)
mas sync           # bring the installed skills back to the packaged version
mas demo           # no API key needed - watch it close a loop on its own
```

`mas install` copies the **agent skills into the Codex / Claude Code skill
directories** and **registers the MCP server** in `~/.codex/config.toml`
and `~/.claude.json`. Open a new terminal afterwards and both hosts know
the workflow.

- Idempotent: running it twice never registers twice and never clobbers
  your existing configuration.
- Safe: every configuration file it modifies is backed up as
  `*.bak-<timestamp>` first.
- No secrets: the install never writes an API key.

> Add `--json` to see exactly what it did.

### Option 2: install from source

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

Exact semantics (verified against the implementation - write criteria against this):

| type | exact verdict | common trap |
| --- | --- | --- |
| `contains` | artifact contains the string(s); `arg` may be an array and **every** item must be present | one missing item fails the whole criterion |
| `not_contains` | artifact contains **none** of the given strings | an array means "not one of them may appear" |
| `min_chars` / `max_chars` | **character** count of the artifact, whitespace and newlines included | not a word count, not a byte count |
| `regex` | `re.search`, **DOTALL is always on** (`.` spans lines) | no `(?s)` needed; it matches a substring, not the whole artifact |
| `lines_min` | count of **non-empty** lines (blank lines do not count) | blank lines between paragraphs do not help |
| `section` | the artifact contains a line that is exactly `## <arg>` (trailing whitespace allowed) | it must be a **second-level** heading - `#` and `###` do not count; merely mentioning `## conclusion` in a sentence does **not** count, and `## conclusion analysis` does **not** satisfy `## conclusion` |
| `json_keys` | the artifact must be a JSON **object** holding every given key | invalid JSON fails immediately |

An unknown type is judged `unjudged` (i.e. **not passed**) - it is never silently let through.

**A missing argument invalidates the criterion - it does not pass it.** A check with no
usable argument cannot decide anything, so the engine refuses to start (exit code 1,
zero tokens burned) instead of letting it pass by default:

| type | required argument | rejected example |
| --- | --- | --- |
| `contains` / `not_contains` | a non-empty string, or a non-empty list of strings | `""`, `[]` |
| `min_chars` / `max_chars` / `lines_min` | a non-negative integer | missing, `-1`, `"many"` |
| `regex` | a compilable pattern | `""`, `"([unclosed"` |
| `section` | the heading text | `""` |
| `json_keys` | a key name, or a non-empty list of key names | `[]`, missing |

A broken argument in the **shorthand** form fails just as fast. `--criteria 'regex:'`
and `--criteria 'min_chars:many'` keep their check type and are caught by the table
above - exit code 1, zero tokens. The same standard written as JSON or as a shorthand
now behaves identically: a missing argument is a missing argument, never a quiet
fallback to "the model decides".

**A model's verdict is read, not coerced with ``bool()``.** If the reviewer answers
`"pass": "false"`, `bool("false")` is `True` - a criterion the reviewer just failed would
close the loop. The engine reads what the value *means*: `"false"` / `"0"` / `"no"` fail,
`"true"` / `"1"` / `"yes"` pass, and anything unreadable (`"maybe"`, empty, missing) counts
as **unjudged** rather than a guess.

A **misspelled type name** is refused too. In the JSON form a type that does
not exist (`{"check":{"type":"mystery"}}`) stops the run up front and lists the
known types - a typo that slipped through would degrade into "the model decides",
and a model that says "looks fine" would close the criterion.

Only in the **shorthand** form does an unknown type name fall back to a plain
description (`--criteria 'unknown_type:abc'`) - there the intention was never to
write a machine check, which is different from writing one and getting it wrong.

### Shorthand notation (since v0.4.2)

Machine checks do not have to be written as JSON. The CLI accepts `<type>:<arg>` directly:

```bash
mas run --goal "write a dedupe function" \
  --criteria 'regex:def dedupe.*set' \
  --criteria 'min_chars:200' \
  --criteria 'contains:key conclusion'
```

Shorthand and JSON are fully equivalent - the verdict still comes from the engine's
deterministic checker (recorded as `source: rule` in the ledger). Shorthand is
resolved in the engine layer, so criteria replayed from MCP, HTTP or the ledger
are handled identically.

**Several criteria at once** are given as a JSON array - every entry in the array
keeps its own machine check and is actually verified. The array is never taken
as one plain description (that would mean no machine check ran at all and the
closure actually came from a model saying "looks fine"):

```bash
mas run --goal "write a release note for v0.1.0" \
  --criteria '[{"id":"c1","desc":"contains the version","check":{"type":"contains","arg":"v0.1.0"}},
               {"id":"c2","desc":"at least 200 chars","check":{"type":"min_chars","arg":200}}]'
```
 An unknown type prefix (e.g. `just write it well`) stays
a plain description and is never mistaken for a machine check, and a numeric type
with a non-numeric argument (e.g. `min_chars:many`) falls back to a plain
description too - a criterion may never claim a check it cannot perform.

Criteria without a machine check fall back to the model verifier - but **if the
model gives no verdict, the default is fail** (`unjudged`). A criterion marked
`"required": false` is **recorded but never blocks closure**: if it carries a
machine check the rule still runs and its real result lands in the report
(`PASS` or `FAIL`), it just never keeps the loop open; without a machine
check the reviewer's verdict is recorded the same way.

**The `required` flag is read by meaning, not by truthiness.** Writing
`"required": "false"` as a *string* really does mean optional - `bool("false")`
is `True`, and a criterion that travels through JSON, MCP or a shell script
easily picks up those quotes. The engine reads the meaning of the value:
`"false"` / `"0"` / `"no"` are optional, `"true"` / `"1"` / `"yes"` are
required, and anything unreadable (`"maybe"`, missing, `null`) stays
**required** - an unreadable flag must never silently relax a standard.

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


### Keeping it running (service + TLS reverse proxy)

For long-term access from a phone or another device, run the service bound to loopback and expose only a TLS entry point:

```bash
# 1. keep the token in a file, not in version control or the start script
python -c "import secrets,pathlib;pathlib.Path.home().joinpath('.mas/http_token.txt').write_text(secrets.token_urlsafe(32))"

# 2. run the service persistently (systemd / Windows Startup folder / launchd)
export MAS_HTTP_TOKEN=$(cat ~/.mas/http_token.txt)
mas http --host 127.0.0.1 --port 8768

# 3. publish only the TLS entry point (tailnet-only by default)
tailscale serve --bg --https=8768 http://127.0.0.1:8768
```

Then hit `https://<your-host>:<port>/tools` with an `Authorization: Bearer <token>` header.

Why this shape:

- Binding `127.0.0.1` and terminating TLS at the proxy keeps the service off the LAN, unlike `--host 0.0.0.0`.
- The token lives **outside** the start script; if the token file is missing the script exits instead of degrading to unauthenticated.
- `tailscale serve` is **tailnet-only** by default; `funnel` publishes to the public internet, which widens exposure and deserves a separate review.
- To stop: `tailscale serve --https=8768 off`.


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

> The **single source of truth is `mas/skills/`**. It ships inside the wheel,
> so the skill a `pip install` gives you is byte-identical to the one a source
> checkout gives you - there are no two copies that can drift apart.

To remove it again:

```bash
mas uninstall       # remove the skills and the MCP registration (keeps ~/.mas)
pip uninstall openloop-mas
```

---

## Command cheat sheet

```bash
mas install       # install skills + register the MCP server
mas sync          # bring the installed skills back to the packaged version
mas uninstall     # remove the skills + MCP registration (keeps the ledger)
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
python -m unittest discover -s tests -v   # 213 offline tests, no network needed
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
