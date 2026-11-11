<#
.SYNOPSIS
    Remove OpenLoop MAS from this machine.
.PARAMETER KeepData
    Keep ~/.mas (config, ledger, tickets) so history is not lost.
#>
[CmdletBinding()]
param(
    [string]$InstallDir = "",
    [switch]$KeepData
)
$ErrorActionPreference = "Stop"
if (-not $InstallDir) { $InstallDir = Join-Path $env:USERPROFILE ".mas" }

Write-Host "Removing agent skills..."
foreach ($root in @(".codex\skills", ".claude\skills", ".agents\skills", ".gemini\skills")) {
    foreach ($skill in @("mas-loop", "mas-review", "mas-scaffold")) {
        $path = Join-Path $env:USERPROFILE "$root\$skill"
        if (Test-Path $path) {
            Remove-Item -Recurse -Force $path
            Write-Host "[OK] removed $path"
        }
    }
}

Write-Host "Removing MCP registration..."
$codexConfig = Join-Path $env:USERPROFILE ".codex\config.toml"
if (Test-Path $codexConfig) {
    $lines = Get-Content -Encoding UTF8 $codexConfig
    $out = New-Object System.Collections.Generic.List[string]
    $skipping = $false
    foreach ($line in $lines) {
        if ($line -match '^\[mcp_servers\.mas_loop\]') { $skipping = $true; continue }
        if ($skipping) {
            if ($line -match '^\[') { $skipping = $false } else { continue }
        }
        $out.Add($line)
    }
    Copy-Item $codexConfig "$codexConfig.bak-uninstall-$(Get-Date -Format yyyyMMdd-HHmmss)"
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($codexConfig, (($out -join "`n") + "`n"), $utf8)
    Write-Host "[OK] cleaned ~/.codex/config.toml"
}

if ($KeepData) {
    Write-Host "[OK] kept $InstallDir (use -KeepData:$false to delete it)"
} else {
    if (Test-Path $InstallDir) {
        Remove-Item -Recurse -Force $InstallDir
        Write-Host "[OK] removed $InstallDir"
    }
}
Write-Host "Done."
