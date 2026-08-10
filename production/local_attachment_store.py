"""Production adapter over the shared atomic content-addressed store."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from infrastructure.content_store import (
    QUARANTINE_DIR,
    TEMP_PREFIX,
    LocalContentAddressedStore,
)
from production.attachment_types import (
    AttachmentDiagnosticIssue,
    AttachmentDiagnosticKind,
    AttachmentDiagnosticsReport,
    AttachmentStorageReference,
    StoredContent,
    StorageVerification,
)
from production.errors import (
    AttachmentIntegrityError,
    AttachmentNotFoundError,
    AttachmentPathError,
    AttachmentRootUnavailableError,
)


class LocalAttachmentStore(LocalContentAddressedStore):
    """Keep the production AttachmentStore contract over shared CAS mechanics."""

    integrity_error = AttachmentIntegrityError
    not_found_error = AttachmentNotFoundError
    path_error = AttachmentPathError
    root_error = AttachmentRootUnavailableError

    def put(self, content: bytes, expected_sha256: str) -> StoredContent:
        stored = super().put(content, expected_sha256)
        return StoredContent(
            stored.storage_key,
            stored.sha256,
            stored.size_bytes,
            stored.deduplicated,
        )

    def verify(self, storage_key: str, expected_sha256: str) -> StorageVerification:
        result = super().verify(storage_key, expected_sha256)
        return StorageVerification(
            result.storage_key,
            result.expected_sha256,
            result.actual_sha256,
            result.exists,
            result.is_valid,
        )

    def diagnostics(
        self,
        references: Iterable[AttachmentStorageReference],
    ) -> AttachmentDiagnosticsReport:
        reference_list = list(references)
        issues: list[AttachmentDiagnosticIssue] = []
        try:
            root = self.readable_root()
        except AttachmentRootUnavailableError as error:
            issues.append(
                AttachmentDiagnosticIssue(
                    AttachmentDiagnosticKind.ROOT_UNAVAILABLE,
                    str(error),
                    resolved_path=self.root,
                )
            )
            return AttachmentDiagnosticsReport(self.root, tuple(issues), len(reference_list), 0)

        referenced_keys: set[str] = set()
        for reference in reference_list:
            try:
                path = self.resolve(reference.storage_key)
                referenced_keys.add(path.relative_to(root).as_posix())
                verification = self.verify(reference.storage_key, reference.sha256)
            except AttachmentPathError as error:
                issues.append(
                    AttachmentDiagnosticIssue(
                        AttachmentDiagnosticKind.INVALID_STORAGE_KEY,
                        str(error),
                        reference.storage_key,
                        reference.attachment_id,
                    )
                )
                continue
            except AttachmentRootUnavailableError as error:
                issues.append(
                    AttachmentDiagnosticIssue(
                        AttachmentDiagnosticKind.ROOT_UNAVAILABLE,
                        str(error),
                        reference.storage_key,
                        reference.attachment_id,
                    )
                )
                continue
            if not verification.exists:
                issues.append(
                    AttachmentDiagnosticIssue(
                        AttachmentDiagnosticKind.MISSING_FILE,
                        "Метаданные ссылаются на отсутствующий физический файл",
                        reference.storage_key,
                        reference.attachment_id,
                        path,
                    )
                )
            elif not verification.is_valid:
                issues.append(
                    AttachmentDiagnosticIssue(
                        AttachmentDiagnosticKind.HASH_MISMATCH,
                        "SHA-256 физического файла не совпадает с метаданными",
                        reference.storage_key,
                        reference.attachment_id,
                        path,
                    )
                )

        checked_files = 0
        try:
            paths = tuple(self.iter_files())
        except AttachmentRootUnavailableError as error:
            issues.append(
                AttachmentDiagnosticIssue(
                    AttachmentDiagnosticKind.ROOT_UNAVAILABLE,
                    str(error),
                    resolved_path=root,
                )
            )
            return AttachmentDiagnosticsReport(root, tuple(issues), len(reference_list), 0)
        for path in paths:
            relative = path.relative_to(root)
            if relative.parts and relative.parts[0] == QUARANTINE_DIR:
                continue
            checked_files += 1
            storage_key = relative.as_posix()
            if path.name.startswith(TEMP_PREFIX):
                issues.append(
                    AttachmentDiagnosticIssue(
                        AttachmentDiagnosticKind.TEMP_FILE,
                        "Обнаружен незавершенный временный файл",
                        storage_key,
                        resolved_path=path,
                    )
                )
            elif storage_key not in referenced_keys:
                issues.append(
                    AttachmentDiagnosticIssue(
                        AttachmentDiagnosticKind.ORPHAN_FILE,
                        "Физический файл не связан ни с одной записью Attachment",
                        storage_key,
                        resolved_path=path,
                    )
                )
        return AttachmentDiagnosticsReport(
            root,
            tuple(issues),
            len(reference_list),
            checked_files,
        )
