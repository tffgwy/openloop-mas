# Changelog

All notable changes to OpenLoop MAS are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

## [0.7.27] - 2026-11-14

### Fixed

Three defects that made a loop close wrong - or fail to close at all.

* **A list-shaped file check could never close the loop.** The engine
  parsed the check argument with its own logic while the machine check
  used a different parser. The two disagreed on the list form: the check
  saw `notes/a.txt`, the engine saw the literal text `['notes/a.txt']`.
  The worker was told to write a file with brackets in its name while
  the check looked at another path - so the loop never closed, no matter
  how many iterations it ran. The engine now reads the path with the
  check's own parser, so the two can never disagree.

* **A recovered run reported the error it had already survived.** A run
  that was truncated, retried and then closed carried `status` `closed`
  *and* `error` `truncated answer: ...`. The CLI printed the error as an
  explanation, which reads like a failed run. A transient failure now
  belongs to the iteration it happened in - closing clears it - while the
  ledger keeps its `truncated` entry, so the audit trail loses nothing.

* **The token ceiling was not a ceiling.** The budget was only checked at
  the end of an iteration, and the truncation retry runs before that. A
  run that kept hitting the output limit could spend far past the
  ceiling the user set (measured: ceiling 200,000, spent 240,000). The
  ceiling is now checked at the top of every iteration as well, so it
  really is a ceiling.

### Added

* Seven regression tests: three on the write-path argument (list, tuple
  and object form), two on the recovered run (no error on a closed run,
  the truncation still in the ledger) and two on the token ceiling.
  Verified: with each fix reverted, 2 + 2 + 1 tests fail.

### Verified

* `pytest tests/` - 434 passed, 4 skipped.
* `unittest discover -s tests` - Ran 438 tests, OK.
* `ruff check mas tests` - all checks passed.
* Live run against the local bridge (`claude-opus-5-5`): a two-candidate
  parallel run with a file-level check closed on the first iteration, the
  file really landed on disk and the check read the real file. The same
  task with the list-shaped argument - the case that used to deadlock -
  also closed on the first iteration.

## [0.7.26] - 2026-11-14

### Fixed

The engine caught `ProviderError` and nothing else. Any other exception
raised inside the loop escaped `run()` entirely - and a run that ends
without a ledger entry is a run that cannot be audited.

* **An unexpected error erased the audit trail.** The ledger showed a
  run that started and never ended: no error entry, no closure record,
  no ticket. The closure state of that run was simply unknowable, which
  is the one thing this project exists to make knowable.

* **The engine was not a safe library entry point.** The CLI, the MCP
  tool and the HTTP route each have their own exception boundary, so the
  user saw a clean error message. But the engine is also called
  directly - by a library user, by a script, by anything embedding the
  runtime - and there it raised a bare traceback.

* The loop now catches everything that is not a `ProviderError`,
  records it in the ledger as an `internal` error and returns a normal
  `error` result. The distinction is kept: a provider failure still
  reads `provider error`, and the criterion validation still raises its
  `ValueError` before the loop starts, because a broken rule must never
  look like a closed run.

### Added

* Seven regression tests: the run does not raise, the error is reported
  on the result, the ledger records it with the `internal` kind, the run
  stays auditable, the parallel path and the broken-worker path are
  contained, and a provider failure still reads as a provider failure.
  Verified: with the containment removed six of the seven fail.

## [0.7.25] - 2026-11-14

### Fixed

The truncation handling added in v0.7.24 threw the truncated answer
away - including the tokens the endpoint had already spent to produce
it. The budget therefore under-reported the real cost of a run that had
to retry.

* **A truncated call was free.** The endpoint generated tokens, billed
  them, and then stopped. The exception carried the answer but not the
  usage, so the engine charged the call nothing. A run that truncated
  five times looked five times cheaper than it was, and the budget
  ceiling was effectively five times too generous.

* **The parallel pool under-reported by a factor of N.** With three
  workers, three calls were truncated and three sets of tokens were
  spent - but only the single re-raised truncation was counted. The
  other two candidates' tokens vanished. The whole pool's spend is now
  collected before the truncation is raised, so the number in the ledger
  is the number the endpoint billed.

* `TruncatedAnswer` now carries the usage of the call that produced it,
  and the engine charges it to the budget. The ledger's `truncated`
  entry reports the tokens that were spent.

### Added

* Two regression tests: the truncated call is charged to the budget, and
  every truncated candidate of a parallel pool is charged. Verified:
  with the charging removed the parallel test reports 0 tokens instead
  of the expected 2400.

## [0.7.24] - 2026-11-14

### Fixed

A cut off answer was delivered as if it were a finished one. The
endpoint's `finish_reason` was captured, put into the raw payload and
then never read again - so the one signal that says *this answer is
incomplete* was silently dropped.

* **A truncated answer could close the loop.** A model that stops at the
  output token limit produces a *partial* answer - and a partial answer
  can satisfy the acceptance checks by accident: the searched string may
  already be in the first paragraph, and the fragment may already be long
  enough for `min_chars`. The loop then closed on the fragment, reported
  `closed` and handed a half-written deliverable to the user. This is
  the same family of false closure this project exists to prevent, and it
  was the most likely one to happen in practice.

* **The output budget was never raised.** The provider default of 4000
  output tokens was used for every call, and it was never increased. A
  deliverable that does not fit in 4000 tokens was therefore always cut
  off - and always silently accepted.

* **A truncated answer is now a retryable iteration failure.** The
  providers raise `TruncatedAnswer` instead of returning an incomplete
  answer, the engine records the truncation in the ledger and retries the
  iteration. The partial answer is discarded on purpose: a fragment that
  happens to satisfy a check must not become the deliverable.

* **The retry gets more room.** The output budget doubles with every
  attempt (8000, 16000, 32000 - capped), so the loop gets a real chance
  to finish the answer instead of failing the same way twice. The budget
  is configurable with `output_max_tokens`.

* **The parallel path behaves the same.** A truncated candidate is not
  counted as a lost angle: the whole pool is retried. Otherwise the loop
  would rank a pool of fragments and pick the longest one.

### Added

* `mas.providers.TruncatedAnswer`, plus `is_truncated` and
  `finish_reason_of` for reading the signal out of a chat-completions or
  an Anthropic Messages answer.
* Thirteen regression tests: eight on the provider side (the signal, the
  two providers, the complete-answer case) and five on the engine side
  (no false closure, the fragment is never delivered, the ledger entry,
  the growing budget, the parallel path). Verified: with the detection
  removed one provider test fails, and with the engine retry removed all
  five engine tests fail.

## [0.7.23] - 2026-11-14

### Fixed

The reasoning stripper only understood the *closed* shape of a
reasoning block, and only the `<think>` spelling of it. Two real cases
slipped through and the scratchpad was delivered again.

* **An unclosed reasoning block was delivered whole.** When a model
  runs out of tokens in the middle of its scratchpad, the closing tag
  never arrives - the answer is a `<think>` that never ends. The stripper
  matched only `<think>...</think>`, so the whole truncated scratchpad
  was delivered as the artifact, was counted in the length tie break and
  was written into the workspace files. An unclosed block now drops
  everything from the opening tag on: there is no deliverable after an
  opening tag that was never closed.

* **Other gateways spell the tag differently.** `<thinking>` was
  already handled; `<reasoning>`, `<scratchpad>` and `<reflection>` were
  not, so on those endpoints the scratchpad reached the deliverable
  unchanged. All five spellings are recognised now, closed and unclosed.

* The closed case is still handled first, so a *closed* block is never
  mistaken for an opening one and the real answer behind it survives.

### Added

* Nine regression tests covering the unclosed block, every tag
  spelling, the closed-block-after-open interaction and the plain text
  case. Verified: with the unclosed rule removed two tests fail, and
  with the extra spellings removed three more fail.

## [0.7.22] - 2026-11-14

### Fixed

The parallel candidate path forgot to strip the reasoning block. A
reasoning model wraps its scratchpad in `<think>...</think>`. The single
worker path has always stripped it before the artifact was judged,
written or delivered - the parallel path did not.

* **The scratchpad was delivered as the artifact.** What the user
  received, what the verifier was asked to judge and what was written
  into the workspace files was the model's private reasoning, prefixed
  to the real answer. The single worker path was never affected.

* **The scratchpad decided the tie break.** All candidates failing the
  checks equally, the winner was chosen by the length of the artifact.
  A candidate with a longer scratchpad therefore won the race - the
  ranking was effectively measuring how much the model thought, not how
  good its answer was.

* **The scratchpad polluted the text level checks.** `min_chars` counted
  the reasoning as content, and `contains` was satisfied by a token that
  only appeared in the reasoning.

* The parallel path now strips the reasoning block *before* the ranking,
  which is what makes the two paths agree: the same input yields the
  same artifact whether one worker or three produce it.

### Added

* Regression tests asserting that the parallel artifact carries no
  reasoning block, that the single and the parallel runs deliver
  identical content, and that the reasoning length cannot decide the tie
  break. Verified: with the strip removed the candidate grows from 34 to
  369 characters and the tests fail immediately.

## [0.7.21] - 2026-11-14

### Fixed

The parallel candidate ranking compared the candidates against the *old*
contents of the workspace. The file level checks read the real disk, and
at ranking time no candidate had written its file yet - so every file
level check failed for every candidate, and the file rules had no
influence on the choice at all.

* **The winner was picked by length, not by the rules.** With every
  candidate scoring zero on the file rules, the tie was broken by the
  length of the artifact. A candidate that would write a file violating
  the rule could win the race, and the loop would then fail - or worse,
  close on a file that a different candidate had produced. The single
  worker path was never affected: it writes first and checks second, in
  the right order. Only the parallel path checked before it wrote.

* **The ranking now scores a candidate against the file it would
  produce.** A candidate's write blocks are parsed into a *plan* without
  touching the disk, and the file checks read that plan through an
  overlay. The winner is then written to disk as before - and only the
  winner. The disk state during the ranking is never modified, so the
  candidates cannot see each other's output.

* `mas.filewrite.plan_writes` and the `overlay` argument of
  `mas.filechecks.evaluate_file_check` are the two new building blocks.
  An overlay is a plain mapping from absolute path to bytes; it changes
  what a check *reads* and nothing else.

### Added

* Regression tests for the write order: three candidates write different
  contents to the same file, the wrong ones deliberately *longer*, and
  the test asserts that the winner is the one whose file satisfies the
  rule and that the disk holds that winner's content. With the overlay
  removed the test fails - the long wrong candidate wins and the right
  one is discarded - so the test provably catches the defect.
* Tests for the overlay itself: it replaces the file contents, it makes
  a missing file exist, and a path outside the overlay is still read
  from the disk.

## [0.7.20] - 2026-11-14

### Fixed

The parallel candidate ranking scored the candidates against the wrong
directory. The winner of the race is picked on that ranking, so the wrong
directory did not just mislabel a score - it decided *which* candidate
became the deliverable.

* **The ranking ran without the configuration.** The candidate scoring
  called the check evaluator without the configuration, and the file level
  checks resolve their paths against the workspace *in the configuration*.
  With no configuration the workspace falls back to the current working
  directory. Every file level check therefore ran against a directory that
  the run was not using, every candidate scored zero on the file rules, and
  the ranking degenerated into a tie broken by length - so a candidate that
  violated the file rule could win the race, and the file rules could not
  influence the choice at all. The single worker path and the final
  verdict always passed the configuration; only the ranking forgot it.
  The ranking now passes the configuration too, so all three paths resolve
  the same workspace.

* Note that the *final* verdict was never affected: it is recomputed with
  the configuration after the winner is chosen. The damage was limited to
  the choice of candidate - but that is exactly what the race exists for.

### Added

* A regression test that runs the race with the workspace in a different
  directory than the current working directory, with three candidates that
  answer differently, and asserts that the winner is the one that passes
  the file rule. Reverting the fix makes the test fail with every
  candidate at zero passes - the test is proven to catch the defect, not
  merely to run.

## [0.7.19] - 2026-11-14

### Fixed

The role contract of the loop was silently discarded on Responses-style
gateways. This is the most damaging defect this project has shipped, and it
was found by running the loop against a real Codex Responses bridge instead
of a mock.

* **The system instruction never reached the model.** The provider sent
  `messages` (the chat-completions shape) *and* `input` (the Responses
  shape). A Responses-style bridge reads `input` and ignores `messages`
  entirely - and `input` used to carry only the user prompt. The system
  instruction was therefore dropped on the floor on every such gateway.
  The visible symptom was subtle: the endpoint answered normally, `mas
  doctor` reported the endpoint as healthy, and the loop still "closed" -
  but the verdicts were guesswork. The worker's "output the deliverable
  itself", the verifier's JSON schema, the reflector's and the lead's
  decision contract: none of them were in effect. A closed loop built on
  a discarded contract is exactly the fake closure this project exists to
  prevent.
  `input` now carries the whole conversation - the system item included -
  in the Responses item shape, while `messages` keeps the chat-completions
  shape for strict gateways. Both carry the instruction, so it survives
  whichever dialect the endpoint actually speaks, and nothing has to be
  configured to pick one.

* **`mas doctor` reported a broken setup as healthy.** The handshake only
  proved that the endpoint answers and which dialect it speaks - it could
  not tell whether the instruction it sent was applied. A new
  `instruction` check asks the model to repeat a token that only the
  system instruction mentions, and reports a `warn` with an explicit
  "角色契约可能未生效，闭环判定不可信" when the endpoint answers but the
  instruction was not applied. The probe now also sends the same payload
  shape the provider sends, so the handshake tests the request the runtime
  really makes.

### Added

* `mas.probe.probe_instruction_echo` - the instruction check on its own,
  usable outside `mas doctor`.
* `mas doctor` gained the `instruction` check. It runs on every configured
  non-mock endpoint and is reported by the MCP tool `mas_doctor` as well.
* Regression tests for the dropped-system case, for the provider payload
  shape and for the new check.

## [0.7.18] - 2026-11-14

### Fixed

Two defects in the checks added in 0.7.17. Both passed on Windows and
failed on Linux and macOS.

* The installer test compared the registered `PYTHONPATH` after turning
  every forward slash into a backslash. That is right on Windows and
  wrong on POSIX, where the path has no backslashes to begin with - the
  assertion named a file that cannot exist. The comparison now follows
  the platform.
* The smoke check started the installed runtime through its package path
  without putting the package directory on `sys.path`, so the import of
  `mas` failed with `ModuleNotFoundError`. The runtime is now started the
  way the MCP registration starts it.

## [0.7.17] - 2026-11-14

### Fixed

The one-click installer had three defects that made the "download one script
and run it" promise untrue. All three were found by testing the installer the
way a beginner uses it: with only the script on disk and no source checkout.

* **The installer required a source checkout.** `install.ps1` and `install.sh`
  failed outright when no `mas/` package sat next to them. They now fetch the
  published wheel from the GitHub release and unpack it with the standard
  library, so a single downloaded file is enough - no git, no pip, no
  checkout. `-Version` (or `MAS_VERSION`) pins a specific release.

* **The installer was shadowed by its own working directory.** The runtime was
  started through `python -m mas`, and `python -m` puts the current working
  directory first on `sys.path`. In a checkout that directory contains the
  `mas/` package, so the MCP registration and the agent skills came from the
  checkout while the freshly installed runtime sat unused. The installer now
  registers the runtime by its package path, and the registration points at
  the installed copy explicitly.

* **The MCP registration did not say where the configuration was.** The
  registration carried `PYTHONPATH` but not the configuration location, so a
  host process could start the runtime and still read a different
  `config.json`. Both registrations now carry the configuration path, and the
  runtime resolves it through one helper.

Two further defects were environment-specific but hit real machines:

* The interpreter path was written into the registration as found on `PATH`.
  Under Git Bash that is a POSIX path without an `.exe` suffix, which the
  Windows host cannot execute. The path is now normalised to an absolute
  interpreter path before it is registered.

* `install.sh` separated `PYTHONPATH` entries with a colon. The Windows
  Python it invoked only understands the platform separator, so the installed
  path never reached `sys.path`.

### Added

* `-Wheel` (PowerShell) and `MAS_WHEEL` (shell) install a wheel from disk
  instead of downloading one. The release pipeline and the CI install matrix
  use it to install a build that has not been published yet.
* `tools/verify_install.py` now checks what a user actually receives: that
  `import mas` resolves to the installed runtime rather than to a checkout,
  that the registered command and entry point exist, that both registrations
  point at the installed runtime, and that both carry the configuration
  location.
* `tools/check_install_smoke.py` asserts the version-independent contract of
  an installation, so a published release stays verifiable after the
  registration format moves on.
* `tools/check_installed_version.py` asserts that the installed runtime is
  exactly the build it was handed.
* CI gained a standalone install leg that stages the installer scripts
  alone - no checkout, no package - and installs from them, plus a second leg
  that installs a wheel built from the current checkout and asserts the
  installed version matches the repository.

### Changed

* The runtime is launched through its package path everywhere: the
  installer, the launcher, the MCP registration, and the verifier.
* `pyproject.toml` restricts pytest to `tests/`. The scratch scripts under
  `work/` were being collected as test modules and failed the run on their
  own import errors - a failure in a scratch file, not in the product.

## [0.7.16] - 2026-11-13

### Added

* `mas relay`: the DeepSeek Harness host can drive `mas_run` end to end.
  Verified inside a DSH 0.2.2-rc.3 host - `status=closed`, `iterations=1`,
  `tokens_used=4468`, `run_id=R-9644103b33`.
* DSH plugin `dsh-openloop-mas` for the desktop and web profiles.

## [0.7.15] - 2026-11-13

### Added

* File-level machine checks and the write-file protocol: the closure loop can
  now verify real artefacts on disk instead of only self-reported claims.

## [0.7.14] - 2026-11-12

### Added

* DeepSeek Harness integration. The closure capability is now shared across
  clients rather than living inside one.

## [0.7.13] - 2026-11-12

### Fixed

* An absent reviewer no longer counts as an abstention vote. A missing vote
  was previously indistinguishable from a deliberate abstention, which made
  the verdict wrong.

## [0.7.8] and earlier

See the repository history for the versions before this file was introduced.
