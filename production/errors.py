"""Domain errors for production contracts.

These exceptions intentionally have no UI, database or transport semantics.
Application boundaries decide how to present them to a user or API client.
"""

from __future__ import annotations


class ProductionDomainError(ValueError):
    """Base class for invalid production-domain data or transitions."""


class InvalidReadinessError(ProductionDomainError):
    """Raised when readiness is outside the inclusive 0..100 range."""


class ProductRequiredForConfirmationError(ProductionDomainError):
    """Raised when an event is confirmed without a product."""


class InvalidUtcDateTimeError(ProductionDomainError):
    """Raised when a production timestamp is naive or not UTC."""


class CorrectionSourceRequiredError(ProductionDomainError):
    """Raised when a correction does not identify the original event."""


class InvalidLifecycleStateError(ProductionDomainError):
    """Raised when lifecycle data is internally inconsistent."""


class InvalidAttachmentMetadataError(ProductionDomainError):
    """Raised when attachment metadata violates its storage contract."""


class InvalidProductionStageCodeError(ProductionDomainError):
    """Raised when a production-stage machine code is invalid."""


class InvalidProductionStageNameError(ProductionDomainError):
    """Raised when a production-stage display name is invalid."""


class ProductionStageCodeExistsError(ProductionDomainError):
    """Raised when a production-stage machine code is already registered."""


class ProductionStageNotFoundError(ProductionDomainError):
    """Raised when a requested production stage does not exist."""


class AttachmentStorageError(RuntimeError):
    """Base error for attachment filesystem operations."""


class AttachmentPathError(AttachmentStorageError):
    """Raised when a storage key escapes or violates the configured root."""


class AttachmentIntegrityError(AttachmentStorageError):
    """Raised when physical content does not match its expected SHA-256."""


class AttachmentRootUnavailableError(AttachmentStorageError):
    """Raised when the configured attachment root cannot be used."""


class AttachmentSourceExistsError(ProductionDomainError):
    """Raised when one logical source attachment is already registered."""


class AttachmentNotFoundError(ProductionDomainError):
    """Raised when attachment metadata or physical content is missing."""
