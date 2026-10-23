from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
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


def _has_update(entry: CatalogEntry, installed_versions: dict[str, str]) -> bool:
    return _status_for(entry, installed_versions)[0].startswith("更新あり")


class RecommendedPluginsDialog(QDialog):
    def __init__(self, entries: list[CatalogEntry], installed_versions: dict[str, str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("おすすめプラグイン")
        self.resize(560, 380)
        self.selected_urls: list[str] = []
        self._entries = entries
        self._installed_versions = installed_versions
        layout = QVBoxLayout(self)
        self.list_widget = QListWidget()
        for entry in entries:
            label, _ = _status_for(entry, installed_versions)
            item = QListWidgetItem(f"{entry.name}  [{label}]\n{entry.description}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(_URL_ROLE, entry.url)
            self.list_widget.addItem(item)
        if not entries:
            layout.addWidget(QLabel("おすすめプラグインはまだ登録されていません。"))
        layout.addWidget(self.list_widget)
        select_updates_button = QPushButton("更新ありを全て選択")
        select_updates_button.clicked.connect(self._check_updates)
        layout.addWidget(select_updates_button)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("チェックしたものを導入・更新")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("閉じる")
        buttons.accepted.connect(self._accept_checked)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _check_updates(self) -> None:
        for row, entry in enumerate(self._entries):
            if _has_update(entry, self._installed_versions):
                self.list_widget.item(row).setCheckState(Qt.CheckState.Checked)

    def _accept_checked(self) -> None:
        self.selected_urls = [
            self.list_widget.item(row).data(_URL_ROLE)
            for row in range(self.list_widget.count())
            if self.list_widget.item(row).checkState() == Qt.CheckState.Checked
        ]
        if not self.selected_urls:
            return
        self.accept()
