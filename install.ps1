<#
.SYNOPSIS
  Sets up the media toolbox on this PC: checks Docker and the GPU, writes .env,
  creates your studio folder, pulls the images and starts everything.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\install.ps1
  powershell -ExecutionPolicy Bypass -File .\install.ps1 -StudioDir D:\media-studio -RecordingsDir D:\Recordings
  powershell -ExecutionPolicy Bypass -File .\install.ps1 -NoGpu     # no NVIDIA card: CPU tools only

  Safe to run again: an existing .env is kept (use -Force to rewrite it).
#>
param(
    # Where your video projects, sound library and generated files live.
    [string]$StudioDir = (Join-Path $HOME "media-studio"),
    # Your raw recordings (any recorder: point it here). The toolbox reads them but never writes there.
    [string]$RecordingsDir = (Join-Path $StudioDir "recordings"),
    [int]$Port = 8765,
    [switch]$NoGpu,
    [switch]$Build,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Step($text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }
function Ok($text) { Write-Host "    OK  $text" -ForegroundColor Green }
function Warn($text) { Write-Host "    !!  $text" -ForegroundColor Yellow }
function Fail($text) { Write-Host "    XX  $text" -ForegroundColor Red; exit 1 }
function Slash($path) { return ($path -replace '\\', '/').TrimEnd('/') }

# 1. Docker -------------------------------------------------------------------------------
Step "Docker Desktop"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail "Docker isn't installed. Install Docker Desktop (WSL 2 backend), start it, then run this again: https://www.docker.com/products/docker-desktop/"
}
docker info --format "{{.ServerVersion}}" *> $null
if ($LASTEXITCODE -ne 0) { Fail "Docker is installed but not running. Start Docker Desktop and run this again." }
Ok "Docker is running"

# 2. GPU ----------------------------------------------------------------------------------
Step "NVIDIA GPU"
$gpu = $false
if (-not $NoGpu) {
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        # Read all output before taking the first line: stopping the pipeline early
        # (Select-Object -First 1) can kill nvidia-smi and report exit code -1.
        $out = @(& nvidia-smi --query-gpu=name --format=csv,noheader 2>$null)
        $code = $LASTEXITCODE
        $name = if ($out.Count -gt 0) { "$($out[0])".Trim() } else { "" }
        if ($code -eq 0 -and $name) { $gpu = $true; Ok "Found $name" }
    }
    if (-not $gpu) { Warn "No NVIDIA GPU found. Transcription, sound effects and text to speech will be off; ffmpeg, renders and Shorts still work." }
} else {
    Warn "-NoGpu: GPU tools off"
}

# 3. Folders and .env ---------------------------------------------------------------------
Step "Studio folder and settings"
foreach ($dir in @($StudioDir, (Join-Path $StudioDir "sfx_library"), (Join-Path $StudioDir "_scratch"), $RecordingsDir)) {
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
}
Ok "Studio: $StudioDir"

$homeDir = Slash $HOME
foreach ($p in @($StudioDir, $RecordingsDir)) {
    if (-not ((Slash $p) -like "$homeDir*")) { Warn "$p is outside $HOME; the toolbox can only reach files under your user folder." }
}

if ((Test-Path .env) -and -not $Force) {
    Ok ".env already exists, kept (use -Force to rewrite it)"
} else {
    $profiles = ""
    if ($gpu) { $profiles = "gpu" }
    $values = @{
        "HOST_HOME"        = $homeDir
        "PORT"             = "$Port"
        "COMPOSE_PROFILES" = $profiles
        "OUTPUTS_DIR"      = (Slash (Join-Path $StudioDir "_scratch"))
        "SFX_LIBRARY"      = (Slash (Join-Path $StudioDir "sfx_library"))
        "SHORTS_ROOT"      = (Slash $StudioDir)
        "PROTECTED_DIRS"   = (Slash $RecordingsDir)
    }
    $lines = foreach ($line in (Get-Content .env.example)) {
        if ($line -match '^([A-Z_]+)=') {
            $key = $Matches[1]
            if ($values.ContainsKey($key)) { "$key=$($values[$key])"; continue }
        }
        $line
    }
    # UTF-8 without BOM, so docker compose reads the first key correctly.
    [System.IO.File]::WriteAllLines((Join-Path $PSScriptRoot ".env"), $lines, (New-Object System.Text.UTF8Encoding $false))
    Ok "Wrote .env"
}

# 4. Hugging Face (Stable Audio is gated) --------------------------------------------------
if ($gpu) {
    Step "Hugging Face login (for sound effects)"
    $hfHome = $env:HF_HOME
    if (-not $hfHome) { $hfHome = Join-Path $HOME ".cache\huggingface" }
    if (Test-Path (Join-Path $hfHome "token")) {
        Ok "Token found"
    } else {
        Warn "Not logged in. Sound effects need it (transcription and voices don't):"
        Warn "  1. Accept the license at https://huggingface.co/stabilityai/stable-audio-open-1.0"
        Warn "  2. Run: pip install -U huggingface_hub ; hf auth login"
    }
}

# 5. Images and start ---------------------------------------------------------------------
Step "Images (first time: several GB, takes a while)"
if ($Build) {
    docker compose up -d --build
} else {
    docker compose pull
    if ($LASTEXITCODE -ne 0) {
        Warn "Couldn't pull prebuilt images; building them here instead (~10 GB download)."
        docker compose up -d --build
    } else {
        docker compose up -d
    }
}
if ($LASTEXITCODE -ne 0) { Fail "docker compose failed; see the output above." }

Step "Waiting for the gateway"
$url = "http://localhost:$Port"
$up = $false
for ($i = 0; $i -lt 60; $i++) {
    try {
        Invoke-WebRequest -UseBasicParsing -Uri "$url/api/state" -TimeoutSec 3 | Out-Null
        $up = $true; break
    } catch { Start-Sleep -Seconds 3 }
}
if (-not $up) { Fail "The gateway didn't answer at $url. Check: docker compose logs gateway" }
Ok "Dashboard: $url"

Write-Host ""
Write-Host "Done. Next, in Claude Code:" -ForegroundColor Cyan
Write-Host "  /plugin marketplace add JohanDuran/video-studio"
Write-Host "  /plugin install video-studio"
Write-Host "  /studio-setup"
Write-Host ""
Write-Host "Or register only the MCP server:"
Write-Host "  claude mcp add --scope user --transport http media-toolbox $url/mcp"
