"""Main application window."""

from __future__ import annotations

import logging
import webbrowser

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow, QMessageBox, QSplitter, QTabWidget

import excel_export
from app_modules import (
    MODULE_DIRECTORIES,
    MODULE_EMPLOYEE_ADMIN,
    MODULE_LEGACY_IMPORT,
    MODULE_PAYROLL,
    MODULE_REPORT_EXPORT,
    MODULE_UPDATES,
    MODULE_USERS,
    MODULE_WORKBOT_INBOX,
    role_can_access,
)
from auth import AuthService, AuthSession, role_label
from config import ConfigManager
from constants import APP_ICON_FILE, APP_NAME
from database import Database, DirectoryRepository, EmployeeRepository, WorkLogRepository
from legacy_import.service import LegacyExcelImportService
from integrations.workbot.repository import WorkBotRepository
from integrations.workbot.service import WorkBotIntegrationService
from services import AnalyticsService, DirectoryService, EmployeeService, WorkLogService
from update_checker import UpdateChecker
from ui.auth_dialogs import LoginDialog, UserManagementDialog
from ui.dialogs import AboutDialog, DirectoryDialog, EmployeeDialog, HelpDialog, UpdateStatusDialog
from ui.analytics_widget import AnalyticsWidget
from ui.employee_widget import EmployeeWidget
from ui.legacy_import_dialog import LegacyImportDialog
from ui.report_viewer_widget import ReportViewerWidget
from ui.setup_wizard import InitialSetupDialog
from ui.style import APP_STYLESHEET
from ui.worklog_widget import WorkLogWidget
from ui.workbot_inbox import WorkBotInboxWidget

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self, database: Database, auth_service: AuthService, auth_session: AuthSession) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        if APP_ICON_FILE.exists():
            self.setWindowIcon(QIcon(str(APP_ICON_FILE)))
        self.database = database
        self.auth_service = auth_service
        self.auth_session = auth_session
        self.directories = DirectoryService(DirectoryRepository(database))
        self.employees = EmployeeService(EmployeeRepository(database), self.directories)
        self.worklogs = WorkLogService(WorkLogRepository(database), self.directories)
        self.analytics = AnalyticsService(self.worklogs, self.employees, self.directories)
        self.legacy_importer = LegacyExcelImportService(database, self.employees, self.directories, self.worklogs)
        self.workbot = WorkBotIntegrationService(
            WorkBotRepository(database),
            self.employees,
            self.directories,
            self.worklogs,
        )
        self.config_manager = ConfigManager()
        self.config = self.config_manager.load()
        self._startup_checked = False
        self.resize(self.config.window_width, self.config.window_height)
        self.employee_widget = EmployeeWidget()
        self.worklog_widget = WorkLogWidget()
        self.report_viewer = ReportViewerWidget()
        self.analytics_widget = AnalyticsWidget()
        self.workbot_inbox = WorkBotInboxWidget(self.workbot)
        self.workbot_inbox.set_source_path(self.config.workbot_database_path)
        self.workbot_inbox.set_reviewer(self.auth_session.username)
        self._build_menu()
        self._build_layout()
        self._connect()
        self._apply_style()
        self._sync_config_from_auth_profile()
        self._apply_access_policy()
        self.statusBar().showMessage("Готово")
        self.refresh_directories()
        self.refresh_employees()
        self.refresh_report_viewer()
        self.refresh_analytics()
        self.workbot_inbox.refresh()
        self.employee_widget.apply_column_widths(self.config.employee_column_widths)
        self.worklog_widget.apply_column_widths(self.config.worklog_column_widths)
        if self.config.check_updates_on_startup:
            self.check_updates(silent=True)

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("Файл")
        self.organization_action = QAction("Авторизация", self)
        self.import_action = QAction("Импорт сотрудников", self)
        self.import_legacy_reports_action = QAction("Импорт старых отчетов Excel", self)
        self.export_employees_action = QAction("Экспорт сотрудников", self)
        self.exit_action = QAction("Выход", self)
        file_menu.addAction(self.organization_action)
        file_menu.addSeparator()
        file_menu.addAction(self.import_action)
        file_menu.addAction(self.import_legacy_reports_action)
        file_menu.addAction(self.export_employees_action)
        file_menu.addSeparator()
        file_menu.addAction(self.exit_action)

        settings_menu = self.menuBar().addMenu("Настройки")
        self.directories_action = QAction("Справочники", self)
        self.update_action = QAction("Проверка обновлений", self)
        settings_menu.addAction(self.directories_action)
        settings_menu.addAction(self.update_action)

        export_menu = self.menuBar().addMenu("Экспорт")
        self.export_report_action = QAction("Экспорт отчета", self)
        self.export_assignment_action = QAction("Экспорт сменного задания", self)
        export_menu.addAction(self.export_report_action)
        export_menu.addAction(self.export_assignment_action)

        help_menu = self.menuBar().addMenu("Справка")
        self.help_action = QAction("Посмотреть справку", self)
        self.about_action = QAction("О программе", self)
        help_menu.addAction(self.help_action)
        help_menu.addAction(self.about_action)

    def _build_layout(self) -> None:
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.employee_widget)
        self.splitter.addWidget(self.worklog_widget)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 3)
        self.splitter.setSizes(self._initial_splitter_sizes())
        self.tabs = QTabWidget()
        self.tabs.addTab(self.splitter, "Заполнение отчетов")
        self.tabs.addTab(self.report_viewer, "Просмотр отчетов")
        self.tabs.addTab(self.analytics_widget, "Аналитика")
        self.tabs.addTab(self.workbot_inbox, "Входящие отчеты")
        self.setCentralWidget(self.tabs)

    def _initial_splitter_sizes(self) -> list[int]:
        sizes = self.config.splitter_sizes or [430, 980]
        if len(sizes) != 2:
            return [430, 980]
        left, right = sizes
        total = max(left + right, 1)
        max_left = min(470, int(total * 0.38))
        if left > max_left:
            right += left - max_left
            left = max_left
        return [left, right]

    def _connect(self) -> None:
        self.organization_action.triggered.connect(self.switch_user)
        self.import_action.triggered.connect(self.import_employees)
        self.import_legacy_reports_action.triggered.connect(self.import_legacy_reports)
        self.export_employees_action.triggered.connect(self.export_employees)
        self.exit_action.triggered.connect(self.close)
        self.directories_action.triggered.connect(self.edit_directories)
        self.update_action.triggered.connect(lambda: self.check_updates(silent=False))
        self.export_report_action.triggered.connect(self.export_report)
        self.export_assignment_action.triggered.connect(self.export_assignment)
        self.help_action.triggered.connect(self.show_help)
        self.about_action.triggered.connect(self.show_about)
        self.employee_widget.search_changed.connect(self.refresh_employees)
        self.employee_widget.selected.connect(self._employee_selected)
        self.employee_widget.add_requested.connect(self.add_employee)
        self.employee_widget.edit_requested.connect(self.edit_employee)
        self.employee_widget.delete_requested.connect(self.delete_employee)
        self.employee_widget.import_requested.connect(self.import_employees)
        self.employee_widget.export_requested.connect(self.export_employees)
        self.worklog_widget.save_requested.connect(self.save_worklog)
        self.worklog_widget.validation_failed.connect(self._warn)
        self.worklog_widget.objects_directory_requested.connect(self.open_objects_directory)
        self.report_viewer.filters_changed.connect(self.refresh_report_viewer)
        self.report_viewer.entry_open_requested.connect(self.open_worklog_entry)
        self.analytics_widget.filters_changed.connect(self.refresh_analytics)
        self.workbot_inbox.source_path_changed.connect(self._save_workbot_source_path)
        self.workbot_inbox.imported.connect(self.refresh_worklogs)
        self.workbot_inbox.bindings_changed.connect(self.refresh_employees)
        self.workbot_inbox.status_message.connect(lambda message: self.statusBar().showMessage(message, 10000))

    def refresh_directories(self) -> None:
        active_objects = self.directories.list("objects")
        active_positions = self.directories.list("positions")
        active_groups = self.directories.list("employee_groups")
        self.worklog_widget.set_directories(
            self.directories.list("locations"),
            active_objects,
            self.directories.list("work_types"),
            self.directories.list_products(active_only=False),
        )
        self.employee_widget.set_group_filter_options(active_groups)
        self.employee_widget.set_position_filter_options(active_positions)
        self.report_viewer.set_objects(self.directories.list_all("objects"))
        self.analytics_widget.set_objects(self.directories.list_all("objects"))
        self.analytics_widget.set_products(self.directories.list_products(active_only=False))
        self._refresh_workbot_reference_data()

    def refresh_employees(self, search: str = "") -> None:
        self.employee_widget.set_employees(
            self.employees.list(
                search,
                self.employee_widget.current_position_filter(),
                self.employee_widget.current_group_filter(),
            )
        )
        self.report_viewer.set_employees(self.employees.list())
        self.analytics_widget.set_employees(self.employees.list())
        self._refresh_workbot_reference_data()

    def refresh_worklogs(self) -> None:
        self.refresh_employee_worklog_table()
        self.refresh_report_viewer()
        self.refresh_analytics()

    def refresh_employee_worklog_table(self) -> None:
        employee = self.employee_widget.current_employee()
        if employee is None or employee.id is None:
            self.worklog_widget.set_employee_entries([])
            return
        self.worklog_widget.set_employee_entries(self.worklogs.search_entries(employee_id=employee.id))

    def refresh_report_viewer(self) -> None:
        entries = self.worklogs.search_entries(
            employee_id=self.report_viewer.employee_id(),
            date_from=self.report_viewer.date_from_value(),
            date_to=self.report_viewer.date_to_value(),
            object_id=self.report_viewer.object_id(),
        )
        self.report_viewer.set_entries(entries)

    def refresh_analytics(self) -> None:
        if not role_can_access(self.auth_session.role, MODULE_PAYROLL):
            return
        result = self.analytics.build(
            employee_id=self.analytics_widget.employee_id(),
            date_from=self.analytics_widget.date_from_value(),
            date_to=self.analytics_widget.date_to_value(),
            object_id=self.analytics_widget.object_id(),
            product_id=self.analytics_widget.product_id(),
            monthly_hours_norm=self.config.monthly_hours_norm,
        )
        self.analytics_widget.set_result(result)

    def _sync_config_from_auth_profile(self) -> None:
        profile = self.auth_service.load_profile()
        self.config.organization_name = profile.organization_name
        self.config.department_name = profile.department_name
        self.config.leader_full_name = profile.leader_full_name
        self.config_manager.save(self.config)

    def _apply_access_policy(self) -> None:
        is_employee_admin = role_can_access(self.auth_session.role, MODULE_EMPLOYEE_ADMIN)
        can_edit_directories = role_can_access(self.auth_session.role, MODULE_DIRECTORIES)
        can_import_legacy = role_can_access(self.auth_session.role, MODULE_LEGACY_IMPORT)
        can_export_reports = role_can_access(self.auth_session.role, MODULE_REPORT_EXPORT)
        can_view_payroll = role_can_access(self.auth_session.role, MODULE_PAYROLL)
        can_check_updates = role_can_access(self.auth_session.role, MODULE_UPDATES)
        can_view_workbot = role_can_access(self.auth_session.role, MODULE_WORKBOT_INBOX)
        self.import_action.setEnabled(is_employee_admin)
        self.export_employees_action.setEnabled(is_employee_admin)
        self.directories_action.setEnabled(can_edit_directories)
        self.update_action.setEnabled(can_check_updates)
        self.import_legacy_reports_action.setEnabled(can_import_legacy)
        self.export_report_action.setEnabled(can_export_reports)
        self.export_assignment_action.setEnabled(can_export_reports)
        self.employee_widget.set_management_enabled(is_employee_admin)
        analytics_index = self.tabs.indexOf(self.analytics_widget)
        if analytics_index >= 0:
            self.tabs.setTabVisible(analytics_index, can_view_payroll)
            if not can_view_payroll and self.tabs.currentWidget() is self.analytics_widget:
                self.tabs.setCurrentIndex(0)
        workbot_index = self.tabs.indexOf(self.workbot_inbox)
        if workbot_index >= 0:
            self.tabs.setTabVisible(workbot_index, can_view_workbot)
            if not can_view_workbot and self.tabs.currentWidget() is self.workbot_inbox:
                self.tabs.setCurrentIndex(0)
        self.setWindowTitle(f"{APP_NAME} - {self.auth_session.username}")

    def _refresh_workbot_reference_data(self) -> None:
        self.workbot_inbox.set_reference_data(
            self.employees.list(),
            self.directories.list_all("locations"),
            self.directories.list_all("objects"),
            self.directories.list_all("work_types"),
            self.directories.list_products(active_only=False),
        )

    def _save_workbot_source_path(self, value: str) -> None:
        self.config.workbot_database_path = value
        self.config_manager.save(self.config)
        self.statusBar().showMessage("Путь к базе WorkBot сохранен", 5000)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._startup_checked:
            self._startup_checked = True
            self._ensure_initial_setup()

    def switch_user(self) -> None:
        dialog = LoginDialog(
            self.auth_service,
            self,
            close_app_on_reject=False,
            current_session=self.auth_session,
            allow_user_management=role_can_access(self.auth_session.role, MODULE_USERS),
        )
        if dialog.exec():
            self.auth_session = dialog.session()
            self.workbot_inbox.set_reviewer(self.auth_session.username)
            self._sync_config_from_auth_profile()
            self._apply_access_policy()
            self.refresh_directories()
            self.refresh_employees()
            self.refresh_report_viewer()
            self.refresh_analytics()
            self.workbot_inbox.refresh()
            self.statusBar().showMessage(
                f"Выполнен вход: {self.auth_session.username} ({role_label(self.auth_session.role)})",
                7000,
            )

    def manage_users(self) -> None:
        if not self._require_access(MODULE_USERS):
            return
        dialog = UserManagementDialog(self.auth_service, self.auth_session.username, self)
        dialog.exec()

    def _ensure_initial_setup(self) -> None:
        if self.config.initial_setup_done:
            return
        if not self.auth_session.is_admin:
            self._warn("Первичная настройка доступна только руководителю")
            self.close()
            QApplication.instance().quit()
            return
        self.directories.apply_department_defaults(self.config.department_name)
        self.refresh_directories()
        dialog = InitialSetupDialog(self.employees, self.directories, self.config.department_name)
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        icon = self.windowIcon()
        if not icon.isNull():
            dialog.setWindowIcon(icon)
        if dialog.exec():
            self.config.initial_setup_done = True
            self._run(lambda: self.config_manager.save(self.config), "Первичная настройка завершена")
            self.refresh_directories()
            self.refresh_employees()
        else:
            self.close()
            QApplication.instance().quit()

    def add_employee(self) -> None:
        if not self._require_access(MODULE_EMPLOYEE_ADMIN):
            return
        dialog = EmployeeDialog(self.directories.list("positions"), parent=self)
        if dialog.exec():
            self._run(lambda: self.employees.save(dialog.employee()), "Сотрудник сохранен")
            self.refresh_directories()
            self.refresh_employees()

    def edit_employee(self, employee) -> None:
        if not self._require_access(MODULE_EMPLOYEE_ADMIN):
            return
        dialog = EmployeeDialog(self.directories.list("positions"), employee, self)
        if dialog.exec():
            self._run(lambda: self.employees.save(dialog.employee(employee.id)), "Сотрудник обновлен")
            self.refresh_directories()
            self.refresh_employees()

    def delete_employee(self, employee) -> None:
        if not self._require_access(MODULE_EMPLOYEE_ADMIN):
            return
        if self._ask("Удалить", f"Удалить сотрудника '{employee.full_name}'?"):
            self._run(lambda: self.employees.delete(employee.id), "Сотрудник удален")
            self.refresh_employees()

    def import_employees(self) -> None:
        if not self._require_access(MODULE_EMPLOYEE_ADMIN):
            return
        file_name, _ = QFileDialog.getOpenFileName(self, "Импорт сотрудников", "", "Excel (*.xlsx)")
        if not file_name:
            return
        imported = self._run(lambda: excel_export.import_employees(file_name, self.employees), "Сотрудники импортированы")
        if imported is not None:
            self.statusBar().showMessage(f"Импортировано сотрудников: {imported}", 7000)
            self.refresh_employees()

    def import_legacy_reports(self) -> None:
        if not self._require_access(MODULE_LEGACY_IMPORT):
            return
        if not self.config.initial_setup_done:
            self._warn("Импорт старых отчетов доступен после завершения мастера настройки ProLOG")
            return
        dialog = LegacyImportDialog(self.legacy_importer, self)
        if dialog.exec():
            self.refresh_directories()
            self.refresh_employees()
            self.refresh_worklogs()
            self.statusBar().showMessage("Старые отчеты Excel импортированы", 7000)

    def export_employees(self) -> None:
        if not self._require_access(MODULE_EMPLOYEE_ADMIN):
            return
        path = excel_export.default_employee_export_path()
        result = self._run(lambda: excel_export.export_employees(path, self.employees.list()), "Сотрудники экспортированы")
        if result:
            self.statusBar().showMessage(f"Файл сохранен: {result}", 10000)

    def edit_directories(self) -> None:
        if not self._require_access(MODULE_DIRECTORIES):
            return
        dialog = DirectoryDialog(
            self.directories,
            self,
            can_edit_pay_rates=role_can_access(self.auth_session.role, MODULE_PAYROLL),
        )
        dialog.exec()
        self.refresh_directories()
        self.refresh_analytics()
        self.statusBar().showMessage("Справочники обновлены", 5000)

    def open_objects_directory(self) -> None:
        if not self._require_access(MODULE_DIRECTORIES):
            return
        before_names = {item.name.casefold() for item in self.directories.list_all("objects")}
        dialog = DirectoryDialog(
            self.directories,
            self,
            initial_key="objects",
            can_edit_pay_rates=role_can_access(self.auth_session.role, MODULE_PAYROLL),
        )
        dialog.exec()
        objects = self.directories.list_all("objects")
        self.refresh_directories()
        new_objects = [item for item in objects if item.name.casefold() not in before_names]
        if new_objects:
            self.worklog_widget.select_object(new_objects[-1].id)
        self.refresh_analytics()
        self.statusBar().showMessage("Справочник объектов обновлен", 5000)

    def save_worklog(self, entry) -> None:
        saved = self._run(lambda: self.worklogs.save(entry), "Запись сохранена")
        if saved:
            self.worklog_widget.clear_form()
            self.refresh_worklogs()

    def open_worklog_entry(self, entry_id: int) -> None:
        entry = self.worklogs.get(entry_id)
        if entry is None:
            self._warn("Запись не найдена")
            return
        employee = self.employees.get(entry.employee_id)
        if employee is None:
            self._warn("Сотрудник записи не найден")
            return
        if self.employee_widget.search.text():
            self.employee_widget.search.clear()
        self.refresh_employees()
        self.employee_widget.select_employee(employee.id)
        self.worklog_widget.set_employee(employee)
        self.worklog_widget.load_entry(entry)
        self.tabs.setCurrentIndex(0)
        self.statusBar().showMessage("Запись открыта для редактирования", 5000)

    def export_report(self) -> None:
        if not self._require_access(MODULE_REPORT_EXPORT):
            return
        work_date = self.worklog_widget.selected_date()
        entries = self.worklogs.report_entries(work_date=work_date)
        result = self._run(lambda: excel_export.export_work_report(entries, work_date, self.config), "Отчет экспортирован")
        if result:
            self.statusBar().showMessage(f"Отчет сохранен: {result}", 10000)

    def export_assignment(self) -> None:
        if not self._require_access(MODULE_REPORT_EXPORT):
            return
        work_date = self.worklog_widget.selected_date()
        entries = self.worklogs.report_entries(work_date=work_date)
        result = self._run(lambda: excel_export.export_shift_assignment(entries, work_date, self.config), "Сменное задание экспортировано")
        if result:
            self.statusBar().showMessage(f"Сменное задание сохранено: {result}", 10000)

    def check_updates(self, silent: bool = False) -> None:
        if not role_can_access(self.auth_session.role, MODULE_UPDATES):
            if not silent:
                self._warn("Недостаточно прав для проверки обновлений")
            return
        if not silent:
            dialog = UpdateStatusDialog(self)
            dialog.exec()
            self.refresh_directories()
            self.refresh_employees()
            return
        info = UpdateChecker().check()
        if info is None:
            if not silent:
                self._warn("Не удалось проверить обновления")
            return
        if info.is_newer:
            if self._ask("Обновление", f"Доступна версия {info.latest_version}. Открыть страницу релиза?"):
                webbrowser.open(info.release_url)
        elif not silent:
            self.statusBar().showMessage("Установлена актуальная версия", 5000)
            self._info("Установлена актуальная версия", "Обновления")

    def show_help(self) -> None:
        HelpDialog(self).exec()

    def show_about(self) -> None:
        AboutDialog(self).exec()

    def _employee_selected(self, employee) -> None:
        self.worklog_widget.set_employee(employee)
        self.report_viewer.set_current_employee(employee)
        self.analytics_widget.set_current_employee(employee)
        self.refresh_worklogs()
        self.statusBar().showMessage(f"Выбран сотрудник: {employee.full_name}", 5000)

    def closeEvent(self, event) -> None:
        self.config.window_width = self.width()
        self.config.window_height = self.height()
        self.config.splitter_sizes = self.splitter.sizes()
        self.config.employee_column_widths = self.employee_widget.column_widths()
        self.config.worklog_column_widths = self.worklog_widget.column_widths()
        self.config_manager.save(self.config)
        super().closeEvent(event)

    def _run(self, action, success_message: str = "Готово"):
        try:
            result = action()
            self.statusBar().showMessage(success_message, 5000)
            return result
        except Exception as exc:
            logger.exception("Operation failed")
            self._warn(str(exc))
            return None

    def _require_access(self, module: str) -> bool:
        if role_can_access(self.auth_session.role, module):
            return True
        self._warn("Недостаточно прав для выполнения операции")
        return False

    def _warn(self, message: str) -> None:
        self.statusBar().showMessage(f"Ошибка: {message}", 10000)
        self._message("Ошибка", message, QMessageBox.Icon.Warning)

    def _info(self, message: str, title: str = "Информация") -> None:
        self._message(title, message, QMessageBox.Icon.Information)

    def _ask(self, title: str, message: str) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(message)
        yes_button = box.addButton("Да", QMessageBox.ButtonRole.YesRole)
        box.addButton("Нет", QMessageBox.ButtonRole.NoRole)
        box.setIcon(QMessageBox.Icon.Question)
        box.exec()
        return box.clickedButton() == yes_button

    def _message(self, title: str, message: str, icon: QMessageBox.Icon) -> None:
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(message)
        box.setIcon(icon)
        box.addButton("ОК", QMessageBox.ButtonRole.AcceptRole)
        box.exec()

    def _apply_style(self) -> None:
        self.setStyleSheet(APP_STYLESHEET)
