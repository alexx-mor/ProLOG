"""Atomic content-addressed attachment storage on a local or mounted path."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import BinaryIO, Iterable

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


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TEMP_PREFIX = ".tmp-"
_QUARANTINE_DIR = "quarantine"


class LocalAttachmentStore:
    """Store immutable original bytes under deterministic SHA-256 keys."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def put(self, content: bytes, expected_sha256: str) -> StoredContent:
        expected = _normalize_sha256(expected_sha256)
        actual = hashlib.sha256(content).hexdigest()
        if actual != expected:
            raise AttachmentIntegrityError(
                "Хэш переданного содержимого не совпадает с ожидаемым SHA-256"
            )
        root = self._ensure_root()
        storage_key = self.storage_key_for_sha256(expected)
        target = self.resolve(storage_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            self._require_existing_hash(target, expected, storage_key)
            return StoredContent(storage_key, expected, len(content), True)

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=_TEMP_PREFIX,
                suffix=".partial",
                dir=target.parent,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
            if _hash_file(temporary_path) != expected:
                raise AttachmentIntegrityError("Временный файл поврежден при записи")
            if target.exists():
                self._require_existing_hash(target, expected, storage_key)
                temporary_path.unlink(missing_ok=True)
                return StoredContent(storage_key, expected, len(content), True)
            os.replace(temporary_path, target)
            temporary_path = None
            self._require_existing_hash(target, expected, storage_key)
            return StoredContent(storage_key, expected, len(content), False)
        except OSError as error:
            raise AttachmentRootUnavailableError(
                f"Не удалось записать вложение в каталог {root}: {error}"
            ) from error
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def exists(self, storage_key: str) -> bool:
        return self.resolve(storage_key).is_file()

    def open(self, storage_key: str) -> BinaryIO:
        path = self.resolve(storage_key)
        try:
            return path.open("rb")
        except FileNotFoundError as error:
            raise AttachmentNotFoundError(f"Файл вложения не найден: {storage_key}") from error
        except OSError as error:
            raise AttachmentRootUnavailableError(
                f"Не удалось открыть файл вложения {storage_key}: {error}"
            ) from error

    def read(self, storage_key: str) -> bytes:
        with self.open(storage_key) as source:
            return source.read()

    def verify(self, storage_key: str, expected_sha256: str) -> StorageVerification:
        expected = _normalize_sha256(expected_sha256)
        try:
            path = self.resolve(storage_key)
        except AttachmentPathError:
            raise
        if not path.is_file():
            return StorageVerification(storage_key, expected, None, False, False)
        try:
            actual = _hash_file(path)
        except OSError as error:
            raise AttachmentRootUnavailableError(
                f"Не удалось проверить файл вложения {storage_key}: {error}"
            ) from error
        return StorageVerification(storage_key, expected, actual, True, actual == expected)

    def delete(self, storage_key: str) -> None:
        path = self.resolve(storage_key)
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            raise AttachmentRootUnavailableError(
                f"Не удалось удалить файл вложения {storage_key}: {error}"
            ) from error

    def quarantine(self, storage_key: str) -> str:
        source = self.resolve(storage_key)
        if not source.is_file():
            raise AttachmentNotFoundError(f"Файл вложения не найден: {storage_key}")
        root = self._ensure_root()
        quarantine = root / _QUARANTINE_DIR
        quarantine.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = quarantine / f"{timestamp}-{source.name}"
        counter = 1
        while target.exists():
            target = quarantine / f"{timestamp}-{counter}-{source.name}"
            counter += 1
        try:
            os.replace(source, target)
        except OSError as error:
            raise AttachmentRootUnavailableError(
                f"Не удалось поместить файл в карантин: {error}"
            ) from error
        return target.relative_to(root).as_posix()

    def resolve(self, storage_key: str) -> Path:
        parts = _validate_storage_key(storage_key)
        root = self._resolved_root()
        candidate = root.joinpath(*parts)
        try:
            resolved = candidate.resolve(strict=False)
        except OSError as error:
            raise AttachmentPathError(f"Не удалось разрешить storage_key: {error}") from error
        if not resolved.is_relative_to(root):
            raise AttachmentPathError("storage_key выходит за пределы attachment root")
        return resolved

    def diagnostics(
        self,
        references: Iterable[AttachmentStorageReference],
    ) -> AttachmentDiagnosticsReport:
        reference_list = list(references)
        issues: list[AttachmentDiagnosticIssue] = []
        try:
            root = self._require_readable_root()
        except AttachmentRootUnavailableError as error:
            issues.append(
                AttachmentDiagnosticIssue(
                    AttachmentDiagnosticKind.ROOT_UNAVAILABLE,
                    str(error),
                    resolved_path=self._root,
                )
            )
            return AttachmentDiagnosticsReport(self._root, tuple(issues), len(reference_list), 0)

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
            paths = tuple(root.rglob("*"))
        except OSError as error:
            issues.append(
                AttachmentDiagnosticIssue(
                    AttachmentDiagnosticKind.ROOT_UNAVAILABLE,
                    f"Не удалось просканировать attachment root: {error}",
                    resolved_path=root,
                )
            )
            return AttachmentDiagnosticsReport(root, tuple(issues), len(reference_list), 0)
        for path in paths:
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            if relative.parts and relative.parts[0] == _QUARANTINE_DIR:
                continue
            checked_files += 1
            storage_key = relative.as_posix()
            if path.name.startswith(_TEMP_PREFIX):
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

    @staticmethod
    def storage_key_for_sha256(sha256: str) -> str:
        value = _normalize_sha256(sha256)
        return f"{value[:2]}/{value[2:4]}/{value}"

    def _ensure_root(self) -> Path:
        try:
            if self._root.exists() and not self._root.is_dir():
                raise AttachmentRootUnavailableError(
                    f"Attachment root не является каталогом: {self._root}"
                )
            self._root.mkdir(parents=True, exist_ok=True)
            return self._require_readable_root()
        except AttachmentRootUnavailableError:
            raise
        except OSError as error:
            raise AttachmentRootUnavailableError(
                f"Attachment root недоступен: {self._root}: {error}"
            ) from error

    def _require_readable_root(self) -> Path:
        try:
            if not self._root.is_dir():
                raise AttachmentRootUnavailableError(
                    f"Attachment root недоступен: {self._root}"
                )
            root = self._resolved_root()
            next(root.iterdir(), None)
            return root
        except AttachmentRootUnavailableError:
            raise
        except OSError as error:
            raise AttachmentRootUnavailableError(
                f"Attachment root недоступен: {self._root}: {error}"
            ) from error

    def _resolved_root(self) -> Path:
        try:
            return self._root.resolve(strict=False)
        except OSError as error:
            raise AttachmentRootUnavailableError(
                f"Не удалось разрешить attachment root {self._root}: {error}"
            ) from error

    @staticmethod
    def _require_existing_hash(path: Path, expected: str, storage_key: str) -> None:
        try:
            actual = _hash_file(path)
        except OSError as error:
            raise AttachmentRootUnavailableError(
                f"Не удалось прочитать существующий файл {storage_key}: {error}"
            ) from error
        if actual != expected:
            raise AttachmentIntegrityError(
                "Существующий файл по ожидаемому storage_key имеет другой SHA-256"
            )


def _validate_storage_key(storage_key: str) -> tuple[str, ...]:
    value = storage_key.strip()
    windows = PureWindowsPath(value)
    posix = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or ":" in value
        or windows.is_absolute()
        or bool(windows.drive)
        or posix.is_absolute()
        or ".." in posix.parts
        or any(part in {"", "."} for part in posix.parts)
    ):
        raise AttachmentPathError("Некорректный или небезопасный storage_key")
    return posix.parts


def _normalize_sha256(value: str) -> str:
    normalized = value.strip().lower()
    if _SHA256_RE.fullmatch(normalized) is None:
        raise AttachmentIntegrityError("Ожидался SHA-256 из 64 шестнадцатеричных символов")
    return normalized


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
