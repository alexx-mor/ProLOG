"""Atomic local content-addressed storage shared by independent domains."""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import BinaryIO, Iterator


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TEMP_PREFIX = ".tmp-"
QUARANTINE_DIR = "quarantine"


class ContentStoreError(RuntimeError):
    pass


class ContentIntegrityError(ContentStoreError):
    pass


class ContentNotFoundError(ContentStoreError):
    pass


class ContentPathError(ContentStoreError):
    pass


class ContentRootUnavailableError(ContentStoreError):
    pass


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


class LocalContentAddressedStore:
    """Store immutable bytes below a configurable root using SHA-256 keys."""

    integrity_error = ContentIntegrityError
    not_found_error = ContentNotFoundError
    path_error = ContentPathError
    root_error = ContentRootUnavailableError

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def put(self, content: bytes, expected_sha256: str) -> StoredContent:
        expected = self._normalize_sha256(expected_sha256)
        actual = hashlib.sha256(content).hexdigest()
        if actual != expected:
            raise self.integrity_error(
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
                prefix=TEMP_PREFIX,
                suffix=".partial",
                dir=target.parent,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
            if hash_file(temporary_path) != expected:
                raise self.integrity_error("Временный файл поврежден при записи")
            try:
                os.link(temporary_path, target)
            except FileExistsError:
                self._require_existing_hash(target, expected, storage_key)
                return StoredContent(storage_key, expected, len(content), True)
            temporary_path.unlink()
            temporary_path = None
            self._require_existing_hash(target, expected, storage_key)
            return StoredContent(storage_key, expected, len(content), False)
        except self.integrity_error:
            raise
        except OSError as error:
            raise self.root_error(
                f"Не удалось записать содержимое в каталог {root}: {error}"
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
            raise self.not_found_error(f"Файл не найден: {storage_key}") from error
        except OSError as error:
            raise self.root_error(f"Не удалось открыть файл {storage_key}: {error}") from error

    def read(self, storage_key: str) -> bytes:
        with self.open(storage_key) as source:
            return source.read()

    def verify(self, storage_key: str, expected_sha256: str) -> StorageVerification:
        expected = self._normalize_sha256(expected_sha256)
        path = self.resolve(storage_key)
        if not path.is_file():
            return StorageVerification(storage_key, expected, None, False, False)
        try:
            actual = hash_file(path)
        except OSError as error:
            raise self.root_error(
                f"Не удалось проверить файл {storage_key}: {error}"
            ) from error
        return StorageVerification(storage_key, expected, actual, True, actual == expected)

    def delete(self, storage_key: str) -> None:
        path = self.resolve(storage_key)
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            raise self.root_error(f"Не удалось удалить файл {storage_key}: {error}") from error

    def quarantine(self, storage_key: str) -> str:
        source = self.resolve(storage_key)
        if not source.is_file():
            raise self.not_found_error(f"Файл не найден: {storage_key}")
        root = self.ensure_root()
        quarantine = root / QUARANTINE_DIR
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
            raise self.root_error(f"Не удалось поместить файл в карантин: {error}") from error
        return target.relative_to(root).as_posix()

    def resolve(self, storage_key: str) -> Path:
        parts = self._validate_storage_key(storage_key)
        root = self._resolved_root()
        candidate = root.joinpath(*parts)
        try:
            resolved = candidate.resolve(strict=False)
        except OSError as error:
            raise self.path_error(f"Не удалось разрешить storage_key: {error}") from error
        if not resolved.is_relative_to(root):
            raise self.path_error("storage_key выходит за пределы корневого каталога")
        return resolved

    def ensure_root(self) -> Path:
        return self._ensure_root()

    def readable_root(self) -> Path:
        return self._require_readable_root()

    def iter_files(self) -> Iterator[Path]:
        root = self._require_readable_root()
        try:
            for path in root.rglob("*"):
                if path.is_file():
                    yield path
        except OSError as error:
            raise self.root_error(f"Не удалось просканировать каталог: {error}") from error

    @classmethod
    def storage_key_for_sha256(cls, sha256: str) -> str:
        value = cls._normalize_sha256(sha256)
        return f"{value[:2]}/{value[2:4]}/{value}"

    def _ensure_root(self) -> Path:
        try:
            if self._root.exists() and not self._root.is_dir():
                raise self.root_error(f"Корневой путь не является каталогом: {self._root}")
            self._root.mkdir(parents=True, exist_ok=True)
            return self._require_readable_root()
        except self.root_error:
            raise
        except OSError as error:
            raise self.root_error(f"Корневой каталог недоступен: {self._root}: {error}") from error

    def _require_readable_root(self) -> Path:
        try:
            if not self._root.is_dir():
                raise self.root_error(f"Корневой каталог недоступен: {self._root}")
            root = self._resolved_root()
            next(root.iterdir(), None)
            return root
        except self.root_error:
            raise
        except OSError as error:
            raise self.root_error(f"Корневой каталог недоступен: {self._root}: {error}") from error

    def _resolved_root(self) -> Path:
        try:
            return self._root.resolve(strict=False)
        except OSError as error:
            raise self.root_error(f"Не удалось разрешить корневой каталог: {error}") from error

    def _require_existing_hash(self, path: Path, expected: str, storage_key: str) -> None:
        try:
            actual = hash_file(path)
        except OSError as error:
            raise self.root_error(
                f"Не удалось прочитать существующий файл {storage_key}: {error}"
            ) from error
        if actual != expected:
            raise self.integrity_error(
                "Существующий файл по ожидаемому storage_key имеет другой SHA-256"
            )

    @classmethod
    def _validate_storage_key(cls, storage_key: str) -> tuple[str, ...]:
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
            raise cls.path_error("Некорректный или небезопасный storage_key")
        return posix.parts

    @classmethod
    def _normalize_sha256(cls, value: str) -> str:
        normalized = value.strip().lower()
        if SHA256_RE.fullmatch(normalized) is None:
            raise cls.integrity_error(
                "Ожидался SHA-256 из 64 шестнадцатеричных символов"
            )
        return normalized


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
