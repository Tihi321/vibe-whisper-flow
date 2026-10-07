<#
.SYNOPSIS
  Builds the portable VibeFlow folder + zip (PyInstaller one-folder, whisper.cpp engine bundled).
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\build.ps1
  powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -SkipTests -Cuda
.NOTES
  Never copies config.toml, .env, logs or models into the output.
#>
param(
    [switch]$SkipTests,
    [switch]$Cuda   # bundle the CUDA (cuBLAS) engine instead of the CPU one
)

$ErrorActionPreference = 'Stop'

$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $root

$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    Write-Error "Missing $python. Create the venv first: python -m venv .venv"
}

function Invoke-Native {
    param([string]$What, [scriptblock]$Cmd)
    & $Cmd
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)" }
}

# --- 1. dependencies ---------------------------------------------------------
Write-Host "== Installing build dependencies" -ForegroundColor Cyan
Invoke-Native "pip install" { & $python -m pip install -r (Join-Path $root 'requirements-build.txt') }

# --- 2. tests ----------------------------------------------------------------
if (-not $SkipTests) {
    Write-Host "== Running tests" -ForegroundColor Cyan
    Invoke-Native "pytest" { & $python -m pytest -q tests }
}

# --- 3. icon -----------------------------------------------------------------
Write-Host "== Generating icon" -ForegroundColor Cyan
$buildDir = Join-Path $root 'build'
New-Item -ItemType Directory -Force -Path $buildDir | Out-Null
$icoPath = Join-Path $buildDir 'vibeflow.ico'
$iconScript = @"
import sys
from vibeflow.tray import make_icon
img = make_icon('#e8e8e8')
img.save(sys.argv[1], format='ICO', sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64)])
"@
Invoke-Native "icon generation" { & $python -c $iconScript $icoPath }

# --- 4. PyInstaller ----------------------------------------------------------
Write-Host "== Running PyInstaller" -ForegroundColor Cyan
$dist = Join-Path $root 'dist'
$appDir = Join-Path $dist 'VibeFlow'
if (Test-Path $appDir) { Remove-Item -Recurse -Force $appDir }
Invoke-Native "PyInstaller" {
    & $python -m PyInstaller (Join-Path $root 'packaging\VibeFlow.spec') --noconfirm `
        --distpath $dist --workpath (Join-Path $buildDir 'pyinstaller')
}

# --- 5. whisper engine (cached) ----------------------------------------------
$engineName = if ($Cuda) { 'whisper-engine-cuda' } else { 'whisper-engine' }
$engineCache = Join-Path $buildDir $engineName
if (-not (Test-Path (Join-Path $engineCache 'whisper-cli.exe'))) {
    Write-Host "== Downloading whisper.cpp engine ($engineName)" -ForegroundColor Cyan
    $setupArgs = @('-m', 'vibeflow.whisper_setup', '--engine', '--dest', $engineCache)
    if ($Cuda) { $setupArgs += '--cuda' }
    Invoke-Native "engine download" { & $python @setupArgs }
}
else {
    Write-Host "== Using cached engine $engineCache" -ForegroundColor Cyan
}
$whisperDir = Join-Path $appDir 'whisper'
New-Item -ItemType Directory -Force -Path (Join-Path $whisperDir 'models') | Out-Null
Get-ChildItem -Path $engineCache -File | Where-Object { $_.Extension -in '.exe', '.dll' } |
    ForEach-Object { Copy-Item -Path $_.FullName -Destination $whisperDir -Force }

# --- 6. extras ---------------------------------------------------------------
Copy-Item -Path (Join-Path $root '.env.example') -Destination $appDir -Force
Copy-Item -Path (Join-Path $root 'README.md') -Destination $appDir -Force

# --- 7. zip ------------------------------------------------------------------
$versionLine = Select-String -Path (Join-Path $root 'vibeflow\__init__.py') -Pattern '__version__\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $versionLine) { throw "Could not read __version__ from vibeflow/__init__.py" }
$version = $versionLine.Matches[0].Groups[1].Value
$suffix = if ($Cuda) { '-cuda' } else { '' }
$zip = Join-Path $dist "VibeFlow-$version-win64$suffix.zip"
if (Test-Path $zip) { Remove-Item -Force $zip }
Write-Host "== Creating $zip" -ForegroundColor Cyan
Compress-Archive -Path $appDir -DestinationPath $zip -CompressionLevel Optimal

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "  Folder: $appDir"
Write-Host "  Exe:    $(Join-Path $appDir 'VibeFlow.exe')"
Write-Host "  Zip:    $zip"
