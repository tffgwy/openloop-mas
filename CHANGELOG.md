# Changelog

All notable changes to OpenLoop MAS are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

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
