<#
.SYNOPSIS
    Set up this machine for the Rootsmarkt orchestration course. Windows only.

.DESCRIPTION
    Installs uv and the Astro CLI, checks Docker, and pulls the Airflow image.

    Deliberately uses NO package manager. winget is not present on every machine
    -- Microsoft ships it as part of App Installer, which arrives through the
    Microsoft Store and may never do so on a managed laptop. Downloading the
    tools directly works everywhere.

    It also needs NO administrator rights, with one exception noted below. That
    matters more than winget on a corporate laptop:

        uv             installs under your user profile
        Astro CLI      a bare .exe, copied to a folder you own
        Docker Desktop has a per-user install mode (not automated here)

    THE ONE THING THAT NEEDS ADMIN, whichever way you install: enabling WSL 2.
    It is a one-time, per-machine operation, and Docker cannot run Linux
    containers without it. If this script stops on that step, you need IT --
    which is exactly why you are running this a week early and not on the day.

    Safe to run more than once. Anything already installed is left alone.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\setup-windows.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\setup-windows.ps1 -SkipDockerPull
#>

[CmdletBinding()]
param(
    # Skip the ~1GB image pull. You still need it before the course.
    [switch]$SkipDockerPull
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# ---------------------------------------------------------------------------
# Pinned. Keep in step with cli/src/roots/main.py and docs/version-verification.md
# -- tools/tests/test_pinned_versions.py fails if these drift apart.
#
# Checksums are Astronomer's own, from astro_1.42.1_checksums.txt. Verified
# 2026-09-15. The amd64 line also matches the winget manifest's InstallerSha256,
# so both routes deliver the same binary.
# ---------------------------------------------------------------------------
$AstroVersion  = '1.42.1'
$RuntimeImage  = 'astrocrpublic.azurecr.io/runtime:3.3-2'
$AstroChecksums = @{
    'amd64' = '31951f8a17d6ba7273ac5bdd743c2efd647820a4dc83aebee7237cf8385dbb11'
    'arm64' = 'ef093c15eca0f8bef324b3006a96cbec5d0e1ef4edd2bc18c1f25b8d6b125955'
    '386'   = '30ed927a736ac9e3903e89e612e627571212cb7b0f22f0941cc445317932574d'
}
$DockerDownload = 'https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe'
$InstallDir = Join-Path $env:LOCALAPPDATA 'Programs\rootsmarkt\bin'

$script:Problems = @()

function Write-Step { param([string]$Text) Write-Host "`n=== $Text" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Text) Write-Host "  OK    $Text" -ForegroundColor Green }
function Write-Info { param([string]$Text) Write-Host "        $Text" -ForegroundColor Gray }
function Write-Warn { param([string]$Text) Write-Host "  ....  $Text" -ForegroundColor Yellow }
function Write-Bad {
    param([string]$Text)
    Write-Host "  FAIL  $Text" -ForegroundColor Red
    $script:Problems += $Text
}

function Test-Command {
    param([string]$Name)
    $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}


# ---------------------------------------------------------------- 1. the machine

Write-Step '1/5  This machine'

$os = Get-CimInstance Win32_OperatingSystem
Write-Info "$($os.Caption) build $($os.BuildNumber)"
if ([int]$os.BuildNumber -lt 19045) {
    Write-Warn "Docker Desktop wants Windows 10 22H2 (build 19045) or Windows 11 23H2+."
    Write-Info "You are on $($os.BuildNumber). It may still work; if Docker refuses, this is why."
}

# PROCESSOR_ARCHITECTURE is the process's view and reads x86 under a 32-bit
# host; PROCESSOR_ARCHITEW6432 is the machine's real one when that happens.
$rawArch = $env:PROCESSOR_ARCHITEW6432
if ([string]::IsNullOrEmpty($rawArch)) { $rawArch = $env:PROCESSOR_ARCHITECTURE }
switch ($rawArch) {
    'AMD64' { $arch = 'amd64' }
    'ARM64' { $arch = 'arm64' }
    'x86'   { $arch = '386' }
    default { $arch = 'amd64'; Write-Warn "Unrecognised architecture '$rawArch', assuming amd64." }
}
Write-Ok "architecture: $arch"
if ($arch -eq 'arm64') {
    Write-Info "Windows-on-ARM. winget has no arm64 package for Astro $AstroVersion,"
    Write-Info "so this download path is the only one available to you."
}


# ------------------------------------------------------------------- 2. WSL 2

Write-Step '2/5  WSL 2 (Docker needs it to run Linux containers)'

$wslOk = $false
if (Test-Command 'wsl') {
    # `wsl --status` is non-zero when WSL is present but not configured.
    $null = & wsl --status 2>&1
    $wslOk = ($LASTEXITCODE -eq 0)
}
if ($wslOk) {
    Write-Ok 'WSL is enabled'
} else {
    Write-Bad 'WSL 2 is not enabled -- Docker cannot run Linux containers without it'
    Write-Host ''
    Write-Host '  This is the ONE step that needs administrator rights.' -ForegroundColor Yellow
    Write-Host '  In an ADMINISTRATOR PowerShell, run these, then reboot:' -ForegroundColor Yellow
    Write-Host '      wsl --update' -ForegroundColor White
    Write-Host '      wsl --install --no-distribution' -ForegroundColor White
    Write-Host ''
    Write-Host '  --no-distribution is deliberate: you are installing the kernel' -ForegroundColor Gray
    Write-Host '  Docker uses, not a Linux you will log into.' -ForegroundColor Gray
    Write-Host ''
    Write-Host '  If you cannot elevate, ask IT now. Everything else below needs' -ForegroundColor Gray
    Write-Host '  no admin, but nothing works without this.' -ForegroundColor Gray
    Write-Host ''
    Write-Host 'Stopping here. Re-run this script once WSL 2 is enabled.' -ForegroundColor Red
    exit 1
}


# ----------------------------------------------------------------- 3. Docker

Write-Step '3/5  Docker Desktop'

$dockerRunning = $false
if (Test-Command 'docker') {
    $version = & docker info --format '{{.ServerVersion}}' 2>$null
    if ($LASTEXITCODE -eq 0 -and $version) {
        Write-Ok "Docker daemon $version"
        $dockerRunning = $true
    } else {
        Write-Bad 'Docker is installed but not running -- open Docker Desktop and wait for it'
    }
} else {
    Write-Bad 'Docker Desktop is not installed'
    Write-Host ''
    Write-Host '  Download:' -ForegroundColor Yellow
    Write-Host "      $DockerDownload" -ForegroundColor White
    Write-Host ''
    Write-Host '  Then install it WITHOUT admin rights, in per-user mode:' -ForegroundColor Yellow
    Write-Host '      & "$HOME\Downloads\Docker Desktop Installer.exe" install --user' -ForegroundColor White
    Write-Host ''
    Write-Host '  (Per-user is the installer default if you just double-click it.' -ForegroundColor Gray
    Write-Host '   It installs under your profile and needs no administrator.)' -ForegroundColor Gray
    Write-Host ''
    Write-Host '  Open Docker Desktop, wait for it to start, then re-run this script.' -ForegroundColor Gray
}


# --------------------------------------------------------------------- 4. uv

Write-Step '4/5  uv (Python environment manager)'

if (Test-Command 'uv') {
    Write-Ok "uv already installed: $(& uv --version)"
} else {
    Write-Info 'installing uv from astral.sh (user profile, no admin) ...'
    try {
        Invoke-RestMethod -Uri 'https://astral.sh/uv/install.ps1' | Invoke-Expression
    } catch {
        Write-Bad "uv install failed: $($_.Exception.Message)"
        Write-Info 'Manual alternative: https://docs.astral.sh/uv/getting-started/installation/'
    }
    # The installer puts uv here and edits PATH for NEW shells, so look directly
    # rather than trusting this session's PATH.
    $uvPath = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
    if (Test-Path $uvPath) {
        Write-Ok "uv installed: $(& $uvPath --version)"
        $env:PATH = "$(Split-Path $uvPath);$env:PATH"
    } elseif (Test-Command 'uv') {
        Write-Ok "uv installed: $(& uv --version)"
    } else {
        Write-Bad 'uv is still not on PATH -- open a new terminal and check `uv --version`'
    }
}


# -------------------------------------------------------------- 5. Astro CLI

Write-Step "5/5  Astro CLI $AstroVersion"

$astroTarget = Join-Path $InstallDir 'astro.exe'
$needAstro = $true

if (Test-Command 'astro') {
    $found = (& astro version 2>$null | Out-String)
    if ($found -match '(\d+\.\d+\.\d+)') {
        if ($Matches[1] -eq $AstroVersion) {
            Write-Ok "astro $AstroVersion already installed"
            $needAstro = $false
        } else {
            Write-Warn "astro $($Matches[1]) is installed; the course pins $AstroVersion"
            Write-Info 'Installing the pinned version alongside it.'
        }
    }
}

if ($needAstro) {
    $asset = "astro_${AstroVersion}_windows_${arch}.exe"
    $url = "https://github.com/astronomer/astro-cli/releases/download/v$AstroVersion/$asset"
    $expected = $AstroChecksums[$arch]
    $temp = Join-Path $env:TEMP $asset

    Write-Info "downloading $asset (~80 MB) ..."
    try {
        # Explicitly TLS 1.2: Windows PowerShell 5.1 still defaults lower on some
        # builds, and GitHub refuses those connections.
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $ProgressPreference = 'SilentlyContinue'   # ~10x faster for large files
        Invoke-WebRequest -Uri $url -OutFile $temp -UseBasicParsing
    } catch {
        Write-Bad "download failed: $($_.Exception.Message)"
        Write-Info "Download it by hand from https://github.com/astronomer/astro-cli/releases/tag/v$AstroVersion"
    }

    if (Test-Path $temp) {
        $actual = (Get-FileHash -Path $temp -Algorithm SHA256).Hash.ToLower()
        if ($actual -ne $expected) {
            Remove-Item $temp -Force
            Write-Bad 'CHECKSUM MISMATCH -- the download was corrupted or tampered with. Deleted.'
            Write-Info "expected $expected"
            Write-Info "got      $actual"
        } else {
            Write-Ok "checksum verified ($($expected.Substring(0,16))...)"
            New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
            Move-Item -Path $temp -Destination $astroTarget -Force
            Write-Ok "installed to $astroTarget"

            # User PATH only -- the machine PATH would need admin.
            $userPath = [Environment]::GetEnvironmentVariable('PATH', 'User')
            if ($null -eq $userPath) { $userPath = '' }
            if ($userPath -notlike "*$InstallDir*") {
                [Environment]::SetEnvironmentVariable(
                    'PATH', "$userPath;$InstallDir".Trim(';'), 'User')
                Write-Ok 'added to your PATH (new terminals only)'
            }
            $env:PATH = "$InstallDir;$env:PATH"
        }
    }
}


# ------------------------------------------------------------ the Airflow image

if ($dockerRunning -and -not $SkipDockerPull) {
    Write-Step 'Pulling the Airflow image (a few minutes, once)'
    & docker pull $RuntimeImage
    if ($LASTEXITCODE -eq 0) {
        Write-Ok $RuntimeImage
    } else {
        Write-Bad "could not pull $RuntimeImage -- retry, it is usually transient"
    }
} elseif (-not $dockerRunning) {
    Write-Warn "skipping the image pull -- Docker is not running. Pull it before the course:"
    Write-Info "docker pull $RuntimeImage"
}


# ------------------------------------------------------------------- the report

Write-Host ''
if ($script:Problems.Count -eq 0) {
    Write-Host '==============================================' -ForegroundColor Green
    Write-Host ' Setup complete.' -ForegroundColor Green
    Write-Host '==============================================' -ForegroundColor Green
    Write-Host ''
    Write-Host ' OPEN A NEW TERMINAL so the PATH changes apply, then:' -ForegroundColor White
    Write-Host ''
    Write-Host '     astro version                        # expect 1.42.1'
    Write-Host '     uv sync'
    Write-Host '     uv run roots join <your-team-code>'
    Write-Host '     uv run --env-file .env roots doctor'
    Write-Host ''
    Write-Host ' README.md picks up from there.' -ForegroundColor Gray
    exit 0
} else {
    Write-Host '==============================================' -ForegroundColor Red
    Write-Host " $($script:Problems.Count) thing(s) still to do:" -ForegroundColor Red
    Write-Host '==============================================' -ForegroundColor Red
    foreach ($p in $script:Problems) { Write-Host "  - $p" -ForegroundColor Red }
    Write-Host ''
    Write-Host ' Fix those and run this script again -- it is safe to re-run.' -ForegroundColor Yellow
    Write-Host ' Stuck? WINDOWS-SETUP.md has the same steps by hand.' -ForegroundColor Gray
    exit 1
}
