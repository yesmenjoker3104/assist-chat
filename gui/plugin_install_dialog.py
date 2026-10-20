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
from backend.updater import is_newer_version

_URL_ROLE = Qt.ItemDataRole.UserRole


def _status_for(entry: CatalogEntry, installed_versions: dict[str, str]) -> tuple[str, str]:
    """Return (status label, button text) for a catalog entry."""
    if entry.name not in installed_versions:
        return "未導入", "導入"
    current = installed_versions[entry.name]
    if not entry.latest_version or not current:
        return "導入済み", "導入"
    try:
        newer = is_newer_version(entry.latest_version, current)
    except ValueError:
        return "導入済み", "導入"
    if newer:
        return f"更新あり（v{current} → v{entry.latest_version}）", "更新"
    return f"最新（v{current}）", "導入"


class RecommendedPluginsDialog(QDialog):
    def __init__(self, entries: list[CatalogEntry], installed_versions: dict[str, str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("おすすめプラグイン")
        self.resize(560, 380)
        self.selected_url: str | None = None
        self._entries = entries
        self._installed_versions = installed_versions
        layout = QVBoxLayout(self)
        self.list_widget = QListWidget()
        for entry in entries:
            label, _ = _status_for(entry, installed_versions)
            item = QListWidgetItem(f"{entry.name}  [{label}]\n{entry.description}")
            item.setData(_URL_ROLE, entry.url)
            self.list_widget.addItem(item)
        if not entries:
            layout.addWidget(QLabel("おすすめプラグインはまだ登録されていません。"))
        layout.addWidget(self.list_widget)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_button.setText("導入")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("閉じる")
        self.list_widget.currentRowChanged.connect(self._update_button_text)
        buttons.accepted.connect(self._accept_selected)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _update_button_text(self, row: int) -> None:
        if 0 <= row < len(self._entries):
            _, text = _status_for(self._entries[row], self._installed_versions)
            self._ok_button.setText(text)

    def _accept_selected(self) -> None:
        item = self.list_widget.currentItem()
        if item is None:
            return
        self.selected_url = item.data(_URL_ROLE)
        self.accept()
