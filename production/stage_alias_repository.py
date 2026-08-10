"""Persistence for production-specific stage aliases."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from matching_text import normalize_alias_text
from production.matching_models import ProductionStageAlias


class ProductionStageAliasRepository:
    def __init__(self, database) -> None:
        self.database = database

    def list_all(self, *, active_only: bool = False) -> list[ProductionStageAlias]:
        where = "WHERE is_active = 1" if active_only else ""
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM ProductionStageAliases
                {where}
                ORDER BY normalized_alias, id
                """
            ).fetchall()
        return [_map(row) for row in rows]

    def create(self, stage_id: int, alias_text: str) -> ProductionStageAlias:
        normalized = normalize_alias_text(alias_text)
        if not normalized:
            raise ValueError("Алиас производственного этапа не может быть пустым")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        uid = uuid4()
        with self.database.connect() as connection:
            if connection.execute(
                "SELECT 1 FROM ProductionStages WHERE id = ?", (stage_id,)
            ).fetchone() is None:
                raise ValueError("Производственный этап не найден")
            cursor = connection.execute(
                """
                INSERT INTO ProductionStageAliases (
                    uid, stage_id, alias_text, normalized_alias, is_active,
                    created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                (str(uid), stage_id, alias_text.strip(), normalized, now, now),
            )
        return ProductionStageAlias(
            stage_id, alias_text.strip(), normalized, True,
            int(cursor.lastrowid), uid,
        )

    def set_active(self, alias_id: int, is_active: bool) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE ProductionStageAliases
                SET is_active = ?, updated_at_utc = ? WHERE id = ?
                """,
                (int(is_active), now, alias_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Алиас производственного этапа не найден")


def _map(row) -> ProductionStageAlias:
    return ProductionStageAlias(
        stage_id=int(row["stage_id"]),
        alias_text=str(row["alias_text"]),
        normalized_alias=str(row["normalized_alias"]),
        is_active=bool(row["is_active"]),
        id=int(row["id"]),
        uid=UUID(str(row["uid"])),
    )
