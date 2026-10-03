<#
.SYNOPSIS
  Downloads the whisper.cpp Windows build and ggml models into ./whisper for VibeFlow.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\setup-whisper.ps1
  powershell -ExecutionPolicy Bypass -File scripts\setup-whisper.ps1 -Cuda -Models small,medium,large-v3-turbo
#>
param(
    [string[]]$Models = @("small", "medium"),
    [switch]$Cuda,
    [string]$Dest = "$PSScriptRoot\..\whisper"
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls13

$validModels = @("tiny", "tiny.en", "base", "base.en", "small", "small.en", "medium", "medium.en", "large-v3", "large-v3-turbo")
$Models = @($Models | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
foreach ($m in $Models) {
    if ($validModels -notcontains $m) {
        Write-Error "Unknown model '$m'. Valid: $($validModels -join ', ')"
    }
}

New-Item -ItemType Directory -Force -Path $Dest | Out-Null
$Dest = (Resolve-Path $Dest).Path
$modelsDir = Join-Path $Dest "models"
New-Item -ItemType Directory -Force -Path $modelsDir | Out-Null
Write-Host "Destination: $Dest"

# --- 1. whisper.cpp binaries -------------------------------------------------
$headers = @{ "User-Agent" = "vibeflow-setup"; "Accept" = "application/vnd.github+json" }
# The repo moved to ggml-org; the newest release can be a source-only tag without binaries,
# so scan the recent releases for the first one that carries the wanted asset.
$releasesUrl = "https://api.github.com/repos/ggml-org/whisper.cpp/releases?per_page=20"
Write-Host "Querying $releasesUrl"
$releases = Invoke-RestMethod -Uri $releasesUrl -Headers $headers

if ($Cuda) { $pattern = '^whisper-cublas-.*-bin-x64\.zip$' } else { $pattern = '^whisper-bin-x64\.zip$' }

$asset = $null
$release = $null
foreach ($r in $releases) {
    if ($r.draft -or $r.prerelease) { continue }
    $candidates = @($r.assets | Where-Object { $_.name -match $pattern })
    if ($candidates.Count -gt 0) {
        # prefer the highest version in the name (e.g. cublas-12.4.0 over 11.8.0)
        $asset = $candidates | Sort-Object {
            if ($_.name -match '(\d+(\.\d+)+)') { [version]$Matches[1] } else { [version]'0.0' }
        } -Descending | Select-Object -First 1
        $release = $r
        break
    }
}

if (-not $asset) {
    Write-Host "No asset matching $pattern found. Assets seen in recent releases:" -ForegroundColor Yellow
    foreach ($r in $releases | Select-Object -First 3) {
        Write-Host "  $($r.tag_name): $(($r.assets | ForEach-Object { $_.name }) -join ', ')"
    }
    Write-Host "Download a Windows x64 build manually from https://github.com/ggml-org/whisper.cpp/releases" -ForegroundColor Yellow
    Write-Host "and copy whisper-cli.exe plus all .dll files into $Dest" -ForegroundColor Yellow
    exit 1
}

Write-Host "Using release $($release.tag_name): $($asset.name) ($([math]::Round($asset.size / 1MB, 1)) MB)"
$zip = Join-Path $env:TEMP $asset.name
$extract = Join-Path $env:TEMP ("vibeflow-whisper-" + [guid]::NewGuid().ToString("N"))
try {
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zip -UseBasicParsing -Headers @{ "User-Agent" = "vibeflow-setup" }
    Expand-Archive -Path $zip -DestinationPath $extract -Force

    $cli = Get-ChildItem -Path $extract -Recurse -Filter "whisper-cli.exe" | Select-Object -First 1
    if (-not $cli) { $cli = Get-ChildItem -Path $extract -Recurse -Filter "main.exe" | Select-Object -First 1 }
    if (-not $cli) { throw "whisper-cli.exe (or main.exe) not found inside $($asset.name)" }

    Copy-Item -Path $cli.FullName -Destination $Dest -Force
    $binDir = $cli.DirectoryName
    $dlls = @(Get-ChildItem -Path $binDir -Filter "*.dll")
    if ($dlls.Count -eq 0) { $dlls = @(Get-ChildItem -Path $extract -Recurse -Filter "*.dll") }
    foreach ($dll in $dlls) { Copy-Item -Path $dll.FullName -Destination $Dest -Force }
    Write-Host "Installed $($cli.Name) and $($dlls.Count) DLL(s)"
}
finally {
    Remove-Item -Path $zip -Force -ErrorAction SilentlyContinue
    Remove-Item -Path $extract -Recurse -Force -ErrorAction SilentlyContinue
}

# --- 2. ggml models ----------------------------------------------------------
foreach ($m in $Models) {
    $file = Join-Path $modelsDir "ggml-$m.bin"
    if ((Test-Path $file) -and ((Get-Item $file).Length -gt 1MB)) {
        Write-Host "Model $m already present, skipping"
        continue
    }
    $url = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-$m.bin"
    Write-Host "Downloading model $m (this can take a while)..."
    $tmp = "$file.part"
    Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing
    Move-Item -Path $tmp -Destination $file -Force
}

# --- 3. summary --------------------------------------------------------------
Write-Host ""
Write-Host "Done. Installed in $Dest" -ForegroundColor Green
Get-ChildItem -Path $Dest -Filter "*.exe" | ForEach-Object { Write-Host "  $($_.Name)" }
Get-ChildItem -Path $modelsDir -Filter "ggml-*.bin" | ForEach-Object {
    Write-Host ("  models\{0}  ({1} MB)" -f $_.Name, [math]::Round($_.Length / 1MB))
}
Write-Host ""
Write-Host "config.toml:"
Write-Host "  [transcription.local]"
Write-Host "  whisper_cli = `"whisper/whisper-cli.exe`""
Write-Host "  model = `"whisper/models/ggml-$($Models[0]).bin`""
