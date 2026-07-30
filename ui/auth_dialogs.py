"""Authentication dialogs."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
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
    QVBoxLayout,
)

from auth import AuthService, AuthSession, RegistrationData, role_label


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
    def __init__(self, auth_service: AuthService, parent=None) -> None:
        super().__init__(parent)
        self.auth_service = auth_service
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
        exit_button = QPushButton("Выход")
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


def _password_edit() -> QLineEdit:
    edit = QLineEdit()
    edit.setEchoMode(QLineEdit.EchoMode.Password)
    return edit
