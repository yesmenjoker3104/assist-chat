import json
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
SETTINGS_FILE = DATA_DIR / "settings.json"
HISTORY_DATABASE_FILE = DATA_DIR / "chat_history.db"
HISTORY_LIMITS_FILE = DATA_DIR / "history_limits.json"
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
    "youtube_downloader_dir": str(BASE_DIR / "plugin" / "YoutubeAudioDownloader"),
    "youtube_downloader_cli_exe": str(BASE_DIR / "plugin" / "YoutubeAudioDownloader" / "YoutubeAudioDownloaderCli.exe"),
    "youtube_download_output_dir": None,
    "youtube_download_parallel": 3,
    "tech_news_dir": str(BASE_DIR / "plugin" / "GetNewTechInfo"),
    "tech_news_cli_exe": str(BASE_DIR / "plugin" / "GetNewTechInfo" / "GetNewTechInfoCli.exe"),
    "tech_news_auto_fetch_enabled": True,
    "tech_news_auto_fetch_minutes": 30,
    "chat_history_retention": 5000,
    "chat_history_llm_turns": 10,
    "chat_history_initial_messages": 200,
}

DEFAULT_HISTORY_LIMITS = {
    "max_retention": 50000,
    "max_llm_turns": 100,
    "max_initial_messages": 2000,
}


def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            return {**DEFAULT_SETTINGS, **saved}
        except (OSError, json.JSONDecodeError):
            pass
    return DEFAULT_SETTINGS.copy()


def load_history_limits() -> dict:
    if not HISTORY_LIMITS_FILE.exists():
        HISTORY_LIMITS_FILE.write_text(
            json.dumps(DEFAULT_HISTORY_LIMITS, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return DEFAULT_HISTORY_LIMITS.copy()
    try:
        saved = json.loads(HISTORY_LIMITS_FILE.read_text(encoding="utf-8"))
        return {**DEFAULT_HISTORY_LIMITS, **saved}
    except (OSError, json.JSONDecodeError):
        return DEFAULT_HISTORY_LIMITS.copy()

def save_settings(settings: dict) -> None:
    SETTINGS_FILE.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
    )
