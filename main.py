"""ProLOG entry point."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from auth import AuthService, AuthSession
from config import ConfigManager
from database import Database
from ui.style import APP_STYLESHEET
from ui.auth_dialogs import LoginDialog, RegistrationDialog
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
    app = QApplication(sys.argv)
    app.setStyleSheet(APP_STYLESHEET)
    settings = ConfigManager().load()
    database = Database(
        Path(settings.prolog_database_path) if settings.prolog_database_path else None,
        employees_path=(
            Path(settings.employees_database_path)
            if settings.employees_database_path
            else None
        ),
        objects_path=(
            Path(settings.objects_database_path)
            if settings.objects_database_path
            else None
        ),
        products_path=(
            Path(settings.products_database_path)
            if settings.products_database_path
            else None
        ),
        aliases_path=(
            Path(settings.aliases_database_path)
            if settings.aliases_database_path
            else None
        ),
    )
    try:
        configured_paths = {
            "ядро ProLOG": settings.prolog_database_path,
            "сотрудники": settings.employees_database_path,
            "объекты": settings.objects_database_path,
            "изделия": settings.products_database_path,
            "алиасы": settings.aliases_database_path,
        }
        for label, configured_path in configured_paths.items():
            if configured_path and not Path(configured_path).is_file():
                raise FileNotFoundError(f"не найден файл компонента «{label}»: {configured_path}")
        database.initialize()
    except Exception as exc:
        logging.getLogger(__name__).exception("Failed to initialize ProLOG database")
        QMessageBox.critical(
            None,
            "База данных ProLOG",
            "Не удалось открыть выбранную базу данных:\n"
            f"{database.path}\n\n{exc}\n\n"
            "Проверьте доступ к серверу или исправьте путь в config.json.",
        )
        return 1
    auth_service = AuthService()
    session = _authorize(auth_service)
    if session is None:
        return 0
    _sync_config_from_auth(auth_service)
    window = MainWindow(database, auth_service, session)
    window.show()
    return app.exec()


def _authorize(auth_service: AuthService) -> AuthSession | None:
    try:
        if auth_service.has_profile():
            dialog = LoginDialog(auth_service)
            if not dialog.exec():
                return None
            return dialog.session()
        dialog = RegistrationDialog()
        while True:
            if not dialog.exec():
                return None
            try:
                return auth_service.register_initial(dialog.registration_data())
            except ValueError as exc:
                QMessageBox.warning(dialog, "Регистрация", str(exc))
    except ValueError as exc:
        QMessageBox.critical(None, "Авторизация", str(exc))
        return None


def _sync_config_from_auth(auth_service: AuthService) -> None:
    config_manager = ConfigManager()
    settings = config_manager.load()
    profile = auth_service.load_profile()
    settings.organization_name = profile.organization_name
    settings.department_name = profile.department_name
    settings.leader_full_name = profile.leader_full_name
    config_manager.save(settings)


if __name__ == "__main__":
    raise SystemExit(main())
