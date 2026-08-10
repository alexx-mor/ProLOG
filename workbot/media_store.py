"""WorkBot-specific name for the shared content-addressed media store."""
from __future__ import annotations

from infrastructure.content_store import LocalContentAddressedStore


class WorkBotMediaStore(LocalContentAddressedStore):
    """Physical source-media area, separate from production attachments."""
