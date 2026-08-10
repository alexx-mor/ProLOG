"""Запуск WorkBot в режиме Long Polling для пилотной эксплуатации."""

from __future__ import annotations

import logging
import time

from workbot.config import WorkBotConfig
from workbot.max_client import MaxApiError, MaxClient
from workbot.service import WorkBotService
from workbot.storage import WorkBotStorage


logger = logging.getLogger(__name__)


def run_polling(config: WorkBotConfig) -> None:
    storage = WorkBotStorage(config.database_path)
    storage.initialize()
    client = MaxClient(config.token, config.api_base, timeout=config.poll_timeout + 10)
    service = WorkBotService(config, storage, client)
    bot = client.get_me()
    logger.info("WorkBot запущен: %s (@%s)", bot.get("first_name", "бот"), bot.get("username", ""))

    saved_marker = storage.get_state("updates_marker")
    marker = int(saved_marker) if saved_marker else None
    retried = service.retry_source_media()
    if retried:
        logger.info("Повторена загрузка незавершенных WorkBot media: %s", retried)
    backoff = 1
    while True:
        try:
            payload = client.get_updates(marker, timeout=config.poll_timeout)
            for update in payload.get("updates", []):
                try:
                    service.handle_update(update)
                except Exception:
                    logger.exception("Не удалось обработать обновление MAX")
            service.retry_source_media()
            new_marker = payload.get("marker")
            if new_marker is not None:
                marker = int(new_marker)
                storage.set_state("updates_marker", str(marker))
            backoff = 1
        except KeyboardInterrupt:
            logger.info("WorkBot остановлен")
            return
        except MaxApiError as exc:
            logger.warning("Ошибка MAX API: %s. Повтор через %s с.", exc, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)
        except Exception:
            logger.exception("Неожиданная ошибка цикла WorkBot")
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)


def identify_sender(config: WorkBotConfig) -> None:
    client = MaxClient(config.token, config.api_base, timeout=config.poll_timeout + 10)
    print("Отправьте боту в MAX любое текстовое сообщение. Ожидание...")
    marker: int | None = None
    while True:
        payload = client.get_updates(marker, timeout=config.poll_timeout)
        marker_value = payload.get("marker")
        marker = int(marker_value) if marker_value is not None else marker
        for update in payload.get("updates", []):
            message = update.get("message") or {}
            sender = message.get("sender") or {}
            if sender.get("is_bot") or not sender.get("user_id"):
                continue
            recipient = message.get("recipient") or {}
            if str(recipient.get("chat_type") or "").casefold() != "dialog" and recipient.get("chat_id"):
                continue
            name = " ".join(
                str(sender.get(key) or "").strip() for key in ("last_name", "first_name")
            ).strip()
            username = f"@{sender.get('username')}" if sender.get("username") else "без username"
            print(f"MAX user_id: {sender['user_id']} — {name or 'без имени'}, {username}")
            return
