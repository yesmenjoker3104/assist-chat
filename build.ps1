# Assist Chat を AssistChat.exe としてビルドする（出力先はプロジェクト直下）
# exe は data/・plugin/・ollama/・models/ を自身の隣から参照するため、同じフォルダに出力する。
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location $Root

$venv = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $venv)) {
    $bootstrapPython = Get-Command python -ErrorAction SilentlyContinue
    if (-not $bootstrapPython) { throw "Python was not found. Install Python once to create .venv." }
    Write-Host "Creating project Python environment..."
    & $bootstrapPython.Source -m venv (Join-Path $Root ".venv")
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venv)) { throw "Failed to create .venv." }
}

# PyInstaller はビルド時のみ必要なため requirements.txt には含めない
& $venv -m pip install -r (Join-Path $Root "requirements.txt") pyinstaller --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw "Failed to install build dependencies." }

# PyInstaller は相対パスを spec の出力先（build\）基準に解釈するため、絶対パスで渡す
$icon = Join-Path $Root "gui\icon\icon.ico"
& $venv -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name AssistChat `
    --icon $icon `
    --add-data "$icon;gui\icon" `
    --distpath $Root `
    --workpath (Join-Path $Root "build") `
    --specpath (Join-Path $Root "build") `
    (Join-Path $Root "main.py")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed. If AssistChat.exe is running, close it and run build.ps1 again." }

$exe = Join-Path $Root "AssistChat.exe"
if (-not (Test-Path $exe)) { throw "Build failed: AssistChat.exe was not created." }
Write-Host "Build succeeded: $exe"
