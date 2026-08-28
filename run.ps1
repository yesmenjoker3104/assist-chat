param(
    [switch]$Setup
)

# Assist Chat 起動スクリプト
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$venv = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venv)) {
    $bootstrapPython = Get-Command python -ErrorAction SilentlyContinue
    if (-not $bootstrapPython) { throw "Python was not found. Install Python once to create .venv." }
    Write-Host "Creating project Python environment..."
    & $bootstrapPython.Source -m venv (Join-Path $PSScriptRoot ".venv")
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venv)) { throw "Failed to create .venv." }
}

# ここから先はシステムPythonを使わず、プロジェクト内のPythonだけを使う。
& $venv -m pip install -r (Join-Path $PSScriptRoot "requirements.txt") --disable-pip-version-check
if ($Setup) {
    Set-ExecutionPolicy -Scope Process Bypass
    & (Join-Path $PSScriptRoot "setup.ps1")
}
& $venv (Join-Path $PSScriptRoot "main.py")
