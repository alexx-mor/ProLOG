"""Archive MAX envelopes before parsing and coordinate source-media downloads."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from infrastructure.content_store import (
    ContentPathError,
    ContentRootUnavailableError,
    QUARANTINE_DIR,
    TEMP_PREFIX,
)
from workbot.media_store import WorkBotMediaStore
from workbot.source_models import (
    DownloadedMedia,
    MediaUnavailableError,
    MediaDownloadStatus,
    SourceAttachmentInput,
    SourceRevisionInput,
    StoredSourceRevision,
    WorkBotMediaDiagnosticIssue,
    WorkBotMediaDiagnosticKind,
    WorkBotMediaDiagnosticsReport,
)
from workbot.source_repository import WorkBotSourceRepository


class MediaDownloader(Protocol):
    def download_media(
        self,
        source_url: str | None,
        attachment_type: str,
        source_token: str | None,
    ) -> DownloadedMedia: ...


class WorkBotSourceService:
    def __init__(
        self,
        repository: WorkBotSourceRepository,
        store: WorkBotMediaStore,
        downloader: MediaDownloader,
        *,
        max_download_attempts: int = 4,
        retry_base_seconds: int = 5,
    ) -> None:
        self.repository = repository
        self.store = store
        self.downloader = downloader
        self.max_download_attempts = max(1, max_download_attempts)
        self.retry_base_seconds = max(1, retry_base_seconds)
        self.store.ensure_root()

    def archive_update(self, update: dict[str, Any]) -> StoredSourceRevision | None:
        update_type = str(update.get("update_type") or "")
        received_at_utc = datetime.now(timezone.utc)
        if update_type == "message_removed":
            message_id = str(update.get("message_id") or "").strip()
            if message_id:
                self.repository.record_tombstone(
                    message_id,
                    _as_int(update.get("chat_id")),
                    _timestamp_utc(update.get("timestamp"), fallback=received_at_utc),
                    _json(update),
                )
            return None
        if update_type not in {"message_created", "message_edited"}:
            return None
        message = update.get("message")
        if not isinstance(message, dict):
            return None
        sender = message.get("sender") or {}
        if not isinstance(sender, dict) or sender.get("is_bot"):
            return None
        sender_id = _as_int(sender.get("user_id"))
        if sender_id is None:
            return None
        body = message.get("body") or {}
        if not isinstance(body, dict):
            body = {}
        recipient = message.get("recipient") or {}
        if not isinstance(recipient, dict):
            recipient = {}
        chat_id = _as_int(recipient.get("chat_id"))
        if chat_id is None:
            chat_id = _as_int(update.get("chat_id"))
        message_timestamp = _timestamp_utc(
            message.get("timestamp") or update.get("timestamp"),
            fallback=received_at_utc,
        )
        attachments_raw = body.get("attachments")
        if not isinstance(attachments_raw, list):
            attachments_raw = []
        text_value = body.get("text")
        source_text = str(text_value) if text_value is not None else None
        content = {
            "text": source_text,
            "attachments": attachments_raw,
            "link": message.get("link"),
        }
        content_json = _json(content)
        content_hash = hashlib.sha256(content_json.encode("utf-8")).hexdigest()
        message_id = _source_message_id(
            body,
            sender_id,
            chat_id,
            message_timestamp,
            content_hash,
        )
        source = SourceRevisionInput(
            source_message_id=message_id,
            chat_id=chat_id,
            sender_max_user_id=sender_id,
            sender_display_snapshot=_sender_display(sender),
            message_timestamp_utc=message_timestamp,
            edited_at_utc=(
                _timestamp_utc(update.get("timestamp"), fallback=received_at_utc)
                if update_type == "message_edited"
                else None
            ),
            source_sequence=_as_int(body.get("seq")),
            source_text=source_text,
            content_hash=content_hash,
            content_json=content_json,
            raw_envelope_json=_json(update),
            received_at_utc=received_at_utc,
            attachments=tuple(
                _attachment_input(item, order)
                for order, item in enumerate(attachments_raw)
                if isinstance(item, dict) and _is_source_media(item)
            ),
        )
        stored = self.repository.record_revision(source)
        if stored.created:
            for attachment_id in stored.attachment_ids:
                attachment = self.repository.get_attachment(attachment_id)
                if attachment and attachment.download_status is MediaDownloadStatus.PENDING:
                    self._download(attachment)
        return stored

    def retry_pending_media(self, *, limit: int = 50) -> int:
        now = datetime.now(timezone.utc)
        candidates = self.repository.list_download_candidates(
            now,
            now - timedelta(minutes=10),
            self.max_download_attempts,
            limit,
        )
        for attachment in candidates:
            self._download(attachment)
        return len(candidates)

    def diagnostics(
        self,
        *,
        pending_age: timedelta = timedelta(hours=1),
    ) -> WorkBotMediaDiagnosticsReport:
        issues: list[WorkBotMediaDiagnosticIssue] = []
        revisions = self.repository.list_revisions_for_diagnostics()
        attachments = self.repository.list_attachments_for_diagnostics()
        now = datetime.now(timezone.utc)
        for revision in revisions:
            revision_id = int(revision["id"])
            message_id = str(revision["source_message_id"])
            if revision["parent_message_id"] is None:
                issues.append(
                    WorkBotMediaDiagnosticIssue(
                        WorkBotMediaDiagnosticKind.REVISION_WITHOUT_MESSAGE,
                        "Ревизия не связана с исходным сообщением",
                        message_id,
                        revision_id,
                    )
                )
            actual = hashlib.sha256(str(revision["content_json"]).encode("utf-8")).hexdigest()
            if actual != str(revision["content_hash"]):
                issues.append(
                    WorkBotMediaDiagnosticIssue(
                        WorkBotMediaDiagnosticKind.REVISION_CONTENT_INCONSISTENCY,
                        "Content hash ревизии не совпадает с сохраненным содержимым",
                        message_id,
                        revision_id,
                    )
                )

        referenced_keys: set[str] = set()
        for row in attachments:
            attachment_id = int(row["id"])
            message_id = str(row["source_message_id"] or "")
            revision_id = int(row["revision_id"])
            if row["parent_revision_id"] is None:
                issues.append(
                    WorkBotMediaDiagnosticIssue(
                        WorkBotMediaDiagnosticKind.ATTACHMENT_WITHOUT_REVISION,
                        "Метаданные media не связаны с ревизией",
                        message_id,
                        revision_id,
                        attachment_id,
                    )
                )
                continue
            status = MediaDownloadStatus(str(row["download_status"]))
            storage_key = str(row["storage_key"])
            if status is MediaDownloadStatus.DOWNLOADED:
                try:
                    path = self.store.resolve(storage_key)
                    referenced_keys.add(path.relative_to(self.store.root.resolve()).as_posix())
                    verification = self.store.verify(storage_key, str(row["sha256"]))
                except ContentPathError as error:
                    issues.append(
                        WorkBotMediaDiagnosticIssue(
                            WorkBotMediaDiagnosticKind.UNSAFE_STORAGE_KEY,
                            str(error),
                            message_id,
                            revision_id,
                            attachment_id,
                            storage_key,
                        )
                    )
                    continue
                except ContentRootUnavailableError as error:
                    issues.append(
                        WorkBotMediaDiagnosticIssue(
                            WorkBotMediaDiagnosticKind.ROOT_UNAVAILABLE,
                            str(error),
                            message_id,
                            revision_id,
                            attachment_id,
                            storage_key,
                        )
                    )
                    continue
                if not verification.exists:
                    issues.append(
                        WorkBotMediaDiagnosticIssue(
                            WorkBotMediaDiagnosticKind.MISSING_FILE,
                            "Downloaded metadata ссылаются на отсутствующий файл",
                            message_id,
                            revision_id,
                            attachment_id,
                            storage_key,
                        )
                    )
                elif not verification.is_valid:
                    issues.append(
                        WorkBotMediaDiagnosticIssue(
                            WorkBotMediaDiagnosticKind.HASH_MISMATCH,
                            "SHA-256 source media не совпадает с метаданными",
                            message_id,
                            revision_id,
                            attachment_id,
                            storage_key,
                        )
                    )
            elif status in {MediaDownloadStatus.PENDING, MediaDownloadStatus.DOWNLOADING}:
                received_at = datetime.fromisoformat(str(row["received_at_utc"]))
                if now - received_at > pending_age:
                    issues.append(
                        WorkBotMediaDiagnosticIssue(
                            WorkBotMediaDiagnosticKind.STALE_PENDING,
                            "Загрузка media остается незавершенной слишком долго",
                            message_id,
                            revision_id,
                            attachment_id,
                        )
                    )
            elif status is MediaDownloadStatus.FAILED:
                issues.append(
                    WorkBotMediaDiagnosticIssue(
                        WorkBotMediaDiagnosticKind.FAILED_DOWNLOAD,
                        str(row["last_error"] or "Загрузка media завершилась ошибкой"),
                        message_id,
                        revision_id,
                        attachment_id,
                    )
                )
            elif status is MediaDownloadStatus.UNAVAILABLE:
                issues.append(
                    WorkBotMediaDiagnosticIssue(
                        WorkBotMediaDiagnosticKind.UNAVAILABLE_MEDIA,
                        str(row["last_error"] or "MAX media недоступно"),
                        message_id,
                        revision_id,
                        attachment_id,
                    )
                )

        issues.extend(self._duplicate_identity_issues())
        checked_files = 0
        try:
            root = self.store.readable_root()
            for path in self.store.iter_files():
                relative = path.relative_to(root)
                if relative.parts and relative.parts[0] == QUARANTINE_DIR:
                    continue
                checked_files += 1
                key = relative.as_posix()
                if path.name.startswith(TEMP_PREFIX):
                    issues.append(
                        WorkBotMediaDiagnosticIssue(
                            WorkBotMediaDiagnosticKind.TEMP_FILE,
                            "Обнаружен незавершенный временный файл WorkBot media",
                            storage_key=key,
                        )
                    )
                elif key not in referenced_keys:
                    issues.append(
                        WorkBotMediaDiagnosticIssue(
                            WorkBotMediaDiagnosticKind.ORPHAN_FILE,
                            "Физический WorkBot media не связан с metadata",
                            storage_key=key,
                        )
                    )
        except ContentRootUnavailableError as error:
            root = self.store.root
            issues.append(
                WorkBotMediaDiagnosticIssue(
                    WorkBotMediaDiagnosticKind.ROOT_UNAVAILABLE,
                    str(error),
                )
            )
        return WorkBotMediaDiagnosticsReport(
            root,
            tuple(issues),
            len(revisions),
            len(attachments),
            checked_files,
        )

    def _download(self, attachment) -> None:
        attempted_at = datetime.now(timezone.utc)
        if not self.repository.begin_download(attachment.id, attempted_at):
            return
        try:
            downloaded = self.downloader.download_media(
                attachment.source_url,
                attachment.attachment_type,
                attachment.source_token,
            )
            sha256 = hashlib.sha256(downloaded.content).hexdigest()
            stored = self.store.put(downloaded.content, sha256)
            self.repository.complete_download(
                attachment.id,
                sha256=sha256,
                storage_key=stored.storage_key,
                size_bytes=len(downloaded.content),
                mime_type=downloaded.mime_type or attachment.mime_type,
                original_name=downloaded.original_name or attachment.original_name,
                downloaded_at_utc=datetime.now(timezone.utc),
            )
        except MediaUnavailableError as error:
            self.repository.fail_download(
                attachment.id,
                status=MediaDownloadStatus.UNAVAILABLE,
                error=str(error),
                next_retry_at_utc=None,
            )
        except Exception as error:
            attempt = attachment.download_attempts + 1
            delay = min(self.retry_base_seconds * (2 ** max(0, attempt - 1)), 300)
            next_retry = (
                datetime.now(timezone.utc) + timedelta(seconds=delay)
                if attempt < self.max_download_attempts
                else None
            )
            self.repository.fail_download(
                attachment.id,
                status=MediaDownloadStatus.FAILED,
                error=str(error),
                next_retry_at_utc=next_retry,
            )

    def _duplicate_identity_issues(self) -> list[WorkBotMediaDiagnosticIssue]:
        with self.repository.storage.connect() as connection:
            rows = connection.execute(
                """
                SELECT revision_id, source_attachment_id, COUNT(*) AS count
                FROM source_message_attachments
                GROUP BY revision_id, source_attachment_id
                HAVING COUNT(*) > 1
                """
            ).fetchall()
        return [
            WorkBotMediaDiagnosticIssue(
                WorkBotMediaDiagnosticKind.DUPLICATE_SOURCE_IDENTITY,
                "Одна source attachment identity повторяется внутри ревизии",
                revision_id=int(row["revision_id"]),
            )
            for row in rows
        ]


def _attachment_input(raw: dict[str, Any], order: int) -> SourceAttachmentInput:
    payload = raw.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    identity_value: object | None = None
    identity_kind = ""
    for container, keys in (
        (raw, ("attachment_id", "file_id", "id")),
        (payload, ("attachment_id", "file_id", "id", "token")),
    ):
        for key in keys:
            if container.get(key) not in {None, ""}:
                identity_value = container[key]
                identity_kind = key
                break
        if identity_value is not None:
            break
    source_url = _first_url(payload)
    if identity_value is None and source_url:
        identity_value = source_url
        identity_kind = "url"
    if identity_value is None:
        metadata = _json(raw)
        identity_value = f"derived:{order}:{hashlib.sha256(metadata.encode('utf-8')).hexdigest()}"
        identity_kind = "derived_metadata_hash"
    source_token = str(payload.get("token")) if payload.get("token") else None
    mime_type = str(raw.get("mime_type") or payload.get("mime_type") or "")
    original_name = str(
        raw.get("filename")
        or raw.get("file_name")
        or payload.get("filename")
        or payload.get("file_name")
        or ""
    )
    source_size = _as_int(raw.get("size") or payload.get("size"))
    return SourceAttachmentInput(
        source_attachment_id=str(identity_value),
        identity_kind=identity_kind,
        source_order=order,
        attachment_type=str(raw.get("type") or "unknown").casefold(),
        mime_type=mime_type,
        original_name=original_name,
        source_size=source_size,
        source_url=source_url,
        source_token=source_token,
        source_payload_json=_json(payload),
    )


def _is_source_media(raw: dict[str, Any]) -> bool:
    return str(raw.get("type") or "").casefold() not in {
        "inline_keyboard",
        "reply_keyboard",
        "contact",
        "location",
        "data",
    }


def _first_url(value: object) -> str | None:
    if isinstance(value, dict):
        direct = value.get("url")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
        for nested in value.values():
            result = _first_url(nested)
            if result:
                return result
    elif isinstance(value, list):
        for nested in value:
            result = _first_url(nested)
            if result:
                return result
    return None


def _source_message_id(
    body: dict[str, Any],
    sender_id: int,
    chat_id: int | None,
    timestamp: datetime,
    content_hash: str,
) -> str:
    for key in ("mid", "message_id", "id"):
        if body.get(key):
            return str(body[key])
    return f"generated:{chat_id}:{sender_id}:{timestamp.isoformat()}:{content_hash[:20]}"


def _sender_display(sender: dict[str, Any]) -> str:
    name = " ".join(
        str(sender.get(key) or "").strip()
        for key in ("last_name", "first_name")
    ).strip()
    return name or str(sender.get("name") or sender.get("username") or "").strip()


def _timestamp_utc(value: object, *, fallback: datetime) -> datetime:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback.astimezone(timezone.utc)
    if number > 10_000_000_000:
        number /= 1000
    return datetime.fromtimestamp(number, tz=timezone.utc)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _as_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
