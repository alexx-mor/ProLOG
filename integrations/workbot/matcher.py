"""Deterministic matching of product references in WorkBot messages."""

from __future__ import annotations

import re
from dataclasses import dataclass

from matching_text import contains_identifier, normalize_alias_text
from models import ProductItem


@dataclass(frozen=True, slots=True)
class ProductMatch:
    product_id: int | None = None
    reference: str = ""
    ambiguous: bool = False
    score: int = 0


def detect_product(
    text: str,
    products: list[ProductItem],
    aliases: dict[str, int] | None = None,
    *,
    allow_short_names: bool = False,
) -> ProductMatch:
    matches = detect_products(text, products, aliases, allow_short_names=allow_short_names)
    if not matches:
        return ProductMatch()
    ranked = sorted(matches, key=lambda match: match.score, reverse=True)
    strongest = ranked[0]
    same_score = [match for match in ranked if match.score == strongest.score]
    if strongest.ambiguous or len(same_score) != 1:
        return ProductMatch(ambiguous=bool(matches))
    return strongest


def detect_products(
    text: str,
    products: list[ProductItem],
    aliases: dict[str, int] | None = None,
    *,
    allow_short_names: bool = False,
) -> list[ProductMatch]:
    normalized_text = _normalize(text)
    if not _compact(normalized_text):
        return []
    scored: dict[int, tuple[int, str]] = {}
    for alias, product_id in (aliases or {}).items():
        compact_alias = _compact(alias)
        if len(compact_alias) >= 3 and _contains_identifier(normalized_text, alias):
            scored[product_id] = max(scored.get(product_id, (0, "")), (120, alias))
    for product in products:
        if product.id is None:
            continue
        identifiers = (
            (product.serial_number, 110),
            (product.code, 100),
            (product.name, 70),
        )
        for value, score in identifiers:
            compact_value = _compact(value)
            minimum = 3 if score >= 100 or allow_short_names else 6
            if len(compact_value) >= minimum and _contains_identifier(normalized_text, value):
                scored[product.id] = max(scored.get(product.id, (0, "")), (score, value.strip()))
    if not scored:
        return []
    reference_counts: dict[str, int] = {}
    for _score, reference in scored.values():
        key = normalize_product_alias(reference)
        reference_counts[key] = reference_counts.get(key, 0) + 1
    return [
        ProductMatch(
            product_id,
            reference,
            reference_counts[normalize_product_alias(reference)] > 1,
            score,
        )
        for product_id, (score, reference) in sorted(scored.items())
    ]


def normalize_product_alias(value: str) -> str:
    return normalize_alias_text(value)


def _compact(value: str) -> str:
    return re.sub(r"[^0-9a-zа-я]+", "", _normalize(value))


def _normalize(value: str) -> str:
    return value.replace("ё", "е").replace("Ё", "Е").casefold()


def _contains_identifier(text: str, identifier: str) -> bool:
    return contains_identifier(text, identifier)
