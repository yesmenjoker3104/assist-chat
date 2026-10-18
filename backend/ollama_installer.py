from __future__ import annotations

import os
import subprocess
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from config import OLLAMA_DIR, OLLAMA_EXE, OLLAMA_MODELS_DIR

OLLAMA_PACKAGE_URL = "https://github.com/ollama/ollama/releases/latest/download/ollama-windows-amd64.zip"
_USER_AGENT = "AssistChat-Installer"


def install_ollama(progress_callback: Callable[[int], None] | None = None) -> None:
    """公式配布ZIPをダウンロードし、ollama フォルダへ展開する。進捗は 0-100 の割合で通知する。"""
    if OLLAMA_EXE.exists():
        return
    parsed_url = urlparse(OLLAMA_PACKAGE_URL)
    if parsed_url.scheme != "https" or not parsed_url.netloc:
        raise ValueError("Ollama download URL must use HTTPS")

    OLLAMA_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="assist-chat-ollama-") as temp_dir:
        archive = Path(temp_dir) / "ollama-windows-amd64.zip"
        request = urllib.request.Request(OLLAMA_PACKAGE_URL, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(request, timeout=60) as response, archive.open("wb") as output:
            total = int(response.headers.get("Content-Length") or 0)
            downloaded = 0
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                downloaded += len(chunk)
                if progress_callback and total:
                    progress_callback(min(100, downloaded * 100 // total))
        with zipfile.ZipFile(archive) as package:
            package.extractall(OLLAMA_DIR)

    if not OLLAMA_EXE.exists():
        raise RuntimeError("ollama.exe was not found after extracting the Ollama package.")


def launch_ollama_server() -> subprocess.Popen:
    return subprocess.Popen(
        [str(OLLAMA_EXE), "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        env={**os.environ, "OLLAMA_NOHISTORY": "1", "OLLAMA_MODELS": str(OLLAMA_MODELS_DIR)},
    )
