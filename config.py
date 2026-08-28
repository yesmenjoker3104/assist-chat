import json
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
SETTINGS_FILE = DATA_DIR / "settings.json"
OLLAMA_DIR = BASE_DIR / "ollama"
OLLAMA_EXE = OLLAMA_DIR / "ollama.exe"
OLLAMA_MODELS_DIR = BASE_DIR / "models"
DATA_DIR.mkdir(parents=True, exist_ok=True)
OLLAMA_DIR.mkdir(parents=True, exist_ok=True)
OLLAMA_MODELS_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_SETTINGS = {
    "ollama_url": "http://127.0.0.1:11434",
    "model": "qwen2.5:7b",
    "gws_command": "gws",
    "youtube_downloader_dir": str(BASE_DIR / "release_runtime" / "YoutubeAudioDownloader"),
    "youtube_downloader_cli_exe": str(BASE_DIR / "release_runtime" / "YoutubeAudioDownloader" / "YoutubeAudioDownloaderCli.exe"),
    "youtube_download_output_dir": None,
    "youtube_download_parallel": 3,
    "tech_news_dir": str(BASE_DIR / "release_runtime" / "GetNewTechInfo"),
    "tech_news_cli_exe": str(BASE_DIR / "release_runtime" / "GetNewTechInfo" / "GetNewTechInfoCli.exe"),
}


def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            return {**DEFAULT_SETTINGS, **saved}
        except (OSError, json.JSONDecodeError):
            pass
    return DEFAULT_SETTINGS.copy()
