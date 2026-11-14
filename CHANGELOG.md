# Changelog

All notable changes to OpenLoop MAS are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

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
