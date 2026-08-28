# Assist Chat 初回セットアップ
# Ollama本体とローカルLLMモデルを自動で準備します。
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$OllamaDir = Join-Path $Root "ollama"
$ModelsDir = Join-Path $Root "models"
$OllamaExe = Join-Path $OllamaDir "ollama.exe"
$Model = "qwen2.5:7b"
$GwsProject = "my-cal-ctrl"
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

if (-not (Test-Path $OllamaExe)) {
    Write-Host "Downloading Ollama into the project folder..."
    Invoke-WebRequest -Uri $OllamaUrl -OutFile $OllamaZip -UseBasicParsing
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
