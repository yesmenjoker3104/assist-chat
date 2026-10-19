from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QSpinBox, QVBoxLayout,
)

from config import load_history_limits, load_settings, save_settings


# 新しいおすすめモデルを先頭に置く。サイズは Ollama ライブラリ掲載値の目安
MODEL_CATALOG = [
    ("Gemma4 E4B (6.6-9.5 GB)", "gemma4:e4b"),
    ("Gemma4 12B (7.7-8.0 GB)", "gemma4:12b"),
    ("Gemma4 26B (16-19 GB)", "gemma4:26b"),
    ("Qwen3.8 27B (18 GB)", "qwen3.8:27b"),
    ("Nemotron 3.5 Bolt (25 GB)", "nemotron-3.5-bolt"),
    ("Bonsai 2 9B (3.2 GB)", "bonsai-2:9b"),
    ("Qwen2.5 0.5B (397 MB)", "qwen2.5:0.5b"),
    ("Qwen2.5 1.5B (986 MB)", "qwen2.5:1.5b"),
    ("Qwen2.5 3B (1.9 GB)", "qwen2.5:3b"),
    ("Qwen2.5 7B (4.7 GB)", "qwen2.5:7b"),
    ("Qwen3 0.6B (523 MB)", "qwen3:0.6b"),
    ("Qwen3 1.7B (1.4 GB)", "qwen3:1.7b"),
    ("Qwen3 4B (2.5 GB)", "qwen3:4b"),
    ("Qwen3 8B (5.2 GB)", "qwen3:8b"),
    ("Qwen3 14B (9.3 GB)", "qwen3:14b"),
    ("Qwen3 30B (19 GB)", "qwen3:30b"),
    ("Qwen3 32B (20 GB)", "qwen3:32b"),
    ("Gemma3 1B (815 MB)", "gemma3:1b"),
    ("Llama3.2 3B (2.0 GB)", "llama3.2:3b"),
    ("Mistral 7B (4.1 GB)", "mistral:7b"),
]


class ModelDownloadThread(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(bool, str, str)

    def __init__(self, llm, model_name: str):
        super().__init__()
        self.llm = llm
        self.model_name = model_name

    def run(self):
        success, error = self.llm.pull_model(self.model_name, self.progress.emit)
        self.finished.emit(success, self.model_name, error)


class ModelDeleteThread(QThread):
    completed = pyqtSignal(bool, str, str)

    def __init__(self, llm, model_name: str):
        super().__init__()
        self.llm = llm
        self.model_name = model_name

    def run(self):
        success, error = self.llm.delete_model(self.model_name)
        self.completed.emit(success, self.model_name, error)


class SettingsDialog(QDialog):
    settings_applied = pyqtSignal(dict)

    def __init__(self, llm, parent=None):
        super().__init__(parent)
        self.llm = llm
        self.download_thread = None
        self.delete_thread = None
        self.setWindowTitle("設定")
        self.setMinimumWidth(560)
        self._build_ui()
        self._load_settings()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.ollama_url = QLineEdit()
        form.addRow("Ollama URL", self.ollama_url)

        model_row = QHBoxLayout()
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        model_row.addWidget(self.model_combo, 1)
        refresh_button = QPushButton("更新")
        refresh_button.clicked.connect(self._refresh_models)
        model_row.addWidget(refresh_button)
        switch_button = QPushButton("切り替え")
        switch_button.clicked.connect(self._switch_model)
        model_row.addWidget(switch_button)
        self.delete_button = QPushButton("削除")
        self.delete_button.clicked.connect(self._delete_model)
        model_row.addWidget(self.delete_button)
        form.addRow("使用モデル", model_row)

        download_row = QHBoxLayout()
        self.download_combo = QComboBox()
        self.download_combo.setEditable(True)
        self.download_combo.addItem("モデル名を選択または入力", "")
        for label, name in MODEL_CATALOG:
            self.download_combo.addItem(label, name)
        self.download_combo.currentIndexChanged.connect(self._copy_selected_model)
        download_row.addWidget(self.download_combo, 1)
        download_button = QPushButton("ダウンロード")
        download_button.clicked.connect(self._download_model)
        download_row.addWidget(download_button)
        form.addRow("モデル追加", download_row)
        self.model_status = QLabel()
        form.addRow("", self.model_status)

        self.gws_command = QLineEdit()
        form.addRow("gws コマンド", self.gws_command)
        self.download_output_dir = QLineEdit()
        form.addRow("動画保存先", self.download_output_dir)
        self.auto_fetch_enabled = QCheckBox("技術記事を自動取得して通知する")
        form.addRow("技術記事", self.auto_fetch_enabled)
        self.auto_fetch_minutes = QSpinBox()
        self.auto_fetch_minutes.setRange(1, 1440)
        self.auto_fetch_minutes.setSuffix(" 分")
        form.addRow("取得間隔", self.auto_fetch_minutes)

        history_limits = load_history_limits()
        self.history_retention = QSpinBox()
        self.history_retention.setRange(1, int(history_limits["max_retention"]))
        self.history_retention.setSuffix(" 件")
        form.addRow("会話履歴の保存数", self.history_retention)
        self.history_llm_turns = QSpinBox()
        self.history_llm_turns.setRange(0, int(history_limits["max_llm_turns"]))
        self.history_llm_turns.setSuffix(" 往復")
        form.addRow("LLMへ渡す履歴", self.history_llm_turns)
        self.history_initial_messages = QSpinBox()
        self.history_initial_messages.setRange(0, int(history_limits["max_initial_messages"]))
        self.history_initial_messages.setSuffix(" 件")
        form.addRow("起動時に復元する履歴", self.history_initial_messages)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _load_settings(self):
        settings = load_settings()
        self.ollama_url.setText(settings["ollama_url"])
        self.gws_command.setText(settings["gws_command"])
        self.download_output_dir.setText(settings.get("youtube_download_output_dir") or "")
        self.auto_fetch_enabled.setChecked(settings.get("tech_news_auto_fetch_enabled", True))
        self.auto_fetch_minutes.setValue(int(settings.get("tech_news_auto_fetch_minutes", 30)))
        self.history_retention.setValue(int(settings.get("chat_history_retention", 5000)))
        self.history_llm_turns.setValue(int(settings.get("chat_history_llm_turns", 10)))
        self.history_initial_messages.setValue(int(settings.get("chat_history_initial_messages", 200)))
        self._refresh_models(settings["model"])

    def _refresh_models(self, selected_model: str | None = None):
        self.llm.base_url = self.ollama_url.text().strip().rstrip("/") or self.llm.base_url
        selected_model = selected_model or self.model_combo.currentText() or self.llm.model
        models = self.llm.get_available_models()
        self.model_combo.clear()
        self.model_combo.addItems(models or [selected_model])
        self.model_combo.setCurrentText(selected_model)
        self.model_status.setText(
            f"インストール済みモデル: {len(models)}件" if models else "Ollamaへ接続できないか、モデルがありません。"
        )

    def _copy_selected_model(self, index: int):
        model_name = self.download_combo.itemData(index)
        if model_name:
            self.download_combo.setEditText(model_name)

    def _switch_model(self):
        model_name = self.model_combo.currentText().strip()
        if not model_name:
            return
        self.llm.model = model_name
        self.model_status.setText(f"使用モデルを「{model_name}」に切り替えました。保存すると次回起動後も使用します。")

    def _download_model(self):
        if (
            self.download_thread and self.download_thread.isRunning()
        ) or (self.delete_thread and self.delete_thread.isRunning()):
            return
        model_name = self.download_combo.currentText().strip()
        if not model_name or model_name.startswith("モデル名を選択"):
            return
        self.model_status.setText(f"ダウンロード中: {model_name}")
        self._set_model_controls_enabled(False)
        self.download_thread = ModelDownloadThread(self.llm, model_name)
        self.download_thread.progress.connect(self.model_status.setText)
        self.download_thread.finished.connect(self._on_download_finished)
        self.download_thread.start()

    def _on_download_finished(self, success: bool, model_name: str, error: str):
        if success:
            self._refresh_models(model_name)
            self._switch_model()
            self.model_status.setText(f"「{model_name}」をダウンロードして切り替えました。")
        else:
            detail = f"\n{error}" if error else ""
            self.model_status.setText(f"「{model_name}」のダウンロードに失敗しました。{detail}")
        self.download_thread = None
        self._set_model_controls_enabled(True)

    def _delete_model(self):
        if (
            self.delete_thread and self.delete_thread.isRunning()
        ) or (self.download_thread and self.download_thread.isRunning()):
            return
        model_name = self.model_combo.currentText().strip()
        installed_models = [
            self.model_combo.itemText(index)
            for index in range(self.model_combo.count())
        ]
        if not model_name or model_name not in installed_models:
            self.model_status.setText("インストール済みモデルを選択してください。")
            return
        if model_name == self.llm.model:
            QMessageBox.warning(
                self,
                "モデルを削除できません",
                "現在使用中のモデルです。先に別のモデルへ切り替えてください。",
            )
            return
        answer = QMessageBox.question(
            self,
            "モデルを削除",
            f"「{model_name}」を削除しますか？\nこの操作は元に戻せません。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.model_status.setText(f"削除中: {model_name}")
        self._set_model_controls_enabled(False)
        self.delete_thread = ModelDeleteThread(self.llm, model_name)
        self.delete_thread.completed.connect(self._on_delete_finished)
        self.delete_thread.start()

    def _on_delete_finished(self, success: bool, model_name: str, error: str):
        if success:
            self._refresh_models(self.llm.model)
            self.model_status.setText(f"「{model_name}」を削除しました。")
        else:
            detail = f"\n{error}" if error else ""
            self.model_status.setText(f"「{model_name}」の削除に失敗しました。{detail}")
        self.delete_thread = None
        self._set_model_controls_enabled(True)

    def _set_model_controls_enabled(self, enabled: bool):
        self.model_combo.setEnabled(enabled)
        self.download_combo.setEnabled(enabled)
        self.delete_button.setEnabled(enabled)

    def _save(self):
        settings = load_settings()
        settings.update({
            "ollama_url": self.ollama_url.text().strip() or "http://127.0.0.1:11434",
            "model": self.model_combo.currentText().strip() or settings["model"],
            "gws_command": self.gws_command.text().strip() or "gws",
            "youtube_download_output_dir": self.download_output_dir.text().strip() or None,
            "tech_news_auto_fetch_enabled": self.auto_fetch_enabled.isChecked(),
            "tech_news_auto_fetch_minutes": self.auto_fetch_minutes.value(),
            "chat_history_retention": self.history_retention.value(),
            "chat_history_llm_turns": self.history_llm_turns.value(),
            "chat_history_initial_messages": self.history_initial_messages.value(),
        })
        save_settings(settings)
        self.settings_applied.emit(settings)
        self.accept()