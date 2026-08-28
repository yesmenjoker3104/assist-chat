import atexit
import os
import shutil
import subprocess
import time

from PyQt6.QtWidgets import QLabel, QMainWindow

from backend.google_calendar import GoogleWorkspace
from backend.llm_engine import OllamaEngine
from backend.tech_news import TechNewsClient
from backend.video_downloader import VideoDownloader
from config import OLLAMA_EXE, OLLAMA_MODELS_DIR, load_settings
from gui.chat_widget import ChatWidget


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        settings = load_settings()
        self.llm = OllamaEngine(settings["ollama_url"], settings["model"])
        self.gws = GoogleWorkspace(settings["gws_command"])
        self.tech_news = TechNewsClient(
            settings["tech_news_dir"],
            settings.get("tech_news_cli_exe"),
        )
        self.downloader = VideoDownloader(
            settings["youtube_downloader_dir"],
            settings.get("youtube_download_output_dir"),
            cli_exe_path=settings.get("youtube_downloader_cli_exe"),
            max_parallel_downloads=settings.get("youtube_download_parallel", 3),
        )
        self.ollama_process = None
        self.setWindowTitle("Assist Chat - Google Workspace")
        self.resize(760, 640)
        self.status = QLabel("Google Calendar アシスタント")
        self.statusBar().addPermanentWidget(self.status)
        self.chat = ChatWidget(self.llm, self.gws, self.downloader, self.tech_news)
        self.setCentralWidget(self.chat)

    def start_ollama(self) -> str:
        if self.llm.health_check():
            return "Ollama接続済み"
        executable = str(OLLAMA_EXE) if OLLAMA_EXE.exists() else None
        if not executable:
            return "プロジェクト内のollama\\ollama.exeが見つかりません。"
        self.ollama_process = subprocess.Popen(
            [executable, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env={**os.environ, "OLLAMA_NOHISTORY": "1", "OLLAMA_MODELS": str(OLLAMA_MODELS_DIR)},
        )
        atexit.register(self.stop_ollama)
        for _ in range(30):
            if self.llm.health_check():
                if not self.llm.has_model():
                    return f"Ollama接続済み。ただし {self.llm.model} がありません。"
                return "Ollama接続済み"
            time.sleep(1)
        return "Ollamaの起動を待機中です。"

    def stop_ollama(self):
        if self.ollama_process and self.ollama_process.poll() is None:
            self.ollama_process.terminate()
