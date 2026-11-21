"""Entry point: ``python -m ytdownloader``."""

import sys
from importlib.resources import files

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from ytdownloader.tools import APP_NAME
from ytdownloader.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setOrganizationName(APP_NAME)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName("YouTube Downloader")
    app.setWindowIcon(QIcon(str(files("ytdownloader") / "resources" / "logo.png")))

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
