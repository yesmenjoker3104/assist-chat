# Assist Chat 初回セットアップ
# Ollama本体とローカルLLMモデルを自動で準備します。
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$OllamaDir = Join-Path $Root "ollama"
$ModelsDir = Join-Path $Root "models"
$OllamaExe = Join-Path $OllamaDir "ollama.exe"
$Model = "qwen2.5:7b"
$OllamaZip = Join-Path $env:TEMP "assist-chat-ollama-windows-amd64.zip"
$OllamaUrl = "https://github.com/ollama/ollama/releases/latest/download/ollama-windows-amd64.zip"

New-Item -ItemType Directory -Force -Path $OllamaDir, $ModelsDir | Out-Null
$env:OLLAMA_MODELS = $ModelsDir

# Google Workspace CLIの前提を準備する
$gcloud = Get-Command gcloud -ErrorAction SilentlyContinue
if (-not $gcloud) {
    $gcloudCandidates = @(
        (Join-Path $env:LOCALAPPDATA "Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"),
        (Join-Path $env:ProgramFiles "Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd")
    )
    $gcloudPath = $gcloudCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($gcloudPath) { $gcloud = Get-Item $gcloudPath }
}
if (-not $gcloud) {
    $winget = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps\winget.exe"
    if (-not (Test-Path $winget)) { throw "gcloud was not found. Install Google Cloud CLI." }
    Write-Host "Installing Google Cloud CLI..."
    & $winget install --id Google.CloudSDK --exact --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) { throw "Google Cloud CLI installation failed." }
    $gcloudPath = Join-Path $env:LOCALAPPDATA "Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
    if (-not (Test-Path $gcloudPath)) { throw "gcloud executable was not found after installation." }
    $gcloud = Get-Item $gcloudPath
}
if ($gcloud -is [System.IO.FileInfo]) {
    $env:Path = "$(Split-Path $gcloud.FullName);$env:Path"
} else {
    $env:Path = "$(Split-Path $gcloud.Source);$env:Path"
}

$gws = Get-Command gws -ErrorAction SilentlyContinue
if (-not $gws) {
    $gwsCandidates = @(
        (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages\OpenJS.NodeJS.LTS_Microsoft.Winget.Source_8wekyb3d8bbwe\node-v24.19.0-win-x64\gws.cmd"),
        (Join-Path $env:APPDATA "npm\gws.cmd")
    )
    $gwsPath = $gwsCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($gwsPath) { $gws = Get-Item $gwsPath }
}
if ($gws -is [System.IO.FileInfo]) {
    $env:Path = "$(Split-Path $gws.FullName);$env:Path"
}
if (-not $gws) {
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npm) { throw "gws was not found. Install Node.js/npm." }
    Write-Host "Installing Google Workspace CLI..."
    & npm install --global @googleworkspace/cli --allow-scripts=@googleworkspace/cli
    if ($LASTEXITCODE -ne 0) { throw "Google Workspace CLI installation failed." }
}

if (-not (Get-Command gws -ErrorAction SilentlyContinue)) {
    $gwsPath = Join-Path $env:APPDATA "npm\gws.cmd"
    if (Test-Path $gwsPath) { $env:Path = "$(Split-Path $gwsPath);$env:Path" }
}
if (-not (Get-Command gws -ErrorAction SilentlyContinue)) {
    throw "gws is still unavailable after installation. Restart PowerShell and try again."
}
Write-Host "Google CLI ready: gcloud / gws"

# GitHub CLIのセットアップ
$ghCandidates = @(
    (Join-Path $env:ProgramFiles "GitHub CLI\gh.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "GitHub CLI\gh.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\GitHub CLI\gh.exe")
)
$gh = Get-Command gh -ErrorAction SilentlyContinue
if (-not $gh) {
    $ghExe = $ghCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($ghExe) { $env:Path = "$(Split-Path $ghExe);$env:Path"; $gh = Get-Command gh -ErrorAction SilentlyContinue }
}
if (-not $gh) {
    $winget = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps\winget.exe"
    if (-not (Test-Path $winget)) { throw "gh was not found. Install GitHub CLI." }
    Write-Host "Installing GitHub CLI..."
    & $winget install --id GitHub.cli --exact --accept-source-agreements --accept-package-agreements
    # wingetは「既にインストール済み」の場合もゼロ以外を返すので、パスを再チェック
    $ghExe = $ghCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($ghExe) { $env:Path = "$(Split-Path $ghExe);$env:Path" }
    $gh = Get-Command gh -ErrorAction SilentlyContinue
    if (-not $gh) { throw "GitHub CLI installation failed. Try restarting PowerShell." }
}
Write-Host "GitHub CLI ready: gh"
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
& gh auth status 2>&1 | Out-Null
$ghExitCode = $LASTEXITCODE
$ErrorActionPreference = $prevEAP
if ($ghExitCode -ne 0) {
    Write-Host "Authenticating with GitHub..."
    & gh auth login --web --git-protocol https
    if ($LASTEXITCODE -ne 0) { throw "GitHub authentication failed." }
}
Write-Host "GitHub authenticated."

# YouTube Audio Downloaderのセットアップ
$YtdlDir = Join-Path $Root "plugin\YoutubeAudioDownloader"
$YtdlCli = Join-Path $YtdlDir "YoutubeAudioDownloaderCli.exe"
$YtdlZip = Join-Path $env:TEMP "YoutubeAudioDownloader-Windows.zip"
$YtdlReleaseDir = Join-Path $Root "plugin"

if (-not (Test-Path $YtdlCli)) {
    Write-Host "Downloading YouTube Audio Downloader..."
    New-Item -ItemType Directory -Force -Path $YtdlReleaseDir | Out-Null
    & gh release download dev-latest --repo yesmenjoker3104/youtube-audio-downloader --pattern "YoutubeAudioDownloader-Windows.zip" --dir $env:TEMP --clobber
    if ($LASTEXITCODE -ne 0) { throw "YouTube Audio Downloader download failed." }
    if (-not (Test-Path $YtdlZip)) { throw "YoutubeAudioDownloader-Windows.zip not found after download." }
    Expand-Archive -LiteralPath $YtdlZip -DestinationPath $YtdlReleaseDir -Force
    Remove-Item -LiteralPath $YtdlZip -Force -ErrorAction SilentlyContinue
    if (-not (Test-Path $YtdlCli)) {
        throw "YoutubeAudioDownloaderCli.exe was not found after extracting. Check the ZIP structure."
    }
}
Write-Host "YouTube Audio Downloader ready: $YtdlCli"

# GetNewTechInfo CLIのセットアップ
$GntiDir = Join-Path $Root "plugin\GetNewTechInfo"
$GntiCli = Join-Path $GntiDir "GetNewTechInfoCli.exe"
$GntiZip = Join-Path $env:TEMP "GetNewTechInfoCli-Windows.zip"

if (-not (Test-Path $GntiCli)) {
    Write-Host "Downloading GetNewTechInfo CLI..."
    New-Item -ItemType Directory -Force -Path $GntiDir | Out-Null
    & gh release download dev-latest --repo yesmenjoker3104/get_new_tech_info --pattern "GetNewTechInfoCli-Windows.zip" --dir $env:TEMP --clobber
    if ($LASTEXITCODE -ne 0) { throw "GetNewTechInfo CLI download failed." }
    if (-not (Test-Path $GntiZip)) { throw "GetNewTechInfoCli-Windows.zip not found after download." }
    Expand-Archive -LiteralPath $GntiZip -DestinationPath $GntiDir -Force
    Remove-Item -LiteralPath $GntiZip -Force -ErrorAction SilentlyContinue
    if (-not (Test-Path $GntiCli)) {
        throw "GetNewTechInfoCli.exe was not found after extracting."
    }
}
Write-Host "GetNewTechInfo CLI ready: $GntiCli"

# GCalCtrl CLIのセットアップ
$GcalDir = Join-Path $Root "plugin\GCalCtrl"
$GcalCli = Join-Path $GcalDir "GCalCtrlCli.exe"
$GcalZip = Join-Path $env:TEMP "GCalCtrlCli-Windows.zip"

if (-not (Test-Path $GcalCli)) {
    Write-Host "Downloading GCalCtrl CLI..."
    New-Item -ItemType Directory -Force -Path $GcalDir | Out-Null
    & gh release download dev-latest --repo yesmenjoker3104/gcal-ctrl --pattern "GCalCtrlCli-Windows.zip" --dir $env:TEMP --clobber
    if ($LASTEXITCODE -ne 0) { throw "GCalCtrl CLI download failed." }
    if (-not (Test-Path $GcalZip)) { throw "GCalCtrlCli-Windows.zip not found after download." }
    Expand-Archive -LiteralPath $GcalZip -DestinationPath $GcalDir -Force
    Remove-Item -LiteralPath $GcalZip -Force -ErrorAction SilentlyContinue
    if (-not (Test-Path $GcalCli)) {
        throw "GCalCtrlCli.exe was not found after extracting."
    }
}
Write-Host "GCalCtrl CLI ready: $GcalCli"

# GMailCtrl CLIのセットアップ
$GmailDir = Join-Path $Root "plugin\GmailCtrl"
$GmailCli = Join-Path $GmailDir "GMailCtrlCli.exe"
$GmailZip = Join-Path $env:TEMP "GMailCtrlCli-Windows.zip"

if (-not (Test-Path $GmailCli)) {
    Write-Host "Downloading GMailCtrl CLI..."
    New-Item -ItemType Directory -Force -Path $GmailDir | Out-Null
    & gh release download dev-latest --repo yesmenjoker3104/gmail-ctrl --pattern "GMailCtrlCli-Windows.zip" --dir $env:TEMP --clobber
    if ($LASTEXITCODE -ne 0) { throw "GMailCtrl CLI download failed." }
    if (-not (Test-Path $GmailZip)) { throw "GMailCtrlCli-Windows.zip not found after download." }
    Expand-Archive -LiteralPath $GmailZip -DestinationPath $GmailDir -Force
    Remove-Item -LiteralPath $GmailZip -Force -ErrorAction SilentlyContinue
    if (-not (Test-Path $GmailCli)) {
        throw "GMailCtrlCli.exe was not found after extracting."
    }
}
Write-Host "GMailCtrl CLI ready: $GmailCli"

if (-not (Test-Path $OllamaExe)) {
    Write-Host "Downloading Ollama into the project folder..."
    & curl.exe -L $OllamaUrl -o $OllamaZip
    if (-not (Test-Path $OllamaZip)) { throw "Ollama download failed." }
    Expand-Archive -LiteralPath $OllamaZip -DestinationPath $OllamaDir -Force
    Remove-Item -LiteralPath $OllamaZip -Force -ErrorAction SilentlyContinue
    if (-not (Test-Path $OllamaExe)) {
        throw "ollama.exe was not found after extracting the Ollama package."
    }
}

$server = Get-Process ollama -ErrorAction SilentlyContinue
if (-not $server) {
    Write-Host "Starting Ollama server..."
    Start-Process -FilePath $OllamaExe -ArgumentList "serve" -WindowStyle Hidden
    for ($i = 0; $i -lt 30; $i++) {
        try { Invoke-WebRequest "http://127.0.0.1:11434/api/tags" -UseBasicParsing -TimeoutSec 2 | Out-Null; break } catch { Start-Sleep -Seconds 1 }
    }
}

Write-Host "Preparing local model $Model..."
$modelList = & $OllamaExe list
if ($LASTEXITCODE -ne 0 -or -not ($modelList | Select-String -SimpleMatch $Model)) {
    Write-Host "Downloading model into the project models folder..."
    & $OllamaExe pull $Model
    if ($LASTEXITCODE -ne 0) { throw "LLM model download failed." }
}
if (-not (& $OllamaExe list | Select-String -SimpleMatch $Model)) {
    throw "LLM model $Model was not found after download."
}
Write-Host "Setup complete. You can start main.py." -ForegroundColor Green
