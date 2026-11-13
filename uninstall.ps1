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

# One implementation, one behaviour: the python installer owns both the
# skills and the MCP registration, so removing them is also one call.
Write-Host "Removing agent skills and the MCP registration..."
$pythonExe = ""
foreach ($name in @("python3", "python", "py")) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if (-not $cmd) { continue }
    try {
        & $cmd.Source -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { $pythonExe = $cmd.Source; break }
    } catch { }
}
if ($pythonExe) {
    $appDir = Join-Path $InstallDir "app"
    if (Test-Path (Join-Path $appDir "mas\installer.py")) {
        $env:PYTHONPATH = $appDir
        & $pythonExe -m mas uninstall
    } else {
        Write-Host "[!!] MAS runtime not found under $appDir - remove the skills and the MCP entry by hand."
    }
} else {
    Write-Host "[!!] Python 3.10+ not found - remove the skills and the MCP entry by hand."
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
