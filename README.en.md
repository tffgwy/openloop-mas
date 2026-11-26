# OpenLoop MAS · A Multi-Agent System That Actually Closes Its Loops

> A **zero-dependency, standard-library-only** multi-agent loop runtime. It turns
> "plan → work → verify → reflect" into an **engineering loop with acceptance
> criteria, exit conditions and an auditable ledger**. One command to install;
> beginners can run it too.

[English](README.en.md) | [中文](README.md)


[![ci](https://github.com/tffgwy/openloop-mas/actions/workflows/ci.yml/badge.svg)](https://github.com/tffgwy/openloop-mas/actions/workflows/ci.yml)
[![license](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![deps](https://img.shields.io/badge/dependencies-0-orange.svg)]()

**First time here?** Jump to the [5-minute quickstart](#5-minute-quickstart) -
one script installs it, and you can close a real loop without any API key.

**Why does it claim to close?** See [Why](#why).

**Want the scars?** The last section, [Gate archaeology](#gate-archaeology),
records where each of the gates came from.

---

## 5-minute quickstart

> The configuration always lives in `~/.mas/config.json` (Windows:
> `%USERPROFILE%\.mas\config.json`). The run ledger lives under `state_dir`
> instead - the two are separate, so moving the state directory never loses
> your setup. In CI, use `MAS_CONFIG_PATH` for the config file and
> `MAS_STATE_DIR` for the state directory.

### Option 1: one script (cross-platform, no Python setup needed)

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/tffgwy/openloop-mas/main/install.sh | bash

# Windows (PowerShell)
irm https://raw.githubusercontent.com/tffgwy/openloop-mas/main/install.ps1 | iex
```

The script creates the virtual environment, installs the package,
registers the MCP server and runs a self-check.

### Option 2: pip install (needs Python 3.10+)

```bash
pip install openloop-mas     # see the note below: this name is not on PyPI yet
mas install        # install skills + register the MCP server
mas doctor         # self-check (skill drift + runtime version gap)
mas sync           # bring the installed skills back to the packaged version
mas runtime        # show which runtime build is installed on disk
mas upgrade        # replace an outdated installed runtime with this one
mas demo           # no API key needed - watch it close a loop on its own
```

> **Upgrading?** Run `mas upgrade` first (it replaces the runtime that is
> actually serving), then `mas install` (so the MCP registration points at
> the new build). Doing only the latter refreshes the skills and the
> registration while **the old code keeps running** - which is exactly
> what the new `runtime` check in `mas doctor` now points out.

> **About `pip install openloop-mas`**: the name has **not been created on
> PyPI yet**, so pip answers `No matching distribution found`. Use the
> install script above for now, or take a wheel from
> [Releases](https://github.com/tffgwy/openloop-mas/releases/latest).
> `mas doctor` **looks the name up** on PyPI and tells you the truth -
> once the name is published, both the note and the check line up.

`mas doctor` talks to a person: every check explains what it *is*, is
tagged **required / recommended / optional**, and the report ends with
one conclusion and one next command.

```
[OK  ] python（必需）
       3.14.10
       是什么：Python 版本够不够新（3.10 以上才能跑）
[注意] skill_codex（可选）
       未安装: ...\.codex\skills\mas-loop\SKILL.md（运行 mas install）
       是什么：给 Codex 装的 skill：让 Codex 会用这个闭环工具
...
小结：8 项正常，6 项注意，0 项坏了
结论：闭环能跑，但智能体接不上：skill_codex、skill_claude、skills_sync、mcp_codex、mcp_claude 还没装。
下一步：运行 mas install，一条命令装上 skill 并注册 MCP 服务。
```

The three verdicts mean different things: **the loop cannot run** (a
required part is broken), **the loop runs but no agent can reach it**
(skills / MCP not installed), or **everything is ready**. The exit code
is non-zero only in the first case.

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

### Option 3: download one script (no dependencies - best for beginners)

No clone, no pip, nothing to install yourself - **download one script file**
and run it:

```powershell
# Windows: download install.ps1 anywhere, then run it
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

```bash
# macOS / Linux: download install.sh anywhere, then run it
bash install.sh
```

The script fetches the published wheel from the GitHub release, unpacks it
with the standard library, installs it into `~/.mas/`, registers the MCP
server and the skills, and finishes with an offline self-check.
**No git, no pip, no compiler.**

**No Python on the machine yet? The script installs one.**

- Windows: it downloads the official Python installer and runs it
  **silently for the current user** (no administrator rights, no `PATH`
  change, no py launcher), then finds the interpreter where it just put
  it and carries on.
- macOS / Linux: it uses the system package manager (Homebrew / apt / dnf /
  yum) to install Python 3.12. If that fails - no `sudo`, say - it says so
  plainly and gives you the exact command to copy.

> Prefer it not to touch your machine? Pass `-NoBootstrap` (Windows) or
> `MAS_NO_BOOTSTRAP=1` (shell) and it will only check.

Pin a specific release instead of "the latest":

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Version 0.11.4
```

```bash
MAS_VERSION=0.11.4 bash install.sh
```

> The script adds `~/.mas/bin` to `PATH` (on Windows it writes the current
> user's registry key). Pass `-SkipPath` (Windows) or `MAS_SKIP_PATH=1`
> (shell) to leave `PATH` alone.

### Option 4: install from source (for development)

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
| Close | `lead` | **judges only what nobody judged**: `close` / `revise` / `escalate` | engine (**final veto**) |

The `lead` is an *advisor, not a judge of facts*: a failed machine check
is a fact, so the lead is not consulted at all in that case, and when it
is consulted it may only fill a gap. The engine keeps the final word
over stagnation, budget and deadline, and a lead that cannot decide
leaves the criterion unjudged and the loop asking.

A loop is closed only when **artifact + per-criterion verdicts + decision +
ledger entry** all exist. Otherwise the status is one of exactly three:
`escalated` (handed to a human), `budget_exhausted` (stopped by budget) or
`error`. **There is no "almost done" state.**


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

### The unified resource index and multi-format preview (v0.7.71): one folder is one index

```bash
mas files                  # index every workspace on this machine
mas files ./out            # index one folder
mas preview ./out/report.pdf   # preview one file
```

```bash
$ mas files ./out

[./out]  5 files  263.7 KB
    pdf        1
    image      2
    model      1
      836.0 B  pdf        report.pdf  pages 2, text chars 57
      327.0 B  image      shot.png  width 12, height 7
      117.0 B  image      liar.txt  width 4, height 4
      213.0 B  model      ascii.stl  triangles 2
      251.0 B  zip        bundle.zip  members 2
```

**The format comes from the file's own header, not from its name** - the
`liar.txt` above is really a PNG, so it is reported as `image`. **A file that
cannot be read is listed with the reason** - that warning line is information,
not noise: an unreadable file in the middle of a deliverable is exactly what a
reader needs to see.

`mas preview` pulls a PDF's words out of its *compressed stream* and a Word
document's words out of its *archive member*; an image with no body says so
plainly instead of showing a blank screen. A missing file or a folder gives
**exit code 2**.

See **[docs/resources.md](docs/resources.md)**.

### The draft layer (v0.7.72): the change goes into a draft, a human writes it back

```bash
mas drafts                              # what is waiting for a decision
mas drafts show D-1a2b3c4d              # the diff of one draft
mas drafts approve D-1a2b3c4d           # approve: write it back
mas drafts reject  D-1a2b3c4d           # reject: nothing changes
mas drafts token                        # the human token (an agent cannot approve its own draft)
mas drafts ticket D-1a2b3c4d --token <token>      # sign a ticket: one draft, one use, 15 minutes
mas drafts conflict                                # which drafts had their file moved on?
mas drafts approve D-1a2b3c4d --force                # overwrite anyway: you know what you are doing
```

When a run wants to change a file that **already exists**, it no longer
overwrites it. The change goes into a draft, **the original keeps every byte**,
the draft carries a **readable diff**, and only your word - "approve" - writes
it back. "Reject" changes nothing at all.

```bash
$ mas drafts

workspace ~/mas/artifacts: 1 draft
  D-1a2b3c4d  pending  notes.txt  changed
        reason: the loop proposed a change to an existing file
```

**A new file is still written right away** - there is nothing there to lose.
Set `"edit_policy": "overwrite"` in the configuration to get the old behaviour
back (the default is `"draft"`).

**The machine checks read the drafted bytes**, not the old file on the disk -
otherwise the loop would be stuck forever: propose a change, read the old
file, fail, propose again, read the old file. The draft lives in the workspace
next to the file it changes (`.mas-drafts/`), so a copied folder carries its
drafts along - a lost draft is a change that can never be reviewed.

See **[docs/drafts.md](docs/drafts.md)**.

### An agent stages a draft over MCP (v0.7.73): proposing and approving are two actions

`mas drafts` is the **human** half. The MCP server carries the **other half,
for the agent** - and the two halves are **not the same thing**:

```bash
# the human picks up the token on their own machine (once)
mas drafts token
```

| MCP tool | What it does | Can it change a file? |
|---|---|---|
| `mas_draft_stage` | **stage** a change (with a diff and a reason) | **No** - the original keeps every byte |
| `mas_drafts` | list the drafts, read one diff | No |
| `mas_undos` | list the **approvals that were taken back**; filter by run | read only |
| `mas_draft_resolve` | approve or reject | **Only with the human token** |
| `mas_files` | index the workspace | read only |
| `mas_preview` | preview one file | read only |

**An agent cannot approve its own proposal.** A wrong token, an empty token
and a missing token are all refused, and the original file does not move.
The token is not about secrecy, it is about **separation of duties** - the
proposer does not confirm what it proposed.

```bash
# the agent stages a change (the file does not move)
mas_draft_stage {path: "notes.txt", content: "the new text", reason: "adding a conclusion"}

# the agent tries to approve it itself - refused
mas_draft_resolve {action: "approve", draft_id: "D-1a2b3c4d"}
-> {error: "human_token_required"}

# the human approves - now it is written back
mas_draft_resolve {action: "approve", draft_id: "D-1a2b3c4d", token: "<the human token>"}
```

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

**A failover's spend is booked to the backend that paid for it**: the tokens
the primary burnt before it died are charged to the *dead* primary, not to
the backup that took over. The run's total does not change - the failed
call really was paid for - but the attribution becomes true:

```
- 本次运行花费 24 token：`dead-primary` 15、`backup-kimi` 9
```

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

## DeepSeek Harness integration (optional)

The repo ships a **DSH plugin** (`integrations/dsh-openloop-mas`). It does not
re-implement the loop: it talks to the local runtime over **MCP**, so the
verdicts and the ledger in a DSH conversation are the *same facts* a Codex or
Claude Code client sees.

```powershell
# optional switch, off by default - the main line stays dependency free
# (pure Python, no Node / DSH / npm needed)
powershell -ExecutionPolicy Bypass -File .\install.ps1 -InstallDshPlugin
```

That adds 6 tools to DSH: `mas_run`, `mas_status`, `mas_ledger`, `mas_tickets`,
`mas_report`, `mas_doctor`.

Details, configuration, measured evidence and the boundaries are in
**[docs/dsh-integration.md](docs/dsh-integration.md)**.

### Making DSH actually talk: `mas relay`

Installing the plugin is not the same as the model being able to use it. DSH's
`dsh-llm-pi-ai` adapter **insists on an API key**, while a private endpoint often
**wants no key at all** (and may even reject a placeholder with `401`). That is a
protocol-contract vs endpoint-contract collision, not a misconfiguration.
`mas relay` sits in between: it drops the client's `Authorization` and optionally
substitutes the upstream's real key.

```powershell
# generate a working DSH profile (writes config + installs the plugin + prints commands)
python tools/setup_dsh_bridge.py --profile masbridge --relay-port 8766

# terminal 1
mas relay --upstream http://127.0.0.1:16024 --port 8766

# terminal 2: pi-ai insists on a key - a placeholder is fine, the relay drops it
$env:MAS_BRIDGE_API_KEY = "placeholder"
dsh --profile masbridge headless "run a closure with mas_run: write a release note, acceptance criterion file_exists on out/report.md"
```

> **`headless` is a profile name, not a subcommand.** The correct shape is
> `dsh --profile <name> headless "<task>"`.

Two authentication modes, the security boundary and the verified/unverified list
are in **[docs/relay.md](docs/relay.md)**.

### A sibling plugin in the same host: the MOKI library

[MOKI](https://github.com/JKZeng-cell/moki-library-for-dsh) (`dsh-moki-library`) is
a **different project** - do not confuse it with OpenMausBot. It adds a **local
resource manager** to DeepSeek Harness: unified organisation of files and web
links, multi-format preview, `Alt+D` to summon an AI chat over a selection, and
**edits land in a draft first** - only written back after the user confirms.

The split is clean: **MOKI organises the material, MAS decides whether the
deliverable actually passes.**

```bash
dsh plugin --profile web add "dsh-moki-library@file:<abs>/dsh-moki-library-0.1.73.tgz" --allow-build=sharp
dsh plugin --profile web add "dsh-openloop-mas@file:<abs>/dsh-openloop-mas-0.7.16.tgz"
```

> MOKI needs **DSH >= 0.2.2-rc.1**, **Node >= 22.19**, and it is a **web-platform
> plugin** - in a CLI / headless profile it stays **pending** (wrong host shape,
> not a broken install).

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
mas files         # index the workspace: every file, its format, its facts
mas preview FILE  # preview one file (PDF / Word / text...)
mas drafts        # drafts waiting for a human: list / show / approve / reject / undo / token / ticket / conflict
mas drafts token  # the human token: an agent cannot approve its own draft
mas drafts ticket D-ID --token <token>  # a ticket: one draft, one use, 15 minutes
mas status        # local state
mas ledger        # closure ledger
mas ledger --undone   # only the approvals that were taken back
mas undos        # which approvals were taken back: the pairs, and how many lines
mas report        # Markdown closure report
mas replay        # replay a run trace
mas export        # export the ledger as Markdown
mas tickets       # human tickets
mas resume ID     # continue with a human note
mas teams         # team templates
mas doctor        # installation self-check
mas selftest      # the flaky-bug gate: hammer the paths that used to fail at random (offline)
mas clean --selftest-ledger   # remove the rows the old self-test left in the ledger
mas serve         # run as an MCP stdio server
mas http          # run as an HTTP + SSE server
mas relay         # OpenAI-compatible relay for clients that insist on an API key (127.0.0.1:8766)
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
python -m mas selftest                     # the flaky-bug gate (offline)
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

---

## Gate archaeology

This project now holds **53 gates**, and every one of them grew out of a
**real incident**. They are listed below newest first - if you just want to
use the tool, skip this section.

- **The 53rd gate `a_run_knows_its_ceiling`** - no verdict means no conclusion,
  and no conclusion cannot drift. The run also has to name its own ceiling.

  v0.7.88 gave every closure a **seal**: the criteria, the deliverable's hash,
  the hashes of the files the run wrote. `mas seal` re-derives the verdict from
  that evidence and says whether it still holds.

  But the seal was written **unconditionally** - every run, including the ones
  that never judged anything. A run that died on the budget ceiling carried a
  seal with an **empty score list** and a hard-coded `"fail"`.

  The report read that seal as a **conclusion**. A conclusion that cannot be
  recomputed came back as `unrecomputable`, and the report counted it as
  **drifted**:

      Seal recheck: 1 sealed, 1 conclusion drifted

  That accuses a run of lying about something it never said. It never judged
  anything, so it never concluded anything, so nothing it concluded can have
  drifted.

  **A false alarm about drift is the worst kind** - a reader who learns to
  ignore it will miss the real one.

  Now **no verdict means no seal**: the seal is written only when the run
  actually decided, and that guard lives in its own method so it can be broken
  on purpose. **An old ledger's verdict-less seal is not drift either** - it
  is read as `no-verdict`, so upgrading does not make your old ledger start
  accusing itself.

  The gate watches six things: a run that never judged carries no seal and is
  not counted as drifted; an old verdict-less seal reads as `no-verdict`; the
  headline does not accuse the machine of a false drift; **a closure that did
  decide and was edited afterwards is still reported as drifted** - the fix
  does not weaken into "never report anything"; and the run says which ceiling
  stopped it.

  It **proves it can fail**: break the guard, and the gate has to notice that
  the machine started accusing itself.

  ```bash
  mas selftest --only a_run_knows_its_ceiling
  ```

- **The 52nd gate `a_run_knows_who_paid`** - the run knows which backend
  really spent the money.

  The failover chain folded the dead backend's spend into the survivor's
  usage: the backup spent 3 tokens, the dead primary spent 5, and the ledger
  said "the backup spent 8".

  **The dead money was booked on the living backend.** The endpoint that was
  burning the budget was exactly the one that had just died - and a reader
  asking "which backend is eating my budget?" got the healthy one's bill.

  The spend is now booked **per backend**, and the run detail says so:
  "24 tokens spent on this run: dead-primary 15, backup-kimi 9". **The total
  did not change** - the run really did pay for the failed call; only the
  attribution did.

- **The 51st gate `a_run_knows_its_backend`** - the run knows which backend
  really delivered it.

- **The 50th gate `a_run_knows_its_approval`** - an approval was taken back,
  and the run has to know it by itself.

  The 49th gate let the **ledger** answer "which of my runs once had a
  wrong approval?". The **run** could not answer it.

  `mas/report.py:group_runs()` deliberately keeps the decision rows out
  of the run rows - a human decision about a draft is not a round of the
  loop, and folding it in would invent a run with no goal and no
  closure. That reasoning is right about the **grouping** and left a
  hole in the **reading**: the run summary counted five entries, said
  "closed", and had no word for the fact that one of its own approvals
  had been taken back.

  So the reader who opened the one run that had been wrong was shown a
  clean, successful run.

  The decisions are not folded in. They are filed **beside** the run
  that proposed them, and read as that run's own facts: the run table
  gains a "taken back approvals" column, the run's own detail says it
  in words, and the command line and the MCP surface say the same
  thing.

  A decision with no thread belongs to no run - it is never spread over
  every run.

- **The 49th gate `a_decision_hangs_on_its_run`** - the answer to a
  proposal has to hang on the run that proposed it.

  A draft is a **proposal**; a human decision about it is the
  **answer**. The answer was filed without the question:
  `mas/drafts.py:ledger_row_for()` hard-coded `"run_id": ""` and
  `"task_id": ""` on every decision row.

  So the question everybody actually asks could not be asked at all:

  > which of my runs once had a wrong approval?

  The draft now remembers the run that proposed it, the decision row
  copies that thread out, and the report, the draft list, the command
  line and the MCP surface can all be asked about one run.

- **The 48th gate `an_undone_draft_is_still_waiting_everywhere`** - an
  approval that was taken back leaves the change waiting for a human
  again, and every surface has to say so.

  An undone draft is a **live** change that is waiting for a person.
  Three places read only `pending`, so the machine check judged the
  **old** bytes while the loop proposed the new ones, the queue said
  "nothing is waiting", and the closure page said "closed".

- **The 47th gate `a_taken_back_approval_is_visible_everywhere`** - an
  approval that was taken back has to be visible everywhere.

  The report said which approvals were wrong, but the draft queue, the
  status page and the MCP surface did not - so a reader who asked the
  one surface that showed it was the only one who knew.

- **The 32nd gate `selftest_isolation`** - the self-test is a program too,
  and it **approves drafts**, **undoes** them, **writes files**. Approving is
  a **human decision**, and a human decision has to be written into the main
  ledger.

  So every run of `mas selftest` left rows in the user's
  `~/.mas/ledgers/runs.jsonl` pointing at a **temporary directory that was
  deleted a moment later**. The rows stayed. The chains they pointed at did
  not.

  The consequence was not "a few rows of noise". It was **the report began
  to lie**: `mas report` reported **hundreds of audit problems**, because the
  ledger said "the human approved something" and the chain said "there is no
  such chain" - and those hundreds of approvals were **the test's own**.
  A user who ran the self-test once was left with a machine that reported
  hundreds of problems for ever, and **no way to tell** a real problem from
  a leftover.

  A self-test that changes the state of the machine it runs on is not a
  self-test. **It is a side effect with an exit code.**

  This gate watches three things:

  - after the self-test, the user's ledger is **exactly as it was found** -
    not "roughly the same", but the same number of rows and the same bytes;
  - the **environment is put back** - one gate re-pointed `MAS_STATE_DIR`
    and never put it back, which silently **re-pointed every gate that ran
    after it**, hiding the next leak;
  - the decisions the self-test makes are **still recorded** - in a state
    directory of the test's own. Isolation that threw the records away would
    be isolation by amnesia, and the gates that prove the audit trail works
    would be proving nothing.

  The suite also carries a **safety net**: the whole run compares the
  machine's state directory before and after, so anything that changed
  outside the temporary folders is named and **fails the whole suite**.

  The gate **proves it can fail**: the isolation is broken on purpose - it
  isolates nothing at all - and the gate has to notice that it can no longer
  tell a clean run from a dirty one.

  ```bash
  mas selftest --only selftest_isolation
  ```

- **The 33rd gate `report_tickets`** - the engine opens a **human ticket**
  whenever a loop cannot close itself; `mas tickets` shows the queue and
  `mas status` counts it. But **`mas report` never looked at it** -
  `mas/report.py` reads the ledger and nothing else, and the ledger only
  remembers *that* a run escalated, not **whether the problem is still
  waiting for a person**.

  The consequence is concrete: three tickets sit in the human queue, and
  the report reads like a machine that **already finished its work** - three
  runs, one closure, a 33% closure rate, every number green, and not one
  word about the three things a person still has to do. A capability that
  lets a human see *which work is waiting*, but is not consumed by the
  main view, is a capability nobody has. This is the **fourth hole of the
  same family**: v0.7.89 was the report not looking at the seal, v0.7.90
  the report not looking at the undo, v0.7.91 the report not looking at the
  audit chain, v0.7.93 the report not looking at the ticket queue.

  The report now says it: a headline line reads "N tickets waiting for a
  person (M in total)"; a section of its own - "human tickets" - names
  **the ticket id**, **the state**, **what the problem is**, **why it
  stopped** (read as a sentence, not the engine's
  `budget: reached max_iterations=3`), **how many attempts** it took,
  **which runs** belong to it, and **the continuation command you can
  paste**; and the JSON payload carries the same numbers, so CI and
  scripts never have to parse Markdown.

  More important, the **queue and the ledger check each other**, and all
  three kinds of gap are named: a ticket the ledger points at that is **not
  in the queue** (the escalation record is gone), a ticket in the queue the
  **ledger never mentions** (a human is being asked about a problem the
  ledger does not hold), and a ticket that **is still waiting** although
  **every run it covers closed** (the queue is asking for work that is
  already done).

  The gate proves it can fail: the report's ticket reading is broken on
  purpose - the queue is reported as empty - and the gate has to notice
  that it can no longer tell a waiting ticket from no ticket at all.

  ```bash
  mas selftest --only report_tickets
  ```

- **The 34th gate `report_drafts`** - when the engine wants to change a
  file that **already exists**, it does not change it: it stages the
  change as a **draft** and leaves the file alone until a human
  approves it - that is the `edit_policy` rule of v0.7.72, and the
  reason the tool does not destroy work. `mas drafts` shows the queue
  and `mas status` counts it. But **`mas report` never looked at it** -
  `run_summary()` in `mas/report.py` reads the `closure`, `planner`,
  `candidates`, `review`, `reflector` and `handback` rows, and there is
  **no `draft` branch in that dispatch at all**.

  The consequence is concrete: a draft waiting for a human was
  **invisible** in `mas status` and in `mas report` - the two views a
  beginner actually reads. Worse, a run that closed *because its own
  proposed change* was judged by the machine checks (the checks read
  the draft as an overlay, so the loop can close on its own proposal)
  reported `closed` with a deliverable that passed, while the file on
  the disk was **byte for byte the old one**. The report said "closed",
  the disk said "nothing happened", and nothing in between said a word.
  A capability that lets a change wait for a human, but is not
  consumed by the main view, is a capability nobody has. This is the
  **fifth hole of the same family**: v0.7.89 was the report not looking
  at the seal, v0.7.90 the report not looking at the undo, v0.7.91 the
  report not looking at the audit chain, v0.7.93 the report not
  looking at the ticket queue, v0.7.94 the report not looking at the
  draft queue.

  The report now says it: a headline line reads "N drafts waiting for
  a person (the change is not on the disk yet)"; a section of its own -
  "drafts waiting for approval" - names **the draft id**, **its
  state**, **which file**, **which run proposed it**, and **the
  approval command you can paste**; and the JSON payload carries the
  same numbers, so CI and scripts never have to parse Markdown.

  More important, the **queue and the ledger check each other**: a
  draft the ledger staged but the **queue does not hold** (the record
  of the proposal is gone, so nobody can approve it), and a draft the
  queue holds that the **ledger never mentions** (a human is being
  asked to approve a change the ledger cannot account for). And the
  sharp one is named on its own: **a run that reported closure while
  its change is still waiting in front of a human** - because that is
  the one fact a person most needs to know.

  The gate proves it can fail: the report's draft reading is broken on
  purpose - the queue is reported as empty - and the gate has to notice
  that the report can no longer tell a waiting change from no change
  at all.

  ```bash
  mas selftest --only report_drafts
  ```

- **The 35th gate `doc_text_integrity`** - while writing the v0.7.93
  release notes, the text was produced through a shell that treats a
  backquote as an **escape character**. So `budget` came out as a
  **backspace**, `tickets` as a **tab**, `reason_text` as a **carriage
  return** plus an `eason_text` that had lost its first letter, and a
  fenced block came out as `` ``bash ``.

  The damage was small, and it was almost invisible in a rendered
  Markdown page: the sentence still read, the code block still looked
  like a code block. But the **file on the disk was corrupted**, and it
  **shipped** - because **nothing was looking**.

  That is the point. A document is the thing a beginner opens first. A
  document with a backspace in it is not a document; it is a file that
  happens to be mostly text. And this project's own rule is that a
  **capability nobody checks is a capability nobody has** - which is
  why every hole in this project got a gate. This one was waiting too.

  The thirty-fifth gate now reads the **documents of the project** -
  both READMEs, the CHANGELOG and the `docs/` folder, in the
  repository **and in the wheel** - and checks three things: **no
  control characters** (a backspace, a stray carriage return), **no
  stray tabs in the prose**, and **the code fences are intact** (an
  opening fence names a language or nothing, a closing fence names
  nothing, every fence is closed).

  And it proves it can fail: a document is corrupted on purpose - a
  backspace is injected into it - and the gate has to notice that it
  can no longer tell a clean document from a mangled one.

  ```bash
  mas selftest --only doc_text_integrity
  ```

- **Gate 36 `report_trace_roles`** --- the last column of the ledger table is
  the summary. It comes from `_short_detail()` in `mas/report.py`, which
  dispatches on the ledger role: `closure`, `planner`, `review`,
  `reflector`, `handback` and so on.
  **Six roles had no branch at all**: `write`, `draft`, `truncated`,
  `reask`, `reask_failed` and `lead`.
  The dispatch falls through to `return ""`, so those rows rendered as
  **empty strings** --- the row was there, with its number, its
  timestamp, its run id and its role, and the one column that said
  *what happened* was blank.
  That is not a cosmetic gap: the `write` row is the one line that
  answers **"what did the agent actually change on my machine?"** ---
  which file, how many bytes, created or overwritten ---
  and it was invisible. A run that wrote three files and a run that
  wrote nothing looked exactly the same.
  Now the `write` row names the file, the size and whether it was
  created, the `draft` row names the draft and the file, the
  `truncated` row says the answer was cut off and will be retried,
  the `reask` row names the criteria that were still missing, and the
  `lead` row reads the advisor's advice.
  The gate **proves it can fail**: break `_short_detail()` on purpose
  so it says nothing, and the gate has to notice that it can no longer
  tell a row that happened from a row that did not.

  ```bash
  mas selftest --only report_trace_roles
  ```

- **The thirty-seventh gate `mcp_artifacts`** - the main road into this
  project is MCP: an agent holding the MCP tools (Claude, GPT, Kimi) can
  run a loop, read the report, replay the ledger, audit the decisions and
  list every file it wrote. It could **not answer "what did I actually
  get?"**.
  `mas artifacts` could answer that - but **only on the terminal**. The
  listing lived inside the CLI command, where MCP could not reach it. So
  an agent that had just closed a loop, holding the whole toolkit, still
  had to *guess* where the deliverable was.
  Worse: there was **one** implementation, so the moment a second one was
  written the two would drift, and the CLI and the MCP would tell two
  different stories about what this machine produced.
  Now the listing lives in `mas/report.py` (`artifact_rows()` /
  `format_artifacts()`) and **both surfaces call the same one**; MCP gains
  a `mas_artifacts` tool that answers with **data** (the task, the path,
  the size, the rounds, whether the file is still there) rather than
  Markdown the caller has to parse.
  **A deliverable that was deleted afterwards is still listed, and
  flagged as missing** - "the file is gone" is a fact the reader needs;
  hiding it is the problem.
  The gate **proves it can fail**: the shared listing is broken on purpose
  - it returns nothing - and the gate has to notice that the MCP caller
  can no longer tell a finished job from no job at all.

  ```bash
  mas selftest --only mcp_artifacts
  ```

- **The thirty-eighth gate `closure_verdict`** - this is the project's
  **own question**: "is this system closed?"
  `mas doctor` only inspects the configuration, `mas run` only runs one
  task, and **no tool in MCP could answer it**.
  The report can show every number - how many runs, what closure rate,
  how many tickets are waiting for a person, how many drafts are
  unapproved - but **the rule that decides "closed" was never written
  down anywhere the reader could reach it**.
  An agent holding the whole MCP toolkit could read every number in
  the report and still have to *assemble the pieces in its own head* to
  answer "is the work done?", and different readers assemble them
  differently.
  Now the verdict lives in `mas/report.py` (`closure_verdict()` /
  `format_closure()` / `closure_verdict_for()`) and **both surfaces call
  the same one**; MCP gains a `mas_closure` tool, the CLI gains
  `mas closure`.
  Closed is **not** "the runs succeeded", it is **nothing is waiting**:
  no unclosed run, no open problem thread, no ticket waiting for a
  person, no draft waiting for approval, no drifted seal, no broken
  decision chain.
  **Every cell is counted and explained on its own** - a bare "not
  closed" sends the reader hunting; "3 drafts are pending a human" tells
  them what to do.
  **An empty ledger is not closed**: an empty ledger is not "a perfect
  loop", it is "nothing has run yet", and the two must never be spelled
  the same way.
  The verdict **only reads**: it reads the ledger, the ticket queue and
  the draft queue, and **it starts no run**.
  The gate **implements the verdict twice, independently**, and
  **proves it can fail**: the shared verdict is replaced with one that
  always says closed, and the gate has to notice.
  ```bash
  mas selftest --only closure_verdict
  ```
- **The thirty-ninth gate `closure_supersede`** - v0.7.97 moved the
  closure verdict into the code; this release fixes the fact that it
  **counted at the wrong grain**.

  The report groups runs into **problems** (one problem, two attempts,
  closed - the later attempt superseded the earlier one), while the
  verdict counted **attempts**. So one report could say both at once:

  ```
  threads:    [('T-1', closed=True)]        <- the problem is closed
  VERDICT:    ok=False ['runs_not_closed']   <- something is still waiting
  ```

  One report, two answers. And the consequence was worse than a wording
  problem: **a problem that failed and then succeeded could never reach
  `ok=True`**. Fail, resume, succeed - the core workflow of this project -
  could not finish. The queue of open problems could only ever grow, and
  a queue that never empties teaches the reader to ignore it.

  Closure is now counted at the grain of the **problem**:

  * a failure that a later successful attempt **superseded** is
    **history**, not work in progress - the history is kept, it no
    longer holds the queue open;
  * `not_closed` (every failed attempt), `not_closed_open` (still
    waiting) and `not_closed_superseded` (replaced) each say their own
    thing;
  * the attempt closure rate and the new **problem closure rate** both
    exist, and each names its grain;
  * the rendering says it out loud: "1 of the failures was replaced by a
    later successful attempt, so it is not work in progress".

  The gate **implements the reading twice** and **proves it can fail**:
  the verdict is broken on purpose - back to the old grain, where every
  failure counts as waiting - and the gate has to notice that a
  superseded problem is reported as unfinished again.
  ```bash
  mas selftest --only closure_supersede
  ```
- **The fortieth gate `status_grain`** - v0.7.98 put the **report** and the
  **verdict** on the same grain, the grain of the problem. This release
  finds the **third speaker** still on the old grain of the **attempt**:
  the **status page**.

  On one machine `mas status` said "1 not closed" while `mas report` and
  `mas closure` said "0 open problems" - **two answers, one machine**,
  and the reader is one person. The MCP side was worse: `mas_status`
  reported only `open_tickets` and said **nothing at all** about closure,
  so from an MCP caller the loop looked like it had **never** closed,
  even when it had closed long ago.

  Now **all three read one function** (`closure_grain()` in `mas/report.py`):

  * the status page talks in three numbers: **still waiting** (real work
    in progress), **historical failures** (superseded, no longer holding
    the queue), and the **problem count**;
  * "still waiting" counts **problems** (the last attempt decides),
    "historical failures" counts **attempts** (everyone who ever failed
    is remembered);
  * the report headline changes from "not closed: N" to "**still
    waiting: N**", plus a line "historical failures: N times (M of them
    were superseded by a later successful attempt, so they are not work
    in progress)";
  * the MCP `mas_status` gains a `closure` section carrying
    `attempts_open` / `attempts_superseded` / `problems_open` and both
    closure rates, saying **the same number** as the command line.

  The gate **implements the reading twice** and **proves it can fail**:
  the function the status page reads is broken on purpose - back to the
  old grain - and the gate has to notice that the status page and the
  verdict now give opposite answers.
  ```bash
  mas selftest --only status_grain
  ```
- **The forty-seventh gate `a_taken_back_approval_is_visible_everywhere`** - v0.7.85
  taught the **command line** to read the audit chain. This release finds that
  the **MCP face** still spoke the old wording, and that the **report** and the
  **status page** each missed half of it.
  ```bash
  mas drafts --json
  ```
  After a draft is approved, taken back and approved again, the draft file
  holds only the **newest** state - the wrong approval lives in the audit chain
  alone:
  ```text
  mas drafts --json   ->  ever_undone=True, undos=1        <- command line: taken back
  mas_drafts (MCP)    ->  ever_undone=None, undos=None      <- MCP: says nothing at all
  ```
  **One machine, two answers** - and the **agent** is the one that acts on it.
  An agent driving this machine through MCP with Claude, GPT or Kimi, asking
  "is this draft still good?", reads `approved` and never learns that the
  approval was taken back once. Two more holes in the same family: the
  report's draft table said only "已批准", so a reader had to cross-reference
  another section; the status page counted drafts as `pending` or `approved`
  and had no bucket at all for "an approval of this was taken back", so a
  draft whose approval was taken back and never given again fell out of every
  count.
  Now the chain is read in **one place** (`mas/undos.py`:
  `draft_histories()`), and the command line, the MCP face, the report and the
  status page **all call it**:
  * the MCP face (`mas_drafts`, both `list` and `show`) gives every draft
    `approvals` / `undos` / `ever_undone` / `reverted_line_count`;
  * the report's draft table gains a "**被撤销**" column, reading `1 次`;
  * the status page gains two counts (`undone` / `with_history`) and a line
    of words: "⚠ 批准被撤销过：N 个草稿的批准被撤回过".
  The chain lives in the **workspace the decision was made in**, so the status
  page takes its roots **from the drafts themselves** (every draft names its
  own `workspace`) - the same rule the draft listing walks. A draft that
  lives in a workspace of its own is no longer listed without its
  taken-back approval.
  The gate watches four things: the MCP face can say "this draft was taken
  back"; the command line and the MCP face say the **same** number; the report
  **names** the draft; the status page **counts** it. And it reads the facts
  **twice**, independently - once from the pages, once from the raw audit
  chain - and the two readings have to agree.
  The gate proves it can fail, and the break is on the **wiring**, not on the
  shared function: cut the **MCP face's** read of the chain (which is exactly
  the old bug) and the gate has to notice that the two faces are arguing.
  > Breaking the shared function would not do: every face reads it, so
  > when it breaks they break **together** - they agree, and the gate sees
  > nothing.
  ```bash
  mas selftest --only a_taken_back_approval_is_visible_everywhere
  ```
- **The forty-sixth gate `replay_listens_to_the_queue`** - v0.8.4 made the
  **report** listen to the ticket queue. This release finds that the
  **replay** and the **MCP** faces still spoke the old wording.
  ```bash
  mas replay
  ```

  `mas replay` is the screen a reader opens to see **what the loop did**, and
  it built its threads from the ledger alone. The command line had been
  handing it the queue since v0.8.4 - and the replay threw it away:

  ```text
  mas report --run-id R1     ->  | do the thing | resolved |   <- report: done
  mas replay                  ->  [pending] do the thing         <- replay: not done
  ```

  **One machine, two answers** - and the replay is exactly where a reader
  goes to find out what happened.

  The MCP face was worse: it **never opened the queue at all**. Not "read it
  and got it wrong" - never opened it. So an agent driving Claude, GPT or
  Kimi through MCP got a replay that could not tell a finished problem from
  an open one, while the same person at the same terminal saw the right
  answer.

  A **third** answer hid in the same place. The report kept a failed run's
  `mas resume` pointer when the queue had **not been read** - because "nobody
  looked" is not "there is nothing to do". The replay dropped it, so the
  replay told a reader to walk away from an open problem whose state was
  merely **unknown**.

  Now the queue is read in **one** place (`queue_state()` in
  `mas/report.py`), and a ticket has **one** four-way state
  (`ticket_state_of()`):

  | the queue | ticket state | resume pointer | why |
  | --- | --- | --- | --- |
  | waiting | `waiting` | **yes** | a person still holds it |
  | resolved | `resolved` | no | finished work; pointing there redoes it |
  | read, but absent | `lost` | no | `mas resume` would raise on it |
  | never read | `unknown` | **yes** | "nobody looked" is not "nothing to do" |

  The gate watches five things: the replay and the report count the **same**
  open problems; a problem the queue says a later attempt closed reads as
  closed **in the replay too**; the MCP replay **reads the queue**; a replay
  whose queue was **never read** keeps its resume pointer; and a ticket the
  queue has **lost** gets no pointer.

  And it **implements the reading twice** (once from the page, once from the
  raw facts) - the two must agree.

  The gate **proves it can fail**, and it breaks the **wiring**, not the
  shared function: the replay's queue is **thrown away** again - which is
  the old bug itself - and the gate has to notice that the two surfaces are
  arguing again.

  > Breaking the shared function would not work: the report **and** the
  > replay both read it, so when both break they **agree** - and the gate
  > sees nothing.

  ```bash
  mas selftest --only replay_listens_to_the_queue
  ```
- **The forty-fifth gate `single_run_supersede`** - the first four gates
  put the **grain** on every surface, and this release finds that the most
  **common** way of looking was still the old one: the single-run report.
  ```bash
  mas report --run-id R1
  ```

  The ledger of that report holds one run - the **failed** one. The attempt
  that actually closed the problem (`R2`) happened **later**, in **another
  run**, and is simply **not in that ledger** - so four lines of the same
  page contradicted each other:

  ```text
  attempts: 1  closed: 0  still waiting: 1  attempt rate: 0%  <- headline
  | do the thing | pending |                               <- overview
  | T1 | resolved |                                          <- ticket table
  - human ticket: `T1` (no need to continue)                <- and the detail
  ```

  Four lines, three answers - and the reader of a single-run report has
  **no way to reconcile them**, because the evidence that would settle it
  lives in a report they never opened.

  The root cause is **asking the wrong authority**. "Is anyone still
  holding this problem?" is a question about the **human queue**, and the
  queue answered it. The verdict asked the **ledger** instead - and for
  one run, the ledger holds nothing but the failure.

  The **queue** is the authority now, and it already carries the evidence:
  when an attempt closes a problem, the engine records **which attempt
  closed it** on the ticket (`closed_run_id`). A resolved ticket that names
  that attempt is proof the problem was finished, so the failure it covers
  becomes **history** - and the report **names the attempt that replaced
  it**.

  Two things must **not** follow from that:

  * a ticket a person closed **by hand** (`mas_ticket_resolve`) carries no
    `closed_run_id` - no successful attempt was ever recorded - so the
    failure **stays open**. The human handled the ticket; the machine
    still has nothing to show for it. Reporting that as finished would be
    the tool **lying in the direction that hides work**;
  * a queue that was **never read** is not an empty queue: a caller that
    hands in no queue has not looked, so it cannot retire a ticket, and
    the failure **keeps its resume pointer**.

  This gate watches six things: the single-run report listens to the
  queue, the four lines of the page agree, the detail names the
  replacement, **a hand-closed ticket still counts as pending**, **an
  unread queue keeps the resume pointer**, and the verdict page reaches
  the same answer. It **implements the reading twice** - once from the
  rendered page, once from the raw facts - and the two must agree. The
  gate **proves it can fail**: the thread grouping is broken on purpose -
  made to ignore the queue - and the gate has to notice that the page
  contradicts itself again.
  ```bash
  mas selftest --only single_run_supersede
  ```
- **The forty-fourth gate `run_detail_grain`** - v0.7.100 made the
  **status page** and the **report headline** say the grain out loud, and
  v0.8.2 made the **verdict page** say it. This release finds that the
  **per-run detail** still spoke the old wording - and that is exactly
  the section a reader flips to when they want to know *what to do next*.

  ```text
  attempts: 2  closed: 1  still waiting: 0  attempt rate: 50%   <- headline
  history: 1 failure (replaced by a later successful attempt, not pending)

  ## run `R-sup1`
  - not closed reason: stagnation: the deliverable did not change ...
  - human ticket: `T-sup1` (see it with `mas tickets`, continue with
    `mas resume <ticket>`)
  ```

  **One machine, two answers**: the headline says "nothing is waiting",
  while the detail hands the reader a **`mas resume`** command for a run
  that a later successful attempt had already replaced. The overview
  table marked that row `ESC` too, which reads as "still pending".

  Every failure now carries **its own grain** (`superseded` / `open`),
  decided by the same rule the headline uses:
  * a replaced attempt is filed as **history** in the detail, its reason
    is relabelled "the reason at the time", and it **hands out no resume
    pointer**;
  * the overview table marks a replaced attempt `HIS` and says
    "replaced";
  * the resume pointer goes **only** to a run whose ticket is **still
    waiting in the queue** - a run keeps the id of the ticket it closed,
    so "has a ticket id" is not "has work waiting";
  * **a real pending problem keeps its guidance**: a run that is still
    waiting still gets its reason, its next-step hint and its
    `mas resume`;
  * **"the ticket is lost" is not "the work is done"**: a ticket the
    ledger records and the queue does not hold reads "it can no longer
    be found - get it back first", **not** "no need to continue" -
    `mas resume` raises on a ticket it cannot find, so pointing there
    only sends the reader into an error.

  The gate **implements the reading twice** and **proves it can fail**:
  the detail is broken on purpose - made to hand out the resume command
  again - and the gate has to notice that the page sends the reader to
  resume work that is already done.
  ```bash
  mas selftest --only run_detail_grain
  ```
- **The forty-third gate `closure_grain_text`** - v0.7.98 put the closure
  verdict on the **problem** grain, v0.7.99 put the verdict in one place,
  and v0.7.100 made the **status page** and the **report** say the grain
  out loud. This release finds that **the verdict page still spoke the
  old wording**: `mas closure` and the MCP `closure_text` still printed
  "closed 1 / not closed 1".

  ```text
  ledger: 2 attempts  closed 1  still waiting 0      <- the status page
  verdict: closed: nothing left waiting.
            2 attempts (closed 1 / not closed 1)      <- the verdict page
  ```

  **One machine, two answers**: one says "nothing is waiting", the other
  says "one attempt did not close". The reader is one person, and the
  reader who reads the verdict first is exactly the one who went there
  *because* they wanted the answer.

  The verdict page now splits the two numbers the same way (nothing is
  hidden - the history is still counted, only labelled):
  * the attempt line becomes "N attempts (closed X / still waiting Y),
    attempt closure rate Z%";
  * a second line reads "history: N failures (M of them replaced by a
    later successful attempt, not pending)";
  * the old "closed / not closed" wording is **gone** from the verdict
    page.

  The gate **implements the reading twice** and **proves it can fail**:
  the verdict page is broken on purpose - made to print the old wording
  again - and the gate has to notice that one machine is giving two
  answers again.
  ```bash
  mas selftest --only closure_grain_text
  ```
- **The forty-second gate `queue_brief`** - v0.8.0 gave the status
  tool ticket briefs. This release finds that **the queue had a second
  home** and it had not kept up: `mas tickets --json` and the MCP
  `mas_tickets` tool **still returned the raw record**.

  ```json
  {"tickets": [{"id": "T-...", "state": "waiting_human",
                "payload": {"reason": "stagnation: ...", "budget": {...}}}]}
  ```

  A reader could see the title and the raw reason string, but *why* the
  loop stopped was machine code only, and *how to continue* (the command
  with the widened budget that does not hit the same wall) was **not in
  there at all** - it lived only in the command line's human text
  layout. And the queue is the surface most likely to be **polled**: an
  agent asking "what needs me?" polls the queue.
  **The one tool whose whole job is "list the tickets" was the one
  surface where a ticket could not be picked up.**

  The queue now reads **the same function**
  (`ticket_brief()` in `mas/ticket_brief.py`), and the three surfaces
  (the status tool, the command line's JSON, the MCP queue) say **the
  same words**:

  * the MCP `mas_tickets` gains `briefs` (the briefs), `briefs_count`
    and `briefs_text` (one line per ticket);
  * the command line's `mas tickets --json` gains `briefs` and
    `briefs_count`;
  * **the raw tickets do not move at all**: `tickets` is still the same
    raw record it has always been. The briefs are added **beside** it,
    never **in place of** it - anything already reading `tickets` keeps
    working;
  * the queue and the status tool describe the same ticket **field by
    field**.

  The gate **implements the reading twice** (the second time by hand,
  straight from the raw ticket) and **proves it can fail**: the brief
  is broken on purpose - made to drop the "how to continue" - and the
  gate has to notice that the queue could no longer be picked up.
  ```bash
  mas selftest --only queue_brief
  ```
- **The forty-first gate `ticket_brief`** - v0.7.99 put the status page,
  the report and MCP on the same grain. This release finds **another
  road with a missing piece**: the MCP `mas_status` gave the waiting
  tickets as **nothing but a list of ids**.

  ```
  "open_tickets": ["T-20261121-104405-7a81"]
  ```

  An agent asking the status tool "what needs me?" got a list of
  handles: it had no way to learn what any of them was about, what it
  was stuck on, or how to continue. The command line has always said
  all of that plainly (the task, the check it failed, why the loop
  stopped, the command that continues it) - which is the **same shape**
  this project has been fixing since v0.7.89: a capability that
  reaches the command line but not the agent.

  Now both surfaces read **one function**
  (`ticket_brief()` in `mas/ticket_brief.py`):

  * a brief carries **what it is** (title, goal, which run), **what it
    is stuck on** (the unmet check, in words), **why it stopped**
    (the raw reason, the reading, the hint) and **what to do next**
    (the command that continues it, with a widened budget so it does
    not hit the same wall), plus **where to edit**;
  * the MCP `mas_status` gains `waiting_tickets` (the briefs),
    `waiting_tickets_count` and `waiting_tickets_text` (one line each);
  * the **id list stays** (`open_tickets`) - that is the handle every
    other tool takes, and a handle is not replaced just because a
    richer view exists;
  * the command line and the MCP tool describe the same ticket with
    the same words.

  The gate **implements the reading twice** (the second time by hand,
    straight from the raw ticket) and **proves it can fail**: the
    brief is broken on purpose - back to "an id and nothing else" - and
    the gate has to notice that an agent can no longer read the
    tickets.
  ```bash
  mas selftest --only ticket_brief
  ```
- **The 31st gate `report_audit`** - v0.7.76 made every human decision
  (the proposal, the approval, the undo) leave a hash-linked entry next to the
  workspace it belongs to, with a copy in the main ledger; `mas audit` can
  check those chains and `mas status` shows the result. But **`mas report`
  never looked at them** - there was not even an `import mas.audit` in
  `mas/report.py`.

  The consequence is concrete: the ledger row says "approved", the chain says
  something else, and the report says neither. A capability that can prove a
  record was changed on the disk, but is not consumed by the main view, is a
  capability nobody has. This is the **third hole of the same family**:
  v0.7.89 was the report not looking at the seal, v0.7.90 the report not
  looking at the undo, v0.7.91 the report not looking at the audit chain.

  The report now says it: a headline line reads "N chains, M entries, K
  problems" (or "all intact" when there are none); a section of its own -
  "audit chain review" - names **which chain**, **which entry was changed**
  and **which chain is gone entirely**; and the JSON payload carries the same
  numbers. The chains are found **by reading the rows themselves** rather
  than by guessing a folder (which is what `mas status` does): a row that
  knows its workspace says which workspace it belongs to, so the report looks
  at exactly the chains that wrote those decisions. A chain that was deleted
  **whole** reads as nothing, but the ledger still holds the decision - so the
  report names "the ledger holds N human decisions, but the audit chain is
  gone" as a **gap in the record**. The exit code does not change (the report
  still exits 0; `mas audit` and `mas seal` are the alarms).

  The gate proves it can fail: the report's audit reading is broken on
  purpose - every chain is reported as intact - and the gate has to notice
  that it can no longer tell a healthy chain from a changed one.

  ```bash
  mas selftest --only report_audit
  ```

- **The 30th gate `report_undo`** - v0.7.78 made an undo leave a trace in the ledger, and
  v0.7.85 gave `mas undos` a way to list *which approvals were taken back*; but
  **`mas report` never looked at either of them**. `run_summary()` does not read
  the `draft_approve` / `draft_undo` rows at all, because a human decision about a
  draft is not a run.

  The consequence is concrete: the approvals you took back three weeks ago sit
  in the ledger, while the report in front of you says "resolved - machine-closed".
  A capability that can say *which approvals were wrong*, but is not consumed by
  the main view, is a capability nobody has.

  The report now says it: a headline line reads "N approvals were taken back";
  a section of its own - "taken-back approvals" - names **which draft** and
  **which file**, how many times it was taken back and how many lines went back;
  and the JSON payload carries the same numbers, so CI and scripts do not have to
  parse Markdown. It also fixes a **false statement**: a ledger that holds only
  human decisions and no runs used to be described as "the ledger is empty" -
  which is not true. It now says "this machine has not run a single closure yet,
  but the ledger holds human decisions" and points at `mas undos`.

  The pairing is **not re-implemented**: it reuses the positional stack pairing in
  `mas.undos`, so an undo that cannot be paired is **counted rather than hidden**
  and is never guessed at. The gate proves it can fail: the report's undo
  counting is broken on purpose - every approval is reported as standing - and
  the gate has to notice that it can no longer tell a wrong approval from a
  right one.

  ```bash
  mas selftest --only report_undo
  ```

- **The 29th gate `report_seal`** - v0.7.88 gave every closure a *seal*, but the report never looked at it;
  the acceptance criteria, the deliverable's sha256, the sha256 of every
  file the run wrote. `mas seal` can re-derive the verdict from that
  sealed evidence and say whether it still holds.

  But the report never looked at the seal. The report is the screen a
  human actually reads - it is where "did this work?" gets answered -
  and it went on writing "resolved" and "machine-closed" for a closure
  whose bytes had been edited underneath. A capability that can prove
  drift but is not consumed by the main view is a capability nobody
  has.

  The report now re-derives the seal: a problem whose conclusion was
  edited is no longer called "resolved" but "**conclusion drifted**";
  the run overview stops giving it a green light; the detail of the run
  names **which file was edited**; and the JSON payload carries the
  same reconciliation, so CI and scripts do not have to parse Markdown.
  The gate proves it can fail: the report's seal check is broken on
  purpose - every seal is reported as holding - and the gate has to
  notice that it can no longer tell a healthy closure from an edited
  one.

  ```bash
  mas selftest --only report_seal
  ```

- **The 28th gate `closure_seal`** - this project has always promised
  one thing: **the same artifact and the same standard always produce the
  same verdict**. That promise was only half kept.
  The ledger recorded the hash of the deliverable's *text*, but the
  acceptance criteria lived in the engine's memory only - so a reader
  afterwards could not recompute the verdict, they could only trust it.
  And the files a worker wrote were recorded by *length*, not by content:
  somebody editing a file afterwards - even to the **same length** - left
  no trace at all, and the run still reported `closed / pass`.
  In other words: **the closure's verdict was not nailed to the bytes it
  was decided on.**
  Now the loop books *what it decided on* at the moment it decides: the
  acceptance criteria in full, the sha256 of the deliverable, the sha256
  of every file it wrote, and the per-criterion verdicts. Afterwards
  `mas seal` re-derives the verdict from that seal alone: it re-reads the
  disk, re-runs the machine checks and compares the result with the
  sealed verdict. A file that was edited afterwards is reported as
  edited. **A verdict that cannot be recomputed is not a pass**: a
  criterion only a model judged is reported as `model-judged` instead of
  being quietly counted as a pass.
  The seal is a record, not a notarisation - somebody who rewrites the
  seal and the ledger together can still produce a seal that verifies.
  What it buys is that an **ordinary** edit afterwards becomes visible.
  The gate caught **two real bugs** the moment it was written: the two
  human-deliverable paths (`handback` and `handback_workspace`) passed a
  variable that **did not exist** while sealing - the deliverable was
  judged, the file was written, and the run then **crashed on its way to
  the ledger**; and the seal recorded the **run's own folder** while the
  machine checks read the **folder the human had edited** - so the
  recomputation read a different folder's files and reported a drift that
  never happened. Neither was pinned by a test, because **no test ever
  asked a human path for a seal**.

  ```bash
  mas selftest --only closure_seal
  ```

- **The 27th gate `bootstrap_execution`** - v0.7.86 wrote "install a
  Python yourself when there is none" into the installers, and added the
  26th gate to watch the rules for drift. But that gate compares **text**,
  and every CI install job starts with `actions/setup-python` - so
  **every machine that runs the tests already has a Python**.
  The branch that says "this machine has no Python" was therefore
  **executed nowhere**: not in CI, not on a developer's machine, not on a
  contributor's machine. A path nobody runs breaks silently, and it
  breaks in the hardest place to debug: on a beginner's new laptop, with
  nobody around who could explain the error message.
  Now the branch is **actually executed**: every Python is **hidden from
  `PATH`** so the machine really looks bare; a **rehearsal switch** puts
  the interpreter where a real bootstrap would have put it; the
  installer has to find it **with the production lookup** - unchanged
  code, unchanged path; and the installation that follows has to be
  **complete** (runtime in place, launcher in place, self-check green).
  The **counter-proof** has to work too: staged where the lookup cannot
  read it, the rehearsal must **say so honestly**.
  It also watches two things nobody watched before: the **Microsoft Store
  stub** (on a brand-new Windows, `WindowsApps\python.exe` and
  `python3.exe` are **already there** even though no Python was ever
  installed - they are aliases, and running one **opens the Microsoft
  Store**, prints nothing, and fails; an installer that treats them as
  "a Python was found" reports success and then **cannot run**) and the
  **wait** (the official installer exe is a **bootstrapper**: it may
  return while its files are still being written, so a lookup that tries
  once misses).
  Three real drifts went with it: **Windows did not honour
  `MAS_NO_BOOTSTRAP`** (both installers could refuse, but one only knew
  the switch and the other only the variable); **the rehearsal switches
  were spelled differently on the two sides** (they now share one set of
  environment variables, and the 26th gate watches that an installer
  losing one means that platform stops being rehearsed); and **the gate
  itself read the wrong output** - it read the rehearsal's output rather
  than the refusal run's.
  The gate then caught **two real defects of its own**, and both only
  bite in a **restricted environment** (a service account, a scheduled
  task, a CI step that builds its own): **`PATHEXT` that is present but
  useless** (it may be `.CPL` - a list that names **no executable
  extension at all**; the shell then treats `python.exe` as a
  **document**, calling it in a pipeline raises "Cannot run a document
  in the middle of a pipeline", and calling it on its own produces
  **no output** and leaves `$LASTEXITCODE` empty - so the installer
  judged a **perfectly good Python** unusable and told the user the
  machine had none); and **the lookup asked the shell** (`Get-Command`
  goes through `PATHEXT` too, and misses an interpreter that is sitting
  right there on `PATH`).
  The installer now **walks `PATH` itself** and **starts the
  interpreter through .NET**, and the gate's rehearsal deliberately
  carries `PATHEXT=.CPL` so that path is walked **every single time**.

- **The 26th gate `bootstrap_path`** - this version closes a gap that was
  written into the README itself. The README said the first install path is
  *"one script, cross-platform, no Python environment preparation"*, and both
  installers said the same. Then the **first thing** both installers did was
  look for Python 3.10+ and `exit 1` when they did not find it. A brand-new
  Windows laptop or a freshly installed Linux box **hits that wall on step
  one**, and the wall says "go and install a Python yourself".
  The irony is that **everything after that step really is zero-dependency**:
  the runtime is pure standard library, the wheel is unpacked by hand, no pip,
  no git, no compiler. **Only the bootstrap still asked for human labour.**
  Now there is exactly one set of bootstrap rules, in `mas/bootstrap.py`:
  the minimum version 3.10, a **pinned** bootstrap version 3.12.8 (not
  "latest" - a bootstrap that resolves to "whatever is newest now" is a
  bootstrap whose behaviour changes without anyone changing the code), the
  official download URL for that exact version, and the **copy-pasteable**
  commands for POSIX. The Windows installer downloads the official installer
  and runs it **silently for the current user** (no administrator rights, no
  `PATH` change), then **finds the interpreter where it just put it** -
  because a per-user install is *not* on `PATH`, and looking nowhere would
  mean "not found" right after installing one. The POSIX installer uses the
  system package manager, and if that fails it **says it failed** and gives
  the exact command - it never pretends. The user can always refuse the
  bootstrap (`-NoBootstrap` / `MAS_NO_BOOTSTRAP=1`).
  The rules exist in **three** copies (the module and the two installers) -
  the installers *cannot import* the module at the moment they need the
  rules, because at that moment the machine may have no Python at all - so
  this gate **watches the three copies for drift**, and proves it can fail
  with **five counter-proofs** (a changed version, a changed series, the old
  "install it yourself" message coming back, the opt-out flag removed, the
  bootstrap path removed - all five have to go red).

- **The 25th gate `undo_visibility`** - v0.7.77 made an undo put the file back
  byte for byte, and v0.7.78 made it leave a verifiable trace. Both of those
  are still a *stream*: a ledger is a river, one line after another. An audit
  needs a *list*.
  What the person at the screen actually wants is the question **"which of the
  things I approved were wrong?"** - and until now they had to read the whole
  ledger, pick out the undo lines, and work out by hand which approval each
  one cancels. That is exactly the work the tool should be doing.
  The second gap is quieter and worse. A draft that was approved, taken back
  and approved again looks *approved* - "this was once wrong" disappears from
  the draft's own state. The draft file holds only the newest state; the chain
  remembers it, but nobody put it in front of the reader.
  So there is now **`mas undos`**: it pairs every undo with the approval it
  cancels - read off a *stack*, because an undo takes back the most recent
  approval that is still standing, so the pairing is read, not guessed - and
  it says **how big the change was** (the approval records
  `changed_line_count`, the undo records `reverted_line_count`, and the two
  ends have to agree). `mas ledger --undone` and `mas ledger --draft <id>`
  filter down to just those rows; `mas drafts list` now says outright
  "this draft's approval was taken back N times"; `mas audit` ends with a
  summary of the taken-back approvals; MCP gains **`mas_undos`**, so an agent
  asks the same question a person asks and gets the same answer.
  Honest edges: an undo that pairs with nothing lands in `unmatched` and is
  **not** paired with a neighbour; a record from before v0.7.85 without
  `reverted_line_count` keeps the gap and is **not guessed at** - "not written"
  and "zero" are two different facts and are not merged.
  Found the same way as the rest: the pairing is changed on purpose to "always
  pair the newest approval" (which may be an approval that happened *after*
  the undo), and the gate has to notice.

- **The 24th gate `packaged_docs`** - installing the published wheel into a
  **clean home** found a real defect: `mas selftest --only onboarding_path`
  **reported red on itself** (exit 2).
  The cause was not that the gate was too strict. It was that **a wheel
  carries the package, not the repository's prose** - so the gate looked for
  a README next to `site-packages`, did not find one, and raised
  `FileNotFoundError`.
  That failure is wrong twice over: it says nothing about the beginner, and
  it is not a real defect either. What it *did* expose is that **the wheel a
  beginner receives does not carry the two documents they will read** -
  and those are the only two they will read.
  So the onboarding documents now **travel with the wheel** (`mas/docs/`),
  the gate **reads the packaged copy first and falls back to the
  repository**, and the summary grew a third state: **skipped**. A gate
  with no subject does not claim the beginner is lost, and it is not
  silently counted as a pass either - it is listed on its own.
  The evidence: in a clean home with the new wheel, the gate **really
  passes** (exit 0).

- **The 23rd gate `onboarding_path`** - the first 22 gates all watch the
  *runtime*: does the engine close, does the ledger keep, does the draft hold.
  **Not one of them watched the door** - and a newcomer only ever reads two
  things: the README and the quickstart.
  The cost was real. By the time the project was 22 gates deep, the README had
  grown to **2242 lines**, the opening was filled with gate archaeology, and
  **the quickstart sat at line 1314**. Two install sections were both called
  "option two". The version the docs pinned was still **0.7.17** while the
  release was 0.7.82.
  None of that breaks the engine. All of it stops a beginner - and "a
  beginner can install this in one command" is one of this project's goals.
  So the gate holds the door: the onboarding section has to be **within the
  first 40 lines**, the install options must be **numbered without a
  duplicate**, every `mas` command the docs mention **has to exist in the
  parser**, the pinned version **has to be the running version**, and the
  first path **must not need an API key** (a beginner has no account yet).
  Found the same way as the rest: the locator for the onboarding section is
  blinded on purpose and the gate has to notice. Four more mutations - a
  duplicate option number, a drifting version, a command that does not exist,
  and a first path that demands a key - are caught as well.
  This gate watches **the first page a human reads**, not the result the code
  produces - that is what makes it different from the first 22.

- **The 22nd gate `shadow_guard`** - every check in this project asks a question about
  the **code that is running**. But the person at the keyboard does not run the code
  the installer verified - they run whatever ``mas`` their PATH reaches first.
  On a real machine that was:

  ```
  AppData\Roaming\Python\Python314\Scripts\mas.exe   0.7.16
  .mas\bin\mas.cmd                                      0.7.81
  ```

  The installer unpacked 0.7.81, ran ``mas doctor`` against it and said "everything
  is ready" - and then the user typed ``mas`` and got 0.7.16. Every check passed
  and the wrong program ran anyway. Now ``mas shadow`` walks the PATH the way the
  shell walks it, names the **winner** and the version it would run (read **from a
  file on disk**; when it cannot be traced to a package it says so instead of
  guessing). ``mas doctor`` gained a ``shadow_runtime`` bucket and does **not**
  count it as "not installed" - that copy **is** there, it is just the **wrong**
  one, and ``mas install`` cannot change which copy the PATH reaches; the right
  advice is ``mas upgrade``. ``mas upgrade --fix-shadow`` replaces the old copies
  on the PATH with the running one (backup first, the version is read back
  afterwards, and a replacement that did not land is an error rather than a
  success). A **pip-managed** copy is **reported, not rewritten** - the command
  ``pip uninstall openloop-mas`` is printed instead, because rewriting the files
  would make ``pip list`` start lying. Both installers (``install.ps1`` /
  ``install.sh``) now **prepend** their launcher to the PATH (they used to append
  it, which handed the win to any older copy) and warn on the spot when another
  ``mas`` is still ahead of it. The gap was found by **counter-proof**: make the
  shadow detection always report "no shadow", and the gate has to go red (four
  observations turn red at once).

- **The 21st gate `audit_chain`** - v0.7.77 made the **undo** put the file
  back byte for byte. But the undo was still only a *file operation*: once
  it was done, nothing in the machine's own records said that an approval
  had been taken back - the thread looked like an ordinary approved change.
  Now every **approval** and every **undo** is written **twice**: once into
  a **hash-linked chain** (`<workspace>/.mas-drafts/audit.jsonl`, where every
  entry carries the hash of the one before it and the hash of its own
  content, so a changed or deleted entry is visible), and once into the
  **main ledger** that `mas ledger` shows. The two copies have to agree, and
  the new **`mas audit`** checks exactly that - a changed entry, a deleted
  entry and a copy edited on its own are all **named** when they are found
  ("entry N was changed", "the ledger and the chain disagree about field X
  of draft Y"). The MCP server gained **`mas_audit`**, and `mas report` no
  longer mistakes an approval or an undo for a **run**.
  Honest limit: a chain is **not a notarisation** - rewriting the whole
  chain makes it verify again; what is detected is a **single change**, not
  a rewrite of both copies at once.
  The gap was found by **counter-proof**: replace the chain's verification
  with a stub that always passes, and the gate has to go red.

- **The 20th gate `undo_guard`** - v0.7.72 made the **approval** the only - v0.7.72 made the **approval** the only
  way a change reaches a file. That is right, but it also made the
  approval a **one-way door**: once you approved the wrong thing, the
  only way back was to **edit the file by hand**.
  The **baseline** v0.7.76 keeps was sitting in the draft, unused.
  Now `mas drafts undo <draft id>` takes the change **back**: the file
  returns **byte for byte** to what it held when the draft was made -
  not an approximation, not a reverse patch, but the baseline itself
  written back.
  The draft **does not disappear**: after an undo it can be
  **approved again**, once you understand why it was undone.
  The undo **asks the price too**: if the file was changed *after* the
  approval, the undo is **refused** and it **names the lines it would
  cost** - a rule **symmetric** to the approval: the approval stops
  "overwriting someone else's update", the undo stops "wiping the work
  that came after the approval".
  On the MCP side **`mas_draft_undo`** needs the **human token or a
  one-shot ticket**, just like an approval: an agent may propose a
  draft and ask what it would cost, but **changing a decision that is
  already on disk has to be a human's job**.
  `mas_status` now carries `undone_drafts`: an undone change is **not
  "did not happen"** - it says **an approval was wrong**, and the
  draft is still there, waiting to be approved a second time.
  The seam was found by **refutation**: the undo is broken on purpose -
  it writes the wrong bytes - and the gate has to notice.

- **The 19th gate `force_cost`** - v0.7.75 made the draft remember the
  baseline hash and **refuse** to write back when the file moved. But
  "the file moved" is a **fact**, not a **decision**: nobody can tell
  from that sentence whether the change that moved is worth keeping.
  You have to see **what** moved.
  The draft now **keeps the baseline**, and the cost of overwriting is
  put on the table: `mas drafts compare <draft id>` shows the three
  versions - **baseline → now → draft** - and **names the lines the
  overwrite would take away** (the ones you wrote and the draft does
  not carry). Lines nobody touched are **not** a loss.
  On the MCP side **`mas_draft_compare`** asks the same question.
  The **digest basis** is recorded with the draft (`digest_basis`), so
  an old draft is compared through its baseline, and a draft with no
  baseline **does not cry wolf** - a false alarm is worse than no alarm,
  because it teaches you to ignore the real ones.
  A **binary file is not mistaken** for a changed file.
  `mas_status` now shows the **drafts stuck on a conflict**: that is a
  decision the human still owes, and a draft nobody can see is a
  decision nobody makes.
  The seam was found by **refutation**: the loss detection is broken on
  purpose - every line counts as a loss - and the gate has to notice.

- **The 18th gate `conflict_guard`** - v0.7.72 put the change into a
  draft, but the draft never **checked** the file it was made against:
  if you edited the file while the draft was waiting, approving it
  **silently threw your newer work away**.
  When two agents fight over one file that is a *lost update* - the
  most common accident of a team.
  A draft now **remembers the hash it was made against** and compares
  before writing: when they differ the write is **refused**, your
  newer content **stays on disk**, and the draft **keeps waiting**.
  Only `mas drafts approve --force` overwrites - knowing what you are
  doing is a human's job.
  `mas drafts conflict` asks "is this draft still safe to approve?"
  **without changing anything**.
  A refusal reports **the hash it expected and the hash it found**,
  not just "no".
  One more real gap is closed: **a ticket is one-shot**, and with the
  ticket spent before the conflict was looked for, a failed approval
  would **burn the one decision you handed over**.
  The order is now **peek at the ticket (do not spend it) → look for
  the conflict → spend the ticket → write back**, so a wrong ticket,
  an expired ticket or a moved file all **leave the ticket alive**.
  The gap was found with a **mutation proof**: **all twelve mutations of
  the conflict path were caught** (three of them are exactly the gate's
  own verdict being edited).

- **The 17th gate `ticket_approval`** - The **human token** of v0.7.73 was
  a **standing key**: paste it to an agent once and it can approve **every**
  draft, **for ever**. In a team of agents that is a real gap - the proposer
  ends up holding the power to confirm, and the separation of duties is
  worth nothing.
  There is now a second door: the **ticket**. `mas drafts ticket <draft id>`
  signs one with the human token. It opens **that one draft**, **burns after
  a single use**, and **expires after fifteen minutes** - and only its
  **hash** is written down, so a copied ticket store carries no usable
  ticket away with it. A ticket also authorizes **one action**: a ticket
  signed for a rejection cannot approve.
  `mas_draft_resolve` takes a ticket now, and when the ticket does not fit
  it says exactly where the door stayed shut: `ticket_already_used` /
  `ticket_expired` / `ticket_bound_to_another_draft` /
  `ticket_action_mismatch`.
  The gap was found with a **mutation proof**: **all thirteen mutations of
  the ticket path were caught** (four of them are exactly the gate's own
  verdict being edited); and the verdict is **implemented twice**, so
  **editing the verdict function itself is caught too**.

- **The 16th gate `mcp_draft_approval`** - v0.7.72 turned "the change goes
  into a draft first" into an **engine-level** rule: when the loop wants to
  change a file you already have, it records a draft and you approve it
  before anything is written back. **But that rule only governed the loop
  itself.** An agent that speaks MCP could still reach straight into your
  files and never touch the draft layer at all.
  Now **proposing and approving are two different actions**:
  an agent stages a change over MCP (**the original keeps every byte**, the
  draft carries a diff and a reason), and **approving needs the human
  token** - the one `mas drafts token` prints.
  **An agent cannot approve its own proposal**: a wrong token, an empty
  token and a missing token are all refused, and **the original file does
  not move**. The token is not about secrecy, it is about **separation of
  duties**: the proposer does not confirm what it proposed.
  The agent can also **`mas_files`** (index the workspace) and
  **`mas_preview`** (read one file) - so it can look at what it is about to
  change. And both tools **know the workspace**: when the agent says
  "index this workspace" or "preview this file inside it", the tool reads
  *that* folder - not the process folder, and not an answer saying there is
  nothing here. The gap was found with a **mutation proof**: **all twelve
  mutations of the MCP draft path were caught** (two of them are exactly the
  path resolution being broken). And the verdict of this gate is
  **implemented twice, independently** - the two readings have to agree,
  so breaking the gate itself is caught too.

- **The 15th gate `draft_approval`** - every version so far worked on *reading*
  and *writing*, and not one of them asked **"who approves this change?"**
  When a run wanted to change a file that **already existed**, the engine
  overwrote it - **your own work was gone, and you never even saw what it had
  become**. The dangerous moment is not a loop that stalls, it is a loop that
  runs *too smoothly*. Now the change goes into a **draft**: **the original
  keeps every byte**, the draft carries a **readable diff**, and only your word
  - "approve" - writes it back. "Reject" changes nothing at all.
  **A new file is still written right away** - there is nothing there to lose.
  **The machine checks read the drafted bytes**, otherwise the loop would be
  stuck for ever: propose a change, read the old file, fail, propose again,
  read the old file. The draft lives **in the workspace** (`.mas-drafts/`), so
  a copied folder carries its drafts along - a lost draft is a change that can
  never be reviewed. **All eight mutations of the draft path were caught.**
- **The 14th gate `resource_index`** - every version so far worked on *reading*
  and *writing*, and not one of them answered the plainest question there is:
  **"what did this loop actually produce?"**
  The answer used to be scattered: `mas artifacts` lists only the *deliverable*,
  `mas status` only talks about the *machine*, and a file that is neither -
  a supporting PDF, a font, a 3D model - was not listed anywhere at all.
  Now there is **`mas files`** (one folder is one index) and **`mas preview`**
  (read one file). The index guarantees three things: **every file is listed**
  (including the supporting files no criterion mentions), **the format comes from
  the file's own header** (a PNG named .txt is reported as `image`), and **a file
  that cannot be read is listed with its reason** - never silently dropped.
  The facts are read from the file: the *pages* of a PDF, the *family* of a font,
  the *duration* of a recording, the *triangles* of a model, the *members* of an
  archive - **only what the header can prove**. Standard library only, **read-only,
  never executes anything**, so it runs on any machine with Python.
  **All seven mutations of the index path were caught.**
- **The 13th gate `binary_write`** - v0.7.70 made the machine checks read
  **inside** a PDF, but the loop still could not **write** one: the write
  protocol of v0.7.15 could only carry **text**, the worker's output was a
  string, and the engine wrote it as UTF-8. A task whose deliverable was a PDF
  had no way to produce one - so the strongest reading in the project had
  **nothing to read**. The write protocol now takes an `encoding` attribute:
  `<<<MAS:FILE path="out/report.pdf" encoding="base64">>>>` and the engine
  **decodes the block into real binary** before writing it to disk. A block
  without an `encoding` is still written as text, so the older protocol is
  unchanged. The twelfth gate watches the *reading*; **nothing watched the
  writing**. Mutation testing proved the gap: seven mutations of the write
  path (the overlay carrying the *number* of bytes instead of the bytes /
  the encoding ignored / base64 decoded into text / an unknown encoding
  accepted / a content check not creating a write obligation / broken base64
  swallowed silently / the plan carrying the encoded text) are **all** caught.
- **The 12th gate `content_check`** - every file check so far looked at the
  *bytes* of a file. That is blind for every format whose payload is not plain
  text: a PDF keeps its words in a **compressed stream**, a `.docx` **is** a zip
  file, an image has no words at all. So a task whose deliverable was a PDF
  could not be judged by a machine at all - the criterion went to the model,
  and a model that says "looks fine" closed a criterion **nobody ever checked**.
  That is a **fake closure**, and it is worse than no closure. The machine
  checks now read **inside** the file: PDF streams are decompressed, Word
  documents are unzipped and stripped of markup, and the format is decided by
  the **magic bytes** - the extension lies, a `.txt` that holds a PDF is a PDF.
  A file whose content cannot be read **fails**; it does not fall back to
  "unjudged", because an unjudged criterion goes back to the model and gets
  closed by a sentence. All **nine** mutations of the content path are caught.
- **The 11th gate `folder_handoff`** - v0.7.67 let a human-written deliverable close
  the loop, but **it only accepted a blob of text**. The tasks this loop actually
  runs write *files*, and the acceptance criteria that matter are file checks
  that read the disk. A human who fixes such a task opens the file and edits it;
  the text handback asked them to paste the file's content into a command line
  instead - a **second copy of the work**, and the copy that fails: forget one
  line and the handoff is rejected for a reason that has nothing to do with the
  answer. The ninth gate watches the *engine*, the tenth watches the *report*;
  **nothing watched the human path**. Mutation testing proved the gap: six
  mutations of the folder handoff (read the wrong folder / drop the file content
  from the deliverable / write a deliverable even when the handoff is refused /
  offer only the first file / drop the deliverable into a second folder / forget
  the folder on the ticket) were caught by **none** of them. The eleventh gate
  now watches that path, and **all six mutations are caught**.
- **The folder is the unit of the offer** - the human edits the files **where
  they are** and hands **that folder** back; the deliverable is built from the
  files, so **nothing is pasted by hand**.
- **Draft semantics** - the judge reads an overlay (the bytes on the disk right
  now) and **nothing is written before the judgement**; a **refused handoff
  changes nothing on the disk** and writes no deliverable - the human's work is
  never thrown away.
- **Only an accepted handoff is written back** - and it lands **in the folder the
  human edited**, not in a second one.
- **The ticket is the entrance** - `mas tickets` prints "edit it here: <folder>"
  and "hand it back: mas handback <id> --workspace "<path>"".
- **The new command `mas handback --workspace <dir>`** - `--file` / `--text`
  keep working exactly as before.

- **The 10th gate `handback_report`** - v0.7.67 let a human-written deliverable
  close the loop, but **the report could not see it**. The ninth gate watches
  the *engine* (can a human's work close the loop?); **nothing watched the
  report layer**. That gap was found by **mutation testing**: six mutations of
  the reporting layer (drop the `closed` flag, rename the decision, stop
  reading the handback scores, go back to "every member run must close",
  forget which attempt closed it, always label the source as the machine)
  were caught by **none** of them. The tenth gate now watches that layer, and
  **all six mutations are caught**.
- **A "source" column in the report** - both the problem overview and the run
  overview now say who closed the loop: `🤝 human` / `🤖 machine`.
- **A deliverable a human wrote gets its own acceptance section** - each
  criterion listed, plus **the human's own note**; **a rejected deliverable is
  reported too** - a failure may not disappear.
- **`mas replay` shows the human too** - the run header prints the source and
  the human note.

**What v0.7.67 adds** (**a deliverable a human wrote can close the loop**):

- **The 9th gate `handback_closes`** - the ticket queue used to have exactly
  one answer: "resume, and let the machine try again". But the case that
  actually happens when a loop escalates is the opposite - **the human has
  already written the answer** and had nowhere to put it. This release adds
  that path, and a gate catches it on the spot.
- **The same acceptance criteria decide it** - the criteria come back from
  the ticket, so the deliverable is judged by the rules the task was written
  with. A criterion with a machine check is decided by that check; **the
  reviewer cannot overrule it.**
- **A rejected handback does not pretend to close** - the failing criteria
  are written back onto the ticket, and the ticket **stays in the human
  queue**. A rejected handback is a second try, not a closure.
- **A dead reviewer does not destroy the work** - the machine checks are
  already decided when the reviewer is called. **The probe caught this on the
  spot**: the catch was narrower than the one in the main loop, so a plain
  exception from a provider lost a deliverable a human had written.
- **The new command `mas handback`** - every open ticket now prints:

  ```
  mas handback T-20261116-075440-8aae --file <your file>
  ```

- **The MCP tool `mas_handback`** - Codex / Claude can hand a deliverable
  back into the loop without leaving the session.

**What v0.7.66 adds** (**a takeover sees the work of the last attempt, and
gets a budget of its own**):

- **The 8th gate `takeover_handover`** - v0.7.65 made the ticket carry the
  context, but **the run that took it over could not use that context at
  all.** This release fixes the takeover itself, and a gate catches it on
  the spot.
- **Four real defects (each measured first, then fixed)**:
  1. **A run that ran out of tokens was filed as "the loop could not close
     itself"** - a budget stop was filed in the `loop_escalation` queue, so
     the human went looking for a stuck worker when the real cause was a
     budget that was too small. **A budget stop is now
     `budget_exhausted`.**
  2. **The takeover could not see the previous deliverable** - a resumed run
     restarts its attempt counter at one and empties its issue list, so the
     worker was asked for the deliverable from nothing. **The previous
     deliverable is now handed to the worker of the takeover.**
  3. **A ticket that stopped on a ceiling stopped there again on resume** -
     **all three ceilings can now be given for that one run**, and they do
     not leak into the next run on the same engine.
  4. **The "continue this" command on screen was a dead end** - **the
     continuation command now comes with a wider ceiling** (and a floor, so
     the widened budget can actually do work).
- **The flags on the command are spelled the way the command line accepts
  them** - a command that is printed has to work when it is pasted:
  `--max-tokens`, not `--max_tokens`. The gate caught this on the spot.
- **Reverse-proofed six times** - the deliverable is hidden, the budget
  override is dropped, the token stop is filed as an escalation again, the
  widening is removed, the takeover sees nothing, the ticket loses its
  budget - **all six were caught**. Fully offline: no endpoint, no key, no
  network.

**What v0.7.65 adds** (**a run that did not close must leave something a
human can take over**):

- **The 7th gate `handover_ticket`** - the engine promised three things: it
  escalates instead of spinning, it opens a ticket, and that ticket carries
  enough context to resume the loop. The first two were easy to see.
  **The third was never kept.** Now a gate catches it.
- **A ticket is not a note, it is a handover form** - the check *uses* the
  ticket: it feeds the ticket's acceptance criteria back into
  `engine.resume()` and the loop has to close for real. **The verifier in
  this check always answers "fail"**, so the closure can only come from the
  machine check that came back on the ticket - the model cannot fake it.
- **A real bug was caught on the spot** - the ticket payload had drifted
  between the two escalation paths. **Fixed at the root**: both paths now
  share one payload builder - **a ticket has one shape**.
- **Reverse-proofed six times** - the ticket loses its criteria, the criteria
  survive only as prose, the ticket forgets the deliverable, the ticket
  forgets the issues, the ticket is marked handled, the ticket is never
  written - **all six were caught**.

**What v0.7.64 adds** (**the parallel winner has to be deterministic**):

- **A sixth gate, `parallel_pick`.** The engine promises that the winner
  is deterministic, but nobody guarded it: a pool that returns its
  results in *completion order*, a tie broken the wrong way round, or a
  ranking that weighs the *text length* above the machine checks would
  all hand over a different deliverable - and the run would still report
  success. **Now the gate catches it on the spot.**
- **The race is staged, not hoped for.** The check chains the three
  candidates with events, so the pool always finishes in the same
  reversed order. Nothing depends on how the scheduler felt that day.
- **The three candidates make the ranking do real work.** `direct` and
  `thorough` pass the same number of checks and are exactly the same
  length, so only the lowest index can separate them; `concise` is the
  longest text and passes nothing. **The winner is ranked, not raced.**
- **The gate was counter-proven four times**: the pool returns results
  in completion order, the tie is broken towards the later index, the
  length outweighs the checks, the winner is whoever finished first -
  **all four were caught**.

**What v0.7.63 adds** (**the installation proves itself**):

- **The install scripts now run the gate themselves.** `install.ps1` and
  `install.sh` execute `mas selftest --iterations 20` when they finish.
  **A broken install shows up right there**, instead of at the user's
  first real task.
- **Two new gates** (both fully offline: no endpoint, no key, no network):
  - `mcp_handshake` - the MCP handshake answers, every advertised tool
    answers, a call to a tool that does not exist is refused, and the
    server is still alive afterwards;
  - `stream_folds` - both event-stream dialects (chat chunks / Responses
    events) fold into the same text, and `total_tokens` survives.
- **The gate was counter-proven four times**: the MCP server stops
  advertising `mas_run`, stops answering `ping`, the SSE fold drops the
  usage, the SSE fold drops a fragment - **all four were caught**.

**What v0.7.62 adds** (**the flaky-bug gate**):

- **Two real intermittent failures now live in CI**: the relay refusing a
  path without draining the request body (v0.7.59), and a billed failing
  call losing its usage. Both were "you only see it if you run it often"
  failures - a one-shot test cannot cover them.
- **New command `mas selftest`** - the method that found them ("hammer one
  path many times and watch the invariant") becomes a command of its own:
  ```bash
  mas selftest                    # 200 rounds
  mas selftest --iterations 500   # 500 rounds
  mas selftest --only relay_refusal
  ```
  It runs fully offline: it starts its own loopback servers, asks for no
  endpoint, no key and no network.
- **The gate was counter-proven.** Three regressions - the relay, the usage
  accounting and the workspace - were introduced one by one, and all three
  were caught. **A gate that never turns red is decoration.**

**What v0.7.61 adds** (**the output no longer scatters**):

- **The output of a run used to vanish.** Before v0.7.60 the files a run wrote
  landed in the system temp folder (`...\Temp\tmpXXXXX\ws\notes\...`) and were
  gone when the process ended, while the deliverable note was saved somewhere
  else entirely (`<state_dir>/artifacts/<run_id>.md`). **Two halves, two places.**
- **Every run now owns a folder**: `<state_dir>/artifacts/<run_id>/`. The
  deliverable is `deliverable.md` inside it, and every file the workers wrote
  sits next to it. **One folder holds everything a run produced - and it
  survives the process.**
- **A workspace you name still wins.** `mas run --workspace ./my-project` keeps
  writing where you pointed. The rule is one line: **where you point is where
  the run works.**
- **File checks use that same folder.** The checks used to read the *configured*
  workspace while the files were written into the temp folder - two different
  places, so the verdict could not match the disk. Now both read and write the
  same place.
- **The ledger and the report remember it.** The ledger gained `workspace` and
  `files`; `mas artifacts` tells you which extra files a run produced.

**What v0.7.60 adds** (**the ledger speaks too**):

- `mas ledger` used to print the engine's own words: `closure   iter=1 pass close`.
  It now prints **`验收 (closure) 第1轮 通过 (pass) 闭环了 (close)`** - the word first,
  the raw name after it: a person can read it and a script can still grep it.

**What v0.7.59 fixes** (**the relay no longer breaks connections at random**):

- After v0.7.58 CI caught an intermittent failure: when the relay refused an unknown
  path it **did not read the request body**. HTTP/1.1 keeps the connection alive, so
  the leftover bytes stayed in the socket and the *next* request started reading in
  the middle of the previous body. The client then saw "connection aborted" instead
  of the 404 it was promised.
- A 200-request stress loop reproduced it about 9 times - a real bug, not a slow
  machine. A refusal (401 / 404) now drains the body first, closes the connection
  explicitly, and two regression tests pin the behaviour.

**What v0.7.58 adds** (**why the loop did not close, in words**):

- **The line that says why the run stopped was still the engine talking
  to itself** --- v0.7.57 made the *verdict* speak plainly, but the one
  line a beginner most needs is the one that says why the run stopped:
  ```
  [FAIL] closure  not closed (status budget_exhausted): budget: reached max_iterations=1
  - unclosed reason: budget: reached max_iterations=1
  ```
  "budget: reached max_iterations=1" says *that* something happened, not
  *what* happened: not that the loop used up its rounds, not how many
  rounds it had, and not what to do about it. A reader who does not know
  what `max_iterations` is learns nothing at all;
- **Every reason the engine can write now reads in plain language, and
  names the command that raises the ceiling**:
  ```
  status: budget ran out (not finished - stopped for you to take over) (budget_exhausted)
  reason: budget ran out: this round was allowed 1 round, and it used them all.
    to raise the round count: mas init --max-iterations <a bigger number>
  ```
- **The reason is read on every screen where a human looks for it** ---
  `mas run`, `mas resume`, `mas tickets`, `mas report` and `mas replay`;
- **The ledger keeps its English** --- the reason is part of the audit
  record, and scripts read it, so the translation happens at the display
  layer only;
- **`mas init` gained the three ceilings the reasons point at** ---
  `--deadline-sec`, `--max-tokens` and `--stagnation-limit`. Before this
  release the reading said "raise the ceiling" and the ceiling had no
  switch;
- **`mas replay` now says each step in words**, not only the exported
  table;
- **Three real bugs fixed**:
  - **the translator translated reason strings the engine never writes**
    --- it knew `budget: exceeded max_tokens=`, while the strings the
    engine *does* write (`budget: tokens {used} >= {limit}` and
    `budget: wall-clock budget exhausted after {n}s`) had no reading at
    all: the two most common ways a run stops were the two it missed;
  - **the comparison direction was reversed** --- `length 48 < 999999`
    became "the body is only 48 characters, which *exceeds* 999999",
    the exact opposite of the truth, so the reader went looking for the
    wrong problem; the byte count had the same bug;
  - **the reason line could not be read on one line** --- the sentence
    and the "raise the ceiling" hint were joined by a line break, so a
    one-line display (the ticket queue) broke apart;
- **31 regression tests**, **twenty-eight layers each broken on their
  own to prove the tests catch them**.

**What v0.7.57 adds** (**the verdict says why, in words**):

- **The verdict line was still the engine talking to itself** --- v0.7.56
  brought your standard back, but the verdict above it stayed in the
  engine's vocabulary:
  ```
  [PASS] c1 (rule) all required strings present
        requirement: the body must contain 「结论」
  ```
  Two lines saying the same thing, in a language you did not write in;
- **Every verdict now reads in plain language, and says where it came
  from**:
  ```
  [PASS] c1 machine check: all required strings are present
        requirement: the body must contain 「结论」
  [FAIL] c1 file check: the file was modified 2026-11-15T11:17:06+00:00,
         which is not later than 2099-01-01T00:00:00+00:00
        requirement: the file out/report.md must be modified after 2099-01-01T00:00:00Z
  ```
- **Every note the engine writes has a reading** --- too short, a missing
  string, a regex that did not match, a sha that differs, a section that
  is not there, a file that is gone;
- **An unrecognised note is left alone, never guessed** --- a note nobody
  predicted may carry information nobody can see, and replacing it would
  throw that away;
- **The ledger keeps its English** --- the notes are part of the audit
  record and scripts read them, so the translation happens at the display
  layer only: a stored run still means what it meant when it was stored;
- **`mas resume` now speaks the same language as `mas run`** --- the same
  person reads both screens in the same session, and they must not
  disagree about what happened;
- **`mas report` says the status and the decision in words** --- "已闭环
  （closed）", "预算用完了（budget_exhausted）" --- and keeps the raw value
  in parentheses, because that is what a reader greps for;
- **The ticket queue names the standard that is blocked**, not the serial
  number the engine made up;
- **Three real gaps in the vocabularies closed** --- the states the engine
  actually writes when a run does not close (`escalated`, `not_closed`,
  `incomplete`, `deliverable_ok_but_not_closed`) and the sources a
  verdict comes from (`model-missing`, `skipped`, `model`) had no words
  at all;
- **32 regression tests, and 23 layers each reverted on its own** --- every
  one of them fails the suite when it is broken.

**What v0.7.56 adds** (**the verdict quotes your own standard back**):

- **You wrote three standards and the verdict said "c1" and "c2"** ---
  `c1` is not one of your standards, it is a serial number the engine
  made up; nothing in that output told you *which* of the standards you
  wrote was judged;
- **Every verdict now carries the standard back in words**:
  ```
    [PASS] c1 (rule) all required strings present
          requirement: the body must contain 「结论」
    [FAIL] c2 (file) mtime 2026-11-15T11:14:06+00:00 is not newer than 2099-01-01T00:00:00+00:00
          requirement: the file out/report.md must be modified after 2099-01-01T00:00:00Z
  ```
- **Three kinds of standard read three different ways**, because they
  are three different things:
  - **a written description wins** --- your own sentence is kept and
    the machine rule is appended, so you can see what will actually be
    checked;
  - **a bare shorthand** (`contains:结论`) is replaced by its
    reading instead of being echoed twice --- the raw text told you
    nothing you did not already know;
  - **an optional standard is marked as optional**, so you can tell
    "this did not block" from "this was never checked";
- **The sentence is stored in the ledger too** --- `mas report` and
  `mas report --json` quote the same standard back;
- **A real defect fixed along the way** --- a file-level check printed
  its argument as a Python dict:
  ```
  file {'path': 'out/report.md', 'value': '2099-01-01T00:00:00Z'} must be modified after {...}
  ```
  The path and the expectation now each get their own place in the
  sentence, and no dict is printed any more;
- **20 regression tests**, **nine layers individually reverted and
  re-proven** --- the matching tests fail every time.

**What v0.7.53 adds** (**"not found" now says "not found"**):

- **Four commands reported a missing run as an empty ledger** --- and
  that is exactly the sentence a beginner meets first; a mistyped run
  id, or a ticket that points at a run from another machine, both land
  on the same untrue sentence;

- **Reproduced on the live machine: the ledger held twenty-two rows**
  --- `mas replay --run-id R-does-not-exist` answered "账本为空，没有可回放的运行。";
  `mas export --run-id R-does-not-exist` printed a table header and
  nothing else; `mas report --run-id R-does-not-exist` wrote "账本为空，
  暂无运行记录。" into a full Markdown report;

- **The commands now say what does exist** --- so the reader can fix the
  typo instead of wondering whether the work ever happened:

  ```
  没有找到运行 R-does-not-exist。
    账本里有这些运行：R-7e4af659ba
    用 mas status 看一眼有哪些运行，用 mas replay --run-id <运行ID> 回放其中一次。
  ```

- **"Not found" and "empty" are different things now** --- an empty
  ledger still says it is empty; a ledger that holds other runs says
  which ones; the two can no longer be confused;

- **The miss speaks JSON too** --- `{"error": "run_not_found",
  "found": false}` with exit code 2, so a script can tell a miss from a
  hit without parsing prose;

- **`mas export --json` used to be a lie** --- it accepted `--json` and
  then ignored it, handing a script a Markdown table; it now returns
  the data (rows, count, and the Markdown rendering), and the text and
  JSON forms are guaranteed to agree;

- **An empty ledger no longer gives you a bare header row** --- it now
  says the ledger is empty and points at `mas run` and `mas demo`;

- **The Linux build is fixed** --- v0.7.52 added a test that asserted
  `Ledger` and `ledger` are the same directory; that is true on Windows
  and false on Linux and macOS, **where they are two different paths**,
  so every local run passed while Linux failed; **the guard was right
  and the test was wrong** --- the test now states the platform's own
  spelling rule instead of assuming one platform;

- 13 regression tests, **eight changes reverted one at a time** to
  confirm the matching test fails.

**What v0.7.52 adds** (**your ledger will not vanish on its own**):

- **The ledger is the only evidence a loop-engineering run leaves
  behind, and it could vanish** --- **nothing crashed, nothing warned,
  it was just gone**; the tool reported an empty history for work that
  had actually happened;

- **Reproduced on the real machine: the user's `~/.mas/config.json`
  pointed into the system temp folder** ---
  `C:\Users\ADMINI~1\AppData\Local\Temp\mas-handover-n2d4xr0z`,
  **a directory `mkdtemp()` had created for one test run**; when the
  operating system cleaned the temp folder, the ledger went with it;

- **Both guards failed at the same time** --- (1) the guard only
  recognised the literal `tmpXXXXXXXX` shape, **while the project's own
  suite used `mas-handover-` / `mas-state-` / `mas-ledger-` prefixes
  and walked straight past it**; (2) it compared strings, **so the same
  directory spelled `ADMINI~1` and `Administrator` stopped matching**;

- **The guard now looks at the random tail, not the word in front of
  it** --- a directory under the temp folder whose name ends in eight
  random characters after a separator is disposable and is not written
  to disk; **a folder you named yourself - `%TEMP%\myproject` - is
  still yours and is kept**;

- **The guard now compares paths, not strings** --- short spellings,
  trailing slashes and letter case all resolve to the same directory;

- **A directory you named is always kept** --- `mas init --state-dir
  <dir>` writes it even when it lives under the temp folder, **because
  a path a person typed is a decision; the guard stops an injection,
  it does not overrule you**;

- **An environment-injected directory is never persisted** ---
  `MAS_STATE_DIR` decides where *this* run writes; it does not become a
  permanent fact about the installation;

- **The test suite can no longer poison the next test** --- modules
  that pointed `MAS_STATE_DIR` at a throw-away directory and forgot to
  undo it made later tests read that rubbish as the user's
  configuration; **the guard now wraps `unittest.TestCase.run()`**, the
  one entry point every test goes through, **so it holds under the
  standard-library runner as well as pytest, even when a subclass
  overrides `setUp`**;

- 22 regression tests, **seven changes reverted one at a time** to
  confirm the matching test fails.

**What v0.7.51 adds** (**the replay talks like a person**):

- **The replay was the last place that still spoke only in runs** ---
  **v0.7.50 taught the report to count problems, but `mas replay` still
  printed a flat ledger**, and the replay is exactly the screen a person
  reads to find out what the loop did and what to do next;

- **Two attempts used to have no visible boundary** --- **two tries at
  one problem were fourteen flat rows**, and "one problem tried twice"
  looked exactly like "two problems tried once";

- **The replay is now grouped by run** --- **one block per run, with
  its status, round count, token spend, and what it was about**;

- **An unfinished run now hands itself to you** --- **the replay used
  to stop at the last line even when a ticket was waiting**; it now
  prints the exact command: `→ 接着干：mas resume T-... --note "..."`;

- **The replay closes with the problems** --- **one line per problem**
  with its state, attempts and cost, not one line per attempt;

- **The report leads with the problems** --- **the run table lists every
  attempt, so a reader who stops there thinks there is more to do than
  there is**; the report now opens with the problem table;

- **`mas replay --json` says the same thing** --- **it now carries
  `runs`, `threads` and `problems_open`, and every step knows its
  `run_id`**;

- 20 regression tests, with **all eight change layers reverted one by
  one to prove the tests fail without them**.

**What v0.7.50 adds** (one problem, one thread):

- **The ledger counts runs; the human queue counts problems - and the
  two used to disagree** - a run that escalates opens a ticket, and
  every resume makes another run on the *same* problem, while the
  status page and the report counted the runs;
- **Measured on the live model** - after one resume the status page said
  "未闭环 2" while the ticket queue held exactly one ticket, so a
  person could not tell "one problem, two attempts" from "two
  problems, one attempt each";
- **The report and the status page now count problems next to runs** -
  two runs and one problem read as exactly that;
- **The ticket carries the whole bill** - the cumulative rounds spent
  on the problem, not just the last attempt's; a problem that burned
  fifteen rounds used to say "已尝试 5 轮";
- **The ticket names the runs it covers**, so the queue is a real join
  between the human side and the ledger;
- **The ledger books the thread** - every resumed run records the
  ticket it belongs to, including the one that finally closed;
- **A resolved ticket points at the run that closed it**, so the
  deliverable the human actually got sits next to the failed history;
- **20 regression tests**, with **eight layers each individually rolled
  back and re-proven** - the matching test fails every time.

**What v0.7.49 adds** (an unfinished run always leaves a ticket):

- **Only the "escalate" ending opened a ticket.** Every other
  unfinished ending left *nothing* behind: a run that stopped on the
  iteration ceiling, that ran out of wall-clock time, that ran out of
  tokens, or that died on a provider error.
- **Measured on the live model.** A run that hit `max_iterations`
  ended as `budget_exhausted` with the deliverable on disk and **no
  ticket**. The status page said "未闭环 1" and the human queue was
  empty - the loop reported an unfinished job and offered nobody a way
  to finish it. That was the one missing link: escalate -> handle it
  by hand -> resume had no entry point for the run that ran out of
  road.
- **Every ending that is not "closed" now opens a ticket** - the run
  that stops on the ceiling and the run that dies on the backend both
  leave something a human can pick up.
- **The ticket says which kind of failure it was**: a
  `budget_exhausted` ticket or a `run_error` ticket. "Raise the
  budget" and "fix the endpoint" are different human reactions.
- **The ticket carries the reason, the goal, the criteria and the last
  deliverable** - enough to decide whether to resume without
  re-running anything.
- **A closed run still leaves no ticket**, and a resumed run keeps
  the ticket it already has.
- 14 new regression tests; a seven-layer falsification framework
  (AA~GG) - every layer, reverted on its own, is caught by the tests.

**What v0.7.48 adds** (a ticket explains itself):

- **A ticket used to be a serial number.** `mas tickets` printed one
  line per ticket - an id, a state, a kind and a title - and a person
  could not tell what was blocked, why the loop gave up, or which
  command continues it.
- **Every ticket now carries its own manual:** the task, the run, the
  check that failed, the reason the loop stopped, how many rounds it
  spent, the size of the last deliverable, the human note, and the
  exact `mas resume` command.
- **An empty command no longer looks broken.** `mas ledger` used to
  print *nothing at all* on a fresh install. It now says the ledger is
  empty, names the ledger file and shows the command that fills it.
  `mas tickets` does the same and points at `mas demo`.
- **The ledger says where it is** and how many entries it holds.
- **Resolved tickets are listed separately**, with the decision and
  the note the human left - and without a resume hint, because a
  resolved ticket must not tell you to run it again.
- **The status page names the problem, not the number.** Three waiting
  escalations look identical when only their ids are shown; the
  ticket line now carries the task next to the id.
- 23 new regression tests; a nine-layer falsification framework
  (AA~II) - every layer, reverted on its own, is caught by the tests.

**What v0.7.47 adds** (`mas status` talks to a human):

- **The status page used to be a JSON dump.** Fine for a machine,
  useless for the beginner this project is written for: it said
  "entries: 27" and never said "you have 2 deliverables and 1 ticket
  waiting for you". A status page a person cannot read is not a status
  page.
- **It is now a short plain-text page**: provider and model, state
  directory, ledger size, how many runs happened and how many closed,
  the deliverables with their paths, the tickets waiting for a human
  (each with the exact `mas resume` command), and the budget.
- **The JSON payload grew the same sections** (``runs`` and
  ``artifacts``), and the old ones stayed.
- Eleven new regression tests, seven layers counter-proven by reverting
  each on its own.

**What v0.7.46 adds** (the ledger knows what each run was about):

- **The ledger recorded everything about a run except the one thing a
  human needs.** Measured against the live model: the ledger held no
  trace of the task. A replay could say "a run closed, 241 characters,
  sha fe8848013b" and the reader had no way to learn whether it was the
  onboarding guide or the Q3 report. The mock backend had hidden this,
  because its planner summary happens to echo the goal.
- **The run's end entry now books ``goal`` and ``title``**, the summary
  reads them back, the report's overview table gained a "任务" column,
  and every run section opens with "- 任务：<goal>".
- **``mas artifacts`` names the task too** - the listing shows the goal
  above the path and the JSON rows carry ``title`` and ``goal``.
- Eleven new regression tests, seven layers counter-proven by reverting
  each on its own.

**What v0.7.45 adds** (the user can find the deliverable):

- **The deliverable survived, but nobody could find it.** v0.7.44 made the
  artifact outlive the process, but the CLI printed a 1500-character
  *preview* and the ledger path - never the path of the file a human can
  open - and nothing could list the deliverables of previous runs. A
  beginner who came back a week later could not even ask "what did I
  actually get?"
- **`mas run` and `mas resume` name the file.** Both print
  `交付物：<path>` under the ledger line. `resume` matters most: a
  resumed loop is the expected path for an escalated run, so its result
  was the one most likely to be lost.
- **New `mas artifacts` subcommand**: lists every deliverable this
  machine produced - one line each, with the run id, status, character
  count, number of rounds, path, and whether the file is still there.
  `--run-id` narrows it to one run.
- **`--json` gives the same rows as data**, and a deleted deliverable
  is called out ("缺失") instead of being silently dropped.
- Fifteen new regression tests, six layers counter-proven by reverting
  each on its own.

**What v0.7.44 adds** (the deliverable actually survives the process):

- **A closed loop left no deliverable behind.** The artifact lived in
  the engine's memory and nowhere else: the ledger booked its hash, the
  report printed its hash, and the moment the process ended the work was
  gone. A beginner who saw a green light and closed the terminal had a
  verdict and no deliverable. Every run now saves its final artifact to
  `<state_dir>/artifacts/<run_id>.md`, next to the ledger, named after
  the run.
- **The path is booked on the run's end entry** (`artifact_path` and
  `artifact_chars`), so a replay can find the work, and `mas run --json`
  carries it too.
- **The report names the file** - "交付物已保存：`<path>`（N 字，可直接打开
  取用）" - because a hash is a fingerprint, not something a human can
  open.
- **A failed write does not take the run down**: the work is done and the
  artifact is in memory, so a lost file must not be reported as a crash
  of the loop itself.
- Nine new regression tests, six layers counter-proven by reverting each
  on its own.

**What v0.7.43 adds** (the report shows the loop, not only its last turn):

- **Only the last round was shown.** The engine writes one closure row
  per iteration and one reflector row per failed iteration. The report
  read only the last closure row, so a run that revised itself three
  times showed a single "PASS" and the two rounds that came before it
  were nowhere. The report now renders "循环过程（共 N 轮）" - one line
  per round, with the verdict, the decision, how many criteria passed,
  how many were never judged, the issues and the hash of that round's
  deliverable. A run that closed on the first attempt says so plainly.
- **The reflection is visible.** The reflector is the role that says why
  the next attempt must change, and the report never mentioned it. Each
  round now carries the reflection that produced it, and the summary
  gained a `reflections` list.
- **The replay's reflector row is no longer blank.** `_short_detail` had
  no branch for it, so the one line carrying the loop's own reasoning
  was empty.
- Twelve new regression tests, seven layers counter-proven by reverting
  each on its own.

**What v0.7.42 adds** (an unjudged criterion is no longer reported as a failed one):

- **A criterion nobody judged was rendered as `[FAIL]`.** The engine
  keeps three states per acceptance criterion - passed, failed, and
  *unjudged* (no machine check, no reviewer verdict, no lead verdict) -
  and it treats the third one carefully: an unjudged criterion never
  passes and never closes the loop. The report collapsed the three into
  two at the last step, so a criterion that was never checked showed up
  under "failed". The failed lists now use `pass is False` - the only
  thing that means "judged and rejected".
- **"The deliverable failed" was a false claim.** A run whose
  deliverable was never fully checked was rendered as a clean
  rejection. The report now says so and names the criteria.
- **The Markdown has a third mark now.** `UNDECIDED` renders for a
  criterion nobody could judge, and the report lists the unjudged
  criteria with the source of the verdict.
- **The JSON report tells them apart too.** `run_summary` gained an
  `unjudged` key next to `failed` and `optional_failed`.
- Eight new regression tests, every layer counter-proven by reverting
  it on its own.

**What v0.7.41 adds** (the last link of the loop is connected now):

- **The ticket number never entered the ledger.** When a run escalates,
  the engine opens a ticket that holds the deliverable, the criteria
  and the history, and `mas resume` picks the work up from there - but
  the ticket number was never written to the ledger, so the report
  could say nothing beyond "escalated" and a human reading it had no
  way to find the ticket that held the work. The ticket number is now
  booked on the run's end entry.
- **The report can point at the ticket and say what to do with it.**
  The summary now carries the ticket number, and the rendered report
  names it together with the next step: `mas tickets` to look at it,
  `mas resume <ticket>` to continue from there. An escalated run now
  tells the human where to act, not merely that something went wrong.
- Four new tests cover the chain, and every layer was counter-proven
  by reverting it on its own and confirming the matching test fails.

**What v0.7.40 adds** (the reason a run did not close no longer gets
lost):

- **The report never said why the run did not close.** The engine
  books the reason on the run's end entry, and since v0.7.37 it names
  the cause of a stagnation escalation precisely - "the deliverable did
  not change for N consecutive attempts" - but the report read the
  entry for the status and dropped the reason on the floor. The
  rendered report now carries it as "未闭合原因", and an escalation
  whose reason the ledger does not record says so instead of staying
  mute.
- **The stagnation count was thrown away.** The engine counts how many
  consecutive attempts produced the *same* deliverable - that count is
  what tells "the worker is stuck" apart from "the worker needs
  another round". The report now reports it, states it in words, and
  shows it in the overview table next to every run, so a reader
  scanning the table can see which run was stuck.
- Six new tests cover the projection, and every layer was counter-proven
  by reverting it on its own and confirming the matching test fails -
  the overview table initially had no coverage at all, and the
  counter-proof for it caught that gap before the release.

**What v0.7.39 adds** ("not closed" is no longer a vague sentence):

- **A good deliverable was reported as "not closed".** The engine
  already answers two questions separately - *did the deliverable pass
  every check* and *did the run close* - and books the first answer on
  the run's end entry. The report threw that answer away. A real run
  against a live endpoint produced a deliverable that passed every
  acceptance criterion and then stopped on the stagnation ceiling: the
  report said "not closed", as if the work itself were wrong, and the
  user had to guess which half of the system had failed. Such a run is
  now reported as `deliverable_ok_but_not_closed` - a distinguishable
  state, not a third word for "unfinished".
- **The Markdown said nothing about which half failed.** It now says
  it in words: the deliverable passed every criterion but the run did
  not close (budget or deadline), and the artifact is usable as is.
- **"The run never said" is not "the deliverable was bad".** A run
  with no end entry did not know how its deliverable fared, and the
  report now reports `deliverable_ok: None` for it rather than a
  confident `False`.
- Five new tests cover the projection, and every layer was
  counter-proven by reverting it on its own and confirming the
  matching test fails.

**What v0.7.38 adds** (the role that was supposed to close the loop is
actually wired up now):

- **The `lead` role had a contract but no wiring.** `roles.py`
  described a lead agent, `providers.py` had a mock branch for it, the
  README and the skill both listed it as the role that closes the loop -
  and the engine never called it. The outcome was the line
  `decision = "close" if passed else "revise"`. The lead is now
  consulted exactly when there is something for it to decide: some
  acceptance criteria carry no machine check and no reviewer verdict
  either. It receives those criteria and the deliverable, and answers
  with `{"decision", "reason", "judged": [{"id", "met", "note"}]}`.
- **The lead is an advisor, not a judge of facts.** A machine check
  that failed is a fact: the lead is not consulted at all in that case,
  and when it is consulted it may only fill a gap - a criterion the
  machine checks already decided is left exactly as the check decided
  it, whatever the lead says. The engine keeps the final word over
  stagnation, budget and deadline, so a lead cannot close a run that
  has run out of budget.
- **A lead that shrugs closes nothing.** When the lead's `met` field
  cannot be read as a boolean, the criterion stays unjudged and the
  loop keeps asking - the same rule that governs an unreadable
  reviewer answer, applied to the advisor. A broken lead (an
  unreachable endpoint or a garbled reply) leaves the criteria
  unjudged and the run continues as it did before.
- **The lead's call was not in the book.** The lead is a paid call,
  but its role was missing from the ledger's charge list, so every run
  the lead was consulted in under-reported its spend. The role is now
  charged, and the ledger's total equals the run's reported token
  usage.
- Five new engine tests cover the wiring, and every layer of the
  change was counter-proven by reverting it on its own and confirming
  the matching test fails.

**What v0.7.37 adds** (an answer nobody can read can no longer get
past the loop):

- **A score is not a verdict.** A reviewer that answered `"pass": 0.5` -
  "half of this criterion is satisfied" - was converted with
  `bool(value)`, so 0.5 became `True`: the criterion was booked as a
  full pass and the loop closed on it. A number is now a verdict only
  when it is exactly `0` or `1`. Everything else is *unreadable*, and an
  unreadable answer is no answer.
- **An unreadable answer was quietly booked as a failure.** The
  normaliser folds an unreadable verdict into `pass: False`, and the
  engine could not tell that apart from a reviewer who actively said
  "no". The criterion therefore looked *judged*, the engine never
  asked the reviewer again, and the loop burned an iteration on a
  revision that could not help. The unreadable marker is now read on
  the way in: the criterion stays unjudged, the follow-up question is
  asked, and the answer that comes back is the one the engine decides
  on.
- **The panel path treated a garbled reply differently from the single
  reviewer path.** The multi-reviewer aggregate dropped the marker, so
  the same bad answer was re-asked with one reviewer configured and
  booked as a decided failure with three. The aggregate now carries the
  marker and both paths behave the same way.
- **The alternative verdict shape was never held to the same rule.**
  `normalize_verdict` has two paths - `{"scores": [...]}` and
  `{"criteria": [...]}`. v0.7.12 made the summary obey the
  per-criterion answers on the canonical path and left the alternative
  path alone, so a reviewer that wrote `FAIL` on every criterion while
  putting `"verdict": "pass"` on top still produced a green summary.
  Both paths now agree.
- **A summary cannot light up an unreadable review.** When nothing in
  the scores could be read, the model's own summary was kept, so
  `{"verdict": "pass", "scores": [{"pass": "maybe"}]}` reported a pass
  over a criterion nobody judged. Nothing was judged, so nothing
  passed.
- **The iteration count in a report was sometimes `null`.** The
  expression `a or b if rows else 0` parses as `(a or b) if rows else
  0`, so a run whose rows carry no iteration field reported `null`
  instead of a count. The count is now resolved from the run's own end
  entry, then the closure row, then the highest iteration number any
  row mentions - and it is always a number.
- **An escalation that did not say why.** A run that stopped because
  the deliverable stopped changing escalated with the reason "the loop
  could not close itself", which sends the human looking for the wrong
  thing. It now names the cause: the artifact did not change for N
  consecutive attempts.
- 11 regression tests, and every layer above was counter-proved: reverted
  in isolation, the matching test was confirmed to fail.

**What v0.7.36 adds** (the deadline is now a real ceiling, and the
self-check no longer sits silent for minutes):

- **`deadline_sec` was not a ceiling, it was a sentence in the docs.**
  The wall-clock budget was only checked *between* iterations. A single
  provider call that overran the budget - a slow endpoint, a long
  answer - was allowed to finish, and the run still reported a
  successful closed loop. Measured: a 1 second deadline against a 3
  second provider returned after 9.0 seconds and reported success; a
  real run with a 180 second deadline returned after 185.26 seconds and
  reported success. The number in your configuration was documentation,
  not enforcement.
  Every provider call now carries the remaining wall-clock budget as a
  hard ceiling. The HTTP layer takes the smaller of the configured
  timeout and the remaining budget, recomputes it on every retry, and
  raises a dedicated `BudgetExceeded` when the budget is gone - a
  deliberate stop, not a broken endpoint, and it is reported as such.
  The closure decision itself is checked against the clock: a
  deliverable that passes every machine check but arrives after the
  deadline is escalated, not closed, with the reason spelled out. A
  more subtle bug went with it: the engine used to return
  unconditionally once the checks passed, so an `escalate` verdict it
  had decided was thrown away.
- **The self-check sat silent for minutes.** A measured real run took
  460.5 seconds with no output at all, which looks exactly like a hung
  process. `mas verify` now announces every slow step before it
  happens - the endpoint handshake, the instruction probe, the closed
  loop - and reports the elapsed time when the loop ends. The progress
  goes to stderr, so the `--json` contract stays machine readable.
- **The self-check judged a configuration the user never ran.** The
  verify run overrode the user's own wall-clock budget with a hardcoded
  180 seconds. The measured real run took 342 seconds against the
  user's 900 second budget and was reported as "not closed" - the check
  failed a configuration that would have run fine. The self-check now
  uses the budget the user actually configured (capped at 900 seconds so
  it cannot sit silent for a quarter of an hour either).
- **"Not closed" hid which half of the system failed.** A run whose
  deliverable passed every machine check but which overran its budget
  was reported exactly like a run whose deliverable was garbage. The
  user went looking for a broken answer that did not exist while the
  real problem - the clock - stayed unnamed. The engine now tracks the
  two facts separately: `deliverable_ok` says whether the artifact
  passed the checks, `status` says whether the run closed, and the
  self-check says which one it was: "the deliverable passed every
  machine check, but the run exceeded its time budget" names the clock
  and tells the user what to change.
- The ceiling travels to the provider through a thread-local call
  context instead of a new keyword argument on `Provider.complete`, so
  every existing custom provider and plugin keeps working unchanged.
  Parallel workers inherit it explicitly, because a thread pool does
  not inherit thread-local state.

**What v0.7.35 adds** (the self-check now diagnoses *your* configuration
instead of an illusion):

- **The endpoint handshake ran without the key and reported 401.**
  v0.7.34 made the provider read its credential from the environment
  variable named by `api_key_env` - the same contract the wizard writes
  to disk, where only the *name* is stored so that no secret ever hits
  the disk. The handshake did not follow: `probe_from_config` still read
  only the literal `api_key` field, and that field is deliberately
  empty. Against an endpoint that requires authentication the handshake
  therefore went out with an empty `Authorization` header and came back
  `401` - while every real request of the very same run carried the key
  and worked. The beginner was told to fix a setup that was already
  correct, and `mas doctor` and `mas verify` disagreed about the same
  machine. The handshake now resolves its credential exactly like the
  runtime does, so the provider check, the instruction check and the
  endpoint check all speak the same dialect with the same credentials.
- Reproduced against a local server that rejects unauthenticated
  requests: before the fix the endpoint line reported `HTTP 401` while
  the server logged the key on every other request of the same run;
  after the fix the endpoint line reports the endpoint as reachable and
  the closed loop actually closes.

**What v0.7.34 adds** (the self-check tells the truth - and the key you
set actually reaches the model):

- **The key you set was never sent.** The setup wizard tells you to put
  it into `OPENAI_API_KEY` (or `ANTHROPIC_API_KEY`, or any name you
  choose) and records only the *name* in the configuration, so that no
  secret is ever written to disk. That is right. But the provider
  builder only ever read the literal `api_key` field of the
  configuration - and that field is deliberately empty. A user who had
  configured everything exactly as documented talked to the endpoint
  with an empty `Authorization` header and got 401s. The key is now
  resolved the way the configuration always promised, and the
  resolution is covered by a regression test.
- **A fallback backend inherited the primary backend's secret.** A
  fallback that declared its own key variable still received the
  primary's literal key. Sending the primary's credential to a
  different endpoint is both wrong and a leak; a fallback that names
  its own credential drops the inherited one now.
- **The provider line of `mas verify` was a rubber stamp.** It printed
  the configured strings and reported "ok" whenever they *looked*
  plausible - no network call, no key lookup at all. A user with a dead
  endpoint got a green provider line and a red closure line, with
  nothing in between naming the real cause. The provider line now
  states the endpoint and whether the key was found, and two real
  checks sit next to it: an endpoint handshake (the same one `mas
  doctor` uses, so both commands agree on what "reachable" means) and
  an instruction check that proves the system instruction really
  reaches the model. A dead endpoint now says so by name.
- The mock backend stays completely offline: no handshake, no
  instruction probe, no network call at all.
- Ten regression tests. Verified: `mas verify` against the local real
  endpoint -> handshake ok, instruction ok, closure ok, five ledger
  entries; against a dead port -> the endpoint line names the
  connection failure.

**What v0.7.33 adds** (making the replay readable - and killing the
"None puzzle" of the default configuration):

- **The closure row of a closed run replayed as a blank line.** That row
  is the one that says what was judged and how it came out, but its
  summary was built from the issues alone - and a closed run has none.
  The successful case, the one anybody replays to see what a good run
  looks like, showed an empty cell. The summary now names the verdict,
  the decision, how many criteria passed and which ones failed.
- **The single-reviewer mode reported "usable: None, skipped: None,
  missing: None".** The one-reviewer path returned the raw verdict and
  never went through the panel aggregation, so neither the ledger nor
  the report could say whether the one reviewer had answered. A panel of
  one is described like a panel of one now.
- **A silent reviewer was not counted as a missing vote.** In the
  single-reviewer mode a reviewer that produced nothing to judge left
  no trace; it is counted as skipped and missing now, exactly as in a
  panel of several.
- Fourteen regression tests. Verified: reverting each fix one at a time
  -> both reverts are caught by the tests. A real run against the local
  bridge closes the loop, every row of the replay has content, and the
  single reviewer reads "usable=1 skipped=0 missing=0".

**What v0.7.32 adds** (the report now states the status the run actually
ended with):

- The engine never wrote its final status into the ledger, so the report
  had to *guess* it from the closure rows - and the guess was wrong for
  every run that did not end on a decision.
- A run that stopped on the budget ceiling was reported as "not closed",
  which reads as "merely unfinished"; it now says `budget_exhausted`.
- A run that died on a provider error was reported as "incomplete" with
  no reason attached; it now says `error` and shows the reason.
- The artifact of a failed run was invisible: the hash lived on the
  closure row, and a run that failed before it was ever scored has no
  closure row at all, so the report showed an empty artifact.
- Every run now ends with a `run_end` ledger entry carrying the status,
  the decision, the error, the iteration count, the artifact hash, the
  unresolved issues and the total spend. The report reads that entry
  instead of guessing, and the raw ledger table names the ending of
  every run. Ledgers written by older versions still read correctly.
- Fifteen regression tests. Verified: reverting each fix one at a time
  -> all five reverts are caught by the tests. A real run against the
  local bridge closes the loop, the report states the same status as
  the run, and engine = ledger = report agree on the spend to the token.

**What v0.7.31 adds** (making the ledger answer "how much did this run
spend" on its own):

- **The ledger could not answer "how much did this cost" at all.** The
  figure only lived in the engine's memory: it was not in the ledger and
  it was not in the replay report. Every billed call is booked in the
  ledger now, and the report shows the spend of every run and of the
  report as a whole.
- **The single worker path wrote no ledger entry at all.** The parallel
  candidate pool booked its own spend; the single worker did not, so the
  money the worker spent was nowhere in the book. It is booked under the
  same role now.
- **The reflector was never booked.** The reflector runs after every
  failed iteration and it is a paid call, but the ledger never mentioned
  it. A run that revised itself again and again never said what the
  reflection cost. It is booked now.
- **Only the last review round was booked.** The first round and the
  follow-up question are two separate paid calls; they are booked
  separately now, and a token is never counted twice.
- Fourteen regression tests. Verified: reverting each fix one at a time
  makes the corresponding test fail. The invariant sweep covers 45 pool
  shapes and checks that the endpoint's bill, the engine's total and the
  ledger's sum agree to the token.

**What v0.7.30 adds** (fixing two defects in the follow-up question - the
panel mode and the single reviewer mode behaved differently):

- **A run that closed on the follow-up answer was audited as a run whose
  review had judged nothing.** When a reviewer did not judge a criterion,
  the engine asks once more - a cheap question rather than a whole new
  iteration. But in the single reviewer mode the ledger kept the *old*
  verdict after the re-ask, so the book showed a review with no scores
  at all next to a closed run. The panel mode already carried the re-ask
  forward; this mode does the same now - the ledger describes the panel
  the engine actually decided on.
- **A failed follow-up question ended the whole run.** The re-ask is a
  supplementary question - if it fails, the criterion stays unjudged and
  the loop handles it like any other unresolved criterion. The panel mode
  already degraded that way; the single reviewer mode let the error
  escape, so the same missing verdict crashed one setup and merely slowed
  the other down. Both modes now behave the same. The failed call is
  still charged (the endpoint billed it) and recorded in the ledger.
- Six regression tests. Verified: reverting each fix one at a time makes
  the corresponding test fail (three and two respectively).

**What v0.7.29 adds** (fixing three "a failed call was not charged" defects):

- **A failed candidate was charged only if it happened to fail before the
  winner.** The spend of the failed pool members was flushed when the winner
  was reached, so a candidate that failed *after* the winner in the list was
  never charged. The same pool cost a different amount depending on which
  member happened to finish first - and a budget that depends on the race is
  not a budget. The spend is now collected in the same pre-pass as the
  truncation and charged once, after the ranking, so the total no longer
  depends on the order.
- **A discarded pool did not charge the member that failed.** When a
  truncation invalidated the whole pool, the truncated member and the ones
  that had finished were charged - the one that failed outright was not.
  Every member of a pool that is thrown away was billed, and all of them are
  charged now.
- **A discarded review round did not charge the reviewer that failed.** The
  same rule for the review panel: when a truncation threw the round away,
  the reviewer that failed outright was left out of the bill. It is charged
  now, in the first review round and in the re-ask round.
- **A failed call that was billed did not say so.** `ProviderError` now
  carries the usage of the call that failed, so a broken response shape, a
  stream that died halfway, or a failover chain that fell over to the next
  backend reaches the engine with its cost attached. A run that died on a
  provider error used to report a spend of zero for work it had already paid
  for.
- Five regression tests. Verified: reverting each fix one at a time makes
  the corresponding test fail. The invariant sweep now covers 50 pool
  shapes - from one worker to four workers with three reviewers, across
  clean runs, truncated answers, failed calls and mixtures of both - and
  every one of them books exactly what the endpoint charged.

**What v0.7.28 adds** (fixing three "the budget ledger lied" defects and one
"the loop could never close" defect):

- **A discarded candidate pool was charged as if it were free.** When one
  candidate in a parallel pool was cut off by the output limit, the whole
  pool was discarded and retried - but only the *truncated* candidate was
  charged. The candidates that had already finished cost nothing. Measured
  on the real endpoint: the ledger reported 6,000 tokens for a run that had
  actually spent 16,010. The token ceiling was systematically too generous,
  so the budget was not a budget. The pool now charges every candidate,
  finished or cut off - the number on the ledger is the endpoint's bill.
- **The planner and the reflector were never charged at all.** Both are paid
  calls: the planner on the first iteration, the reflector after every
  failed one. Measured: a run that planned, wrote, failed, reflected and
  reviewed reported 922 tokens for 1,033 tokens of real spend. Both are
  charged now, and the ledger shows what the planner call cost.
- **A reviewer that ran out of output room was counted as an absent
  voter.** A truncated reviewer is not a reviewer who disagreed - it is a
  reviewer that never finished. The strict panel counted it as a missing
  vote, so the loop could never close: every iteration burned the whole
  budget and the run ended in an escalation for a reason that was one more
  retry away. A truncation in the review panel is now a retryable failure
  with a bigger output budget on the next attempt - the same treatment the
  worker path already got. The re-ask round follows the same rule.
- Ten regression tests. Verified: reverting each fix one at a time makes
  the corresponding test fail.

**What v0.7.27 adds** (fixing three "the loop closed wrong" defects):

- **A list-shaped file check could never close the loop.** When the engine
  told the worker which files to write, it parsed the check argument with
  *its own* parser - while the machine check used a *different* one. The two
  disagreed on the list form: the check saw `notes/a.txt`, the engine saw
  the literal text `['notes/a.txt']`. The worker was then asked to write a
  file with brackets in its name while the check looked at another path -
  and the loop could never close, no matter how many iterations it ran. The
  engine now reads the path with the check's own parser, so the two can
  never disagree.
- **A recovered run reported the previous iteration's error.** A run that
  was truncated, retried and then closed had `status` `closed` and `error`
  `truncated answer: ...`. The CLI printed it as an explanation, which
  reads like a failed run. A transient failure now belongs to the
  iteration it happened in: closing clears it, while the ledger keeps its
  `truncated` entry - the audit trail loses nothing.
- **The token ceiling was not a ceiling.** The budget was only checked at
  the *end* of an iteration, and a truncation retry happens *before* that.
  A run that kept hitting the output limit could therefore spend far past
  the ceiling the user set (measured: ceiling 200,000, spent 240,000). The
  ceiling is now also checked at the top of every iteration, so it is
  really a ceiling.
- Seven regression tests. Verified: with each fix reverted, 2 + 2 + 1
  tests fail.

**What v0.7.26 adds** (fixing "when it broke, the ledger had no record"):

- **An unexpected error erased the audit trail.** The engine caught
  `ProviderError` and nothing else, so any other exception raised inside
  the loop escaped `run()` entirely - and a run that ends without a
  ledger entry is a run that cannot be audited. The ledger showed a run
  that started and never ended: no error entry, no closure record, no
  ticket. The closure state of that run was simply unknowable.
- **The engine was not a safe library entry point.** The CLI, the MCP tool
  and the HTTP route each have their own exception boundary, so the user
  saw a clean error message. But the engine is also called directly - by
  a library user, by a script, by anything embedding the runtime - and
  there it raised a bare traceback.
- The loop now catches everything that is not a `ProviderError`, records
  it in the ledger as an `internal` error and returns a normal `error`
  result. The distinction is kept: a provider failure still reads
  `provider error`, and the criterion validation still raises its
  `ValueError` before the loop starts, because a broken rule must never
  look like a closed run.
- Seven regression tests. Verified: with the containment removed six of
  the seven fail.

**What v0.7.25 adds** (fixing "the budget ledger was lying"):

- **A truncated call was free.** The endpoint had already generated the
  tokens and been billed for them before it stopped. The exception
  carried the answer but not the usage, so the engine charged the call
  nothing. A run that truncated five times looked five times cheaper
  than it was, and the budget ceiling was effectively five times too
  generous.
- **The parallel pool under-reported by a factor of N.** Three workers
  meant three truncations and three sets of tokens, but only the single
  re-raised truncation was counted - the other two candidates' tokens
  vanished. The whole pool's spend is now collected before the
  truncation is raised, so the number in the ledger is the number the
  endpoint billed.
- `TruncatedAnswer` now carries the usage of the call that produced it,
  and the engine charges it to the budget. The ledger's `truncated`
  entry reports the tokens that were spent.
- Two regression tests: the truncated call is charged to the budget, and
  every truncated candidate of a parallel pool is charged. Verified:
  with the charging removed the parallel test reports 0 tokens instead
  of the expected 2400.

**What v0.7.24 adds** (fixing "the answer was cut off and the loop
closed anyway"):

- **A cut off answer was delivered as a finished one.** The endpoint's
  `finish_reason` was captured, put into the raw payload and then never
  read again - so the one signal that says *this answer is incomplete*
  was silently dropped.
- **A truncated answer could close the loop.** A model that stops at the
  output token limit produces a *partial* answer, and a partial answer
  can satisfy the acceptance checks by accident: the searched string may
  already be in the first paragraph, and the fragment may already be long
  enough for `min_chars`. The loop then closed on the fragment, reported
  `closed` and handed a half-written deliverable to the user. This is the
  same family of false closure this project exists to prevent, and the
  one most likely to happen in practice.
- **The output budget was never raised.** The provider default of 4000
  output tokens was used for every call and never increased, so a
  deliverable that does not fit was always cut off - and always silently
  accepted.
- **A truncated answer is now a retryable iteration failure.** The
  providers raise `TruncatedAnswer` instead of returning an incomplete
  answer, the engine records the truncation in the ledger and retries the
  iteration. The partial answer is discarded on purpose: a fragment that
  happens to satisfy a check must not become the deliverable.
- **The retry gets more room.** The output budget doubles with every
  attempt (8000, 16000, 32000 - capped), so the loop gets a real chance
  to finish. The budget is configurable with `output_max_tokens`.
- **The parallel path behaves the same.** A truncated candidate is not
  counted as a lost angle: the whole pool is retried, so the loop never
  ranks a pool of fragments and picks the longest one.
- Thirteen regression tests (eight on the provider side, five on the
  engine side). Verified: with the detection removed one provider test
  fails, and with the engine retry removed all five engine tests fail.

**What v0.7.23 adds** (fixing two more ways the scratchpad leaked out):

- **An unclosed reasoning block was delivered whole.** When a model
  runs out of tokens in the middle of its scratchpad, the closing tag
  never arrives - the answer is a `<think>` that never ends. The old
  stripper matched only `<think>...</think>`, so the whole truncated
  scratchpad was delivered as the artifact, counted in the length tie
  break, and written into the workspace files. An opening tag that is
  never closed now drops everything from the tag on: there is no
  deliverable behind an unclosed one.
- **The tag is spelled differently on other gateways.** `<think>` and
  `<thinking>` were already handled; `<reasoning>`, `<scratchpad>` and
  `<reflection>` were not, so on those endpoints the scratchpad reached
  the deliverable unchanged. All five spellings are recognised now, both
  closed and unclosed.
- The closed case is still handled first, so a closed block is never
  mistaken for an opening one and the real answer behind it survives.
- Nine regression tests cover the unclosed block, every tag spelling,
  the closed/unclosed interaction and the plain text case. Verified:
  with the unclosed rule removed two tests fail, and with the extra
  spellings removed three more fail.

**What v0.7.22 adds** (fixing the defect that delivered the model's scratchpad):

- **The parallel path forgot to strip the reasoning block.** Models wrap
  their scratchpad in `<think>...</think>`. The single worker path has
  always stripped it; **the parallel path did not**. The result: the
  scratchpad was **delivered as the artifact**, counted in the **length
  tie-break** (a candidate with a longer scratchpad won on length), and
  **written into the files** in the workspace.
- **Both paths now deliver exactly the same content.** The parallel path
  strips the reasoning block *before* the ranking, and that order
  matters: stripping earlier keeps the scratchpad out of the tie-break
  and out of the text level checks (`min_chars`, `contains`).
- The regression tests assert that the parallel artifact carries no
  reasoning block, that the single and the parallel runs deliver
  identical content, and that the reasoning length cannot decide the
  tie-break. Verified: with the strip removed the candidate length goes
  from 34 to 369 and the test fails immediately.

**What v0.7.21 adds** (fixing the defect that made parallel mode pick the
wrong candidate):

- **The candidate ranking read the *old* contents of the workspace.**
  The file level checks read the real disk, and at ranking time no
  candidate had written its file yet - so **every candidate scored zero
  on the file rules**, and the ranking degenerated into "pick by length":
  a candidate that would write a file violating the rule could win the
  race, and even a candidate whose *rival* had written the correct file
  could be discarded. The single worker path was never affected: it
  writes first and checks second, in the right order. Only the parallel
  path checked before it wrote.
- **A candidate is now scored against the file it would produce.** Its
  write blocks are parsed into a *plan* without touching the disk, and
  the file checks read that plan through an **overlay**. The winner is
  then written to disk as before - and only the winner. The disk state
  during the ranking is never modified, so the candidates cannot see
  each other's output.
- Two new building blocks: `mas.filewrite.plan_writes` and the
  `overlay` argument of `mas.filechecks.evaluate_file_check`. An overlay
  is a plain mapping from absolute path to bytes; it changes what a
  check *reads* and nothing else.
- The regression test has three candidates write the same file, the two
  wrong ones deliberately *longer*, and asserts that the winner is the
  one whose file satisfies the rule and that the disk holds the winner's
  content. Verified: with the overlay removed the test fails - the long
  wrong candidate wins and the right one is discarded.

**What v0.7.20 adds** (fixing the defect that decided *which* candidate won):

- **The parallel candidate ranking scored the wrong directory.** The ranking
  called the check evaluator *without* the configuration, but the file level
  checks (`file_contains`, `file_exists`, ...) resolve their paths against the
  workspace *in the configuration*; without it the workspace falls back to the
  current working directory. Every file level check therefore looked in a
  directory the run was not using, **every candidate scored zero on the file
  rules**, and the ranking degenerated into "pick by length" - so the
  candidate that violated the file rule could win the race, and the file
  rules had no influence on the choice at all. The single worker path and
  the final verdict always passed the configuration; only the ranking
  forgot it. All three paths now resolve the same workspace.
- **The final verdict was never affected** - it is recomputed with the
  configuration after the winner is picked. The damage was limited to the
  choice of candidate - but that is exactly what the race exists for.
- The regression test puts the workspace in a *different* directory than the
  current working directory, has three candidates answer differently, and
  asserts that the winner is the one passing the file rule. Verified: with
  the fix reverted the test fails immediately (all candidates at zero
  passes) - the test provably catches the defect, it does not merely run.


**What v0.7.19 adds** (fixing a defect that destroyed the truth of every verdict):

- **The role instruction was dropped entirely on Responses-style gateways.**
  This is the most damaging defect this project has shipped, and it was
  found by running the loop against a *real* Codex Responses bridge rather
  than a mock. The runtime sends `messages` (the chat-completions shape)
  *and* `input` (the Responses shape); a Responses-style gateway reads
  `input` and ignores `messages` completely - and `input` used to carry
  only the user prompt. The system instruction was therefore thrown away:
  the worker's "output the deliverable itself", the verifier's JSON
  schema, the reflector's and the lead's decision contract - none of them
  were in effect. The symptom is deeply misleading: the endpoint answers
  normally, `mas doctor` reports the endpoint as healthy, the loop still
  reports "closed" - but the verdicts are guesswork. A loop built on a
  discarded contract is exactly the fake closure this project exists to
  prevent. `input` now carries the whole conversation - the system item
  included - while `messages` keeps the chat-completions shape, so the
  instruction survives whichever dialect the endpoint speaks, with nothing
  for the user to configure.
- **`mas doctor` no longer reports a broken setup as healthy.** The
  handshake could prove that the endpoint answers, not that the
  instruction was applied. The new `instruction` check asks the model to
  repeat a token that appears *only* in the system instruction, and
  reports a `warn` with an explicit "the role contract may not be in
  effect, the verdicts are not trustworthy" when the endpoint answers but
  the instruction was not applied. The probe now sends the same payload
  shape the provider sends, so the handshake tests the request the runtime
  really makes.

**What v0.7.18 adds** (making the "one-click install" promise actually true):

- **Downloading a single script used to fail** - the installer needed a source
  checkout. It now pulls the published wheel from GitHub Releases, unpacks it
  with the standard library and installs it into `~/.mas/`. **All you need on
  the machine is Python 3.10+: no git, no pip, no build tools, no clone.**
- **The install used to shadow the runtime** - the runtime was started with
  `python -m mas`, and `python -m` puts the **current working directory** first on
  `sys.path`. Inside a source checkout that directory holds the `mas/` package,
  so MCP registration and agent skills came from the checkout and the freshly
  installed runtime was never used. Registration and launch now go through the
  **package path**;
- **Registration did not say where the config lives** - a host could start the
  runtime yet read somebody else's `config.json`. Both registrations now state
  the **runtime path** and the **config file path**;
- **Two real-machine traps** - under Git Bash `python` resolves to a POSIX path
  without `.exe`, which Windows cannot execute; and `install.sh` joined
  `PYTHONPATH` with colons while the Windows Python it invokes only accepts the
  platform separator. Both are fixed;
- **CI now stands up** - the new CI leg stages **only the scripts, no source**, so
  the installer has to take the real download path; a second leg installs a wheel
  **built from the current checkout** and asserts the installed version matches
  the repository;
- A `CHANGELOG.md` now exists, and `MAS_SKIP_PATH` / `MAS_PYTHON` were added so
  the two installers expose exactly the same options.

**What v0.7.14 adds** (DeepSeek Harness integration - one set of facts across clients):

- **Another client, another truth?** - Codex, Claude Code and OpenMausBot already
  come in over MCP, so the verdicts and the ledger were always single sourced. Now
  **DeepSeek Harness** can join: the repo ships a DSH plugin
  (`integrations/dsh-openloop-mas`) that does **not** re-implement the loop but
  calls the local runtime over **MCP** - so the verdicts, vote counts and ledger in
  a DSH conversation are the *same facts* the other clients see;
- **One more switch on the one-click installer** - `install.ps1 -InstallDshPlugin`.
  It is **off by default**: the main line stays **dependency-free pure Python**, so
  a beginner never has to install Node, DSH or npm. When enabled the script packs
  the plugin with `npm pack` and installs it into the chosen profile; if the DSH
  command fails it **degrades gracefully** (prints the tarball path and the manual
  command) instead of taking the whole installer down;
- **The plugin runs inside a real DSH host, and that caught two bugs offline tests
  could never see** - first, DSH mounts a plugin through `dsh.bundle.patch`
  (pointing at `cordis.patch.yml`), *not* `dsh.profile.bundles`; get it wrong and
  the plugin is installed as a plain dependency and **never mounts**. Second, tool
  parameter schemas must satisfy DSH's JSON Schema subset: `items.additionalProperties`
  has to be **explicitly** `true`/`false`, otherwise `defineTool` throws
  `JsonSchemaError` and the whole plugin entry fails to activate. Both bugs only
  show up when you actually boot the host;
- The plugin's MCP client (`lib/mcp-client.js`) has **zero dependencies** and can be
  unit tested without DSH.

**What v0.7.13 fixes** (an absent reviewer may no longer masquerade as an abstention):

- **Three reviewers, two answers, yet the report claimed a unanimous pass** - when the adversarial panel runs against a real endpoint it intermittently comes back short: one reviewer's call fails or times out and its verdict simply is not there. The aggregator only counted the answers that arrived (`usable`); an absent reviewer was neither a passing nor a failing vote, so a **3-seat panel that received 2 votes** was recorded as `votes: "2/2"`, `pass: true`, and the report announced a unanimous pass - **one of three reviewers never expressed a verdict, yet the engine closed the loop as if it had**. This is the same family of false closure as v0.7.11/v0.7.12, but this one was **found on a live endpoint**, not by offline audit;
- The aggregator now knows **how many reviewers the panel should have** (`expected`): an absent vote fails the panel in `all` mode (no closure), the vote count reads `2/3` rather than `2/2`, and the result gains `absent_votes` and `judged_by` fields while the aggregate issues spell out "N of M reviewer(s) produced no usable verdict". The weaker guarantees `majority` / `any` **tolerate absence by design** (you picked the weaker guarantee) - but absence is still recorded and shown instead of vanishing;
- **The re-ask path used to replace the whole panel** - when the panel came back short, the engine re-asks the missing members. That path used to ask a single **persona-less** verifier and then **replace** the entire panel with its answer: the vote count collapsed from `3/3` to `1/1`, so a re-ask actually reduced review strength to zero. The re-ask now runs the **full panel** (the same personas), **each member only fills in the criterion it missed and keeps its own answer**, and one person's opinion is no longer broadcast to everyone. A failed review call no longer collapses the panel silently either - the failed member's seat is preserved as "absent";
- The **ordering** of the review log is fixed too: the ledger used to record the **first** aggregate (before the re-ask), so the post-re-ask state was never persisted. The ledger now records the **final** aggregate (including `missing`), and report and ledger no longer contradict each other.

**What v0.7.12 fixes** (the summary may not contradict the per-criterion answers):

- **The model contradicted itself, and the report repeated it** - `normalize_verdict` normalised the individual `scores` but **kept** whatever top-level `verdict` the model had typed. So `{"verdict": "pass", "scores": [{"id": "c1", "pass": "false"}]}` came out with a summary saying "passed" and its own per-criterion answer saying "no". The closure decision itself was never affected (it is computed from the per-criterion `scores`), but **every report, every replay and every ledger entry** showed that contradiction, and a reader had no way to tell which half of the answer the engine believed;
- The summary now **follows the per-criterion answers**: whenever **at least one** criterion carries a readable verdict, the top-level `verdict` is computed from those answers (all of them must pass). Only when **none** of them is readable does the model's own summary survive - then it is the only signal there is. The point of this fix is "what the report says matches what the engine did", not "it passes more often".

**What v0.7.11 fixes** (a verdict is read by its meaning - the last class of fake closure):

- **Two reviewers both said "no", and the aggregate said "yes"** - the aggregation layer used `bool(vote)` to read a verdict, and `bool("false")` is `True`. A reviewer answering `"pass": "false"` (a string instead of a boolean) was therefore counted as **one passing vote**: two reviewers said "no", the engine aggregated `pass: true, votes: 2/2`, and the loop closed on it. This is the most dangerous class of fake closure this project exists to kill - **the machine was saying no while the ledger said it passed**. A verdict is now read by its **meaning** (`"false"` / `"0"` / `"no"` means not passing), an **unreadable vote never counts as a passing vote** (the conservative side), and the result carries an `unreadable_votes` count. `mode="any"` is no longer fooled by string fake votes either;
- **The mock backend reported one criterion twice** - a criterion without a machine-check rule was appended once from the criteria list (`pass: False`, `no machine check`) and again from the unjudged list; `setdefault` kept only the first, so **the genuinely useful note was overwritten**. "No machine check" is now reported once, by the unjudged branch only - every criterion appears exactly once;
- **`normalize_verdict` pushed an unreadable verdict into `pass`** - the canonical path (`{"verdict": "", "scores": [...]}`) used to keep the original string, and `all(s.get("pass"))` truthified it, so `"maybe"` counted as a pass. An unreadable answer is now `pass: False` + `unreadable: True`, and the verdict is `all(s.get("pass") is True)` - aligned with the `criteria` path;
- **"Undecided" was displayed to the reviewer as FAIL** - the machine-check block had only PASS / FAIL, so `pass: None` (no rule, a broken rule, an unknown rule type) rendered as `FAIL` and the reviewer model read "the machine check failed" instead of "this criterion was never judged". It is now three-state: `UNDECIDED` / `PASS` / `FAIL`.

**What v0.7.10 fixes** (a failed resume no longer pretends to be solved):

- **A failing resume used to mark the ticket as resolved** - an escalation
  ticket is the human queue: it says "the machine could not close this on its
  own, a human has to decide". When `mas resume` finished **without closing
  the loop**, the ticket was still written as `resolved`: the escalation
  vanished from `mas tickets` while the problem it was opened for was not
  solved at all - the queue under-reported and the loop falsely closed. A
  ticket is now closed only when the loop **actually** closes; otherwise it
  stays in the queue, and the failure (status, error, failing criteria) is
  recorded on it under `resume_attempts`;
- **A failed resume opened a duplicate ticket** - one failure was counted as
  two open items, so the human queue lied about how many problems were
  waiting. One problem is now one ticket: the fresh evidence (latest
  artifact, failing criteria, iteration history) lands on it instead of
  spawning another one;
- The CLI now says plainly that ticket X is still open and still in the queue,
  instead of letting an `exit 0` look like a finished job.

**What v0.7.9 fixes** (the flag is read by meaning; a JSON array no longer degrades):

- **`"required": "false"` was treated as required** - `bool("false")` is `True`,
  and quotes are exactly what a criterion picks up when it travels through JSON,
  MCP or a shell script. The user asked for a soft standard, the engine enforced
  a hard one, and the loop could then never close. The flag is now read by
  *meaning* (`"false"` / `"0"` / `"no"` are optional), and **anything unreadable
  stays required** - an unreadable flag must never silently relax a standard;
- **A JSON array passed to `--criteria` degraded as a whole** - an array starts
  with `[`, was not recognised as JSON, and was therefore taken as **one plain
  description**: no machine check ran at all and the closure actually came from
  a model saying "looks fine". Every entry in the array now keeps its own
  machine check, and the report says `source: rule` because that is what ran;
- the CLI layer gained regression tests: array parsing, the machine check really
  running, a string flag read as optional, and a broken JSON array refused.

**What v0.7.8 fixes** (an optional criterion no longer reports a false pass):

- A `required: false` criterion used to be **hard-coded** as passing in the merge
  layer - a criterion with a machine check had its result thrown away, so the
  report showed a green light for a rule that actually failed;
- the rule now runs and its **real** result lands in the report (`PASS` or
  `FAIL`) - it just never keeps the loop open. Without a machine check the
  reviewer's verdict is recorded the same way;
- every verdict gained an explicit `blocking` field: `true` for required, `false`
  for optional. Only blocking criteria decide the closure.

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
