"""ProLOG entry point."""

from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from database import Database
from ui.style import APP_STYLESHEET
from ui.main_window import MainWindow
from utils import ensure_app_directories, setup_logging


def install_exception_hook() -> None:
    def handle_exception(exc_type, exc_value, exc_traceback) -> None:
        logging.getLogger(__name__).critical(
            "Unhandled exception",
            exc_info=(exc_type, exc_value, exc_traceback),
        )
        app = QApplication.instance()
        if app is None:
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        QMessageBox.critical(
            None,
            "Критическая ошибка",
            "В программе произошла непредвиденная ошибка.\n"
            "Подробности записаны в файл data\\prolog.log.",
        )

    sys.excepthook = handle_exception


def main() -> int:
    ensure_app_directories()
    setup_logging()
    install_exception_hook()
    database = Database()
    database.initialize()
    app = QApplication(sys.argv)
    app.setStyleSheet(APP_STYLESHEET)
    window = MainWindow(database)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
