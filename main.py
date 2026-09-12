import sys
from pathlib import Path

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from gui.main_window import MainWindow

_ICON_PATH = Path(__file__).parent / "gui" / "icon" / "icon.ico"
_APP_USER_MODEL_ID = "USER.AssistChat"


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
    window.status.setText(window.start_ollama())
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
