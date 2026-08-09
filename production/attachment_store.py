"""Filesystem-neutral contract for physical attachment content."""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO, Iterable, Protocol

from production.attachment_types import (
    AttachmentDiagnosticsReport,
    AttachmentStorageReference,
    StoredContent,
    StorageVerification,
)


class AttachmentStore(Protocol):
    @property
    def root(self) -> Path: ...

    def put(self, content: bytes, expected_sha256: str) -> StoredContent: ...

    def exists(self, storage_key: str) -> bool: ...

    def open(self, storage_key: str) -> BinaryIO: ...

    def read(self, storage_key: str) -> bytes: ...

    def verify(self, storage_key: str, expected_sha256: str) -> StorageVerification: ...

    def delete(self, storage_key: str) -> None: ...

    def quarantine(self, storage_key: str) -> str: ...

    def resolve(self, storage_key: str) -> Path: ...

    def diagnostics(
        self,
        references: Iterable[AttachmentStorageReference],
    ) -> AttachmentDiagnosticsReport: ...
