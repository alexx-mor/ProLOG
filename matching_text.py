"""Domain-neutral deterministic text normalization for identifier matching."""

from __future__ import annotations

import re


_DASH_RE = re.compile(r"[‐‑‒–—−]")
_SPACE_RE = re.compile(r"\s+")


def normalize_match_text(value: str) -> str:
    """Normalize presentation variants without changing the stored source text."""

    normalized = value.replace("ё", "е").replace("Ё", "Е").casefold()
    normalized = _DASH_RE.sub("-", normalized)
    normalized = re.sub(r"\b(?:no|n)\s*[.]?\s*(?=\d)", "№", normalized)
    normalized = re.sub(r"№\s+", "№", normalized)
    normalized = re.sub(r"\s*-\s*", "-", normalized)
    normalized = re.sub(r"(\d)\s+%", r"\1%", normalized)
    return _SPACE_RE.sub(" ", normalized).strip()


def normalize_alias_text(value: str) -> str:
    return normalize_match_text(value)


def identifier_tokens(value: str) -> tuple[str, ...]:
    return tuple(
        part
        for token in re.findall(r"[0-9a-zа-я]+", normalize_match_text(value))
        for part in re.findall(r"[a-zа-я]+|\d+", token)
    )


def identifier_pattern(identifier: str) -> re.Pattern[str] | None:
    tokens = identifier_tokens(identifier)
    if not tokens:
        return None
    body = r"[^0-9a-zа-я]*".join(re.escape(token) for token in tokens)
    if all(token.isdigit() for token in tokens):
        pattern = rf"(?<!\d){body}(?!\d)"
    else:
        pattern = rf"(?<![0-9a-zа-я]){body}(?![0-9a-zа-я])"
    return re.compile(pattern)


def contains_identifier(text: str, identifier: str) -> bool:
    pattern = identifier_pattern(identifier)
    return bool(pattern and pattern.search(normalize_match_text(text)))


def find_identifier(text: str, identifier: str) -> str:
    pattern = identifier_pattern(identifier)
    if pattern is None:
        return ""
    match = pattern.search(normalize_match_text(text))
    return match.group(0) if match else ""


def find_identifier_span(text: str, identifier: str) -> tuple[int, int] | None:
    """Return a span in normalized text for overlap-aware candidate matching."""

    pattern = identifier_pattern(identifier)
    if pattern is None:
        return None
    match = pattern.search(normalize_match_text(text))
    return match.span() if match else None
