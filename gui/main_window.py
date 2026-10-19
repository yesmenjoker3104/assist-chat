import atexit
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QTextBrowser,
    QVBoxLayout,
)

from backend.llm_engine import OllamaEngine
from backend.ollama_installer import install_ollama, launch_ollama_server
from backend.plugin_installer import (
    PluginExistsError,
    PluginInstallError,
    PluginPackage,
    fetch_catalog,
    fetch_package,
    install_package,
)
from backend.plugin_manager import PluginManager
from backend.tech_news import TechNewsClient
from backend.updater import (
    ReleaseInfo,
    download_release,
    fetch_latest_release,
    start_update_process,
)
from backend.video_downloader import VideoDownloader
from config import BASE_DIR, OLLAMA_EXE, load_settings
from gui.chat_widget import ChatWidget
from gui.plugin_install_dialog import RecommendedPluginsDialog
from gui.settings_dialog import SettingsDialog
from version import __version__


class UpdateWorker(QThread):
    no_update = pyqtSignal()
    update_ready = pyqtSignal(object, str)
    failed = pyqtSignal(str)

    def run(self) -> None:
        try:
            release = fetch_latest_release()
            if release is None:
                self.no_update.emit()
                return
            staged_exe = download_release(
                release,
                Path(tempfile.mkdtemp(prefix="assist-chat-download-")) / "AssistChat.exe",
            )
            self.update_ready.emit(release, str(staged_exe))
        except Exception as error:
            self.failed.emit(str(error))


class CallWorker(QThread):
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, func, parent=None):
        super().__init__(parent)
        self._func = func

    def run(self) -> None:
        try:
            self.done.emit(self._func())
        except Exception as error:
            self.failed.emit(str(error))


class OllamaSetupWorker(QThread):
    progress = pyqtSignal(str, int)
    server_started = pyqtSignal(object)
    succeeded = pyqtSignal()
    failed = pyqtSignal(str)

    _PULL_ATTEMPTS = 3
    _PERCENT_PATTERN = re.compile(r"(\d{1,3})%")

    def __init__(self, llm: OllamaEngine, parent=None):
        super().__init__(parent)
        self.llm = llm

    def run(self) -> None:
        try:
            if not OLLAMA_EXE.exists():
                self.progress.emit("Ollamaをダウンロードしています...", 0)
                install_ollama(lambda percent: self.progress.emit("Ollamaをダウンロードしています...", percent))
            if not self.llm.health_check():
                self.progress.emit("Ollamaサーバーを起動しています...", -1)
                self.server_started.emit(launch_ollama_server())
                for _ in range(60):
                    if self.llm.health_check():
                        break
                    time.sleep(1)
                else:
                    raise RuntimeError("Ollamaサーバーの起動を確認できませんでした。")
            if not self.llm.has_model():
                self._pull_model()
            self.succeeded.emit()
        except Exception as error:
            self.failed.emit(str(error))

    def _pull_model(self) -> None:
        model = self.llm.model
        error = ""
        for attempt in range(1, self._PULL_ATTEMPTS + 1):
            self.progress.emit(f"モデル {model} をダウンロードしています...（{attempt}/{self._PULL_ATTEMPTS}）", -1)
            ok, error = self.llm.pull_model(model, progress_callback=self._on_pull_output)
            if ok:
                return
            if attempt < self._PULL_ATTEMPTS:
                time.sleep(10)
        raise RuntimeError(f"モデル {model} のダウンロードに失敗しました。\n{error}")

    def _on_pull_output(self, line: str) -> None:
        match = self._PERCENT_PATTERN.search(line)
        percent = int(match.group(1)) if match else -1
        self.progress.emit(f"モデル {self.llm.model} をダウンロードしています...", percent)


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
        plugin_menu.addSeparator()
        recommended_action = QAction("おすすめプラグインから導入...", self)
        recommended_action.triggered.connect(self.install_recommended_plugin)
        plugin_menu.addAction(recommended_action)
        plugin_url_action = QAction("URLからプラグインを導入...", self)
        plugin_url_action.triggered.connect(self.install_plugin_from_url)
        plugin_menu.addAction(plugin_url_action)

        help_menu = self.menuBar().addMenu("ヘルプ")
        version_action = QAction("バージョン情報", self)
        version_action.triggered.connect(self.show_version_info)
        help_menu.addAction(version_action)
        self.check_update_action = QAction("更新を確認", self)
        self.check_update_action.triggered.connect(self.check_for_updates)
        help_menu.addAction(self.check_update_action)
        self._update_worker: UpdateWorker | None = None
        self._plugin_worker: CallWorker | None = None
        self._setup_worker: OllamaSetupWorker | None = None
        self._setup_progress: QProgressDialog | None = None
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

    def install_plugin_from_url(self):
        url, ok = QInputDialog.getText(
            self, "URLからプラグインを導入", "プラグインZIPのURL（https://で始まるもの）:"
        )
        if ok and url.strip():
            self._start_plugin_download(url.strip())

    def install_recommended_plugin(self):
        self._run_plugin_task(
            fetch_catalog,
            "おすすめプラグインを取得しています...",
            self._on_catalog_loaded,
        )

    def _on_catalog_loaded(self, entries):
        installed_versions = {p.name: p.version for p in self.plugin_manager.plugins}
        dlg = RecommendedPluginsDialog(entries, installed_versions, self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected_url:
            self._start_plugin_download(dlg.selected_url)

    def _start_plugin_download(self, url: str):
        answer = QMessageBox.question(
            self,
            "プラグインの導入",
            "次の提供元からプラグインをダウンロードして導入します。\n\n"
            f"{url}\n\n"
            "プラグインは実行ファイルとして、あなたのアカウントの権限で動作します。\n"
            "信頼できる提供元のものだけを導入してください。\n\n続けますか？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._run_plugin_task(
            lambda: fetch_package(url),
            "プラグインをダウンロードしています...",
            self._on_package_ready,
        )

    def _on_package_ready(self, package: PluginPackage):
        runtime_dir = self.plugin_manager.runtime_dir
        already_installed = (runtime_dir / package.name).exists()
        try:
            if already_installed:
                answer = QMessageBox.question(
                    self,
                    "上書きの確認",
                    f"{package.name} は既にインストールされています。\n"
                    f"v{package.version} で上書きしますか？\n\n"
                    "プログラムのファイルは置き換えますが、データファイル（DBなど）は残します。",
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
            install_package(package, runtime_dir, replace=already_installed)
        except PluginExistsError as error:
            QMessageBox.warning(self, "プラグイン", str(error))
            return
        except (PluginInstallError, OSError) as error:
            QMessageBox.warning(self, "プラグイン", f"インストールに失敗しました: {error}")
            return
        finally:
            package.cleanup()
        self.plugin_manager.reload()
        QMessageBox.information(
            self,
            "プラグイン",
            f"{package.name} v{package.version} を導入しました。\n"
            "チャットからすぐに利用できます。",
        )

    def _run_plugin_task(self, func, message: str, on_done):
        if self._plugin_worker is not None and self._plugin_worker.isRunning():
            QMessageBox.information(self, "プラグイン", "別のプラグイン処理が実行中です。完了後にもう一度お試しください。")
            return
        progress = QProgressDialog(message, None, 0, 0, self)
        progress.setWindowTitle("プラグイン")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()
        worker = CallWorker(func, self)
        self._plugin_worker = worker
        worker.done.connect(lambda result: (progress.close(), on_done(result)))
        worker.failed.connect(lambda msg: (progress.close(), QMessageBox.warning(self, "プラグイン", msg)))
        worker.start()

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
        self._set_ollama_status("接続済み")
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

    def prepare_ollama(self) -> None:
        if OLLAMA_EXE.exists() or self.llm.health_check():
            self.status.setText(self.start_ollama())
            return
        answer = QMessageBox.question(
            self,
            "Ollamaの準備",
            "Ollamaが見つかりません。公式配布元からダウンロードし、"
            f"モデル {self.llm.model} も取得します。\n"
            "通信量は数GBかかります。続行しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.status.setText(self.start_ollama())
            return
        self._start_ollama_setup()

    def _start_ollama_setup(self) -> None:
        self._setup_progress = QProgressDialog("Ollamaを準備しています...", None, 0, 100, self)
        self._setup_progress.setWindowTitle("Ollamaの準備")
        self._setup_progress.setCancelButton(None)
        self._setup_progress.setMinimumDuration(0)
        self._setup_progress.setAutoClose(False)
        self._setup_progress.setAutoReset(False)
        self._setup_progress.setValue(0)
        self._setup_worker = OllamaSetupWorker(self.llm, self)
        self._setup_worker.progress.connect(self._on_ollama_setup_progress)
        self._setup_worker.server_started.connect(self._on_ollama_server_started)
        self._setup_worker.succeeded.connect(self._on_ollama_setup_succeeded)
        self._setup_worker.failed.connect(self._on_ollama_setup_failed)
        self._setup_worker.start()

    def _on_ollama_setup_progress(self, message: str, percent: int) -> None:
        if self._setup_progress is None:
            return
        self._setup_progress.setLabelText(message)
        if percent < 0:
            self._setup_progress.setRange(0, 0)
        else:
            if self._setup_progress.maximum() == 0:
                self._setup_progress.setRange(0, 100)
            self._setup_progress.setValue(percent)

    def _on_ollama_server_started(self, process: subprocess.Popen) -> None:
        self.ollama_process = process
        self.ollama_pid = process.pid
        atexit.register(self.stop_ollama)

    def _on_ollama_setup_succeeded(self) -> None:
        self._close_ollama_setup_progress()
        self.status.setText(self._ollama_status_text("接続済み"))
        QMessageBox.information(self, "Ollamaの準備", "Ollamaとモデルの準備が完了しました。")

    def _on_ollama_setup_failed(self, message: str) -> None:
        self._close_ollama_setup_progress()
        self.status.setText(self._ollama_status_text("準備に失敗"))
        QMessageBox.critical(
            self,
            "Ollamaの準備失敗",
            f"Ollamaの準備に失敗しました。\n{message}\n\n通信状況を確認して、アプリを再起動してください。",
        )

    def _close_ollama_setup_progress(self) -> None:
        if self._setup_progress is not None:
            self._setup_progress.close()
            self._setup_progress = None

    def start_ollama(self) -> str:
        if self.llm.health_check():
            # 前回終了時の孤立プロセスなど、既存起動分も終了対象として追跡する
            self.ollama_pid = self._find_running_ollama_pid()
            atexit.register(self.stop_ollama)
            return self._ollama_status_text("接続済み")
        executable = str(OLLAMA_EXE) if OLLAMA_EXE.exists() else None
        if not executable:
            return self._ollama_status_text("実行ファイルが見つかりません")
        self.ollama_process = launch_ollama_server()
        self.ollama_pid = self.ollama_process.pid
        atexit.register(self.stop_ollama)
        for _ in range(30):
            if self.llm.health_check():
                if not self.llm.has_model():
                    return self._ollama_status_text(f"接続済み・モデルなし")
                return self._ollama_status_text("接続済み")
            time.sleep(1)
        return self._ollama_status_text("起動待機中")

    def _ollama_status_text(self, state: str) -> str:
        return f"Ollama: {state} / モデル: {self.llm.model}"

    def _set_ollama_status(self, state: str) -> None:
        self.status.setText(self._ollama_status_text(state))

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

    def show_version_info(self) -> None:
        QMessageBox.information(
            self,
            "バージョン情報",
            f"Assist Chat\nバージョン: v{__version__}",
        )

    def check_for_updates(self) -> None:
        if not getattr(sys, "frozen", False):
            QMessageBox.information(
                self,
                "更新確認",
                "更新確認は exe 版で起動したときに利用できます。",
            )
            return
        if self._update_worker is not None and self._update_worker.isRunning():
            return
        self.check_update_action.setEnabled(False)
        self._update_worker = UpdateWorker(self)
        self._update_worker.no_update.connect(self._show_no_update)
        self._update_worker.update_ready.connect(self._confirm_update)
        self._update_worker.failed.connect(self._show_update_error)
        self._update_worker.finished.connect(self._update_finished)
        self._update_worker.start()

    def _show_no_update(self) -> None:
        QMessageBox.information(
            self,
            "更新確認",
            f"現在のバージョン v{__version__} は最新版です。",
        )

    def _confirm_update(self, release: object, staged_exe: str) -> None:
        if not isinstance(release, ReleaseInfo):
            self._show_update_error("更新データの形式が不正です")
            return
        answer = QMessageBox.question(
            self,
            "更新があります",
            f"バージョン {release.version} に更新します。アプリを再起動しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            start_update_process(Path(staged_exe), Path(sys.executable).resolve())
            self.close()
        except Exception as error:
            self._show_update_error(str(error))

    def _show_update_error(self, message: str) -> None:
        QMessageBox.critical(
            self,
            "更新失敗",
            f"更新を確認できませんでした。\n{message}",
        )

    def _update_finished(self) -> None:
        self.check_update_action.setEnabled(True)
        self._update_worker = None

    def closeEvent(self, event):
        self.stop_ollama()
        super().closeEvent(event)
