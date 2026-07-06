"""JSON configuration handling."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, fields

from constants import CONFIG_FILE
from models import AppSettings

logger = logging.getLogger(__name__)


class ConfigManager:
    def __init__(self, path=CONFIG_FILE) -> None:
        self.path = path

    def load(self) -> AppSettings:
        if not self.path.exists():
            settings = AppSettings()
            self.save(settings)
            return settings
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            allowed = {field.name for field in fields(AppSettings)}
            return AppSettings(**{key: value for key, value in data.items() if key in allowed})
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            logger.exception("Failed to load config: %s", exc)
            return AppSettings()

    def save(self, settings: AppSettings) -> None:
        try:
            self.path.write_text(
                json.dumps(asdict(settings), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.exception("Failed to save config: %s", exc)
            raise
