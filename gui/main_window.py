import atexit
import os
import shutil
import subprocess
import time
from pathlib import Path

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QLabel, QMainWindow

from backend.llm_engine import OllamaEngine
from backend.plugin_manager import PluginManager
from backend.tech_news import TechNewsClient
from backend.video_downloader import VideoDownloader
from config import BASE_DIR, OLLAMA_EXE, OLLAMA_MODELS_DIR, load_settings
from gui.chat_widget import ChatWidget
from gui.settings_dialog import SettingsDialog


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        settings = load_settings()
        self.llm = OllamaEngine(settings["ollama_url"], settings["model"])
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
        self.plugin_manager = PluginManager(BASE_DIR, settings)
        self.ollama_process = None
        self.ollama_pid = None
        self.setWindowTitle("Assist Chat")
        self.resize(760, 640)
        self.status = QLabel("Assist Chat")
        self.statusBar().addPermanentWidget(self.status)
        settings_menu = self.menuBar().addMenu("設定")
        open_settings_action = QAction("設定を開く", self)
        open_settings_action.triggered.connect(self.open_settings)
        settings_menu.addAction(open_settings_action)
        self.chat = ChatWidget(
            self.llm,
            self.downloader,
            self.tech_news,
            plugin_manager=self.plugin_manager,
            tech_news_auto_fetch_enabled=settings.get("tech_news_auto_fetch_enabled", True),
            tech_news_auto_fetch_minutes=settings.get("tech_news_auto_fetch_minutes", 30),
        )
        self.setCentralWidget(self.chat)
        # GCalCtrl プラグインが読み込まれていれば起動時に認証状態を確認する
        self._check_gcal_auth()

    def _check_gcal_auth(self):
        found = self.plugin_manager.find("agenda")
        if not found:
            return
        plugin, action_def = found
        result = self.plugin_manager.execute(plugin, action_def, {})
        if not result.get("ok") and result.get("auth_required"):
            self.chat._add_auth_button()

    def open_settings(self):
        dialog = SettingsDialog(self.llm, self)
        dialog.settings_applied.connect(self.apply_settings)
        dialog.exec()

    def apply_settings(self, settings: dict):
        self.llm.base_url = settings["ollama_url"].rstrip("/")
        self.llm.model = settings["model"]
        self.plugin_manager.settings = settings
        self.downloader.output_dir = (
            Path(settings["youtube_download_output_dir"])
            if settings.get("youtube_download_output_dir") else Path.home() / "Downloads"
        )
        self.chat.tech_news_timer.setInterval(settings["tech_news_auto_fetch_minutes"] * 60 * 1000)
        self.chat.apply_history_settings(settings)
        if settings["tech_news_auto_fetch_enabled"]:
            self.chat.tech_news_timer.start()
        else:
            self.chat.tech_news_timer.stop()

    def start_ollama(self) -> str:
        if self.llm.health_check():
            # 前回終了時の孤立プロセスなど、既存起動分も終了対象として追跡する
            self.ollama_pid = self._find_running_ollama_pid()
            atexit.register(self.stop_ollama)
            return "Ollama接続済み"
        executable = str(OLLAMA_EXE) if OLLAMA_EXE.exists() else None
        if not executable:
            return "プロジェクト内のollama\\ollama.exeが見つかりません。"
        self.ollama_process = subprocess.Popen(
            [executable, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env={**os.environ, "OLLAMA_NOHISTORY": "1", "OLLAMA_MODELS": str(OLLAMA_MODELS_DIR)},
        )
        self.ollama_pid = self.ollama_process.pid
        atexit.register(self.stop_ollama)
        for _ in range(30):
            if self.llm.health_check():
                if not self.llm.has_model():
                    return f"Ollama接続済み。ただし {self.llm.model} がありません。"
                return "Ollama接続済み"
            time.sleep(1)
        return "Ollamaの起動を待機中です。"

    def _find_running_ollama_pid(self) -> int | None:
        """プロジェクト同梱のollama.exeに限定してPIDを検索する（システム版は対象外）。"""
        if not OLLAMA_EXE.exists():
            return None
        try:
            result = subprocess.run(
                [
                    "powershell", "-NoProfile", "-NonInteractive", "-Command",
                    "(Get-CimInstance Win32_Process -Filter \"Name='ollama.exe'\" | "
                    f"Where-Object {{ $_.ExecutablePath -eq '{OLLAMA_EXE}' }} | "
                    "Select-Object -First 1 -ExpandProperty ProcessId)",
                ],
                capture_output=True, text=True, timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            pid_text = result.stdout.strip()
            return int(pid_text) if pid_text.isdigit() else None
        except (OSError, subprocess.SubprocessError):
            return None

    def stop_ollama(self):
        if self.ollama_process and self.ollama_process.poll() is not None:
            return
        pid = self.ollama_pid
        if not pid:
            return
        try:
            # 子プロセス（モデル実行用ランナーなど）も含めてツリーごと終了させる
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True, timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            if self.ollama_process:
                self.ollama_process.terminate()
        self.ollama_pid = None

    def closeEvent(self, event):
        self.stop_ollama()
        super().closeEvent(event)
