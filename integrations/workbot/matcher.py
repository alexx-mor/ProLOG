"""Deterministic matching of product references in WorkBot messages."""

from __future__ import annotations

import re
from dataclasses import dataclass

from models import ProductItem


@dataclass(frozen=True, slots=True)
class ProductMatch:
    product_id: int | None = None
    reference: str = ""
    ambiguous: bool = False


def detect_product(
    text: str,
    products: list[ProductItem],
    aliases: dict[str, int] | None = None,
) -> ProductMatch:
    normalized_text = _normalize(text)
    if not _compact(normalized_text):
        return ProductMatch()
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
            minimum = 3 if score >= 100 else 6
            if len(compact_value) >= minimum and _contains_identifier(normalized_text, value):
                scored[product.id] = max(scored.get(product.id, (0, "")), (score, value.strip()))
    if not scored:
        return ProductMatch()
    best_score = max(score for score, _reference in scored.values())
    best = [product_id for product_id, (score, _reference) in scored.items() if score == best_score]
    if len(best) != 1:
        return ProductMatch(ambiguous=True)
    product_id = best[0]
    return ProductMatch(product_id, scored[product_id][1], False)


def normalize_product_alias(value: str) -> str:
    return " ".join(value.replace("ё", "е").replace("Ё", "Е").casefold().split())


def _compact(value: str) -> str:
    return re.sub(r"[^0-9a-zа-я]+", "", _normalize(value))


def _normalize(value: str) -> str:
    return value.replace("ё", "е").replace("Ё", "Е").casefold()


def _contains_identifier(text: str, identifier: str) -> bool:
    tokens = re.findall(r"[0-9a-zа-я]+", _normalize(identifier))
    if not tokens:
        return False
    body = r"[^0-9a-zа-я]*".join(re.escape(token) for token in tokens)
    if all(token.isdigit() for token in tokens):
        pattern = rf"(?<!\d){body}(?!\d)"
    else:
        pattern = rf"(?<![0-9a-zа-я]){body}(?![0-9a-zа-я])"
    return re.search(pattern, text) is not None
