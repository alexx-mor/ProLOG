"""Конфигурация WorkBot из переменных окружения и private/workbot.env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = PROJECT_DIR / "private" / "workbot.env"


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on", "да"}


def _as_ids(value: str | None) -> frozenset[int]:
    if not value:
        return frozenset()
    try:
        return frozenset(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise ValueError("ID в настройках WorkBot должны быть целыми числами через запятую") from exc


def load_env_file(path: Path = DEFAULT_ENV_FILE) -> None:
    """Загрузить простой KEY=VALUE-файл, не заменяя системные переменные."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            os.environ.setdefault(key, value)


@dataclass(frozen=True, slots=True)
class WorkBotConfig:
    token: str = ""
    owner_ids: frozenset[int] = field(default_factory=frozenset)
    allowed_chat_ids: frozenset[int] = field(default_factory=frozenset)
    deny_unauthorized: bool = False
    api_base: str = "https://platform-api2.max.ru"
    database_path: Path = PROJECT_DIR / "data" / "workbot.sqlite3"
    media_root: Path = PROJECT_DIR / "data" / "workbot_media"
    export_dir: Path = PROJECT_DIR / "exports"
    poll_timeout: int = 30
    media_max_attempts: int = 4
    media_retry_base_seconds: int = 5

    @classmethod
    def from_environment(cls, *, require_token: bool = True, require_owner: bool = True) -> "WorkBotConfig":
        load_env_file()
        config = cls(
            token=os.getenv("WORKBOT_TOKEN", "").strip(),
            owner_ids=_as_ids(os.getenv("WORKBOT_OWNER_IDS")),
            allowed_chat_ids=_as_ids(os.getenv("WORKBOT_ALLOWED_CHAT_IDS")),
            deny_unauthorized=_as_bool(os.getenv("WORKBOT_DENY_UNAUTHORIZED"), False),
            api_base=os.getenv("WORKBOT_API_BASE", "https://platform-api2.max.ru").rstrip("/"),
            database_path=Path(
                os.getenv("WORKBOT_DATABASE", str(PROJECT_DIR / "data" / "workbot.sqlite3"))
            ).expanduser(),
            media_root=Path(
                os.getenv("WORKBOT_MEDIA_ROOT", str(PROJECT_DIR / "data" / "workbot_media"))
            ).expanduser(),
            export_dir=Path(os.getenv("WORKBOT_EXPORT_DIR", str(PROJECT_DIR / "exports"))).expanduser(),
            poll_timeout=max(1, min(90, int(os.getenv("WORKBOT_POLL_TIMEOUT", "30")))),
            media_max_attempts=max(
                1,
                min(10, int(os.getenv("WORKBOT_MEDIA_MAX_ATTEMPTS", "4"))),
            ),
            media_retry_base_seconds=max(
                1,
                min(300, int(os.getenv("WORKBOT_MEDIA_RETRY_BASE_SECONDS", "5"))),
            ),
        )
        if require_token and not config.token:
            raise ValueError(
                "Не задан WORKBOT_TOKEN. Создайте private/workbot.env по образцу workbot.env.example."
            )
        if require_owner and not config.owner_ids:
            raise ValueError(
                "Не задан WORKBOT_OWNER_IDS. Сначала выполните: python -m workbot identify"
            )
        return config
