param(
    [string]$Model = "qwen2.5:7b"
)

# Assist Chat 初回セットアップ
# Ollama本体とローカルLLMモデルを自動で準備します。
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$venv = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $venv)) {
    $bootstrapPython = Get-Command python -ErrorAction SilentlyContinue
    if (-not $bootstrapPython) { throw "Python was not found. Install Python once to create .venv." }
    Write-Host "Creating project Python environment..."
    & $bootstrapPython.Source -m venv (Join-Path $Root ".venv")
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venv)) { throw "Failed to create .venv." }
}

& $venv -m pip install -r (Join-Path $Root "requirements.txt") --disable-pip-version-check

$OllamaDir = Join-Path $Root "ollama"
$ModelsDir = Join-Path $Root "models"
$OllamaExe = Join-Path $OllamaDir "ollama.exe"
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
    if (-not $npm) {
        $npmCandidates = @(
            (Join-Path $env:ProgramFiles "nodejs\npm.cmd"),
            (Join-Path $env:LOCALAPPDATA "Programs\nodejs\npm.cmd")
        )
        $npmPath = $npmCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
        if ($npmPath) {
            $env:Path = "$(Split-Path $npmPath);$env:Path"
            $npm = Get-Command npm -ErrorAction SilentlyContinue
        }
    }
    if (-not $npm) {
        $winget = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps\winget.exe"
        if (-not (Test-Path $winget)) { throw "Node.js/npm was not found and winget is unavailable." }
        Write-Host "Installing Node.js LTS..."
        & $winget install --id OpenJS.NodeJS.LTS --exact --accept-source-agreements --accept-package-agreements
        if ($LASTEXITCODE -ne 0) { throw "Node.js installation failed." }
        $npmCandidates = @(
            (Join-Path $env:ProgramFiles "nodejs\npm.cmd"),
            (Join-Path $env:LOCALAPPDATA "Programs\nodejs\npm.cmd")
        )
        $npmPath = $npmCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
        if ($npmPath) { $env:Path = "$(Split-Path $npmPath);$env:Path" }
        $npm = Get-Command npm -ErrorAction SilentlyContinue
    }
    if (-not $npm) { throw "Node.js/npm was not found after installation." }
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
    $modelDownloaded = $false
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        Write-Host "Downloading model into the project models folder (attempt $attempt/3)..."
        & $OllamaExe pull $Model
        if ($LASTEXITCODE -eq 0) {
            $modelDownloaded = $true
            break
        }
        if ($attempt -lt 3) {
            Write-Warning "LLM model download failed. Retrying in 10 seconds..."
            Start-Sleep -Seconds 10
        }
    }
    if (-not $modelDownloaded) {
        throw "LLM model download failed after 3 attempts. Try a smaller model, for example: .\setup.ps1 -Model qwen2.5:1.5b"
    }
}
if (-not (& $OllamaExe list | Select-String -SimpleMatch $Model)) {
    throw "LLM model $Model was not found after download."
}
Write-Host "Setup complete. You can start main.py." -ForegroundColor Green
