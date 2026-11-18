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
    [string]$Wheel = "",
    [switch]$NoBootstrap,
    [string]$BootstrapProbe = "",
    [switch]$ForceBootstrap
)

$ErrorActionPreference = "Stop"
$script:Steps = @()

# Windows resolves a native executable through PATHEXT. In an
# environment where PATHEXT is missing - a service account, a
# scheduled task, a CI step that builds its environment from scratch -
# the shell's own native calls stop working and `$LASTEXITCODE` stays
# empty.
#
# The condition is *not* "PATHEXT is empty". A restricted environment
# can carry a PATHEXT that is present but useless: the rehearsal
# environment this project builds for its own tests has
# ``PATHEXT=.CPL``, which lists no executable extension at all. The
# shell then treats ``python.exe`` as a *document*: calling it in a
# pipeline raises "Cannot run a document in the middle of a pipeline",
# and calling it on its own produces no output and leaves
# `$LASTEXITCODE` empty. A perfectly good interpreter was reported as
# unusable, and the machine was told it had no Python when it had one.
#
# So the check is on the *content*: the list has to name ``.EXE`` for
# native executables to be reachable at all.
$masPathExtensions = @()
if ($env:PATHEXT) { $masPathExtensions = @($env:PATHEXT -split ";" | Where-Object { $_ }) }
if (-not ($masPathExtensions -contains ".EXE")) {
    $env:PATHEXT = (($masPathExtensions + @(".COM", ".EXE", ".BAT", ".CMD")) | Select-Object -Unique) -join ";"
}

# The rehearsal switches come from the command line *or* from the
# environment. Both spellings are honoured, because the POSIX
# installer has only the environment, and the two installers have to
# take the same decision under the same name - a rehearsal that only
# one of them understands is a rehearsal of half the promise.
$masForceBootstrap = $ForceBootstrap -or ($env:MAS_FORCE_BOOTSTRAP -eq "1")
if (-not $BootstrapProbe -and $env:MAS_BOOTSTRAP_PROBE) {
    $BootstrapProbe = $env:MAS_BOOTSTRAP_PROBE
}

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
# The rules below are a *copy* of the rules in mas/bootstrap.py, and the
# twenty-sixth gate (bootstrap_path) is what keeps the copy honest: it
# compares the two and fails the build if they drift apart. The copy has
# to exist: at this point in the script there may be no Python on the
# machine at all, so the module cannot be imported yet - the module is
# the authority for everyone *after* the interpreter exists.
$MAS_MIN_PYTHON = "3.10"
$MAS_BOOTSTRAP_PYTHON = "3.12.8"
$MAS_BOOTSTRAP_URL = "https://www.python.org/ftp/python/$MAS_BOOTSTRAP_PYTHON/python-$MAS_BOOTSTRAP_PYTHON-amd64.exe"

function Invoke-MasProcess {
    # Start an executable and wait for it, *without* going through the
    # shell's native-command resolution.
    #
    # PowerShell's `&` operator resolves a native executable through
    # PATHEXT. Where PATHEXT is missing the call does nothing at all:
    # no output, no error, and `$LASTEXITCODE` left empty. Anything
    # that reads that empty value concludes the executable is broken.
    #
    # That is not a hypothetical: the interpreter probe ran in exactly
    # such an environment and reported a perfectly good Python as
    # unusable - so the machine was told it had no Python when it had
    # one. Starting the process through .NET avoids the shell
    # entirely, and the probe then works the same on every machine.
    # The arguments are a *list*, not a string. A string has to be
    # quoted by hand, and a path with a space in it then breaks the
    # call in a way that is invisible until the installer dies halfway
    # through. A list is quoted for us.
    param([string]$Exe, [string[]]$Arguments = @())
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Exe
    if ($Arguments -and $Arguments.Count -gt 0) {
        $psi.Arguments = ($Arguments | ForEach-Object { ConvertTo-MasArgument $_ }) -join " "
    }
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    [void]$proc.Start()
    $stdout = $proc.StandardOutput.ReadToEnd()
    $stderr = $proc.StandardError.ReadToEnd()
    $proc.WaitForExit()
    return @{
        ExitCode = $proc.ExitCode
        Output = $stdout
        Error = $stderr
    }
}

function ConvertTo-MasArgument {
    # Quote one argument for the Windows command line.
    #
    # The rules are the ones the C runtime uses: a backslash is only
    # special when it stands in front of a quote, and a quote is
    # doubled. An argument that contains no space, no quote and no
    # tab is passed through unchanged, which keeps the common case
    # readable in a log.
    param([string]$Value)
    if ($null -eq $Value) { return '""' }
    if ($Value -eq "") { return '""' }
    if ($Value -notmatch '[\s"]') { return $Value }
    $escaped = $Value -replace '(\*)"', '$1$1\"'
    $escaped = $escaped -replace '(\+)$', '$1$1'
    return '"' + $escaped + '"'
}

function Test-MasPython {
    # Ask the interpreter itself what version it is. A file called
    # `python.exe` in an odd folder is not evidence of anything.
    param([string]$Exe)
    if (-not $Exe) { return $false }
    if (-not (Test-Path -LiteralPath $Exe)) { return $false }
    try {
        $run = Invoke-MasProcess $Exe @('-c', 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)')
        return ($run.ExitCode -eq 0)
    } catch {
        return $false
    }
}

function Get-MasVersion {
    param([string]$Exe)
    try {
        $run = Invoke-MasProcess $Exe @('-c', 'import sys; print(''%d.%d.%d'' % sys.version_info[:3])')
        if ($run.ExitCode -ne 0) { return "" }
        return $run.Output.Trim()
    } catch {
        return ""
    }
}

function Test-MasStoreStub {
    # Windows ships "app execution aliases" in a folder called
    # WindowsApps: `python.exe` and `python3.exe` there are *Store
    # stubs*. Running one does not run Python - it opens the Microsoft
    # Store, on the beginner's screen, with no error and no output.
    #
    # The installer must therefore never *execute* a stub, and never
    # report one as "a Python was found": the machine has no Python, and
    # saying otherwise sends the user down a path that cannot work.
    param([string]$Path)
    if (-not $Path) { return $false }
    try {
        $full = [System.IO.Path]::GetFullPath($Path)
    } catch {
        return $false
    }
    if ($full -notmatch "WindowsApps") { return $false }
    # A real Python installed *into* the Store's own folder would be
    # under Programs\Python, never directly inside WindowsApps.
    if ($full -match "WindowsApps\\Python") { return $false }
    return $true
}

function Find-MasBootstrappedPython {
    # The per-user install lands in %LOCALAPPDATA%\Programs\Python\Python3xx
    # and is *not* on PATH (PrependPath=0), so the interpreter has to be
    # looked up where the installer put it. The newest version wins: a
    # machine that already had an old Python and then got a new one
    # should keep the new one.
    $Root = Join-Path $env:LOCALAPPDATA "Programs\Python"
    if (-not (Test-Path -LiteralPath $Root)) { return $null }
    $best = $null
    $bestVersion = ""
    foreach ($dir in (Get-ChildItem -LiteralPath $Root -Directory -ErrorAction SilentlyContinue)) {
        $exe = Join-Path $dir.FullName "python.exe"
        if (-not (Test-Path -LiteralPath $exe)) { continue }
        # A Store alias is not an interpreter. "Finding" one here would
        # report a Python that cannot run.
        if (Test-MasStoreStub $exe) { continue }
        if (-not (Test-MasPython $exe)) { continue }
        $version = Get-MasVersion $exe
        if (-not $version) { continue }
        if (-not $best) { $best = $exe; $bestVersion = $version; continue }
        if ([version]$version -gt [version]$bestVersion) { $best = $exe; $bestVersion = $version }
    }
    return $best
}
function Wait-MasBootstrappedPython {
    # The official Windows installer is a *bootstrapper*: it can return
    # before the payload has finished being written. Looking for the
    # interpreter once, right after it exits, therefore finds nothing on
    # a slow disk - and the installer then tells the user that Python is
    # still not available, which is both false and exactly the manual
    # step this bootstrap exists to remove.
    #
    # So the lookup is repeated for a while. The interpreter appearing is
    # the *only* thing that ends the wait: it is asked what version it
    # is, every time.
    param([int]$TimeoutSeconds = 120)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $attempt = 0
    while ($true) {
        $attempt++
        $found = Find-MasBootstrappedPython
        if ($found) { return $found }
        if ((Get-Date) -ge $deadline) { return $null }
        if ($attempt -eq 1) {
            Write-Step "waiting for the Python installer to finish writing its files"
        }
        Start-Sleep -Seconds 3
    }
}

$pythonExe = $null
$masNoBootstrap = $NoBootstrap -or ($env:MAS_NO_BOOTSTRAP -eq "1")
if ($Python -and -not $masForceBootstrap -and -not $masNoBootstrap) {
    if (Test-Path $Python) { $pythonExe = (Resolve-Path $Python).Path }
}
if (-not $pythonExe -and -not $masForceBootstrap -and -not $masNoBootstrap) {
    # The PATH is scanned directly instead of through Get-Command.
    #
    # Get-Command resolves a bare name through PATHEXT, and PATHEXT
    # is not something a machine is guaranteed to have: a restricted
    # environment can carry a PATHEXT that lists no executable
    # extension at all, and Get-Command then reports that there is no
    # such command even though the file is sitting right there. The
    # machine was told it had no Python when it had one.
    #
    # So the folders on PATH are walked directly, and every candidate
    # is asked whether it runs.
    foreach ($entry in ($env:PATH -split ";")) {
        if (-not $entry) { continue }
        foreach ($name in @("python3.exe", "python.exe", "python3", "python")) {
            $candidate = Join-Path $entry $name
            if (-not (Test-Path -LiteralPath $candidate)) { continue }
            # A Store stub is not a Python. Executing it would open the
            # Microsoft Store on the user's screen instead of running an
            # interpreter, so it is skipped rather than tried.
            if (Test-MasStoreStub $candidate) { continue }
            if (Test-MasPython $candidate) { $pythonExe = $candidate; break }
        }
        if ($pythonExe) { break }
    }
}
if (-not $pythonExe) {
    # The refusal comes from the switch *or* from the environment
    # variable, because the POSIX installer uses the variable and the
    # two installers have to take the same decision under the same
    # name. A refusal that only one of them honours is a refusal that
    # half the machines ignore.
    if ($masNoBootstrap) {
        # The refusal wins over everything else, including the
        # rehearsal switch. A user who says "do not install a Python"
        # must never have one installed behind their back - and the
        # rehearsal must not become a way around that.
        Write-Fail "Python 3.10+ not found, and -NoBootstrap was given. Install Python from https://www.python.org/downloads/ and re-run."
        exit 1
    }
    Write-Step "No Python 3.10+ on this machine - installing one for you (per-user, no admin rights needed)"
    if ($BootstrapProbe) {
        # The rehearsal.
        #
        # The bootstrap path can only be exercised on a machine that has
        # no Python - and no developer owns such a machine. So the path
        # that promises "one click, no preparation" was never actually
        # executed anywhere: it was only *described*.
        #
        # The rehearsal runs the real branch with the download replaced
        # by a staged interpreter: the machine is told it has no Python,
        # the installer goes to fetch one, and the interpreter is then
        # found *by the same lookup the real bootstrap uses* - in the
        # folder the real bootstrap would have put it in. Everything
        # else - the copy, the skills, the MCP registration, the
        # self-check - is the real thing.
        Write-Step "Rehearsal mode: no Python will be downloaded ($BootstrapProbe)"
        # The material for the rehearsal is an interpreter that is
        # *not* on PATH - otherwise the branch above would never have
        # been entered. The rehearsal therefore has to be handed one
        # explicitly, and it is staged where the real bootstrap would
        # have put it.
        $material = $env:MAS_PROBE_PYTHON
        if ($env:MAS_PROBE_STAGE -eq "0") {
            # The counter-proof: stage nothing, so the lookup finds
            # nothing. The installer has to report that honestly.
            Write-Step "Rehearsal mode: nothing will be staged (the counter-proof)"
            $env:LOCALAPPDATA = $BootstrapProbe
            $pythonExe = Find-MasBootstrappedPython
            if (-not $pythonExe) {
                Write-Fail "the rehearsal could not find the interpreter it staged"
                exit 1
            }
            Write-Ok "rehearsal found the interpreter at $pythonExe"
        } else {
        if (-not $material) {
            Write-Fail "the rehearsal needs an interpreter to stage: set MAS_PROBE_PYTHON to a Python 3.10+ executable"
            exit 1
        }
        if (-not (Test-MasPython $material)) {
            Write-Fail "the rehearsal interpreter is not a usable Python 3.10+: $material"
            exit 1
        }
        # The real bootstrap puts the interpreter under
        # %LOCALAPPDATA%\Programs\Python\Python3xx. The rehearsal
        # moves LOCALAPPDATA to its own folder, so the *production*
        # lookup finds the interpreter where it looks - unchanged code,
        # unchanged path, no special case.
        $probeRoot = $BootstrapProbe
        $probePrograms = Join-Path $probeRoot "Programs\Python"
        $probeFolder = Join-Path $probePrograms ("Python" + ($MAS_BOOTSTRAP_PYTHON -replace "\.", ""))
        if (Test-Path -LiteralPath $probeFolder) {
            Remove-Item -LiteralPath $probeFolder -Recurse -Force -ErrorAction SilentlyContinue
        }
        New-Item -ItemType Directory -Force -Path $probeFolder | Out-Null
        # A *link* to the real interpreter directory, not a copy: a copy
        # would be locked by the previous rehearsal's processes and the
        # rehearsal would fail for a reason that has nothing to do with
        # the code it is meant to exercise.
        $materialDir = Split-Path -Parent $material
        # The link stands in for the whole interpreter *folder*, so the
        # lookup finds ``python.exe`` in it exactly where the real
        # bootstrap would have left it.
        $linkPath = Join-Path $probePrograms ("Python" + ($MAS_BOOTSTRAP_PYTHON -replace "\.", ""))
        if (Test-Path -LiteralPath $linkPath) {
            Remove-Item -LiteralPath $linkPath -Recurse -Force -ErrorAction SilentlyContinue
        }
        New-Item -ItemType Junction -Path $linkPath -Target $materialDir -ErrorAction SilentlyContinue | Out-Null
        $probeExe = Join-Path $linkPath (Split-Path -Leaf $material)
        if (-not (Test-Path -LiteralPath $probeExe)) {
            # No junction available (older Windows, or a filesystem that
            # does not support it): copy the executable as a fallback.
            $probeExe = Join-Path $probeFolder (Split-Path -Leaf $material)
            Copy-Item -LiteralPath $material -Destination $probeExe -Force
        }
        Write-Ok "rehearsal interpreter staged at $probeExe"
        $env:LOCALAPPDATA = $probeRoot
        Write-Step "rehearsal: LOCALAPPDATA now points at $probeRoot"
        # The lookup below is the production one, unchanged.
        $pythonExe = Find-MasBootstrappedPython
        if (-not $pythonExe) {
            Write-Fail "the rehearsal could not find the interpreter it staged"
            exit 1
        }
        Write-Ok "rehearsal found the interpreter at $pythonExe"
        }
    } else {
    $tmpExe = Join-Path ([System.IO.Path]::GetTempPath()) "mas-python-$MAS_BOOTSTRAP_PYTHON-amd64.exe"
    $downloaded = $false
    try {
        # Invoke-WebRequest honours the Windows system proxy. The
        # HTTPS_PROXY environment variable is *not* read by Windows
        # PowerShell 5.1, so it is applied explicitly as a second try:
        # a large share of users reach the internet only through a local
        # proxy, and a download that fails there would send the user back
        # to the manual step this bootstrap exists to remove.
        try {
            Invoke-WebRequest -Uri $MAS_BOOTSTRAP_URL -OutFile $tmpExe -UseBasicParsing -ErrorAction Stop | Out-Null
            $downloaded = $true
        } catch {
            if ($env:HTTPS_PROXY -or $env:https_proxy) {
                $proxyUrl = $env:HTTPS_PROXY
                if (-not $proxyUrl) { $proxyUrl = $env:https_proxy }
                Write-Step "Retrying the download through the proxy at $proxyUrl"
                Invoke-WebRequest -Uri $MAS_BOOTSTRAP_URL -OutFile $tmpExe -UseBasicParsing -Proxy $proxyUrl -ErrorAction Stop | Out-Null
                $downloaded = $true
            } else {
                throw
            }
        }
    } catch {
        Write-Fail "could not download the Python installer ($MAS_BOOTSTRAP_URL): $($_.Exception.Message)"
        Write-Fail "install Python 3.10+ from https://www.python.org/downloads/ and re-run this script"
        exit 1
    }
    if ($downloaded) {
        Write-Ok "Python installer downloaded ($([math]::Round((Get-Item $tmpExe).Length / 1KB)) KB)"
        # Quiet, per-user, no PATH change, no launcher, no file association:
        # the installer changes as little of the machine as it can get away
        # with, and the interpreter is found afterwards by path - not by
        # hoping the registry noticed.
        $installArgs = @(
            "/quiet",
            "InstallAllUsers=0",
            "PrependPath=0",
            "Include_launcher=0",
            "Include_test=0",
            "Include_doc=0",
            "AssociateFiles=0",
            "Shortcuts=0",
            "TargetDir=$(Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312')"
        )
        try {
            $proc = Start-Process -FilePath $tmpExe -ArgumentList $installArgs -Wait -PassThru
            if ($proc.ExitCode -ne 0 -and $proc.ExitCode -ne 3010) {
                Write-Warn "the Python installer exited with code $($proc.ExitCode)"
            }
        } catch {
            Write-Warn "could not run the Python installer: $($_.Exception.Message)"
        }
        Remove-Item -LiteralPath $tmpExe -Force -ErrorAction SilentlyContinue
        # The installer is a bootstrapper: it may return before the
        # interpreter is on disk. Waiting for the interpreter to appear
        # is what makes this path work on a slow machine - a single
        # lookup would report a false "still not available".
        $pythonExe = Wait-MasBootstrappedPython
        if ($pythonExe) {
            Write-Ok "Python $(Get-MasVersion $pythonExe) installed at $pythonExe"
        }
    }
    }
}
if (-not $pythonExe) {
    Write-Fail "Python 3.10+ is still not available on this machine."
    Write-Fail "install it from https://www.python.org/downloads/ and re-run this script"
    exit 1
}
$pyVersion = Get-MasVersion $pythonExe
Write-Ok "Python $pyVersion at $pythonExe"

# The interpreter path is normalised to an absolute Windows path *before*
# it is written into the MCP registration. The registration is read by a
# host process (Codex / Claude Code), not by this script: a relative or
# POSIX-style path would resolve against the host's working directory -
# which is usually not the one the installer ran in.
$pythonExe = (Invoke-MasProcess $pythonExe @('-c', 'import sys; print(sys.executable)')).Output.Trim()

# ---------------------------------------------------------------- app
Write-Step "Installing runtime into $AppDir"
New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
$pkgTarget = Join-Path $AppDir "mas"
$pkgSource = Join-Path $SourceRoot "mas"

# An explicitly supplied wheel always wins. Without this, a caller that
# hands the installer a wheel (the release pipeline, the CI install matrix)
# while running from a source checkout silently got the *source* instead:
# the wheel parameter was parsed, checked, and then dropped on the floor.
# The two paths install different builds, so the choice has to be explicit
# rather than a side effect of the directory the script happens to sit in.
$useWheel = [bool]$Wheel
if ($useWheel) {
    # A source checkout is also present. Say so, so nobody is surprised
    # that the wheel - not the checkout - is what ended up on disk.
    if (Test-Path $pkgSource) {
        Write-Step "A wheel was supplied - using it instead of the source checkout next to this script"
    }
}

if (-not $useWheel -and (Test-Path $pkgSource)) {
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
            $latest = Invoke-MasProcess $pythonExe @($helperPath, "latest")
            $resolved = ("$($latest.Output)$($latest.Error)").Trim()
            if ($latest.ExitCode -eq 0) { $wheelUrl = $resolved }
            else { Write-Warn "could not resolve the latest release: $resolved" }
        } else {
            $wheelUrl = "https://github.com/$repo/releases/download/v$Version/openloop_mas-$Version-py3-none-any.whl"
        }
        if (-not $wheelUrl) {
            Write-Fail "could not determine the wheel URL. Pass -Version explicitly, e.g. -Version 0.7.18"
            exit 1
        }
        $fetch = Invoke-MasProcess $pythonExe @($helperPath, "fetch", $wheelUrl, $wheelPath)
        $fetchOutput = ("$($fetch.Output)$($fetch.Error)").Trim()
        if ($fetch.ExitCode -ne 0) {
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
    # The unpacking program is written to a file rather than passed to
    # ``python -c``. A multi-line program handed to ``-c`` is a
    # quoting minefield: the mangling is silent, so the installer keeps
    # running and only the unpacker dies with a syntax error. A file
    # has no such problem, and it is also what the error messages refer
    # to.
    $unpackPath = Join-Path $InstallDir "_unpack_wheel.py"
    Write-Utf8NoBom $unpackPath @"
import pathlib, sys, zipfile
src = pathlib.Path(sys.argv[1]); dst = pathlib.Path(sys.argv[2])
with zipfile.ZipFile(src) as zf:
    zf.extractall(dst)
"@
    $unpack = Invoke-MasProcess $pythonExe @($unpackPath, $wheelPath, $extractDir)
    if ($unpack.ExitCode -ne 0) {
        $unpackError = ("$($unpack.Output)$($unpack.Error)").Trim()
        Write-Fail "could not unpack the wheel: $unpackError"
        exit 1
    }
    Remove-Item -LiteralPath $unpackPath -Force -ErrorAction SilentlyContinue
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
    # A redirected home means this run is not the user own machine -
    # a test, a sandbox, a CI job. Writing the *real* user PATH from
    # there would put a throwaway directory on the user PATH
    # forever, and every later run would lose to it. The registry is
    # only touched when the home is the real one.
    $realHome = [Environment]::GetFolderPath("UserProfile")
    $homeIsRedirected = $env:USERPROFILE -and ($env:USERPROFILE -ne $realHome)
    if ($homeIsRedirected) {
        Write-Warn "USERPROFILE is redirected to $env:USERPROFILE - skipping the user PATH"
        Write-Warn "add $BinDir to your PATH by hand if you want mas on the command line"
    } else {
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($null -eq $userPath) { $userPath = "" }
    $parts = $userPath -split ";" | Where-Object { $_ -ne "" }
    # The launcher has to come *first*, not last. A PATH is searched in
    # order, so appending the launcher means an older `mas` that was
    # installed earlier (a `pip install openloop-mas`, most often) keeps
    # winning - and the user would type `mas` and run the old copy while
    # the installer reports that everything is ready.
    $rest = $parts | Where-Object { $_ -ne $BinDir }
    $newPath = (@($BinDir) + $rest) -join ";"
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    Write-Ok "added at the front of the PATH (new terminals will see it)"

    # Say it out loud: another `mas` that the PATH reaches earlier is
    # still there, and it will win over this one in the *current*
    # terminal session too.
    $earlier = $parts | Where-Object {
        ($_ -ne $BinDir) -and ((Test-Path (Join-Path $_ "mas.exe")) -or (Test-Path (Join-Path $_ "mas.cmd")))
    }
    if ($earlier) {
        Write-Warn "another mas is still ahead of this one in the current session:"
        foreach ($dir in $earlier) { Write-Warn "  $dir" }
        Write-Warn 'open a NEW terminal, or put $BinDir first, before trusting mas --version'
    }
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
    $installRun = Invoke-MasProcess $pythonExe $installArgs
    if ($installRun.Output) { Write-Host $installRun.Output.TrimEnd() }
    if ($installRun.Error) { Write-Host $installRun.Error.TrimEnd() }
    if ($installRun.ExitCode -eq 0) {
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
        # The plugin is optional, so a command that cannot be resolved
        # is a skip and not a failure. The lookup walks PATH itself,
        # because Get-Command goes through PATHEXT and misses a
        # command in an environment where PATHEXT names nothing.
        $dshCmd = $null
        foreach ($entry in ($env:PATH -split ";")) {
            if (-not $entry) { continue }
            foreach ($name in @("dsh.cmd", "dsh.exe", "dsh")) {
                $candidate = Join-Path $entry $name
                if (Test-Path -LiteralPath $candidate) { $dshCmd = $candidate; break }
            }
            if ($dshCmd) { break }
        }
        if (-not $dshCmd) {
            Write-Warn "'dsh' is not on PATH - skipping the DSH plugin. Install DeepSeek Harness first."
        } else {
            $pkgDir = Join-Path $InstallDir "dsh-plugin"
            New-Item -ItemType Directory -Force -Path $pkgDir | Out-Null
            Push-Location $pluginSrc
            try {
                # Started through .NET, like every other native call
                # in this script: a shell that cannot resolve a bare
                # executable would otherwise report a *successful*
                # pack with an empty exit code, and the plugin would
                # be skipped without a word.
                # npm is looked up the same way python is: by walking
                # PATH. Get-Command resolves through PATHEXT, and in an
                # environment where PATHEXT names no executable suffix it
                # returns nothing for a perfectly good npm.cmd.
                $npmExe = $null
                foreach ($entry in ($env:PATH -split ";")) {
                    if (-not $entry) { continue }
                    foreach ($name in @("npm.cmd", "npm.exe", "npm")) {
                        $candidate = Join-Path $entry $name
                        if (Test-Path -LiteralPath $candidate) { $npmExe = $candidate; break }
                    }
                    if ($npmExe) { break }
                }
                $packExit = 1
                if ($npmExe) {
                    $pack = Invoke-MasProcess $npmExe @("pack", "--pack-destination", $pkgDir)
                    if ($pack.Output) { Write-Host $pack.Output.TrimEnd() }
                    $packExit = $pack.ExitCode
                }
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
                            param($profile, $archive, $dsh)
                            # The resolved path is passed in on purpose: a
                            # bare 'dsh' would be resolved by the job's own
                            # shell, which - as everywhere else in this
                            # script - may fail to resolve a native command
                            # at all and then report a success it never had.
                            & $dsh plugin --profile $profile add ("dsh-openloop-mas@file:" + $archive) 2>&1
                            $LASTEXITCODE
                        } -ArgumentList $DshProfile, $tgz.FullName, $dshCmd
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

# A native command that writes to stderr raises a terminating error under
# ``$ErrorActionPreference = "Stop"`` on Windows PowerShell 5.1 - the
# pipeline stops and the installer dies half way through. The self-check
# writes its progress to stderr *on purpose* (the ``--json`` contract of
# the command has to stay machine readable), so the naive
# ``& $pythonExe $masMain verify`` call aborts the installation the
# moment the check announces its first step. The beginner sees a red
# NativeCommandError and no working install.
#
# The fix is to run the check with a relaxed preference and to merge the
# two streams into the visible output. The exit code is still the exit
# code of the real command, so the summary at the end stays honest.
function Invoke-MasStep {
    param([string[]]$MasArgs)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        # The command is started through .NET rather than with `&`.
        # The self-check writes its progress to stderr *on purpose* -
        # the ``--json`` contract of the command has to stay machine
        # readable - and a native command that writes to stderr raises
        # a terminating error under ``$ErrorActionPreference = "Stop"``
        # on Windows PowerShell 5.1. The pipeline then stops and the
        # installer dies half way through. The beginner sees a red
        # NativeCommandError and no working install.
        #
        # Starting the process directly keeps the streams apart and lets
        # both of them be printed, and the exit code stays the exit code
        # of the real command - so the summary at the end stays honest.
        $run = Invoke-MasProcess $pythonExe (@($masMain) + $MasArgs)
        $lines = @()
        if ($run.Output) { $lines += @($run.Output -split "`r?`n") }
        if ($run.Error) { $lines += @($run.Error -split "`r?`n") }
        foreach ($line in $lines) {
            if ($line -ne "") { Write-Host "      $line" -ForegroundColor DarkGray }
        }
        return $run.ExitCode
    } finally {
        $ErrorActionPreference = $previous
    }
}

$doctorExit = Invoke-MasStep @("doctor")

if (-not $SkipDemo) {
    Write-Step "Running the offline demo (no API key needed)"
    $demoExit = Invoke-MasStep @("demo")

    Write-Step "Running the offline benchmark"
    $benchExit = Invoke-MasStep @("bench", "--repeats", "1")

    Write-Step "Running the closure self-check"
    $verifyExit = Invoke-MasStep @("verify")

    Write-Step "Running the flaky-path gate (mas selftest)"
    $selftestExit = Invoke-MasStep @("selftest", "--iterations", "20")
} else {
    $demoExit = 0
    $benchExit = 0
    $verifyExit = 0
    $selftestExit = 0
}

Write-Host ""
if ($doctorExit -eq 0 -and $demoExit -eq 0 -and $benchExit -eq 0 -and $verifyExit -eq 0 -and $selftestExit -eq 0) {
    Write-Host "Installation finished." -ForegroundColor Green
} else {
    Write-Host "Installation finished with warnings (doctor=$doctorExit demo=$demoExit bench=$benchExit verify=$verifyExit selftest=$selftestExit)." -ForegroundColor Yellow
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
