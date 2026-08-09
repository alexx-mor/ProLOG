"""Structured contracts shared by attachment storage and diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class AttachmentDiagnosticKind(StrEnum):
    ROOT_UNAVAILABLE = "root_unavailable"
    INVALID_STORAGE_KEY = "invalid_storage_key"
    MISSING_FILE = "missing_file"
    HASH_MISMATCH = "hash_mismatch"
    ORPHAN_FILE = "orphan_file"
    TEMP_FILE = "temp_file"


@dataclass(frozen=True, slots=True)
class StoredContent:
    storage_key: str
    sha256: str
    size_bytes: int
    deduplicated: bool


@dataclass(frozen=True, slots=True)
class StorageVerification:
    storage_key: str
    expected_sha256: str
    actual_sha256: str | None
    exists: bool
    is_valid: bool


@dataclass(frozen=True, slots=True)
class AttachmentStorageReference:
    attachment_id: int | None
    storage_key: str
    sha256: str


@dataclass(frozen=True, slots=True)
class AttachmentDiagnosticIssue:
    kind: AttachmentDiagnosticKind
    message: str
    storage_key: str = ""
    attachment_id: int | None = None
    resolved_path: Path | None = None


@dataclass(frozen=True, slots=True)
class AttachmentDiagnosticsReport:
    root: Path
    issues: tuple[AttachmentDiagnosticIssue, ...]
    checked_attachments: int = 0
    checked_files: int = 0

    @property
    def is_healthy(self) -> bool:
        return not self.issues

    def count(self, kind: AttachmentDiagnosticKind) -> int:
        return sum(issue.kind is kind for issue in self.issues)
