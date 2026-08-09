"""SQLite repository dedicated to the ProductionStage aggregate."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING, Iterable
from uuid import UUID

from production.errors import (
    ProductionStageCodeExistsError,
    ProductionStageNotFoundError,
)
from production.models import ProductionStage, require_utc_datetime

if TYPE_CHECKING:
    from database import Database


class ProductionStageRepository:
    """Persist production stages without using the universal directories."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def get_by_id(self, stage_id: int) -> ProductionStage | None:
        return self._get("id = ?", (stage_id,))

    def get_by_uid(self, uid: UUID) -> ProductionStage | None:
        return self._get("uid = ?", (str(uid),))

    def get_by_code(self, code: str) -> ProductionStage | None:
        return self._get("code = ? COLLATE NOCASE", (code,))

    def list_all(self) -> list[ProductionStage]:
        return self._list("")

    def list_active(self) -> list[ProductionStage]:
        return self._list("WHERE is_active = 1")

    def create(
        self,
        stage: ProductionStage,
        *,
        created_at_utc: datetime,
    ) -> ProductionStage:
        require_utc_datetime(created_at_utc, "created_at_utc")
        timestamp = created_at_utc.isoformat(timespec="seconds")
        try:
            with self.database.connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO ProductionStages (
                        uid, code, name, sort_order, is_active,
                        created_at_utc, updated_at_utc
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(stage.uid),
                        stage.code,
                        stage.name,
                        stage.sort_order,
                        int(stage.is_active),
                        timestamp,
                        timestamp,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise ProductionStageCodeExistsError(
                f"Производственный этап с кодом {stage.code} уже существует"
            ) from error
        return replace(stage, id=int(cursor.lastrowid))

    def update_name(
        self,
        stage_id: int,
        name: str,
        *,
        updated_at_utc: datetime,
    ) -> ProductionStage:
        stage = self._required(stage_id)
        self._update(
            stage_id,
            "name = ?, updated_at_utc = ?",
            (name, updated_at_utc.isoformat(timespec="seconds")),
            updated_at_utc,
        )
        return replace(stage, name=name)

    def set_active(
        self,
        stage_id: int,
        is_active: bool,
        *,
        updated_at_utc: datetime,
    ) -> ProductionStage:
        stage = self._required(stage_id)
        self._update(
            stage_id,
            "is_active = ?, updated_at_utc = ?",
            (int(is_active), updated_at_utc.isoformat(timespec="seconds")),
            updated_at_utc,
        )
        return replace(stage, is_active=is_active)

    def update_sort_order(
        self,
        stage_id: int,
        sort_order: int,
        *,
        updated_at_utc: datetime,
    ) -> ProductionStage:
        stage = self._required(stage_id)
        self._update(
            stage_id,
            "sort_order = ?, updated_at_utc = ?",
            (sort_order, updated_at_utc.isoformat(timespec="seconds")),
            updated_at_utc,
        )
        return replace(stage, sort_order=sort_order)

    def reorder(
        self,
        ordered_stage_ids: Iterable[int],
        *,
        updated_at_utc: datetime,
    ) -> None:
        require_utc_datetime(updated_at_utc, "updated_at_utc")
        ordered_ids = list(ordered_stage_ids)
        existing_ids = [stage.id for stage in self.list_all()]
        if len(ordered_ids) != len(set(ordered_ids)) or set(ordered_ids) != set(existing_ids):
            raise ProductionStageNotFoundError(
                "Новый порядок должен содержать каждый производственный этап ровно один раз"
            )
        timestamp = updated_at_utc.isoformat(timespec="seconds")
        with self.database.connect() as connection:
            for sort_order, stage_id in enumerate(ordered_ids, start=1):
                connection.execute(
                    """
                    UPDATE ProductionStages
                    SET sort_order = ?, updated_at_utc = ?
                    WHERE id = ?
                    """,
                    (sort_order, timestamp, stage_id),
                )

    def exists(self, stage_id: int) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM ProductionStages WHERE id = ?",
                (stage_id,),
            ).fetchone()
        return row is not None

    def is_in_use(self, stage_id: int) -> bool:
        """Check future ProductionEvents without requiring a placeholder table."""

        with self.database.connect() as connection:
            table = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'ProductionEvents'
                """
            ).fetchone()
            if table is None:
                return False
            row = connection.execute(
                "SELECT 1 FROM ProductionEvents WHERE stage_id = ? LIMIT 1",
                (stage_id,),
            ).fetchone()
        return row is not None

    def _get(self, condition: str, params: tuple[object, ...]) -> ProductionStage | None:
        with self.database.connect() as connection:
            row = connection.execute(
                f"SELECT * FROM ProductionStages WHERE {condition}",
                params,
            ).fetchone()
        return self._map(row) if row else None

    def _list(self, where_sql: str) -> list[ProductionStage]:
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM ProductionStages
                {where_sql}
                ORDER BY sort_order, id
                """
            ).fetchall()
        return [self._map(row) for row in rows]

    def _required(self, stage_id: int) -> ProductionStage:
        stage = self.get_by_id(stage_id)
        if stage is None:
            raise ProductionStageNotFoundError("Производственный этап не найден")
        return stage

    def _update(
        self,
        stage_id: int,
        assignment_sql: str,
        params: tuple[object, ...],
        updated_at_utc: datetime,
    ) -> None:
        require_utc_datetime(updated_at_utc, "updated_at_utc")
        with self.database.connect() as connection:
            cursor = connection.execute(
                f"UPDATE ProductionStages SET {assignment_sql} WHERE id = ?",
                (*params, stage_id),
            )
            if cursor.rowcount != 1:
                raise ProductionStageNotFoundError("Производственный этап не найден")

    @staticmethod
    def _map(row: sqlite3.Row) -> ProductionStage:
        return ProductionStage(
            id=int(row["id"]),
            uid=UUID(str(row["uid"])),
            code=str(row["code"]),
            name=str(row["name"]),
            sort_order=int(row["sort_order"]),
            is_active=bool(row["is_active"]),
        )
