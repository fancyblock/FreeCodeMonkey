"""FreeCodeMonkey 应用入口。

运行：python main.py
"""

from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from app.config import Settings
from app.constants import APP_NAME, APP_ORG, APP_VERSION
from app.ui import MainWindow


def main() -> int:
    # 设置组织与应用名，确保 QSettings 存储位置稳定
    QApplication.setOrganizationName(APP_ORG)
    QApplication.setApplicationName(APP_NAME)
    QApplication.setApplicationVersion(APP_VERSION)

    app = QApplication(sys.argv)
    settings = Settings()
    window = MainWindow(settings)
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
