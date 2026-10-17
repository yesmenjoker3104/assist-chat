from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from version import __version__

GITHUB_REPO = "yesmenjoker3104/assist-chat"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASE_ASSET_NAME = "AssistChat.exe"
_USER_AGENT = "AssistChat-Updater"

_APPLY_UPDATE_SCRIPT = r"""
param(
    [Parameter(Mandatory = $true)][string]$CurrentExe,
    [Parameter(Mandatory = $true)][string]$StagedExe,
    [Parameter(Mandatory = $true)][int]$ParentPid
)

$ErrorActionPreference = 'Stop'
$current = [System.IO.Path]::GetFullPath($CurrentExe)
$staged = [System.IO.Path]::GetFullPath($StagedExe)
$backup = "$current.previous"
$currentName = [System.IO.Path]::GetFileName($current)
$backupName = [System.IO.Path]::GetFileName($backup)
$applicationDirectory = [System.IO.Path]::GetDirectoryName($current)
$renamed = $false

function Invoke-WithRetry([scriptblock]$Action) {
    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        try {
            & $Action
            return
        }
        catch {
            if ($attempt -eq 119) { throw }
            Start-Sleep -Milliseconds 250
        }
    }
}

try {
    while (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue) {
        Start-Sleep -Milliseconds 250
    }
    if (Test-Path -LiteralPath $backup) {
        Remove-Item -LiteralPath $backup -Force
    }
    Invoke-WithRetry { Rename-Item -LiteralPath $current -NewName $backupName }
    $renamed = $true
    Invoke-WithRetry { Move-Item -LiteralPath $staged -Destination $current -Force }
    Start-Process -FilePath $current -WorkingDirectory $applicationDirectory
    Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
}
catch {
    if ($renamed -and -not (Test-Path -LiteralPath $current)) {
        Rename-Item -LiteralPath $backup -NewName $currentName
        Start-Process -FilePath $current -WorkingDirectory $applicationDirectory
    }
    exit 1
}
"""


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    download_url: str


def _version_key(version: str) -> tuple[int, ...]:
    match = re.fullmatch(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", version.strip())
    if match is None:
        raise ValueError(f"Unsupported version: {version}")
    return tuple(int(part or 0) for part in match.groups())


def is_newer_version(version: str, current_version: str = __version__) -> bool:
    return _version_key(version) > _version_key(current_version)


def fetch_latest_release(timeout_seconds: float = 10.0) -> ReleaseInfo | None:
    request = urllib.request.Request(
        GITHUB_API_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": _USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            release = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise

    tag_name = str(release.get("tag_name", ""))
    if not tag_name or not is_newer_version(tag_name):
        return None

    asset = next(
        (
            item
            for item in release.get("assets", [])
            if item.get("name") == RELEASE_ASSET_NAME
            and isinstance(item.get("browser_download_url"), str)
        ),
        None,
    )
    if asset is None:
        raise RuntimeError(f"Release asset not found: {RELEASE_ASSET_NAME}")
    return ReleaseInfo(tag_name.removeprefix("v"), asset["browser_download_url"])


def download_release(release: ReleaseInfo, destination: Path) -> Path:
    parsed_url = urlparse(release.download_url)
    if parsed_url.scheme != "https" or not parsed_url.netloc:
        raise ValueError("Release download URL must use HTTPS")
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        release.download_url,
        headers={"User-Agent": _USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
    with destination.open("rb") as downloaded:
        if downloaded.read(2) != b"MZ":
            raise RuntimeError("Downloaded file is not a Windows executable")
    return destination


def start_update_process(staged_exe: Path, current_exe: Path) -> None:
    if sys.platform != "win32":
        raise RuntimeError("In-app updates are supported on Windows only")
    script_path = Path(tempfile.mkdtemp(prefix="assist-chat-update-")) / "apply_update.ps1"
    script_path.write_text(_APPLY_UPDATE_SCRIPT, encoding="utf-8-sig")
    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
            "-CurrentExe",
            str(current_exe),
            "-StagedExe",
            str(staged_exe),
            "-ParentPid",
            str(os.getpid()),
        ],
        cwd=tempfile.gettempdir(),
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
        close_fds=True,
    )
