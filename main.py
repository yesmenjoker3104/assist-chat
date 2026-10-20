import sys

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from config import RESOURCE_DIR
from gui.main_window import MainWindow

_ICON_PATH = RESOURCE_DIR / "gui" / "icon" / "icon.ico"
_APP_USER_MODEL_ID = "AssistChat.App.Icon2"


def _set_app_user_model_id() -> None:
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(_APP_USER_MODEL_ID)
    except Exception:
        pass


def main():
    _set_app_user_model_id()
    app = QApplication(sys.argv)
    app.setApplicationName("Assist Chat")
    app.setApplicationDisplayName("Assist Chat")
    icon = QIcon(str(_ICON_PATH)) if _ICON_PATH.exists() else QIcon()
    app.setWindowIcon(icon)
    window = MainWindow()
    window.setWindowIcon(icon)
    window.show()
    window.prepare_ollama()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
