"""Deterministic sender-isolated grouping over immutable P8 source snapshots."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from production.grouping_models import (
    GROUPING_RULE_VERSION,
    BundleCandidate,
    BundleMessageCandidate,
    BundleMessageRole,
    EffectiveInboxMessage,
    GroupingDiagnosticIssue,
    GroupingDiagnosticKind,
    GroupingDiagnosticsReport,
    GroupingResult,
    GroupingStatus,
)
from production.grouping_repository import ProductionInboxGroupingRepository
from production.models import require_utc_datetime


class ProductionInboxGroupingService:
    def __init__(self, repository: ProductionInboxGroupingRepository) -> None:
        self.repository = repository

    def regroup(
        self,
        *,
        source_id: int | None = None,
        window_minutes: int = 15,
        utc_offset_minutes: int = 180,
        as_of_utc: datetime | None = None,
    ) -> GroupingResult:
        _validate_settings(window_minutes, utc_offset_minutes)
        now = as_of_utc or datetime.now(timezone.utc)
        require_utc_datetime(now, "as_of_utc")
        messages = self.repository.list_effective_messages(source_id)
        candidates = self.build_candidates(
            messages,
            window_minutes=window_minutes,
            utc_offset_minutes=utc_offset_minutes,
            as_of_utc=now,
        )
        source_ids = (
            (source_id,)
            if source_id is not None
            else self.repository.grouping_source_ids()
        )
        return self.repository.reconcile(
            candidates,
            tuple(source_ids),
            effective_message_count=len(messages),
            now_utc=now,
        )

    def build_candidates(
        self,
        messages: list[EffectiveInboxMessage],
        *,
        window_minutes: int,
        utc_offset_minutes: int,
        as_of_utc: datetime,
    ) -> tuple[BundleCandidate, ...]:
        _validate_settings(window_minutes, utc_offset_minutes)
        require_utc_datetime(as_of_utc, "as_of_utc")
        partitions: dict[tuple[object, ...], list[EffectiveInboxMessage]] = defaultdict(list)
        for message in messages:
            sender_key: object = message.sender_max_user_id
            if sender_key is None:
                sender_key = ("unknown", message.source_message_id)
            partitions[(message.source_id, message.chat_id, sender_key)].append(message)
        candidates: list[BundleCandidate] = []
        for partition in partitions.values():
            ordered = sorted(partition, key=_source_order)
            candidates.extend(
                _group_partition(
                    ordered,
                    window_seconds=window_minutes * 60,
                    utc_offset_minutes=utc_offset_minutes,
                    as_of_utc=as_of_utc,
                )
            )
        return tuple(
            sorted(
                candidates,
                key=lambda item: (
                    item.started_at_utc,
                    item.source_id,
                    item.chat_id if item.chat_id is not None else 0,
                    item.sender_max_user_id
                    if item.sender_max_user_id is not None else 0,
                    item.source_fingerprint,
                ),
            )
        )

    def list_bundles(self, *, source_id: int | None = None, current_only: bool = False):
        return self.repository.list_bundles(
            source_id=source_id, current_only=current_only
        )

    def diagnostics(
        self,
        *,
        window_minutes: int = 15,
        utc_offset_minutes: int = 180,
        as_of_utc: datetime | None = None,
    ) -> GroupingDiagnosticsReport:
        now = as_of_utc or datetime.now(timezone.utc)
        messages = self.repository.list_effective_messages()
        expected = self.build_candidates(
            messages,
            window_minutes=window_minutes,
            utc_offset_minutes=utc_offset_minutes,
            as_of_utc=now,
        )
        raw = self.repository.raw_diagnostics()
        current = raw["current_bundles"]
        relations = raw["relations"]
        issues: list[GroupingDiagnosticIssue] = []
        relation_by_bundle: dict[int, list] = defaultdict(list)
        current_memberships: Counter[int] = Counter()
        for row in relations:
            bundle_id = int(row["bundle_id"])
            relation_by_bundle[bundle_id].append(row)
            if bool(row["is_current"]):
                current_memberships[int(row["inbox_message_id"])] += 1
        effective_ids = {item.id for item in messages}
        for message_id in sorted(effective_ids):
            count = current_memberships[message_id]
            if count == 0:
                issues.append(
                    GroupingDiagnosticIssue(
                        GroupingDiagnosticKind.UNGROUPED_EFFECTIVE_MESSAGE,
                        "Effective source message не входит в current bundle",
                        inbox_message_id=message_id,
                    )
                )
            elif count > 1:
                issues.append(
                    GroupingDiagnosticIssue(
                        GroupingDiagnosticKind.MULTIPLE_CURRENT_BUNDLES,
                        "Effective source message входит в несколько current bundles",
                        inbox_message_id=message_id,
                    )
                )
        expected_fingerprints = {item.source_fingerprint for item in expected}
        for bundle in current:
            bundle_id = int(bundle["id"])
            rows = relation_by_bundle.get(bundle_id, [])
            orders = [int(row["bundle_order"]) for row in rows]
            if orders != list(range(len(rows))):
                issues.append(
                    GroupingDiagnosticIssue(
                        GroupingDiagnosticKind.BROKEN_BUNDLE_ORDER,
                        "Нарушен последовательный bundle_order",
                        bundle_id,
                    )
                )
            if any(int(row["source_id"]) != int(bundle["source_id"]) for row in rows) or any(
                row["chat_id"] != bundle["chat_id"] for row in rows
            ):
                issues.append(
                    GroupingDiagnosticIssue(
                        GroupingDiagnosticKind.MIXED_SOURCE_OR_CHAT,
                        "Bundle содержит разные source или chat",
                        bundle_id,
                    )
                )
            if any(row["sender_max_user_id"] != bundle["sender_max_user_id"] for row in rows):
                issues.append(
                    GroupingDiagnosticIssue(
                        GroupingDiagnosticKind.MIXED_SENDER,
                        "Bundle содержит сообщения разных sender",
                        bundle_id,
                    )
                )
            actual_fingerprint = _fingerprint_from_rows(bundle, rows)
            if actual_fingerprint != str(bundle["source_fingerprint"]):
                issues.append(
                    GroupingDiagnosticIssue(
                        GroupingDiagnosticKind.FINGERPRINT_MISMATCH,
                        "Fingerprint bundle не соответствует immutable revisions",
                        bundle_id,
                    )
                )
            if str(bundle["grouping_rule_version"]) != GROUPING_RULE_VERSION:
                issues.append(
                    GroupingDiagnosticIssue(
                        GroupingDiagnosticKind.UNKNOWN_RULE_VERSION,
                        "Bundle создан неизвестной версией grouping rule",
                        bundle_id,
                    )
                )
            if str(bundle["source_fingerprint"]) not in expected_fingerprints:
                issues.append(
                    GroupingDiagnosticIssue(
                        GroupingDiagnosticKind.STALE_CURRENT_BUNDLE,
                        "Current bundle не соответствует effective source view",
                        bundle_id,
                    )
                )
            if str(bundle["grouping_status"]) == GroupingStatus.COLLECTING.value:
                ended = _datetime(bundle["ended_at_utc"])
                if _expired(
                    ended,
                    now,
                    int(bundle["grouping_window_seconds"]),
                    int(bundle["day_boundary_utc_offset_minutes"]),
                ):
                    issues.append(
                        GroupingDiagnosticIssue(
                            GroupingDiagnosticKind.EXPIRED_COLLECTING_BUNDLE,
                            "Collecting bundle просрочен и требует перегруппировки",
                            bundle_id,
                        )
                    )
        attachment_orders: dict[int, list[int]] = defaultdict(list)
        for row in raw["attachment_orders"]:
            attachment_orders[int(row["inbox_message_id"])].append(
                int(row["source_order"])
            )
        for message_id, orders in attachment_orders.items():
            if orders != list(range(len(orders))):
                issues.append(
                    GroupingDiagnosticIssue(
                        GroupingDiagnosticKind.BROKEN_ATTACHMENT_ORDER,
                        "Нарушен source_order вложений",
                        inbox_message_id=message_id,
                    )
                )
        for row in raw["lineage"]:
            if row["parent_id"] is None or int(row["id"]) == int(row["parent_id"]):
                issues.append(
                    GroupingDiagnosticIssue(
                        GroupingDiagnosticKind.BROKEN_LINEAGE,
                        "Нарушена lineage bundle",
                        int(row["id"]),
                    )
                )
        return GroupingDiagnosticsReport(
            tuple(issues),
            effective_message_count=len(messages),
            current_bundle_count=len(current),
            historical_bundle_count=int(raw["historical_count"]),
        )


def _group_partition(
    messages: list[EffectiveInboxMessage],
    *,
    window_seconds: int,
    utc_offset_minutes: int,
    as_of_utc: datetime,
) -> list[BundleCandidate]:
    result: list[BundleCandidate] = []
    open_photos: list[EffectiveInboxMessage] = []
    for message in messages:
        if open_photos:
            previous = open_photos[-1]
            if _local_date(previous.message_timestamp_utc, utc_offset_minutes) != _local_date(
                message.message_timestamp_utc, utc_offset_minutes
            ):
                result.append(
                    _candidate(
                        open_photos,
                        GroupingStatus.NEEDS_DESCRIPTION,
                        "day_boundary",
                        window_seconds,
                        utc_offset_minutes,
                    )
                )
                open_photos = []
            elif (
                message.message_timestamp_utc - previous.message_timestamp_utc
            ).total_seconds() > window_seconds:
                result.append(
                    _candidate(
                        open_photos,
                        GroupingStatus.NEEDS_DESCRIPTION,
                        "timeout",
                        window_seconds,
                        utc_offset_minutes,
                    )
                )
                open_photos = []
        if message.has_media and not message.has_text:
            open_photos.append(message)
            continue
        if message.has_media and message.has_text:
            combined = [*open_photos, message]
            result.append(
                _candidate(
                    combined,
                    GroupingStatus.COMPLETE,
                    "captioned_media",
                    window_seconds,
                    utc_offset_minutes,
                )
            )
            open_photos = []
            continue
        if message.has_text:
            if open_photos:
                result.append(
                    _candidate(
                        [*open_photos, message],
                        GroupingStatus.COMPLETE,
                        "closing_text",
                        window_seconds,
                        utc_offset_minutes,
                    )
                )
                open_photos = []
            else:
                result.append(
                    _candidate(
                        [message],
                        GroupingStatus.TEXT_ONLY,
                        "standalone_text",
                        window_seconds,
                        utc_offset_minutes,
                    )
                )
            continue
        result.append(
            _candidate(
                [message],
                GroupingStatus.INVALID,
                "empty_source_message",
                window_seconds,
                utc_offset_minutes,
            )
        )
    if open_photos:
        previous = open_photos[-1]
        status = GroupingStatus.COLLECTING
        reason = "awaiting_text"
        if _local_date(previous.message_timestamp_utc, utc_offset_minutes) != _local_date(
            as_of_utc, utc_offset_minutes
        ):
            status = GroupingStatus.NEEDS_DESCRIPTION
            reason = "day_boundary"
        elif (as_of_utc - previous.message_timestamp_utc).total_seconds() > window_seconds:
            status = GroupingStatus.NEEDS_DESCRIPTION
            reason = "timeout"
        result.append(
            _candidate(
                open_photos,
                status,
                reason,
                window_seconds,
                utc_offset_minutes,
            )
        )
    return result


def _candidate(messages, status, reason, window_seconds, utc_offset_minutes):
    roles: list[BundleMessageCandidate] = []
    for index, message in enumerate(messages):
        if message.has_media and message.has_text:
            role = BundleMessageRole.CAPTIONED_MEDIA
        elif message.has_media:
            role = BundleMessageRole.PHOTO_SOURCE
        elif message.has_text and index == len(messages) - 1 and len(messages) > 1:
            role = BundleMessageRole.CLOSING_TEXT
        elif message.has_text:
            role = BundleMessageRole.TEXT_ONLY
        else:
            role = BundleMessageRole.SOURCE_ONLY
        roles.append(BundleMessageCandidate(message, role))
    fingerprint = bundle_fingerprint(
        messages,
        rule_version=GROUPING_RULE_VERSION,
        window_seconds=window_seconds,
        utc_offset_minutes=utc_offset_minutes,
    )
    return BundleCandidate(
        source_id=messages[0].source_id,
        chat_id=messages[0].chat_id,
        sender_max_user_id=messages[0].sender_max_user_id,
        sender_display_snapshot=messages[-1].sender_display_snapshot,
        started_at_utc=messages[0].message_timestamp_utc,
        ended_at_utc=messages[-1].message_timestamp_utc,
        grouping_status=status,
        close_reason=reason,
        grouping_rule_version=GROUPING_RULE_VERSION,
        grouping_window_seconds=window_seconds,
        day_boundary_utc_offset_minutes=utc_offset_minutes,
        source_fingerprint=fingerprint,
        messages=tuple(roles),
    )


def bundle_fingerprint(
    messages: list[EffectiveInboxMessage],
    *,
    rule_version: str,
    window_seconds: int,
    utc_offset_minutes: int,
) -> str:
    payload = {
        "source_id": messages[0].source_id,
        "chat_id": messages[0].chat_id,
        "sender_max_user_id": messages[0].sender_max_user_id,
        "rule_version": rule_version,
        "window_seconds": window_seconds,
        "utc_offset_minutes": utc_offset_minutes,
        "messages": [
            {
                "source_message_id": item.source_message_id,
                "source_revision_id": item.source_revision_id,
                "source_revision_number": item.source_revision_number,
                "content_hash": item.content_hash,
            }
            for item in messages
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _fingerprint_from_rows(bundle, rows) -> str:
    payload = {
        "source_id": int(bundle["source_id"]),
        "chat_id": bundle["chat_id"],
        "sender_max_user_id": bundle["sender_max_user_id"],
        "rule_version": str(bundle["grouping_rule_version"]),
        "window_seconds": int(bundle["grouping_window_seconds"]),
        "utc_offset_minutes": int(bundle["day_boundary_utc_offset_minutes"]),
        "messages": [
            {
                "source_message_id": str(row["source_message_id"]),
                "source_revision_id": int(row["source_revision_id"]),
                "source_revision_number": int(row["source_revision_number"]),
                "content_hash": str(row["content_hash"]),
            }
            for row in rows
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _source_order(message: EffectiveInboxMessage) -> tuple[object, ...]:
    return (
        message.message_timestamp_utc,
        1 if message.source_sequence is None else 0,
        message.source_sequence if message.source_sequence is not None else 0,
        message.source_message_id,
        message.source_revision_number,
        message.source_revision_id,
    )


def _local_date(value: datetime, utc_offset_minutes: int):
    return (value + timedelta(minutes=utc_offset_minutes)).date()


def _expired(ended, now, window_seconds, utc_offset_minutes) -> bool:
    return (
        _local_date(ended, utc_offset_minutes)
        != _local_date(now, utc_offset_minutes)
        or (now - ended).total_seconds() > window_seconds
    )


def _validate_settings(window_minutes: int, utc_offset_minutes: int) -> None:
    if window_minutes <= 0:
        raise ValueError("Grouping window должен быть положительным")
    if not -840 <= utc_offset_minutes <= 840:
        raise ValueError("UTC offset должен находиться в диапазоне -840..840 минут")


def _datetime(value: object) -> datetime:
    return datetime.fromisoformat(str(value)).astimezone(timezone.utc)
