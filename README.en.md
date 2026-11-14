# OpenLoop MAS · A Multi-Agent System That Actually Closes Its Loops

> A **zero-dependency, standard-library-only** multi-agent loop runtime. It turns
> "plan → work → verify → reflect" into an **engineering loop with acceptance
> criteria, exit conditions and an auditable ledger**. One command to install;
> beginners can run it too.

[English](README.en.md) | [中文](README.md)

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

[![ci](https://github.com/tffgwy/openloop-mas/actions/workflows/ci.yml/badge.svg)](https://github.com/tffgwy/openloop-mas/actions/workflows/ci.yml)
[![license](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![deps](https://img.shields.io/badge/dependencies-0-orange.svg)]()

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

### Option 2: download one script (no dependencies - best for beginners)

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
**All it needs is Python 3.10+ - no git, no pip, no compiler.**

Pin a specific release instead of "the latest":

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Version 0.7.17
```

```bash
MAS_VERSION=0.7.17 bash install.sh
```

> The script adds `~/.mas/bin` to `PATH` (on Windows it writes the current
> user's registry key). Pass `-SkipPath` (Windows) or `MAS_SKIP_PATH=1`
> (shell) to leave `PATH` alone.

### Option 3: install from source (for development)

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
