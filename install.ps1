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
    [switch]$SkipDemo
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

# ---------------------------------------------------------------- skills
if (-not $SkipSkills) {
    Write-Step "Installing agent skills"
    $skillSource = Join-Path $SourceRoot "skills"
    $targets = @(
        (Join-Path $env:USERPROFILE ".codex\skills"),
        (Join-Path $env:USERPROFILE ".claude\skills"),
        (Join-Path $env:USERPROFILE ".agents\skills"),
        (Join-Path $env:USERPROFILE ".gemini\skills")
    )
    $installed = 0
    foreach ($skillDir in Get-ChildItem -Path $skillSource -Directory -ErrorAction SilentlyContinue) {
        foreach ($targetRoot in $targets) {
            if (-not (Test-Path $targetRoot)) { continue }
            $dest = Join-Path $targetRoot $skillDir.Name
            New-Item -ItemType Directory -Force -Path $dest | Out-Null
            Copy-Item -Force (Join-Path $skillDir.FullName "*") $dest -Recurse -ErrorAction SilentlyContinue
            $installed++
        }
    }
    if ($installed -gt 0) { Write-Ok "$installed skill folder(s) installed" } else { Write-Warn "no skill target directory found" }
}

# ---------------------------------------------------------------- mcp
if (-not $SkipMcp) {
    Write-Step "Registering the MCP server"
    $mcpBlock = @"

[mcp_servers.mas_loop]
command = "$($pythonExe -replace '\\', '/')"
args = ["-m", "mas", "serve"]
startup_timeout_sec = 30.0
tool_timeout_sec = 300.0
env = { PYTHONPATH = "$($AppDir -replace '\\', '/')" }
"@
    $codexConfig = Join-Path $env:USERPROFILE ".codex\config.toml"
    if (Test-Path $codexConfig) {
        $text = Get-Content -Raw -Encoding UTF8 $codexConfig
        if ($text -match "\[mcp_servers\.mas_loop\]") {
            Write-Ok "already registered in ~/.codex/config.toml"
        } else {
            $backup = "$codexConfig.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
            Copy-Item $codexConfig $backup
            Add-Content -Path $codexConfig -Value $mcpBlock -Encoding UTF8
            Write-Ok "registered in ~/.codex/config.toml (backup: $backup)"
        }
    } else {
        Write-Warn "~/.codex/config.toml not found, skipped Codex MCP registration"
    }

    $claudeConfig = Join-Path $env:USERPROFILE ".claude.json"
    if (Test-Path $claudeConfig) {
        $claude = Get-Content -Raw -Encoding UTF8 $claudeConfig | ConvertFrom-Json
        if ($null -eq $claude.mcpServers) { $claude | Add-Member -MemberType NoteProperty -Name mcpServers -Value ([PSCustomObject]@{}) }
        if ($claude.mcpServers.PSObject.Properties.Name -contains "mas_loop") {
            Write-Ok "already registered in ~/.claude.json"
        } else {
            $backup = "$claudeConfig.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
            Copy-Item $claudeConfig $backup
            $entry = [PSCustomObject]@{
                command = $pythonExe
                args    = @("-m", "mas", "serve")
                env     = @{ PYTHONPATH = $AppDir }
            }
            $claude.mcpServers | Add-Member -MemberType NoteProperty -Name mas_loop -Value $entry
            Write-Utf8NoBom $claudeConfig ($claude | ConvertTo-Json -Depth 12)
            Write-Ok "registered in ~/.claude.json (backup: $backup)"
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
} else {
    $demoExit = 0
}

Write-Host ""
if ($doctorExit -eq 0 -and $demoExit -eq 0) {
    Write-Host "Installation finished." -ForegroundColor Green
} else {
    Write-Host "Installation finished with warnings (doctor=$doctorExit demo=$demoExit)." -ForegroundColor Yellow
}
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1) open a new terminal, then run:  mas demo"
Write-Host "  2) point it at a real model:"
Write-Host "       mas init --provider openai_compat --model <model> --base-url <endpoint>"
Write-Host "       set MAS_API_KEY=<your key>"
Write-Host "  3) run a task:  mas run --goal ""..."" --criteria ""..."""
Write-Host ""
exit 0
