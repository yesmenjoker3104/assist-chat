from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from backend.plugin_installer import CatalogEntry

_URL_ROLE = Qt.ItemDataRole.UserRole


class RecommendedPluginsDialog(QDialog):
    def __init__(self, entries: list[CatalogEntry], parent=None):
        super().__init__(parent)
        self.setWindowTitle("おすすめプラグイン")
        self.resize(560, 380)
        self.selected_url: str | None = None
        layout = QVBoxLayout(self)
        self.list_widget = QListWidget()
        for entry in entries:
            item = QListWidgetItem(f"{entry.name}\n{entry.description}")
            item.setData(_URL_ROLE, entry.url)
            self.list_widget.addItem(item)
        if not entries:
            layout.addWidget(QLabel("おすすめプラグインはまだ登録されていません。"))
        layout.addWidget(self.list_widget)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("導入")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("閉じる")
        buttons.accepted.connect(self._accept_selected)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept_selected(self) -> None:
        item = self.list_widget.currentItem()
        if item is None:
            return
        self.selected_url = item.data(_URL_ROLE)
        self.accept()
