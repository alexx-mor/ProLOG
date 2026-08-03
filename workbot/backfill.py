"""Импорт ранее отправленных сообщений группового чата MAX."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from workbot.legacy_parser import infer_sender_names, parse_legacy_reports
from workbot.max_client import MaxClient
from workbot.service import WorkBotService
from workbot.storage import WorkBotStorage


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BackfillResult:
    chat_id: int
    title: str
    fetched_messages: int
    new_messages: int
    new_reports: int


def import_chat_history(
    client: MaxClient,
    storage: WorkBotStorage,
    service: WorkBotService,
    chat_id: int,
) -> BackfillResult:
    chat = client.get_chat(chat_id)
    title = str(chat.get("title") or "")
    storage.upsert_chat(chat_id, title=title, status=str(chat.get("status") or "active"))

    before_messages = storage.stats()["reports"]
    known_before = _message_count(storage, chat_id)
    fetched = 0
    before_timestamp: int | None = None
    seen_boundaries: set[int] = set()

    while True:
        payload = client.get_messages(
            chat_id,
            count=100,
            before_timestamp=before_timestamp,
        )
        messages = payload.get("messages") or []
        if not isinstance(messages, list) or not messages:
            break
        fetched += len(messages)
        timestamps: list[int] = []
        for message in reversed(messages):
            if not isinstance(message, dict):
                continue
            timestamp = _timestamp(message.get("timestamp"))
            if timestamp is not None:
                timestamps.append(timestamp)
            service.handle_update(
                {
                    "update_type": "message_created",
                    "timestamp": message.get("timestamp"),
                    "message": message,
                },
                historical=True,
            )
        if len(messages) < 100 or not timestamps:
            break
        oldest = min(timestamps)
        next_boundary = oldest - 1
        if next_boundary in seen_boundaries:
            logger.warning("MAX вернул повторную страницу истории на границе %s", next_boundary)
            break
        seen_boundaries.add(next_boundary)
        before_timestamp = next_boundary
        logger.info("Загружено сообщений: %s", fetched)

    _reprocess_free_form_messages(storage, chat_id)
    reports_after = storage.stats()["reports"]
    known_after = _message_count(storage, chat_id)
    return BackfillResult(
        chat_id=chat_id,
        title=title,
        fetched_messages=fetched,
        new_messages=max(0, known_after - known_before),
        new_reports=max(0, reports_after - before_messages),
    )


def _message_count(storage: WorkBotStorage, chat_id: int) -> int:
    with storage.connect() as connection:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM messages WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()[0]
        )


def _reprocess_free_form_messages(storage: WorkBotStorage, chat_id: int) -> int:
    rows = storage.chat_messages(chat_id)
    inferred = infer_sender_names(
        [(int(row["sender_id"]), str(row["raw_text"])) for row in rows]
    )
    saved = 0
    for row in rows:
        if row["parse_status"] == "parsed":
            continue
        profile_name = " ".join(
            value for value in (row["last_name"], row["first_name"]) if value
        ).strip()
        fallback = (
            str(row["employee_name"] or "").strip()
            or inferred.get(int(row["sender_id"]), "")
            or profile_name
            or str(row["username"] or "").strip()
            or f"MAX {row['sender_id']}"
        )
        received_date = _received_date(str(row["received_at"]))
        reports = parse_legacy_reports(str(row["raw_text"]), received_date, fallback)
        if reports:
            saved += storage.save_historical_reports(str(row["max_message_id"]), reports)
        elif row["parse_status"] == "parsed_legacy":
            storage.mark_message(str(row["max_message_id"]), "ignored")
    return saved


def _timestamp(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _received_date(value: str):
    from datetime import datetime

    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return datetime.now().date()
