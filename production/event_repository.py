"""SQLite persistence for production events and their explicit relations."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from production.errors import (
    InvalidProductionCorrectionError,
    InvalidProductionTransitionError,
    ProductionEventIdempotencyConflictError,
    ProductionEventNotFoundError,
)
from production.models import (
    ActorRef,
    ActorType,
    ProductionEvent,
    ProductionEventAttachment,
    ProductionEventStatus,
    ProductionEventType,
    ProductionEventWorkLog,
    ProductionSourceType,
    WorkLogRelationType,
    require_utc_datetime,
)

if TYPE_CHECKING:
    from database import Database


class ProductionEventRepository:
    """Persist events without product, employee, UI or filesystem decisions."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def create(self, event: ProductionEvent) -> ProductionEvent:
        try:
            with self.database.connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO ProductionEvents (
                        uid, product_id, object_id_snapshot, stage_id, event_type,
                        readiness_percent, description, change_reason,
                        observed_at_utc, recorded_at_utc, source_type, source_ref,
                        reported_by_employee_id, status, supersedes_event_id,
                        idempotency_key, created_at_utc,
                        created_actor_type, created_actor_uid,
                        created_actor_local_user_id,
                        created_actor_display_name_snapshot,
                        confirmed_at_utc, confirmed_actor_type, confirmed_actor_uid,
                        confirmed_actor_local_user_id,
                        confirmed_actor_display_name_snapshot,
                        rejected_at_utc, rejected_actor_type, rejected_actor_uid,
                        rejected_actor_local_user_id,
                        rejected_actor_display_name_snapshot, rejection_reason
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    self._event_params(event),
                )
        except sqlite3.IntegrityError as error:
            if event.idempotency_key and self.find_by_idempotency_key(
                event.idempotency_key
            ) is not None:
                raise ProductionEventIdempotencyConflictError(
                    "Ключ идемпотентности уже принадлежит production event"
                ) from error
            raise
        return replace(event, id=int(cursor.lastrowid))

    def get_by_id(self, event_id: int) -> ProductionEvent | None:
        return self._get("id = ?", (event_id,))

    def get_by_uid(self, uid: UUID) -> ProductionEvent | None:
        return self._get("uid = ?", (str(uid),))

    def find_by_idempotency_key(self, key: str) -> ProductionEvent | None:
        return self._get("idempotency_key = ?", (key,))

    def list_by_product(self, product_id: int) -> list[ProductionEvent]:
        return self._list(
            "WHERE product_id = ? ORDER BY observed_at_utc, id",
            (product_id,),
        )

    def list_confirmed_by_product(self, product_id: int) -> list[ProductionEvent]:
        return self._list(
            """
            WHERE product_id = ? AND status = 'confirmed'
            ORDER BY observed_at_utc, id
            """,
            (product_id,),
        )

    def mark_ready(self, event_id: int) -> ProductionEvent:
        self._update_status(event_id, ProductionEventStatus.DRAFT, ProductionEventStatus.READY)
        return self._required(event_id)

    def reject(
        self,
        event_id: int,
        actor: ActorRef,
        rejected_at_utc: datetime,
        reason: str,
    ) -> ProductionEvent:
        require_utc_datetime(rejected_at_utc, "rejected_at_utc")
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE ProductionEvents
                SET status = 'rejected', rejected_at_utc = ?,
                    rejected_actor_type = ?, rejected_actor_uid = ?,
                    rejected_actor_local_user_id = ?,
                    rejected_actor_display_name_snapshot = ?, rejection_reason = ?
                WHERE id = ? AND status IN ('draft', 'ready')
                """,
                (
                    rejected_at_utc.isoformat(),
                    *_actor_params(actor),
                    reason.strip(),
                    event_id,
                ),
            )
            if cursor.rowcount != 1:
                self._raise_missing_or_transition(connection, event_id, "rejected")
        return self._required(event_id)

    def confirm(
        self,
        event_id: int,
        actor: ActorRef,
        confirmed_at_utc: datetime,
        object_id_snapshot: int,
    ) -> ProductionEvent:
        """Confirm an event and atomically supersede its source when correcting."""

        require_utc_datetime(confirmed_at_utc, "confirmed_at_utc")
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT event_type, status, supersedes_event_id FROM ProductionEvents WHERE id = ?",
                (event_id,),
            ).fetchone()
            if row is None:
                raise ProductionEventNotFoundError("Production event не найден")
            if str(row["status"]) != ProductionEventStatus.READY.value:
                raise InvalidProductionTransitionError(
                    "Подтвердить можно только production event в статусе ready"
                )
            source_id = (
                int(row["supersedes_event_id"])
                if row["supersedes_event_id"] is not None
                else None
            )
            is_correction = str(row["event_type"]) == ProductionEventType.CORRECTION.value
            if is_correction:
                self._require_confirmable_correction(connection, event_id, source_id)
            connection.execute(
                """
                UPDATE ProductionEvents
                SET object_id_snapshot = ?, status = 'confirmed',
                    confirmed_at_utc = ?, confirmed_actor_type = ?,
                    confirmed_actor_uid = ?, confirmed_actor_local_user_id = ?,
                    confirmed_actor_display_name_snapshot = ?
                WHERE id = ? AND status = 'ready'
                """,
                (
                    object_id_snapshot,
                    confirmed_at_utc.isoformat(),
                    *_actor_params(actor),
                    event_id,
                ),
            )
            if is_correction and source_id is not None:
                cursor = connection.execute(
                    """
                    UPDATE ProductionEvents
                    SET status = 'superseded'
                    WHERE id = ? AND status = 'confirmed'
                    """,
                    (source_id,),
                )
                if cursor.rowcount != 1:
                    raise InvalidProductionCorrectionError(
                        "Исходное событие больше нельзя заменить этой корректировкой"
                    )
        return self._required(event_id)

    def add_attachment_relation(
        self,
        event_id: int,
        attachment_id: int,
        sort_order: int,
    ) -> ProductionEventAttachment:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO ProductionEventAttachments (
                    production_event_id, attachment_id, sort_order
                ) VALUES (?, ?, ?)
                ON CONFLICT(production_event_id, attachment_id)
                DO UPDATE SET sort_order = excluded.sort_order
                """,
                (event_id, attachment_id, sort_order),
            )
        return ProductionEventAttachment(event_id, attachment_id, sort_order)

    def remove_attachment_relation(self, event_id: int, attachment_id: int) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                DELETE FROM ProductionEventAttachments
                WHERE production_event_id = ? AND attachment_id = ?
                """,
                (event_id, attachment_id),
            )

    def list_attachments(self, event_id: int) -> list[ProductionEventAttachment]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT production_event_id, attachment_id, sort_order
                FROM ProductionEventAttachments
                WHERE production_event_id = ?
                ORDER BY sort_order, attachment_id
                """,
                (event_id,),
            ).fetchall()
        return [
            ProductionEventAttachment(
                int(row["production_event_id"]),
                int(row["attachment_id"]),
                int(row["sort_order"]),
            )
            for row in rows
        ]

    def next_attachment_sort_order(self, event_id: int) -> int:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT COALESCE(MAX(sort_order) + 1, 0) AS sort_order
                FROM ProductionEventAttachments WHERE production_event_id = ?
                """,
                (event_id,),
            ).fetchone()
        return int(row["sort_order"])

    def add_worklog_relation(
        self,
        event_id: int,
        worklog_entry_id: int,
        relation_type: WorkLogRelationType,
        actor: ActorRef,
        created_at_utc: datetime,
    ) -> ProductionEventWorkLog:
        require_utc_datetime(created_at_utc, "created_at_utc")
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO ProductionEventWorkLogs (
                    production_event_id, worklog_entry_id, relation_type,
                    created_at_utc, created_actor_type, created_actor_uid,
                    created_actor_local_user_id,
                    created_actor_display_name_snapshot
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(production_event_id, worklog_entry_id)
                DO UPDATE SET relation_type = excluded.relation_type,
                    created_at_utc = excluded.created_at_utc,
                    created_actor_type = excluded.created_actor_type,
                    created_actor_uid = excluded.created_actor_uid,
                    created_actor_local_user_id = excluded.created_actor_local_user_id,
                    created_actor_display_name_snapshot =
                        excluded.created_actor_display_name_snapshot
                """,
                (
                    event_id,
                    worklog_entry_id,
                    relation_type.value,
                    created_at_utc.isoformat(),
                    *_actor_params(actor),
                ),
            )
        return ProductionEventWorkLog(
            event_id,
            worklog_entry_id,
            relation_type,
            created_at_utc,
            actor,
        )

    def remove_worklog_relation(self, event_id: int, worklog_entry_id: int) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                DELETE FROM ProductionEventWorkLogs
                WHERE production_event_id = ? AND worklog_entry_id = ?
                """,
                (event_id, worklog_entry_id),
            )

    def list_worklogs(self, event_id: int) -> list[ProductionEventWorkLog]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM ProductionEventWorkLogs
                WHERE production_event_id = ?
                ORDER BY created_at_utc, worklog_entry_id
                """,
                (event_id,),
            ).fetchall()
        return [
            ProductionEventWorkLog(
                production_event_id=int(row["production_event_id"]),
                worklog_entry_id=int(row["worklog_entry_id"]),
                relation_type=WorkLogRelationType(str(row["relation_type"])),
                created_at_utc=datetime.fromisoformat(str(row["created_at_utc"])),
                created_by=_map_actor(row, "created", required=True),
            )
            for row in rows
        ]

    def find_superseding_event(self, event_id: int) -> ProductionEvent | None:
        return self._get(
            "supersedes_event_id = ? AND status = 'confirmed'",
            (event_id,),
        )

    def latest_confirmed_readiness(self, product_id: int) -> int | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT readiness_percent
                FROM ProductionEvents
                WHERE product_id = ? AND status = 'confirmed'
                  AND readiness_percent IS NOT NULL
                ORDER BY observed_at_utc DESC, id DESC
                LIMIT 1
                """,
                (product_id,),
            ).fetchone()
        return int(row["readiness_percent"]) if row else None

    def exists(self, event_id: int) -> bool:
        with self.database.connect() as connection:
            return connection.execute(
                "SELECT 1 FROM ProductionEvents WHERE id = ?",
                (event_id,),
            ).fetchone() is not None

    def _update_status(
        self,
        event_id: int,
        expected: ProductionEventStatus,
        target: ProductionEventStatus,
    ) -> None:
        with self.database.connect() as connection:
            cursor = connection.execute(
                "UPDATE ProductionEvents SET status = ? WHERE id = ? AND status = ?",
                (target.value, event_id, expected.value),
            )
            if cursor.rowcount != 1:
                self._raise_missing_or_transition(connection, event_id, target.value)

    @staticmethod
    def _raise_missing_or_transition(
        connection: sqlite3.Connection,
        event_id: int,
        target: str,
    ) -> None:
        exists = connection.execute(
            "SELECT 1 FROM ProductionEvents WHERE id = ?",
            (event_id,),
        ).fetchone()
        if exists is None:
            raise ProductionEventNotFoundError("Production event не найден")
        raise InvalidProductionTransitionError(
            f"Production event нельзя перевести в статус {target}"
        )

    @staticmethod
    def _require_confirmable_correction(
        connection: sqlite3.Connection,
        event_id: int,
        source_id: int | None,
    ) -> None:
        if source_id is None or source_id == event_id:
            raise InvalidProductionCorrectionError("Некорректная ссылка correction")
        source = connection.execute(
            "SELECT status FROM ProductionEvents WHERE id = ?",
            (source_id,),
        ).fetchone()
        if source is None or str(source["status"]) != ProductionEventStatus.CONFIRMED.value:
            raise InvalidProductionCorrectionError(
                "Correction может заменить только существующее confirmed событие"
            )
        another = connection.execute(
            """
            SELECT 1 FROM ProductionEvents
            WHERE supersedes_event_id = ? AND status = 'confirmed' AND id <> ?
            """,
            (source_id, event_id),
        ).fetchone()
        if another is not None:
            raise InvalidProductionCorrectionError(
                "Исходное событие уже заменено подтвержденной корректировкой"
            )

    def _required(self, event_id: int) -> ProductionEvent:
        event = self.get_by_id(event_id)
        if event is None:
            raise ProductionEventNotFoundError("Production event не найден")
        return event

    def _get(self, condition: str, params: tuple[object, ...]) -> ProductionEvent | None:
        with self.database.connect() as connection:
            row = connection.execute(
                f"SELECT * FROM ProductionEvents WHERE {condition}",
                params,
            ).fetchone()
        return _map_event(row) if row else None

    def _list(self, suffix: str, params: tuple[object, ...]) -> list[ProductionEvent]:
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM ProductionEvents {suffix}",
                params,
            ).fetchall()
        return [_map_event(row) for row in rows]

    @staticmethod
    def _event_params(event: ProductionEvent) -> tuple[object, ...]:
        return (
            str(event.uid),
            event.product_id,
            event.object_id_snapshot,
            event.stage_id,
            event.event_type.value,
            event.readiness_percent,
            event.description,
            event.change_reason,
            event.observed_at_utc.isoformat(),
            event.recorded_at_utc.isoformat(),
            event.source_type.value,
            event.source_ref,
            event.reported_by_employee_id,
            event.status.value,
            event.supersedes_event_id,
            event.idempotency_key,
            event.created_at_utc.isoformat(),
            *_actor_params(event.created_by),
            *_optional_confirmation_params(event),
            *_optional_rejection_params(event),
            event.rejection_reason,
        )


def _actor_params(actor: ActorRef) -> tuple[object, ...]:
    return (
        actor.actor_type.value,
        str(actor.uid),
        actor.local_user_id,
        actor.display_name,
    )


def _optional_confirmation_params(event: ProductionEvent) -> tuple[object, ...]:
    if event.confirmed_by is None:
        return (None, None, None, None, None)
    return (
        event.confirmed_at_utc.isoformat() if event.confirmed_at_utc else None,
        *_actor_params(event.confirmed_by),
    )


def _optional_rejection_params(event: ProductionEvent) -> tuple[object, ...]:
    if event.rejected_by is None:
        return (None, None, None, None, None)
    return (
        event.rejected_at_utc.isoformat() if event.rejected_at_utc else None,
        *_actor_params(event.rejected_by),
    )


def _map_actor(row: sqlite3.Row, prefix: str, *, required: bool) -> ActorRef | None:
    actor_type = row[f"{prefix}_actor_type"]
    if actor_type is None:
        if required:
            raise ValueError(f"Отсутствует обязательный Actor {prefix}")
        return None
    return ActorRef(
        actor_type=ActorType(str(actor_type)),
        uid=UUID(str(row[f"{prefix}_actor_uid"])),
        local_user_id=(
            int(row[f"{prefix}_actor_local_user_id"])
            if row[f"{prefix}_actor_local_user_id"] is not None
            else None
        ),
        display_name=str(row[f"{prefix}_actor_display_name_snapshot"]),
    )


def _map_event(row: sqlite3.Row) -> ProductionEvent:
    return ProductionEvent(
        id=int(row["id"]),
        uid=UUID(str(row["uid"])),
        product_id=int(row["product_id"]) if row["product_id"] is not None else None,
        object_id_snapshot=(
            int(row["object_id_snapshot"])
            if row["object_id_snapshot"] is not None
            else None
        ),
        stage_id=int(row["stage_id"]) if row["stage_id"] is not None else None,
        event_type=ProductionEventType(str(row["event_type"])),
        readiness_percent=(
            int(row["readiness_percent"])
            if row["readiness_percent"] is not None
            else None
        ),
        description=str(row["description"]),
        change_reason=str(row["change_reason"]),
        observed_at_utc=datetime.fromisoformat(str(row["observed_at_utc"])),
        recorded_at_utc=datetime.fromisoformat(str(row["recorded_at_utc"])),
        source_type=ProductionSourceType(str(row["source_type"])),
        source_ref=str(row["source_ref"]) if row["source_ref"] is not None else None,
        reported_by_employee_id=(
            int(row["reported_by_employee_id"])
            if row["reported_by_employee_id"] is not None
            else None
        ),
        status=ProductionEventStatus(str(row["status"])),
        supersedes_event_id=(
            int(row["supersedes_event_id"])
            if row["supersedes_event_id"] is not None
            else None
        ),
        idempotency_key=(
            str(row["idempotency_key"])
            if row["idempotency_key"] is not None
            else None
        ),
        created_at_utc=datetime.fromisoformat(str(row["created_at_utc"])),
        created_by=_map_actor(row, "created", required=True),
        confirmed_at_utc=(
            datetime.fromisoformat(str(row["confirmed_at_utc"]))
            if row["confirmed_at_utc"] is not None
            else None
        ),
        confirmed_by=_map_actor(row, "confirmed", required=False),
        rejected_at_utc=(
            datetime.fromisoformat(str(row["rejected_at_utc"]))
            if row["rejected_at_utc"] is not None
            else None
        ),
        rejected_by=_map_actor(row, "rejected", required=False),
        rejection_reason=str(row["rejection_reason"]),
    )
