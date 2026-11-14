<#
.SYNOPSIS
    One-click installer for OpenLoop MAS (multi-agent loop runtime).

.DESCRIPTION
    Installs the runtime into $HOME\.mas, installs the agent skills into every
    skill directory found on this machine, registers the MCP server in the
    Codex and Claude Code configuration, and finishes with a self-check and an
    offline demo run. It never installs a third-party Python package - the
    runtime only uses the standard library.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Python "C:\Python312\python.exe"
#>
[CmdletBinding()]
param(
    [string]$InstallDir = "",
    [string]$Python = "",
    [switch]$SkipPath,
    [switch]$SkipMcp,
    [switch]$SkipSkills,
    [switch]$SkipDemo,
    [switch]$InstallDshPlugin,
    [string]$DshProfile = "desktop",
    [string]$Version = "",
    [string]$Wheel = ""
)

$ErrorActionPreference = "Stop"
$script:Steps = @()

function Write-Step { param([string]$Message) Write-Host "[*] $Message" -ForegroundColor Cyan }
function Write-Ok { param([string]$Message) Write-Host "[OK] $Message" -ForegroundColor Green }
function Write-Warn { param([string]$Message) Write-Host "[!!] $Message" -ForegroundColor Yellow }
function Write-Fail { param([string]$Message) Write-Host "[XX] $Message" -ForegroundColor Red }

function Write-Utf8NoBom {
    param([string]$Path, [string]$Content)
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $utf8)
}

if (-not $InstallDir) { $InstallDir = Join-Path $env:USERPROFILE ".mas" }
$AppDir = Join-Path $InstallDir "app"
$BinDir = Join-Path $InstallDir "bin"
$SourceRoot = $PSScriptRoot
if (-not $SourceRoot) { $SourceRoot = (Get-Location).Path }

Write-Host ""
Write-Host "OpenLoop MAS - one-click installer" -ForegroundColor White
Write-Host "source: $SourceRoot" -ForegroundColor DarkGray
Write-Host ""

# ---------------------------------------------------------------- python
Write-Step "Locating Python 3.10+"
$pythonExe = $null
if ($Python) {
    if (Test-Path $Python) { $pythonExe = (Resolve-Path $Python).Path }
}
if (-not $pythonExe) {
    $candidates = @()
    foreach ($name in @("python3", "python", "py")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { $candidates += $cmd.Source }
    }
    foreach ($cand in $candidates) {
        try {
            $probe = & $cand -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" 2>$null
            if ($LASTEXITCODE -eq 0) { $pythonExe = $cand; break }
        } catch { }
    }
}
if (-not $pythonExe) {
    Write-Fail "Python 3.10+ not found. Install Python from https://www.python.org/downloads/ and re-run."
    exit 1
}
$pyVersion = & $pythonExe -c "import sys; print('%d.%d.%d' % sys.version_info[:3])"
Write-Ok "Python $pyVersion at $pythonExe"

# The interpreter path is normalised to an absolute Windows path *before*
# it is written into the MCP registration. The registration is read by a
# host process (Codex / Claude Code), not by this script: a relative or
# POSIX-style path would resolve against the host's working directory -
# which is usually not the one the installer ran in.
$pythonExe = (& $pythonExe -c "import sys; print(sys.executable)").Trim()

# ---------------------------------------------------------------- app
Write-Step "Installing runtime into $AppDir"
New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
$pkgTarget = Join-Path $AppDir "mas"
$pkgSource = Join-Path $SourceRoot "mas"

if (Test-Path $pkgSource) {
    # A source checkout is available: copy the runtime from it.
    if (Test-Path $pkgTarget) { Remove-Item -Recurse -Force $pkgTarget }
    Copy-Item -Recurse -Force $pkgSource $pkgTarget
    Get-ChildItem -Path $pkgTarget -Directory -Filter "__pycache__" -Recurse -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Write-Ok "runtime copied from source checkout"
} else {
    # Standalone script: there is no package source next to this file, so the
    # runtime is fetched from the published wheel and unpacked by hand.
    # This is the path a beginner takes when they download install.ps1 alone,
    # so it must work without git, pip, or a checkout.
    Write-Step "No source checkout found - fetching the runtime wheel"
    $wheelPath = Join-Path $InstallDir "openloop_mas.whl"
    # The helper only exists on the download path, but the cleanup below
    # runs on both paths - so the variable is declared here and the removal
    # is guarded, rather than leaving a null path for the cmdlet to trip on.
    $helperPath = Join-Path $InstallDir "_download_helper.py"
    if ($Wheel) {
        # A wheel was supplied explicitly. This is how the release pipeline
        # and the CI install matrix exercise a build that has not been
        # published yet. The download path is then never exercised, so the
        # two paths have to stay behaviourally identical.
        if (-not (Test-Path -LiteralPath $Wheel)) {
            Write-Fail "wheel not found: $Wheel"
            exit 1
        }
        Copy-Item -LiteralPath $Wheel -Destination $wheelPath -Force
        Write-Ok "using the supplied wheel: $Wheel"
    } else {
        $repo = "tffgwy/openloop-mas"
        if (-not $Version) { $Version = "latest" }

        # The download goes through Python's urllib rather than
        # Invoke-WebRequest or curl. Two reasons:
        #
        # * Windows PowerShell 5.1 does not read the HTTPS_PROXY environment
        #   variable, and a large share of users reach GitHub only through a
        #   local proxy. urllib reads HTTPS_PROXY *and* the Windows system
        #   proxy settings, so the installer works with or without one.
        # * curl is not installed on a default Windows machine, and the
        #   installer promises to need nothing but Python.
        # The download helper is written to disk and executed as a file.
        # Passing a multi-line program to `python -c` is a quoting minefield
        # in PowerShell: the mangling is silent, so the script keeps running
        # and only the helper dies with a syntax error. A file has no such
        # problem, and it is also what the error messages refer to.
        Write-Utf8NoBom $helperPath ([System.Text.Encoding]::UTF8.GetString(
            [System.Convert]::FromBase64String("IiIiRG93bmxvYWQgaGVscGVyIGZvciB0aGUgb25lLWNsaWNrIGluc3RhbGxlci4KClRocmVlIHRoaW5ncyBtYWtlIGEgbmFpdmUgZG93bmxvYWQgZmFpbCBvbiBhIHJlYWwgbWFjaGluZSwgc28gdGhlCmhlbHBlciB3b3JrcyBhcm91bmQgZWFjaCBvZiB0aGVtOgoKKiAqKlRMUyBpbnRlcmNlcHRpb24uKiogQSBjb3Jwb3JhdGUgb3IgcmVnaW9uYWwgcHJveHkgY2FuIGJyZWFrIHRoZQogIHN0YW5kYXJkIGxpYnJhcnkncyBUTFMgc3RhY2sgd2hpbGUgYGBjdXJsYGAgbmVnb3RpYXRlcyB0aGUgdmVyeSBzYW1lCiAgY29ubmVjdGlvbiBmaW5lLiBCb3RoIHRyYW5zcG9ydHMgYXJlIHRyaWVkLCBhbmQgdGhlIGZpcnN0IG9uZSB0aGF0CiAgd29ya3Mgd2lucy4KKiAqKkFQSSByYXRlIGxpbWl0cy4qKiBUaGUgR2l0SHViIFJFU1QgQVBJIGFsbG93cyBvbmx5IDYwIGFub255bW91cwogIHJlcXVlc3RzIHBlciBob3VyIGFuZCBwZXIgSVAsIGFuZCBhIHNoYXJlZCBuZXR3b3JrIGNhbiBidXJuIHRoYXQKICBidWRnZXQgYmVmb3JlIHRoZSB1c2VyIGV2ZW4gc3RhcnRzLiBUaGUgYGByZWxlYXNlcy9sYXRlc3RgYCAqcGFnZSoKICBhbnN3ZXJzIHdpdGggYSByZWRpcmVjdCB0byB0aGUgbmV3ZXN0IHRhZyBhbmQgaXMgbm90IHJhdGUgbGltaXRlZCwKICBzbyB0aGUgdmVyc2lvbiBpcyByZXNvbHZlZCBmcm9tIHRoZXJlLgoqICoqTm8gY3VybC4qKiBgYGN1cmxgYCBpcyBtaXNzaW5nIG9uIGEgZGVmYXVsdCBXaW5kb3dzIGluc3RhbGwsIGFuZAogIHRoZSBzdGFuZGFyZCBsaWJyYXJ5IGlzIGFsd2F5cyB0aGVyZS4gVGhlIG9yZGVyIGlzIHRoZXJlZm9yZQogIHN0YW5kYXJkIGxpYnJhcnkgZmlyc3QsIGN1cmwgc2Vjb25kLgoiIiIKCmltcG9ydCBqc29uCmltcG9ydCBvcwppbXBvcnQgcGF0aGxpYgppbXBvcnQgcmUKaW1wb3J0IHNodXRpbAppbXBvcnQgc3VicHJvY2VzcwppbXBvcnQgc3lzCmltcG9ydCB1cmxsaWIuZXJyb3IKaW1wb3J0IHVybGxpYi5yZXF1ZXN0CgpSRVBPID0gInRmZmd3eS9vcGVubG9vcC1tYXMiCkxBVEVTVF9QQUdFID0gImh0dHBzOi8vZ2l0aHViLmNvbS8iICsgUkVQTyArICIvcmVsZWFzZXMvbGF0ZXN0IgoKCmRlZiBfcHJveHlfdXJsKCk6CiAgICBmb3IgbmFtZSBpbiAoIkhUVFBTX1BST1hZIiwgImh0dHBzX3Byb3h5IiwgIkhUVFBfUFJPWFkiLCAiaHR0cF9wcm94eSIpOgogICAgICAgIHZhbHVlID0gb3MuZW52aXJvbi5nZXQobmFtZSkKICAgICAgICBpZiB2YWx1ZToKICAgICAgICAgICAgcmV0dXJuIHZhbHVlCiAgICByZXR1cm4gIiIKCgpkZWYgX2N1cmwoKToKICAgIHJldHVybiBzaHV0aWwud2hpY2goImN1cmwuZXhlIikgb3Igc2h1dGlsLndoaWNoKCJjdXJsIikKCgpkZWYgX29wZW5lcigpOgogICAgcmV0dXJuIHVybGxpYi5yZXF1ZXN0LmJ1aWxkX29wZW5lcigKICAgICAgICB1cmxsaWIucmVxdWVzdC5Qcm94eUhhbmRsZXIodXJsbGliLnJlcXVlc3QuZ2V0cHJveGllcygpKQogICAgKQoKCmNsYXNzIF9Ob1JlZGlyZWN0KHVybGxpYi5yZXF1ZXN0LkhUVFBSZWRpcmVjdEhhbmRsZXIpOgogICAgZGVmIHJlZGlyZWN0X3JlcXVlc3Qoc2VsZiwgcmVxLCBmcCwgY29kZSwgbXNnLCBoZWFkZXJzLCBuZXd1cmwpOgogICAgICAgIHJldHVybiBOb25lCgoKZGVmIF9yZWFkKHVybCwgdGltZW91dCwgZm9sbG93PVRydWUpOgogICAgaWYgZm9sbG93OgogICAgICAgIG9wZW5lciA9IF9vcGVuZXIoKQogICAgZWxzZToKICAgICAgICBvcGVuZXIgPSB1cmxsaWIucmVxdWVzdC5idWlsZF9vcGVuZXIoCiAgICAgICAgICAgIHVybGxpYi5yZXF1ZXN0LlByb3h5SGFuZGxlcih1cmxsaWIucmVxdWVzdC5nZXRwcm94aWVzKCkpLAogICAgICAgICAgICBfTm9SZWRpcmVjdCwKICAgICAgICApCiAgICByZXF1ZXN0ID0gdXJsbGliLnJlcXVlc3QuUmVxdWVzdCgKICAgICAgICB1cmwsIGhlYWRlcnM9eyJVc2VyLUFnZW50IjogIm9wZW5sb29wLW1hcy1pbnN0YWxsZXIifQogICAgKQogICAgcmV0dXJuIG9wZW5lci5vcGVuKHJlcXVlc3QsIHRpbWVvdXQ9dGltZW91dCkKCgpkZWYgX2ZldGNoX2J5dGVzKHVybCwgdGltZW91dCk6CiAgICB3aXRoIF9yZWFkKHVybCwgdGltZW91dCkgYXMgcmVzcG9uc2U6CiAgICAgICAgcmV0dXJuIHJlc3BvbnNlLnJlYWQoKQoKCmRlZiBfY3VybF9ydW4odXJsLCBkZXN0LCB0aW1lb3V0PTYwMCk6CiAgICBiaW5hcnkgPSBfY3VybCgpCiAgICBpZiBub3QgYmluYXJ5OgogICAgICAgIHJhaXNlIFJ1bnRpbWVFcnJvcigiY3VybCBpcyBub3QgYXZhaWxhYmxlIikKICAgIGNvbW1hbmQgPSBbYmluYXJ5LCAiLWZzU0wiLCAiLS1yZXRyeSIsICIzIl0KICAgIHByb3h5ID0gX3Byb3h5X3VybCgpCiAgICBpZiBwcm94eToKICAgICAgICBjb21tYW5kICs9IFsiLXgiLCBwcm94eV0KICAgIGNvbW1hbmQgKz0gWyItbyIsIHN0cihkZXN0KSwgdXJsXQogICAgc3VicHJvY2Vzcy5ydW4oY29tbWFuZCwgY2hlY2s9VHJ1ZSwgdGltZW91dD10aW1lb3V0KQogICAgcmV0dXJuIHBhdGhsaWIuUGF0aChkZXN0KS5zdGF0KCkuc3Rfc2l6ZQoKCmRlZiBkb3dubG9hZCh1cmwsIGRlc3QpOgogICAgZXJyb3JzID0gW10KICAgIHRyeToKICAgICAgICBkYXRhID0gX2ZldGNoX2J5dGVzKHVybCwgMzAwKQogICAgICAgIHBhdGhsaWIuUGF0aChkZXN0KS53cml0ZV9ieXRlcyhkYXRhKQogICAgICAgIHJldHVybiBsZW4oZGF0YSkKICAgIGV4Y2VwdCBFeGNlcHRpb24gYXMgZXhjOgogICAgICAgIGVycm9ycy5hcHBlbmQoInVybGxpYjoge306IHt9Ii5mb3JtYXQodHlwZShleGMpLl9fbmFtZV9fLCBleGMpKQogICAgaWYgX2N1cmwoKToKICAgICAgICB0cnk6CiAgICAgICAgICAgIHJldHVybiBfY3VybF9ydW4odXJsLCBkZXN0KQogICAgICAgIGV4Y2VwdCBFeGNlcHRpb24gYXMgZXhjOgogICAgICAgICAgICBlcnJvcnMuYXBwZW5kKCJjdXJsOiB7fToge30iLmZvcm1hdCh0eXBlKGV4YykuX19uYW1lX18sIGV4YykpCiAgICBmb3IgbGluZSBpbiBlcnJvcnM6CiAgICAgICAgcHJpbnQobGluZSwgZmlsZT1zeXMuc3RkZXJyKQogICAgcmFpc2UgU3lzdGVtRXhpdCgxKQoKCmRlZiBsYXRlc3RfdmVyc2lvbigpOgogICAgIiIiUmVzb2x2ZSB0aGUgbmV3ZXN0IHB1Ymxpc2hlZCB2ZXJzaW9uIHdpdGhvdXQgdGhlIFJFU1QgQVBJLgoKICAgIFRoZSBBUEkgaXMgcmF0ZSBsaW1pdGVkIHRvIDYwIGFub255bW91cyByZXF1ZXN0cyBwZXIgaG91cjsgdGhlCiAgICByZWxlYXNlIHBhZ2UgcmVkaXJlY3RzIHRvIHRoZSBuZXdlc3QgdGFnIGFuZCBpcyBub3QuCiAgICAiIiIKICAgIGVycm9ycyA9IFtdCiAgICB0cnk6CiAgICAgICAgdHJ5OgogICAgICAgICAgICBfcmVhZChMQVRFU1RfUEFHRSwgNjAsIGZvbGxvdz1GYWxzZSkKICAgICAgICBleGNlcHQgdXJsbGliLmVycm9yLkhUVFBFcnJvciBhcyBleGM6CiAgICAgICAgICAgIGxvY2F0aW9uID0gZXhjLmhlYWRlcnMuZ2V0KCJMb2NhdGlvbiIpIG9yICIiCiAgICAgICAgICAgIG1hdGNoID0gcmUuc2VhcmNoKHIiL3RhZy92PyhbMC05XVteL10qKSIsIGxvY2F0aW9uKQogICAgICAgICAgICBpZiBtYXRjaDoKICAgICAgICAgICAgICAgIHJldHVybiBtYXRjaC5ncm91cCgxKQogICAgICAgICAgICBlcnJvcnMuYXBwZW5kKCJ1cmxsaWI6IG5vIHRhZyBpbiByZWRpcmVjdCB7fSIuZm9ybWF0KGxvY2F0aW9uKSkKICAgICAgICBlbHNlOgogICAgICAgICAgICBlcnJvcnMuYXBwZW5kKCJ1cmxsaWI6IG5vIHJlZGlyZWN0IikKICAgIGV4Y2VwdCBFeGNlcHRpb24gYXMgZXhjOgogICAgICAgIGVycm9ycy5hcHBlbmQoInVybGxpYjoge306IHt9Ii5mb3JtYXQodHlwZShleGMpLl9fbmFtZV9fLCBleGMpKQogICAgaWYgX2N1cmwoKToKICAgICAgICB0cnk6CiAgICAgICAgICAgIHJhdyA9IHN1YnByb2Nlc3MucnVuKAogICAgICAgICAgICAgICAgW19jdXJsKCksICItZnNTTCIsICItLXJldHJ5IiwgIjMiLCAiLW8iLCBvcy5kZXZudWxsLCAiLXciLAogICAgICAgICAgICAgICAgICIle3VybF9lZmZlY3RpdmV9IiwgTEFURVNUX1BBR0VdLAogICAgICAgICAgICAgICAgY2hlY2s9VHJ1ZSwgdGltZW91dD0xMjAsIHN0ZG91dD1zdWJwcm9jZXNzLlBJUEUsCiAgICAgICAgICAgICAgICBzdGRlcnI9c3VicHJvY2Vzcy5ERVZOVUxMLAogICAgICAgICAgICApLnN0ZG91dC5kZWNvZGUoInV0Zi04IiwgInJlcGxhY2UiKQogICAgICAgICAgICBtYXRjaCA9IHJlLnNlYXJjaChyIi90YWcvdj8oWzAtOV1bXi9dKikiLCByYXcpCiAgICAgICAgICAgIGlmIG1hdGNoOgogICAgICAgICAgICAgICAgcmV0dXJuIG1hdGNoLmdyb3VwKDEpCiAgICAgICAgICAgIGVycm9ycy5hcHBlbmQoImN1cmw6IG5vIHRhZyBpbiB7fSIuZm9ybWF0KHJhdykpCiAgICAgICAgZXhjZXB0IEV4Y2VwdGlvbiBhcyBleGM6CiAgICAgICAgICAgIGVycm9ycy5hcHBlbmQoImN1cmw6IHt9OiB7fSIuZm9ybWF0KHR5cGUoZXhjKS5fX25hbWVfXywgZXhjKSkKICAgIGZvciBsaW5lIGluIGVycm9yczoKICAgICAgICBwcmludChsaW5lLCBmaWxlPXN5cy5zdGRlcnIpCiAgICByYWlzZSBTeXN0ZW1FeGl0KDEpCgoKZGVmIHdoZWVsX3VybCh2ZXJzaW9uKToKICAgIHJldHVybiAoCiAgICAgICAgImh0dHBzOi8vZ2l0aHViLmNvbS8iICsgUkVQTyArICIvcmVsZWFzZXMvZG93bmxvYWQvdnt9LyIKICAgICAgICAib3Blbmxvb3BfbWFzLXt9LXB5My1ub25lLWFueS53aGwiLmZvcm1hdCh2ZXJzaW9uLCB2ZXJzaW9uKQogICAgKQoKCmlmIF9fbmFtZV9fID09ICJfX21haW5fXyI6CiAgICBtb2RlID0gc3lzLmFyZ3ZbMV0KICAgIGlmIG1vZGUgPT0gImxhdGVzdCI6CiAgICAgICAgcHJpbnQod2hlZWxfdXJsKGxhdGVzdF92ZXJzaW9uKCkpKQogICAgICAgIHJhaXNlIFN5c3RlbUV4aXQoMCkKICAgIGlmIG1vZGUgPT0gImZldGNoIjoKICAgICAgICBwcmludChkb3dubG9hZChzeXMuYXJndlsyXSwgc3lzLmFyZ3ZbM10pKQogICAgICAgIHJhaXNlIFN5c3RlbUV4aXQoMCkKICAgIHJhaXNlIFN5c3RlbUV4aXQoMikK")))
        $wheelUrl = ""
        if ($Version -eq "latest") {
            $resolved = (& $pythonExe $helperPath latest 2>&1 | Out-String).Trim()
            if ($LASTEXITCODE -eq 0) { $wheelUrl = $resolved }
            else { Write-Warn "could not resolve the latest release: $resolved" }
        } else {
            $wheelUrl = "https://github.com/$repo/releases/download/v$Version/openloop_mas-$Version-py3-none-any.whl"
        }
        if (-not $wheelUrl) {
            Write-Fail "could not determine the wheel URL. Pass -Version explicitly, e.g. -Version 0.7.17"
            exit 1
        }
        $fetchOutput = (& $pythonExe $helperPath fetch $wheelUrl $wheelPath 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "could not download the wheel from $wheelUrl : $fetchOutput"
            exit 1
        }
        Write-Ok "wheel downloaded ($([math]::Round((Get-Item $wheelPath).Length / 1KB)) KB)"
    }

    # A wheel is a zip; unpack it with the standard library so the installer
    # stays dependency-free (no pip, no 7zip, no Expand-Archive quirks with
    # nested paths on old PowerShell).
    $extractDir = Join-Path $InstallDir "_wheel"
    if (Test-Path $extractDir) { Remove-Item -Recurse -Force $extractDir }
    New-Item -ItemType Directory -Force -Path $extractDir | Out-Null
    & $pythonExe -c @"
import pathlib, zipfile, sys
src = pathlib.Path(sys.argv[1]); dst = pathlib.Path(sys.argv[2])
with zipfile.ZipFile(src) as zf:
    zf.extractall(dst)
"@ $wheelPath $extractDir
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "could not unpack the wheel"
        exit 1
    }
    $unpacked = Join-Path $extractDir "mas"
    if (-not (Test-Path $unpacked)) {
        Write-Fail "the wheel did not contain the mas package"
        exit 1
    }
    if (Test-Path $pkgTarget) { Remove-Item -Recurse -Force $pkgTarget }
    Move-Item -Path $unpacked -Destination $pkgTarget
    Remove-Item -Recurse -Force $extractDir -ErrorAction SilentlyContinue
    Remove-Item -Force $wheelPath -ErrorAction SilentlyContinue
    if ($helperPath) { Remove-Item -Force $helperPath -ErrorAction SilentlyContinue }
    Write-Ok "runtime unpacked from the published wheel"
}

# ---------------------------------------------------------------- config
Write-Step "Writing configuration"
$configPath = Join-Path $InstallDir "config.json"
if (-not (Test-Path $configPath)) {
    $config = @{
        schema_version = 1
        provider       = "mock"
        model          = "mock-1"
        base_url       = ""
        api_key        = ""
        api_key_env    = "MAS_API_KEY"
        timeout_sec    = 120
        max_retries    = 2
        state_dir      = $InstallDir
        team           = "default"
        language       = "zh"
        budget         = @{ max_iterations = 8; deadline_sec = 900; max_tokens = 200000 }
        stagnation_limit = 3
        orchestration  = @{ parallel_workers = 1; reviewers = 1; review_mode = "all" }
        fallbacks      = @()
    } | ConvertTo-Json -Depth 5
    Write-Utf8NoBom $configPath $config
    Write-Ok "config created (offline mock provider)"
} else {
    Write-Ok "config already exists, kept as is"
}

# ---------------------------------------------------------------- launcher
Write-Step "Creating launcher"
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
$cmdPath = Join-Path $BinDir "mas.cmd"
$cmdBody = @"
@echo off
setlocal
set "MAS_HOME=$InstallDir"
set "MAS_STATE_DIR=$InstallDir"
set "MAS_CONFIG_PATH=$InstallDir\config.json"
set "PYTHONPATH=$AppDir;%PYTHONPATH%"
"$pythonExe" "$AppDir\mas\__main__.py" %*
"@
Write-Utf8NoBom $cmdPath $cmdBody
Write-Ok "launcher: $cmdPath"

# ---------------------------------------------------------------- PATH
if (-not $SkipPath) {
    Write-Step "Adding $BinDir to the user PATH"
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($null -eq $userPath) { $userPath = "" }
    $parts = $userPath -split ";" | Where-Object { $_ -ne "" }
    if ($parts -contains $BinDir) {
        Write-Ok "already in PATH"
    } else {
        $newPath = if ($parts.Count -gt 0) { ($parts + $BinDir) -join ";" } else { $BinDir }
        [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
        Write-Ok "added (new terminals will see it)"
    }
}

# ---------------------------------------------------------------- install
# One implementation, one behaviour: the python installer is the single
# source of truth for both the agent skills and the MCP registration. This
# script only wires the environment up (PYTHONPATH / MAS_STATE_DIR) and
# calls it, so the two install paths can never drift apart.
$env:MAS_STATE_DIR = $InstallDir
$env:MAS_CONFIG_PATH = Join-Path $InstallDir "config.json"
$env:PYTHONPATH = $AppDir
if (-not $SkipSkills -or -not $SkipMcp) {
    Write-Step "Installing agent skills and registering the MCP server"
    # The runtime is started through its *package path*, not through
    # ``-m mas``. ``python -m`` puts the current working directory first
    # on ``sys.path``; when the installer runs inside a source checkout,
    # the checkout's ``mas/`` package shadows the one that was just
    # installed. The skills and the MCP registration would then come from
    # the checkout - so the freshly downloaded wheel was never used at all.
    $installArgs = @((Join-Path $AppDir "mas\__main__.py"), "install", "--python", $pythonExe)
    if ($SkipSkills) { $installArgs += "--skip-skills" }
    if ($SkipMcp) { $installArgs += "--skip-mcp" }
    & $pythonExe @installArgs
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "skills installed and MCP server registered"
    } else {
        Write-Warn "the installer reported problems - check the output above"
    }
}


# ---------------------------------------------------------------- dsh plugin
# Optional: install the DeepSeek Harness plugin that exposes the MAS loop as
# DSH tools. Off by default so the main line stays dependency free - a
# beginner never needs Node, DSH or npm to get a working MAS.
if ($InstallDshPlugin) {
    Write-Step "Installing the DeepSeek Harness plugin (profile: $DshProfile)"
    $pluginSrc = Join-Path $SourceRoot "integrations\dsh-openloop-mas"
    if (-not (Test-Path $pluginSrc)) {
        Write-Warn "plugin source not found at $pluginSrc - skipping"
    } else {
        $dshCmd = Get-Command dsh -ErrorAction SilentlyContinue
        if (-not $dshCmd) {
            Write-Warn "'dsh' is not on PATH - skipping the DSH plugin. Install DeepSeek Harness first."
        } else {
            $pkgDir = Join-Path $InstallDir "dsh-plugin"
            New-Item -ItemType Directory -Force -Path $pkgDir | Out-Null
            Push-Location $pluginSrc
            try {
                & npm pack --pack-destination $pkgDir
                $packExit = $LASTEXITCODE
            } finally {
                Pop-Location
            }
            if ($packExit -ne 0) {
                Write-Warn "npm pack failed - skipping the DSH plugin"
            } else {
                $tgz = Get-ChildItem $pkgDir -Filter "dsh-openloop-mas-*.tgz" |
                    Sort-Object LastWriteTime -Descending | Select-Object -First 1
                if (-not $tgz) {
                    Write-Warn "no tarball produced - skipping the DSH plugin"
                } else {
                    Write-Step "Adding the plugin to the '$DshProfile' profile"
                    # The DSH command line can be slow (it bootstraps a profile) and
                    # on some machines the generated .cmd shim recurses. Both must
                    # degrade into a warning, never take the whole installer down.
                    $addOk = $false
                    try {
                        $job = Start-Job -ScriptBlock {
                            param($profile, $archive)
                            & dsh plugin --profile $profile add ("dsh-openloop-mas@file:" + $archive) 2>&1
                            $LASTEXITCODE
                        } -ArgumentList $DshProfile, $tgz.FullName
                        if (Wait-Job $job -Timeout 180) {
                            $out = Receive-Job $job
                            $out | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray }
                            $addOk = ($out | Select-Object -Last 1) -eq 0
                        } else {
                            Stop-Job $job
                            Write-Warn "the 'dsh plugin add' command did not finish within 180s - skipping"
                        }
                        Remove-Job $job -Force -ErrorAction SilentlyContinue
                    } catch {
                        Write-Warn "the 'dsh plugin add' command failed: $($_.Exception.Message)"
                    }
                    if ($addOk) {
                        Write-Ok "DSH plugin installed from $($tgz.Name)"
                    } else {
                        Write-Warn "the DSH plugin was not installed automatically"
                        Write-Host "      the tarball is ready: $($tgz.FullName)" -ForegroundColor DarkGray
                        Write-Host "      install it manually with:" -ForegroundColor DarkGray
                        Write-Host "        dsh plugin --profile $DshProfile add `"dsh-openloop-mas@file:$($tgz.FullName)`"" -ForegroundColor DarkGray
                        Write-Host "      the 'desktop' profile is managed by the DSH Electron app;" -ForegroundColor DarkGray
                        Write-Host "      install it from the DSH plugin panel, or use -DshProfile cli." -ForegroundColor DarkGray
                    }
                }
            }
        }
    }
}

# ---------------------------------------------------------------- verify
Write-Step "Running the self-check"
$env:MAS_STATE_DIR = $InstallDir
$env:MAS_CONFIG_PATH = Join-Path $InstallDir "config.json"
$env:PYTHONPATH = $AppDir
$masMain = Join-Path $AppDir "mas\__main__.py"
& $pythonExe $masMain doctor
$doctorExit = $LASTEXITCODE

if (-not $SkipDemo) {
    Write-Step "Running the offline demo (no API key needed)"
    & $pythonExe $masMain demo
    $demoExit = $LASTEXITCODE

    Write-Step "Running the offline benchmark"
    & $pythonExe $masMain bench --repeats 1
    $benchExit = $LASTEXITCODE

    Write-Step "Running the closure self-check"
    & $pythonExe $masMain verify
    $verifyExit = $LASTEXITCODE
} else {
    $demoExit = 0
    $benchExit = 0
    $verifyExit = 0
}

Write-Host ""
if ($doctorExit -eq 0 -and $demoExit -eq 0 -and $benchExit -eq 0 -and $verifyExit -eq 0) {
    Write-Host "Installation finished." -ForegroundColor Green
} else {
    Write-Host "Installation finished with warnings (doctor=$doctorExit demo=$demoExit bench=$benchExit verify=$verifyExit)." -ForegroundColor Yellow
}
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1) open a new terminal, then run:  mas demo"
Write-Host "  2) configure interactively:  mas init --wizard"
Write-Host "  3) point it at a real model:"
Write-Host "       mas init --provider openai_compat --model <model> --base-url <endpoint>"
Write-Host "       set MAS_API_KEY=<your key>"
Write-Host "  4) run a task:  mas run --goal ""..."" --criteria ""..."""
Write-Host "  5) review the result:  mas report    (or: mas replay)"
Write-Host "  6) serve it remotely:  mas http --port 8765"
Write-Host "  7) (optional) plug it into DeepSeek Harness:" -ForegroundColor DarkGray
Write-Host "       powershell -ExecutionPolicy Bypass -File .\install.ps1 -InstallDshPlugin"
Write-Host ""
exit 0
