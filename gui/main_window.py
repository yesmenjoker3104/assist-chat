import atexit
import os
import shutil
import subprocess
import time
from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QMainWindow, QTextBrowser, QVBoxLayout

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

        plugin_menu = self.menuBar().addMenu("プラグイン")
        plugin_list_action = QAction("プラグイン一覧", self)
        plugin_list_action.triggered.connect(self.show_plugin_list)
        plugin_menu.addAction(plugin_list_action)
        self.chat = ChatWidget(
            self.llm,
            self.downloader,
            self.tech_news,
            plugin_manager=self.plugin_manager,
            tech_news_auto_fetch_enabled=settings.get("tech_news_auto_fetch_enabled", True),
            tech_news_auto_fetch_minutes=settings.get("tech_news_auto_fetch_minutes", 30),
        )
        self.setCentralWidget(self.chat)
        self._notified_event_ids: set[str] = set()
        self._reminder_timer = QTimer(self)
        self._reminder_timer.setInterval(5 * 60 * 1000)
        self._reminder_timer.timeout.connect(self._check_upcoming_events)
        self._reminder_timer.start()
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

    def _check_upcoming_events(self):
        found = self.plugin_manager.find("agenda")
        if not found:
            return
        plugin, action_def = found
        from datetime import datetime, timezone
        today = datetime.now().strftime("%Y-%m-%d")
        result = self.plugin_manager.execute(plugin, action_def, {"target_date": today})
        if not result.get("ok"):
            return
        now = datetime.now().astimezone()
        for event in result.get("items", []):
            eid = event.get("id", "")
            if not eid or eid in self._notified_event_ids:
                continue
            dt_str = event.get("start", {}).get("dateTime")
            if not dt_str:
                continue
            try:
                start_dt = datetime.fromisoformat(dt_str)
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=timezone.utc)
                diff = (start_dt - now).total_seconds()
                if 0 <= diff <= 15 * 60:
                    title = event.get("summary", "（タイトルなし）")
                    mins = int(diff / 60)
                    self.chat._add_bubble("assistant", f"⏰ リマインダー: 「{title}」まであと{mins}分です。")
                    self._notified_event_ids.add(eid)
            except (ValueError, TypeError):
                continue

    def show_plugin_list(self):
        import json as _json
        plugins = self.plugin_manager.plugins
        if not plugins:
            html = "<p>読み込み済みのプラグインはありません。</p>"
        else:
            parts = [
                "<style>"
                "body{font-family:sans-serif;font-size:13px;}"
                "h3{margin:12px 0 2px 0;border-bottom:1px solid #ccc;padding-bottom:2px;}"
                ".desc{color:#555;margin:2px 0 6px 0;font-size:12px;}"
                ".exe-ok{color:green;font-size:11px;}"
                ".exe-ng{color:red;font-size:11px;}"
                "table{width:100%;border-collapse:collapse;margin-bottom:12px;}"
                "td{padding:4px 6px;vertical-align:top;border-bottom:1px solid #eee;}"
                "td.name{width:14%;white-space:nowrap;font-weight:bold;color:#333;}"
                "td.hint{width:36%;color:#444;}"
                "td.example{width:50%;font-family:monospace;font-size:11px;"
                "  background:#f7f7f7;word-break:break-all;}"
                "</style>"
            ]
            for p in plugins:
                exe_tag = (
                    f'<span class="exe-ok">✔ {p.executable.name}</span>'
                    if p.executable.exists()
                    else f'<span class="exe-ng">✘ {p.executable.name} なし</span>'
                )
                parts.append(
                    f"<h3>{p.name} <small style='font-weight:normal;'>v{p.version}</small>"
                    f"&nbsp;&nbsp;{exe_tag}</h3>"
                )
                parts.append(f'<p class="desc">{p.description}</p>')
                parts.append(
                    "<table><tr>"
                    "<th style='text-align:left;font-size:11px;color:#888;padding:2px 6px;'>アクション</th>"
                    "<th style='text-align:left;font-size:11px;color:#888;padding:2px 6px;'>説明</th>"
                    "<th style='text-align:left;font-size:11px;color:#888;padding:2px 6px;'>プロンプト例</th>"
                    "</tr>"
                )
                for a in p.actions:
                    example = _json.dumps(a.prompt_example, ensure_ascii=False)
                    parts.append(
                        f"<tr>"
                        f"<td class='name'>{a.action}</td>"
                        f"<td class='hint'>{a.prompt_hint}</td>"
                        f"<td class='example'>{example}</td>"
                        f"</tr>"
                    )
                parts.append("</table>")
            html = "".join(parts)

        dlg = QDialog(self)
        dlg.setWindowTitle("読み込み済みプラグイン")
        dlg.resize(780, 480)
        layout = QVBoxLayout(dlg)
        browser = QTextBrowser()
        browser.setHtml(html)
        layout.addWidget(browser)
        btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn.rejected.connect(dlg.reject)
        layout.addWidget(btn)
        dlg.exec()

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
        return "Ollamaの起動を待機中です。しばらくお待ちください。起動しない場合は setup.ps1 を実行してください。"

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
