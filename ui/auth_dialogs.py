"""Authentication dialogs."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QHeaderView,
)

from auth import ROLE_ADMIN, ROLE_USER, AuthService, AuthSession, RegistrationData, UserAccount, role_label


class RegistrationDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Регистрация ProLOG")
        self.setMinimumWidth(620)
        self.organization = QLineEdit()
        self.department = QLineEdit()
        self.leader = QLineEdit()
        self.user = QLineEdit()
        self.user_is_leader = QCheckBox("Пользователь является руководителем")
        self.leader_password = _password_edit()
        self.leader_password_confirm = _password_edit()
        self.user_password = _password_edit()
        self.user_password_confirm = _password_edit()
        self._build_layout()
        self._connect()
        self._sync_user_fields()

    def registration_data(self) -> RegistrationData:
        return RegistrationData(
            organization_name=self.organization.text(),
            department_name=self.department.text(),
            leader_full_name=self.leader.text(),
            leader_password=self.leader_password.text(),
            user_name=self.user.text(),
            user_password=self.user_password.text(),
            user_is_leader=self.user_is_leader.isChecked(),
        )

    def accept(self) -> None:
        if self.leader_password.text() != self.leader_password_confirm.text():
            self._warn("Пароль руководителя и подтверждение не совпадают")
            return
        if not self.user_is_leader.isChecked() and self.user_password.text() != self.user_password_confirm.text():
            self._warn("Пароль пользователя и подтверждение не совпадают")
            return
        super().accept()

    def reject(self) -> None:
        super().reject()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _build_layout(self) -> None:
        title = QLabel("Первичная регистрация")
        title.setObjectName("WizardTitle")
        subtitle = QLabel(
            "Создайте локальные учетные записи. Руководитель получает полный доступ, "
            "обычный пользователь работает только с отчетами."
        )
        subtitle.setWordWrap(True)
        subtitle.setObjectName("WizardSubtitle")

        organization_box = QGroupBox("Организация")
        organization_form = QFormLayout(organization_box)
        organization_form.addRow("Наименование организации", self.organization)
        organization_form.addRow("Отдел", self.department)
        organization_form.addRow("Руководитель отдела", self.leader)

        user_box = QGroupBox("Учетные записи")
        user_form = QFormLayout(user_box)
        user_form.addRow("Пользователь", self.user)
        user_form.addRow("", self.user_is_leader)
        user_form.addRow("Пароль пользователя", self.user_password)
        user_form.addRow("Повтор пароля пользователя", self.user_password_confirm)
        user_form.addRow("Пароль руководителя", self.leader_password)
        user_form.addRow("Повтор пароля руководителя", self.leader_password_confirm)

        save_button = QPushButton("Зарегистрировать")
        cancel_button = QPushButton("Выход")
        save_button.setMinimumWidth(170)
        cancel_button.setMinimumWidth(120)
        save_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(save_button)
        buttons.addWidget(cancel_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(organization_box)
        layout.addWidget(user_box)
        layout.addLayout(buttons)

    def _connect(self) -> None:
        self.user_is_leader.toggled.connect(self._sync_user_fields)

    def _sync_user_fields(self) -> None:
        enabled = not self.user_is_leader.isChecked()
        for widget in (self.user, self.user_password, self.user_password_confirm):
            widget.setEnabled(enabled)
        if not enabled:
            self.user.clear()
            self.user_password.clear()
            self.user_password_confirm.clear()

    def _warn(self, message: str) -> None:
        QMessageBox.warning(self, "Регистрация", message)


class LoginDialog(QDialog):
    def __init__(self, auth_service: AuthService, parent=None, close_app_on_reject: bool = True) -> None:
        super().__init__(parent)
        self.auth_service = auth_service
        self.close_app_on_reject = close_app_on_reject
        self._session: AuthSession | None = None
        self.setWindowTitle("Авторизация ProLOG")
        self.setMinimumWidth(440)
        self.user = QComboBox()
        self.password = _password_edit()
        self._build_layout()
        self._fill_users()

    def session(self) -> AuthSession:
        if self._session is None:
            raise RuntimeError("Пользователь не авторизован")
        return self._session

    def reject(self) -> None:
        super().reject()
        if self.close_app_on_reject:
            app = QApplication.instance()
            if app is not None:
                app.quit()

    def accept(self) -> None:
        username = self.user.currentData() or self.user.currentText()
        try:
            self._session = self.auth_service.authenticate(str(username), self.password.text())
        except ValueError as exc:
            QMessageBox.warning(self, "Авторизация", str(exc))
            self.password.selectAll()
            self.password.setFocus()
            return
        super().accept()

    def _build_layout(self) -> None:
        title = QLabel("Вход в ProLOG")
        title.setObjectName("DialogTitle")
        form = QFormLayout()
        form.addRow("Пользователь", self.user)
        form.addRow("Пароль", self.password)
        login_button = QPushButton("Войти")
        exit_button = QPushButton("Выход" if self.close_app_on_reject else "Отмена")
        login_button.setMinimumWidth(120)
        exit_button.setMinimumWidth(110)
        login_button.clicked.connect(self.accept)
        exit_button.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(login_button)
        buttons.addWidget(exit_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addLayout(form)
        layout.addLayout(buttons)

    def _fill_users(self) -> None:
        self.user.clear()
        for account in self.auth_service.list_users():
            self.user.addItem(f"{account.username} ({role_label(account.role)})", account.username)
        self.user.setFocusPolicy(Qt.FocusPolicy.StrongFocus)


class UserDialog(QDialog):
    def __init__(self, account: UserAccount | None = None, parent=None) -> None:
        super().__init__(parent)
        self.account = account
        self.setWindowTitle("Пользователь")
        self.setMinimumWidth(480)
        self.username = QLineEdit(account.username if account else "")
        self.role = QComboBox()
        self.role.addItem(role_label(ROLE_USER), ROLE_USER)
        self.role.addItem(role_label(ROLE_ADMIN), ROLE_ADMIN)
        if account:
            index = self.role.findData(account.role)
            self.role.setCurrentIndex(index if index >= 0 else 0)
        self.password = _password_edit()
        self.password_confirm = _password_edit()
        if account:
            self.password.setPlaceholderText("Оставьте пустым, чтобы не менять")
            self.password_confirm.setPlaceholderText("Оставьте пустым, чтобы не менять")
        self._build_layout()

    def values(self) -> tuple[str, str, str]:
        return self.username.text(), str(self.role.currentData() or ROLE_USER), self.password.text()

    def accept(self) -> None:
        if not self.username.text().strip():
            QMessageBox.warning(self, "Пользователь", "Укажите имя пользователя")
            return
        if self.password.text() != self.password_confirm.text():
            QMessageBox.warning(self, "Пользователь", "Пароль и подтверждение не совпадают")
            return
        if self.account is None and not self.password.text():
            QMessageBox.warning(self, "Пользователь", "Укажите пароль пользователя")
            return
        super().accept()

    def _build_layout(self) -> None:
        form = QFormLayout()
        form.addRow("Пользователь", self.username)
        form.addRow("Роль", self.role)
        form.addRow("Пароль", self.password)
        form.addRow("Повтор пароля", self.password_confirm)
        save_button = QPushButton("Сохранить")
        cancel_button = QPushButton("Отмена")
        save_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(save_button)
        buttons.addWidget(cancel_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        layout.addLayout(form)
        layout.addLayout(buttons)


class UserManagementDialog(QDialog):
    def __init__(self, auth_service: AuthService, current_username: str, parent=None) -> None:
        super().__init__(parent)
        self.auth_service = auth_service
        self.current_username = current_username
        self.setWindowTitle("Пользователи")
        self.setMinimumWidth(560)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Пользователь", "Роль"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(1, 160)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.add_button = QPushButton("Добавить")
        self.edit_button = QPushButton("Редактировать")
        self.delete_button = QPushButton("Удалить")
        self.close_button = QPushButton("Закрыть")
        self._build_layout()
        self._connect()
        self._refresh()

    def _build_layout(self) -> None:
        title = QLabel("Пользователи ProLOG")
        title.setObjectName("DialogTitle")
        buttons = QHBoxLayout()
        for button in (self.add_button, self.edit_button, self.delete_button):
            buttons.addWidget(button)
        buttons.addStretch()
        buttons.addWidget(self.close_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)
        layout.addWidget(title)
        layout.addWidget(self.table)
        layout.addLayout(buttons)

    def _connect(self) -> None:
        self.add_button.clicked.connect(self._add_user)
        self.edit_button.clicked.connect(self._edit_user)
        self.delete_button.clicked.connect(self._delete_user)
        self.close_button.clicked.connect(self.accept)
        self.table.doubleClicked.connect(self._edit_user)

    def _refresh(self) -> None:
        users = self.auth_service.list_users()
        self.table.setRowCount(len(users))
        for row, account in enumerate(users):
            username = QTableWidgetItem(account.username)
            username.setData(Qt.ItemDataRole.UserRole, account.username)
            role = QTableWidgetItem(role_label(account.role))
            self.table.setItem(row, 0, username)
            self.table.setItem(row, 1, role)

    def _add_user(self) -> None:
        dialog = UserDialog(parent=self)
        if dialog.exec():
            username, role, password = dialog.values()
            self._run(lambda: self.auth_service.create_user(username, password, role))

    def _edit_user(self) -> None:
        account = self._selected_account()
        if account is None:
            return
        if account.username.casefold() == self.current_username.casefold():
            QMessageBox.information(self, "Пользователи", "Текущего пользователя нельзя редактировать в активной сессии")
            return
        dialog = UserDialog(account, self)
        if dialog.exec():
            username, role, password = dialog.values()
            self._run(lambda: self.auth_service.update_user(account.username, username, role, password))

    def _delete_user(self) -> None:
        account = self._selected_account()
        if account is None:
            return
        if account.username.casefold() == self.current_username.casefold():
            QMessageBox.information(self, "Пользователи", "Текущего пользователя нельзя удалить")
            return
        if QMessageBox.question(self, "Пользователи", f"Удалить пользователя '{account.username}'?") != QMessageBox.StandardButton.Yes:
            return
        self._run(lambda: self.auth_service.delete_user(account.username))

    def _selected_account(self) -> UserAccount | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        username = item.data(Qt.ItemDataRole.UserRole) if item else ""
        for account in self.auth_service.list_users():
            if account.username == username:
                return account
        return None

    def _run(self, action) -> None:
        try:
            action()
        except ValueError as exc:
            QMessageBox.warning(self, "Пользователи", str(exc))
            return
        self._refresh()


def _password_edit() -> QLineEdit:
    edit = QLineEdit()
    edit.setEchoMode(QLineEdit.EchoMode.Password)
    return edit
