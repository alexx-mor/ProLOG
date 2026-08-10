"""Минимальный HTTP-клиент актуального MAX Bot API без внешних зависимостей."""

from __future__ import annotations

import json
import ipaddress
import mimetypes
import re
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlencode, urlparse
from urllib.request import Request, urlopen

from workbot.source_models import DownloadedMedia, MediaUnavailableError


class MaxApiError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, code: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class MaxClient:
    def __init__(self, token: str, api_base: str, timeout: int = 35) -> None:
        self.token = token
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout

    def get_me(self) -> dict[str, Any]:
        return self._request("GET", "/me")

    def get_chat(self, chat_id: int) -> dict[str, Any]:
        return self._request("GET", f"/chats/{chat_id}")

    def get_messages(
        self,
        chat_id: int,
        *,
        count: int = 100,
        before_timestamp: int | None = None,
    ) -> dict[str, Any]:
        query: dict[str, object] = {
            "chat_id": chat_id,
            "count": max(1, min(100, count)),
        }
        if before_timestamp is not None:
            query["from"] = before_timestamp
        return self._request("GET", "/messages", query=query)

    def get_updates(self, marker: int | None, timeout: int = 30) -> dict[str, Any]:
        query: dict[str, object] = {
            "timeout": max(0, min(90, timeout)),
            "limit": 100,
            "types": "message_created,message_edited,message_removed,message_callback",
        }
        if marker is not None:
            query["marker"] = marker
        return self._request("GET", "/updates", query=query, timeout=timeout + 10)

    def download_media(
        self,
        source_url: str | None,
        attachment_type: str,
        source_token: str | None,
    ) -> DownloadedMedia:
        url = source_url
        if not url and attachment_type == "video" and source_token:
            details = self._request("GET", f"/videos/{source_token}")
            urls = details.get("urls") or {}
            if isinstance(urls, dict):
                for key in ("mp4_1080", "mp4_720", "mp4_480", "mp4_360", "mp4_240", "mp4_144", "hls"):
                    if urls.get(key):
                        url = str(urls[key])
                        break
        if not url:
            raise MediaUnavailableError(
                "MAX не предоставил URL для скачивания исходного media"
            )
        return self._download_url(url)

    def send_message(
        self,
        text: str,
        *,
        user_id: int | None = None,
        chat_id: int | None = None,
        attachments: list[dict[str, Any]] | None = None,
        notify: bool = True,
    ) -> dict[str, Any]:
        if (user_id is None) == (chat_id is None):
            raise ValueError("Нужно указать ровно один адрес: user_id или chat_id")
        query = {"user_id": user_id} if user_id is not None else {"chat_id": chat_id}
        body: dict[str, Any] = {"text": text, "notify": notify}
        if attachments:
            body["attachments"] = attachments
        return self._request("POST", "/messages", query=query, body=body)

    def answer_callback(
        self,
        callback_id: str,
        *,
        notification: str | None = None,
        message: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if notification:
            body["notification"] = notification
        if message:
            body["message"] = message
        return self._request(
            "POST",
            "/answers",
            query={"callback_id": callback_id},
            body=body,
        )

    def send_file(self, path: Path, caption: str, *, user_id: int) -> dict[str, Any]:
        token = self.upload_file(path)
        attachment = {"type": "file", "payload": {"token": token}}
        delay = 1.0
        for attempt in range(4):
            try:
                return self.send_message(
                    caption,
                    user_id=user_id,
                    attachments=[attachment],
                    notify=True,
                )
            except MaxApiError as exc:
                if exc.code != "attachment.not.ready" or attempt == 3:
                    raise
                time.sleep(delay)
                delay *= 2
        raise MaxApiError("MAX не завершил обработку файла")

    def upload_file(self, path: Path) -> str:
        upload_info = self._request("POST", "/uploads", query={"type": "file"})
        upload_url = str(upload_info.get("url", ""))
        if not upload_url:
            raise MaxApiError("MAX не вернул URL для загрузки файла")
        response = self._multipart_upload(upload_url, path)
        token = response.get("token") or upload_info.get("token")
        if not token:
            raise MaxApiError("MAX не вернул токен загруженного файла")
        return str(token)

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, object] | None = None,
        body: dict[str, Any] | None = None,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        url = f"{self.api_base}{path}"
        if query:
            clean_query = {key: value for key, value in query.items() if value is not None}
            url += "?" + urlencode(clean_query)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        headers = {"Authorization": self.token, "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = Request(url, data=data, headers=headers, method=method)
        return self._open_json(request, timeout or self.timeout)

    def _multipart_upload(self, url: str, path: Path) -> dict[str, Any]:
        boundary = f"----WorkBot{uuid.uuid4().hex}"
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body = bytearray()
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(
            (
                f'Content-Disposition: form-data; name="data"; filename="{path.name}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode("utf-8")
        )
        body.extend(path.read_bytes())
        body.extend(f"\r\n--{boundary}--\r\n".encode())
        request = Request(
            url,
            data=bytes(body),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        return self._open_json(request, max(self.timeout, 60))

    def _download_url(self, url: str) -> DownloadedMedia:
        parsed = urlparse(url)
        if parsed.scheme.casefold() != "https" or not parsed.hostname:
            raise MediaUnavailableError("Разрешена загрузка media только по HTTPS")
        hostname = parsed.hostname.casefold()
        if hostname == "localhost" or hostname.endswith(".localhost"):
            raise MediaUnavailableError("Небезопасный адрес media")
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            pass
        else:
            if not address.is_global:
                raise MediaUnavailableError("Небезопасный адрес media")
        request = Request(
            url,
            headers={"Accept": "*/*", "User-Agent": "ProLOG-WorkBot/0.6"},
            method="GET",
        )
        try:
            with urlopen(request, timeout=max(self.timeout, 60)) as response:
                content = response.read()
                content_type = str(response.headers.get_content_type() or "")
                disposition = str(response.headers.get("Content-Disposition") or "")
                final_url = str(response.geturl() or url)
        except HTTPError as exc:
            if exc.code in {404, 410}:
                raise MediaUnavailableError(f"MAX media недоступно: HTTP {exc.code}") from exc
            raise MaxApiError(f"Не удалось скачать MAX media: HTTP {exc.code}", status=exc.code) from exc
        except URLError as exc:
            raise MaxApiError(f"Не удалось скачать MAX media: {exc.reason}") from exc
        filename = _response_filename(disposition, final_url)
        return DownloadedMedia(content, content_type, filename)

    @staticmethod
    def _open_json(request: Request, timeout: int) -> dict[str, Any]:
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = response.read()
        except HTTPError as exc:
            payload = exc.read()
            try:
                error = json.loads(payload.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                error = {}
            message = str(error.get("message") or f"MAX API вернул HTTP {exc.code}")
            raise MaxApiError(message, status=exc.code, code=str(error.get("code", ""))) from exc
        except URLError as exc:
            raise MaxApiError(f"Не удалось подключиться к MAX API: {exc.reason}") from exc
        if not payload:
            return {}
        try:
            result = json.loads(payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise MaxApiError("MAX API вернул некорректный JSON") from exc
        if not isinstance(result, dict):
            raise MaxApiError("MAX API вернул неожиданный формат ответа")
        return result


def _response_filename(content_disposition: str, url: str) -> str:
    extended = re.search(r"filename\*=UTF-8''([^;]+)", content_disposition, re.IGNORECASE)
    if extended:
        return Path(unquote(extended.group(1))).name
    regular = re.search(r'filename="?([^";]+)', content_disposition, re.IGNORECASE)
    if regular:
        return Path(regular.group(1).strip()).name
    return Path(unquote(urlparse(url).path)).name
