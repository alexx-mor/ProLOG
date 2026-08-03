"""Прикладная логика: доступ, команды владельца и сбор групповых отчётов."""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from workbot.config import WorkBotConfig
from workbot.excel import export_reports
from workbot.legacy_parser import parse_legacy_reports
from workbot.parser import ReportParseError, parse_report
from workbot.storage import WorkBotStorage


class BotClient(Protocol):
    def send_message(self, text: str, *, user_id: int | None = None, chat_id: int | None = None, **kwargs): ...
    def send_file(self, path: Path, caption: str, *, user_id: int): ...
    def answer_callback(self, callback_id: str, **kwargs): ...


class WorkBotService:
    def __init__(self, config: WorkBotConfig, storage: WorkBotStorage, client: BotClient) -> None:
        self.config = config
        self.storage = storage
        self.client = client

    def handle_update(self, update: dict[str, Any], *, historical: bool = False) -> None:
        update_type = str(update.get("update_type") or "")
        if update_type == "message_callback":
            self._handle_callback(update)
            return
        if update_type not in {"message_created", "message_edited"}:
            return
        message = update.get("message")
        if not isinstance(message, dict):
            return
        sender = message.get("sender") or {}
        if not isinstance(sender, dict) or sender.get("is_bot"):
            return
        sender_id = _as_int(sender.get("user_id"))
        if sender_id is None:
            return

        body = message.get("body") or {}
        text = str(body.get("text") or "").strip() if isinstance(body, dict) else ""
        first_name = str(sender.get("first_name") or "").strip()
        last_name = str(sender.get("last_name") or "").strip()
        username = str(sender.get("username") or "").strip().lstrip("@")
        self.storage.upsert_user(sender_id, first_name, last_name, username)

        recipient = message.get("recipient") or {}
        chat_id = _as_int(recipient.get("chat_id")) if isinstance(recipient, dict) else None
        if chat_id is None:
            chat_id = _as_int(update.get("chat_id"))
        chat_type = str(recipient.get("chat_type") or "").casefold() if isinstance(recipient, dict) else ""
        is_private = chat_type == "dialog" or chat_id is None
        is_owner = sender_id in self.config.owner_ids

        if not text:
            return

        if text.startswith("/"):
            if historical:
                return
            command = text.partition(" ")[0].split("@", 1)[0].casefold()
            if is_owner:
                self._handle_owner_command(sender_id, text)
            elif is_private and self.config.deny_unauthorized:
                self.client.send_message("Доступ запрещён.", user_id=sender_id)
            return
        if is_private:
            if not is_owner and self.config.deny_unauthorized:
                self.client.send_message("Доступ запрещён.", user_id=sender_id)
            return
        if self.config.allowed_chat_ids and chat_id not in self.config.allowed_chat_ids:
            return
        self.storage.upsert_chat(chat_id)

        received_at = _message_datetime(message.get("timestamp") or update.get("timestamp"))
        message_id = _message_id(body, sender_id, received_at, text)
        if not self.storage.record_message(
            message_id,
            chat_id,
            sender_id,
            received_at,
            text,
            replace_existing=update_type == "message_edited",
        ):
            return
        fallback_name = " ".join(item for item in (last_name, first_name) if item).strip()
        if not fallback_name:
            fallback_name = username or f"MAX {sender_id}"
        employee_name = self.storage.employee_name_for(sender_id, fallback_name)
        try:
            parsed = parse_report(text, received_at.date())
        except ReportParseError as exc:
            legacy_reports = parse_legacy_reports(text, received_at.date(), employee_name)
            if legacy_reports:
                self.storage.save_historical_reports(message_id, legacy_reports)
            else:
                self.storage.mark_message(message_id, "invalid", str(exc))
            return
        if parsed is None:
            legacy_reports = parse_legacy_reports(text, received_at.date(), employee_name)
            if legacy_reports:
                self.storage.save_historical_reports(message_id, legacy_reports)
            else:
                self.storage.mark_message(message_id, "ignored")
            return

        self.storage.save_report(message_id, sender_id, employee_name, parsed)

    def _handle_owner_command(self, owner_id: int, text: str) -> None:
        command, _, arguments = text.partition(" ")
        command = command.split("@", 1)[0].casefold()
        arguments = arguments.strip()
        if command in {"/start", "/menu"}:
            self._menu(owner_id)
        elif command == "/help":
            self._send(owner_id, _HELP)
        elif command == "/template":
            self._send(owner_id, _TEMPLATE)
        elif command == "/id":
            self._send(owner_id, f"Ваш MAX user_id: {owner_id}")
        elif command == "/bind":
            self._bind(owner_id, arguments)
        elif command == "/users":
            self._users(owner_id)
        elif command == "/chats":
            self._chats(owner_id)
        elif command == "/stats":
            stats = self.storage.stats()
            self._send(
                owner_id,
                f"Пользователей: {stats['users']}\n"
                f"Принято отчётов: {stats['reports']}\n"
                f"Неполных отчётов: {stats['invalid']}",
            )
        elif command == "/errors":
            self._errors(owner_id)
        elif command == "/excel":
            self._excel(owner_id, arguments)
        elif command == "/missing":
            self._missing(owner_id, arguments)
        else:
            self._send(owner_id, "Неизвестная команда. Используйте /help.")

    def _handle_callback(self, update: dict[str, Any]) -> None:
        callback = update.get("callback") or {}
        if not isinstance(callback, dict):
            return
        callback_id = str(callback.get("callback_id") or "")
        payload = str(callback.get("payload") or "")
        user = callback.get("user") or {}
        owner_id = _as_int(user.get("user_id")) if isinstance(user, dict) else None
        if not callback_id or owner_id is None:
            return
        if owner_id not in self.config.owner_ids:
            self.client.answer_callback(callback_id, notification="Доступ запрещён.")
            return

        actions = {
            "menu:stats": lambda: self._handle_owner_command(owner_id, "/stats"),
            "menu:users": lambda: self._handle_owner_command(owner_id, "/users"),
            "menu:chats": lambda: self._handle_owner_command(owner_id, "/chats"),
            "menu:errors": lambda: self._handle_owner_command(owner_id, "/errors"),
            "menu:missing": lambda: self._handle_owner_command(owner_id, "/missing"),
            "menu:template": lambda: self._handle_owner_command(owner_id, "/template"),
        }
        if payload == "menu:refresh":
            self.client.answer_callback(
                callback_id,
                notification="Меню обновлено",
                message=_menu_message(),
            )
            return
        if payload == "menu:excel":
            self.client.answer_callback(callback_id, notification="Формирую Excel…")
            self._excel(owner_id, "")
            return
        action = actions.get(payload)
        if action is None:
            self.client.answer_callback(callback_id, notification="Неизвестная кнопка")
            return
        self.client.answer_callback(callback_id, notification="Готово")
        action()

    def _menu(self, owner_id: int) -> None:
        message = _menu_message()
        self.client.send_message(
            message["text"],
            user_id=owner_id,
            attachments=message["attachments"],
        )

    def _bind(self, owner_id: int, arguments: str) -> None:
        match = re.fullmatch(r"(\d+)\s*\|\s*(.+)", arguments)
        if not match:
            self._send(owner_id, "Формат: /bind MAX_ID | Фамилия Имя Отчество")
            return
        try:
            self.storage.bind_user(int(match.group(1)), match.group(2))
        except ValueError as exc:
            self._send(owner_id, str(exc))
            return
        self._send(owner_id, f"Сопоставление сохранено: {match.group(1)} → {match.group(2).strip()}")

    def _users(self, owner_id: int) -> None:
        rows = self.storage.users()
        if not rows:
            self._send(owner_id, "Пользователи пока не обнаружены.")
            return
        lines = ["Пользователи MAX:"]
        for row in rows[:60]:
            profile = " ".join(item for item in (row["last_name"], row["first_name"]) if item).strip()
            username = f" @{row['username']}" if row["username"] else ""
            employee = f" → {row['employee_name']}" if row["employee_name"] else " → не сопоставлен"
            lines.append(
                f"{row['max_user_id']}: {profile or 'без имени'}{username}{employee} "
                f"(отчётов: {row['reports_count']})"
            )
        self._send(owner_id, "\n".join(lines))

    def _chats(self, owner_id: int) -> None:
        rows = self.storage.chats()
        if not rows:
            self._send(
                owner_id,
                "Группы пока не обнаружены. Отправьте любое сообщение в нужную группу.",
            )
            return
        lines = ["Обнаруженные группы:"]
        for row in rows:
            title = row["title"] or "название ещё не загружено"
            lines.append(
                f"{row['chat_id']}: {title} "
                f"(сообщений: {row['messages_count']}, отчётов: {row['reports_count']})"
            )
        self._send(owner_id, "\n".join(lines))

    def _errors(self, owner_id: int) -> None:
        rows = self.storage.recent_errors()
        if not rows:
            self._send(owner_id, "Неполных отчётов нет.")
            return
        lines = ["Последние неполные отчёты:"]
        for row in rows:
            employee = (row["employee"] or "").strip() or str(row["sender_id"])
            lines.append(f"{row['received_at'][:10]} — {employee}: {row['parse_error']}")
        self._send(owner_id, "\n".join(lines))

    def _excel(self, owner_id: int, arguments: str) -> None:
        try:
            date_from, date_to = _parse_period(arguments)
        except ValueError as exc:
            self._send(owner_id, str(exc))
            return
        reports = self.storage.reports(date_from, date_to)
        path = export_reports(reports, self.config.export_dir, date_from, date_to)
        period = _period_text(date_from, date_to)
        self.client.send_file(
            path,
            f"Отчёты WorkBot{period}. Записей: {len(reports)}.",
            user_id=owner_id,
        )

    def _missing(self, owner_id: int, arguments: str) -> None:
        try:
            date_from, date_to = _parse_period(arguments)
        except ValueError as exc:
            self._send(owner_id, str(exc))
            return
        if date_from is None and date_to is None:
            today = date.today()
            date_from = today - timedelta(days=today.weekday())
            date_to = date_from + timedelta(days=6)
        elif date_from is None or date_to is None:
            self._send(owner_id, "Для /missing укажите обе даты: /missing ДД.ММ.ГГГГ ДД.ММ.ГГГГ")
            return
        weekdays = {
            date_from + timedelta(days=offset)
            for offset in range((date_to - date_from).days + 1)
            if (date_from + timedelta(days=offset)).weekday() < 5
        }
        reports = self.storage.reports(date_from, date_to)
        by_sender: dict[int, set[date]] = {}
        for report in reports:
            by_sender.setdefault(report.sender_id, set()).add(report.work_date)
        lines = [f"Отсутствуют отчёты за {date_from:%d.%m.%Y}–{date_to:%d.%m.%Y}:"]
        missing_found = False
        for user in self.storage.users():
            if not user["employee_name"]:
                continue
            missing = sorted(weekdays - by_sender.get(int(user["max_user_id"]), set()))
            if not missing:
                continue
            missing_found = True
            mention = f"@{user['username']}" if user["username"] else user["employee_name"]
            lines.append(f"{mention}: {', '.join(item.strftime('%d.%m') for item in missing)}")
        self._send(owner_id, "\n".join(lines) if missing_found else "Все сопоставленные сотрудники сдали отчёты.")

    def _send(self, owner_id: int, text: str) -> None:
        self.client.send_message(text[:4000], user_id=owner_id)


def _message_datetime(timestamp: object) -> datetime:
    moscow_timezone = timezone(timedelta(hours=3), name="Europe/Moscow")
    try:
        number = float(timestamp)
    except (TypeError, ValueError):
        return datetime.now(moscow_timezone)
    if number > 10_000_000_000:
        number /= 1000
    return datetime.fromtimestamp(number, tz=timezone.utc).astimezone(moscow_timezone)


def _message_id(body: dict[str, Any], sender_id: int, received_at: datetime, text: str) -> str:
    for key in ("mid", "message_id", "id"):
        if body.get(key):
            return str(body[key])
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]
    return f"generated:{sender_id}:{received_at.isoformat()}:{digest}"


def _as_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _parse_period(arguments: str) -> tuple[date | None, date | None]:
    if not arguments:
        return None, None
    parts = arguments.split()
    if len(parts) not in {1, 2}:
        raise ValueError("Формат: /excel [ДД.ММ.ГГГГ] [ДД.ММ.ГГГГ]")
    parsed = [_command_date(item) for item in parts]
    date_from = parsed[0]
    date_to = parsed[-1]
    if date_from > date_to:
        raise ValueError("Начальная дата не может быть позже конечной")
    return date_from, date_to


def _command_date(value: str) -> date:
    match = re.fullmatch(r"(\d{1,2})[.-](\d{1,2})[.-](\d{4})", value)
    if not match:
        raise ValueError("Дата должна быть в формате ДД.ММ.ГГГГ")
    day, month, year = (int(item) for item in match.groups())
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise ValueError(f"Некорректная дата: {value}") from exc


def _period_text(date_from: date | None, date_to: date | None) -> str:
    if date_from and date_to:
        return f" за {date_from:%d.%m.%Y}–{date_to:%d.%m.%Y}"
    return ""


_TEMPLATE = """Дата: 30.07.2026
Виды работ: Монтаж и проверка шкафа автоматики
Затраченное время: 8
Объект: Цех № 1
Местонахождение: Производство"""

_HELP = """WorkBot — команды владельца:
/menu — кнопки управления
/template — шаблон отчёта
/users — найденные пользователи и их MAX ID
/chats — обнаруженные группы и их chat_id
/bind MAX_ID | ФИО — привязать пользователя к сотруднику
/stats — статистика
/errors — неполные отчёты
/excel [дата_с] [дата_по] — получить Excel
/missing [дата_с] [дата_по] — пропуски по рабочим дням
/id — ваш MAX user_id

В группах бот собирает корректно заполненные отчёты молча."""


def _menu_message() -> dict[str, Any]:
    def button(text: str, payload: str) -> dict[str, str]:
        return {"type": "callback", "text": text, "payload": payload}

    return {
        "text": "WorkBot — управление",
        "attachments": [
            {
                "type": "inline_keyboard",
                "payload": {
                    "buttons": [
                        [
                            button("📊 Получить Excel", "menu:excel"),
                            button("📈 Статистика", "menu:stats"),
                        ],
                        [
                            button("👥 Сотрудники", "menu:users"),
                            button("💬 Группы", "menu:chats"),
                        ],
                        [
                            button("⚠️ Ошибки", "menu:errors"),
                            button("📅 Нет отчётов", "menu:missing"),
                        ],
                        [
                            button("📝 Шаблон", "menu:template"),
                            button("🔄 Обновить", "menu:refresh"),
                        ],
                    ]
                },
            }
        ],
    }
