"""Local authentication and role storage for ProLOG."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from constants import AUTH_FILE

logger = logging.getLogger(__name__)

ROLE_ADMIN = "admin"
ROLE_USER = "user"
SCHEMA_VERSION = 1
PASSWORD_ALGORITHM = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 260_000
MIN_PASSWORD_LENGTH = 4


@dataclass(slots=True)
class UserAccount:
    username: str
    role: str
    password_hash: str


@dataclass(slots=True)
class AuthProfile:
    organization_name: str
    department_name: str
    leader_full_name: str
    users: list[UserAccount]
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class AuthSession:
    username: str
    role: str
    organization_name: str
    department_name: str
    leader_full_name: str

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN


@dataclass(slots=True)
class RegistrationData:
    organization_name: str
    department_name: str
    leader_full_name: str
    leader_password: str
    user_name: str = ""
    user_password: str = ""
    user_is_leader: bool = False


class AuthService:
    def __init__(self, path: Path = AUTH_FILE) -> None:
        self.path = path

    def has_profile(self) -> bool:
        return self.path.exists()

    def register_initial(self, data: RegistrationData) -> AuthSession:
        self._validate_registration(data)
        users = [
            UserAccount(
                username=data.leader_full_name.strip(),
                role=ROLE_ADMIN,
                password_hash=_hash_password(data.leader_password),
            )
        ]
        if not data.user_is_leader:
            users.append(
                UserAccount(
                    username=data.user_name.strip(),
                    role=ROLE_USER,
                    password_hash=_hash_password(data.user_password),
                )
            )
        profile = AuthProfile(
            organization_name=data.organization_name.strip(),
            department_name=data.department_name.strip(),
            leader_full_name=data.leader_full_name.strip(),
            users=users,
        )
        self.save_profile(profile)
        return self.authenticate(data.leader_full_name, data.leader_password)

    def authenticate(self, username: str, password: str) -> AuthSession:
        profile = self.load_profile()
        account = self._find_user(profile, username)
        if account is None or not _verify_password(password, account.password_hash):
            raise ValueError("Неверный пользователь или пароль")
        return AuthSession(
            username=account.username,
            role=account.role,
            organization_name=profile.organization_name,
            department_name=profile.department_name,
            leader_full_name=profile.leader_full_name,
        )

    def list_users(self) -> list[UserAccount]:
        return self.load_profile().users

    def load_profile(self) -> AuthProfile:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.exception("Failed to load auth profile")
            raise ValueError("Не удалось загрузить данные авторизации") from exc
        return _profile_from_dict(data)

    def save_profile(self, profile: AuthProfile) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(profile)
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _find_user(self, profile: AuthProfile, username: str) -> UserAccount | None:
        normalized = username.strip().casefold()
        for account in profile.users:
            if account.username.casefold() == normalized:
                return account
        return None

    def _validate_registration(self, data: RegistrationData) -> None:
        required = {
            "Наименование организации": data.organization_name,
            "Отдел": data.department_name,
            "Руководитель отдела": data.leader_full_name,
            "Пароль руководителя": data.leader_password,
        }
        for label, value in required.items():
            if not value.strip():
                raise ValueError(f"Заполните поле '{label}'")
        _validate_password(data.leader_password, "руководителя")
        if data.user_is_leader:
            return
        if not data.user_name.strip():
            raise ValueError("Укажите пользователя или отметьте, что пользователь является руководителем")
        if data.user_name.strip().casefold() == data.leader_full_name.strip().casefold():
            raise ValueError("Пользователь и руководитель должны быть разными учетными записями")
        _validate_password(data.user_password, "пользователя")


def role_label(role: str) -> str:
    if role == ROLE_ADMIN:
        return "Руководитель"
    return "Пользователь"


def _validate_password(password: str, owner: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Пароль {owner} должен быть не короче {MIN_PASSWORD_LENGTH} символов")


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("ascii"),
        PASSWORD_ITERATIONS,
    )
    encoded = base64.b64encode(digest).decode("ascii")
    return f"{PASSWORD_ALGORITHM}${PASSWORD_ITERATIONS}${salt}${encoded}"


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations_text, salt, expected = password_hash.split("$", 3)
        if algorithm != PASSWORD_ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("ascii"),
            int(iterations_text),
        )
    except (ValueError, TypeError):
        return False
    actual = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(actual, expected)


def _profile_from_dict(data: dict[str, Any]) -> AuthProfile:
    users_data = data.get("users", [])
    if not isinstance(users_data, list):
        users_data = []
    users = [
        UserAccount(
            username=str(item.get("username", "")).strip(),
            role=str(item.get("role", ROLE_USER)).strip() or ROLE_USER,
            password_hash=str(item.get("password_hash", "")),
        )
        for item in users_data
        if isinstance(item, dict) and str(item.get("username", "")).strip()
    ]
    if not users:
        raise ValueError("В файле авторизации нет пользователей")
    return AuthProfile(
        schema_version=int(data.get("schema_version", SCHEMA_VERSION) or SCHEMA_VERSION),
        organization_name=str(data.get("organization_name", "")).strip(),
        department_name=str(data.get("department_name", "")).strip(),
        leader_full_name=str(data.get("leader_full_name", "")).strip(),
        users=users,
    )
