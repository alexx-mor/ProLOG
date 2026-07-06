"""GitHub Releases update checker."""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from constants import APP_VERSION, GITHUB_OWNER, GITHUB_REPO

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class UpdateInfo:
    latest_version: str
    release_url: str
    is_newer: bool


class UpdateChecker:
    def __init__(self, current_version: str = APP_VERSION) -> None:
        self.current_version = current_version

    def check(self) -> UpdateInfo | None:
        url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
        request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            logger.warning("Update check failed: %s", exc)
            return None
        latest = str(payload.get("tag_name") or payload.get("name") or "").lstrip("v")
        return UpdateInfo(
            latest_version=latest,
            release_url=str(payload.get("html_url") or ""),
            is_newer=_version_tuple(latest) > _version_tuple(self.current_version),
        )


def _version_tuple(version: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", version)
    return tuple(int(part) for part in numbers) if numbers else (0,)

