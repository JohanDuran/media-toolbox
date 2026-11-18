<#
.SYNOPSIS
  Installs the media toolbox without cloning the repo: downloads install.ps1, docker-compose.yml,
  .env.example and docs/short-spec.md for one release into %USERPROFILE%\.video-studio\media-toolbox, then runs install.ps1
  with that release's images pinned. Re-run it with a newer -Version to upgrade.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\get.ps1 -Version v0.5.0
  # straight from GitHub (what the video-studio plugin's /studio-setup runs):
  & ([scriptblock]::Create((irm https://raw.githubusercontent.com/JohanDuran/media-toolbox/v0.5.0/get.ps1))) -Version v0.5.0

  Any other parameter (-StudioDir, -RecordingsDir, -Port, -NoGpu, -Force) is passed on to install.ps1.

  The toolbox is one of three pieces (video-studio plugin, media-toolbox, MangoAI); the release you pick here
  should match the video-studio plugin's compatibility table. See docs/ECOSYSTEM.md in this repo.
#>
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$InstallDir = (Join-Path $HOME ".video-studio\media-toolbox"),
    [Parameter(ValueFromRemainingArguments = $true)]$Rest
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
if ($Version -notmatch '^v\d+\.\d+\.\d+$') { throw "-Version must be a release tag like v0.5.0" }

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$base = "https://raw.githubusercontent.com/JohanDuran/media-toolbox/$Version"
# docs/ too: the video-studio skills read docs/short-spec.md from the toolbox folder (toolbox.repo_dir).
New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir "docs") | Out-Null
foreach ($f in @("install.ps1", "docker-compose.yml", ".env.example", "docs/short-spec.md")) {
    Write-Host "    get $f ($Version)"
    Invoke-WebRequest -UseBasicParsing -Uri "$base/$f" -OutFile (Join-Path $InstallDir $f)
}
Set-Content -Path (Join-Path $InstallDir "VERSION") -Value $Version -Encoding ascii

$installArgs = @{ Version = $Version }
for ($i = 0; $i -lt $Rest.Count; $i++) {
    $a = "$($Rest[$i])"
    if ($a -match '^-(\w+)$') {
        $name = $Matches[1]
        $next = if ($i + 1 -lt $Rest.Count) { "$($Rest[$i + 1])" } else { $null }
        if ($null -ne $next -and $next -notmatch '^-') { $installArgs[$name] = $next; $i++ } else { $installArgs[$name] = $true }
    }
}
& (Join-Path $InstallDir "install.ps1") @installArgs
