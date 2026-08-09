"""Application service for the standalone production-stage directory."""

from __future__ import annotations

import re

from production.errors import (
    InvalidProductionStageCodeError,
    InvalidProductionStageNameError,
    ProductionStageCodeExistsError,
    ProductionStageNotFoundError,
)
from production.models import ProductionStage, utc_now
from production.repository import ProductionStageRepository


_CODE_SEPARATOR_RE = re.compile(r"[^A-Z0-9]+")
_VALID_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def normalize_production_stage_code(value: str) -> str:
    """Normalize a user code into a stable uppercase machine identifier."""

    normalized = _CODE_SEPARATOR_RE.sub("_", value.strip().upper()).strip("_")
    if not normalized or _VALID_CODE_RE.fullmatch(normalized) is None:
        raise InvalidProductionStageCodeError(
            "Машинный код должен начинаться с латинской буквы и содержать "
            "только латинские буквы, цифры и знак подчеркивания"
        )
    return normalized


class ProductionStageService:
    """Coordinate production-stage rules without depending on WorkType."""

    def __init__(self, repository: ProductionStageRepository) -> None:
        self.repository = repository

    def create(self, code: str, name: str) -> ProductionStage:
        normalized_code = normalize_production_stage_code(code)
        normalized_name = self._normalize_name(name)
        if self.repository.get_by_code(normalized_code) is not None:
            raise ProductionStageCodeExistsError(
                f"Производственный этап с кодом {normalized_code} уже существует"
            )
        stages = self.repository.list_all()
        next_order = max((stage.sort_order for stage in stages), default=0) + 1
        return self.repository.create(
            ProductionStage(
                code=normalized_code,
                name=normalized_name,
                sort_order=next_order,
            ),
            created_at_utc=utc_now(),
        )

    def rename(self, stage_id: int, name: str) -> ProductionStage:
        return self.repository.update_name(
            stage_id,
            self._normalize_name(name),
            updated_at_utc=utc_now(),
        )

    def deactivate(self, stage_id: int) -> ProductionStage:
        return self.repository.set_active(stage_id, False, updated_at_utc=utc_now())

    def restore(self, stage_id: int) -> ProductionStage:
        return self.repository.set_active(stage_id, True, updated_at_utc=utc_now())

    def move(self, stage_id: int, direction: int, *, active_only: bool = False) -> None:
        if direction not in {-1, 1}:
            raise ValueError("Направление перемещения должно быть -1 или 1")
        all_stages = self.repository.list_all()
        visible = [stage for stage in all_stages if stage.is_active or not active_only]
        current_index = next(
            (index for index, stage in enumerate(visible) if stage.id == stage_id),
            None,
        )
        if current_index is None:
            raise ProductionStageNotFoundError("Производственный этап не найден")
        target_index = current_index + direction
        if target_index < 0 or target_index >= len(visible):
            return
        current_id = visible[current_index].id
        target_id = visible[target_index].id
        ordered_ids = [stage.id for stage in all_stages]
        first = ordered_ids.index(current_id)
        second = ordered_ids.index(target_id)
        ordered_ids[first], ordered_ids[second] = ordered_ids[second], ordered_ids[first]
        self.repository.reorder(ordered_ids, updated_at_utc=utc_now())

    def list_active(self) -> list[ProductionStage]:
        return self.repository.list_active()

    def list_all(self) -> list[ProductionStage]:
        return self.repository.list_all()

    @staticmethod
    def _normalize_name(value: str) -> str:
        name = value.strip()
        if not name:
            raise InvalidProductionStageNameError("Название этапа не может быть пустым")
        return name
