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
    [string]$DshProfile = "desktop"
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

# ---------------------------------------------------------------- app
Write-Step "Installing runtime into $AppDir"
New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
$pkgSource = Join-Path $SourceRoot "mas"
if (-not (Test-Path $pkgSource)) {
    Write-Fail "package source not found: $pkgSource"
    exit 1
}
$pkgTarget = Join-Path $AppDir "mas"
if (Test-Path $pkgTarget) { Remove-Item -Recurse -Force $pkgTarget }
Copy-Item -Recurse -Force $pkgSource $pkgTarget
Get-ChildItem -Path $pkgTarget -Directory -Filter "__pycache__" -Recurse -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Write-Ok "runtime copied"

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
set "PYTHONPATH=$AppDir;%PYTHONPATH%"
"$pythonExe" -m mas %*
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
$env:PYTHONPATH = $AppDir
if (-not $SkipSkills -or -not $SkipMcp) {
    Write-Step "Installing agent skills and registering the MCP server"
    $installArgs = @("-m", "mas", "install", "--python", $pythonExe)
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
$env:PYTHONPATH = $AppDir
& $pythonExe -m mas doctor
$doctorExit = $LASTEXITCODE

if (-not $SkipDemo) {
    Write-Step "Running the offline demo (no API key needed)"
    & $pythonExe -m mas demo
    $demoExit = $LASTEXITCODE

    Write-Step "Running the offline benchmark"
    & $pythonExe -m mas bench --repeats 1
    $benchExit = $LASTEXITCODE

    Write-Step "Running the closure self-check"
    & $pythonExe -m mas verify
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
