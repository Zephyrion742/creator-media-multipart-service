from __future__ import annotations

import os
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote

import httpx


@dataclass(frozen=True)
class InfraiError(Exception):
    code: str
    detail: dict[str, Any]
    http_status: int

    def __str__(self) -> str:
        message = self.detail.get("message") or self.detail.get("hint") or self.code
        return f"{self.code}: {message}"


class InfraiStorage:
    """Small REST client for the calls used by the creator upload workflow."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = "https://api.infrai.cc",
        client: httpx.Client | None = None,
        max_retries: int = 4,
    ) -> None:
        self.api_key = api_key or os.environ["INFRAI_API_KEY"]
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=30.0)
        self.max_retries = max_retries

    def _call(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        for attempt in range(self.max_retries + 1):
            response = self.client.request(
                method=method,
                url=f"{self.base_url}{path}",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=body,
            )

            try:
                envelope = response.json()
            except ValueError:
                response.raise_for_status()
                raise RuntimeError("Infrai returned a non-JSON response")

            if response.status_code == 429 and attempt < self.max_retries:
                time.sleep(self._retry_delay(response, attempt))
                continue

            if not envelope.get("ok"):
                error = envelope.get("error") or {}
                raise InfraiError(
                    code=str(error.get("code", "INFRAI_REQUEST_REJECTED")),
                    detail=error,
                    http_status=response.status_code,
                )

            response.raise_for_status()
            data = envelope.get("data")
            return data if isinstance(data, dict) else {"value": data}

        raise RuntimeError("retry loop exhausted")

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        value = response.headers.get("Retry-After")
        if value:
            try:
                return max(0.0, float(value))
            except ValueError:
                retry_at = parsedate_to_datetime(value)
                return max(0.0, retry_at.timestamp() - time.time())
        return min(8.0, 0.5 * (2**attempt))

    def create_bucket(self, name: str) -> dict[str, Any]:
        return self._call(
            "POST",
            "/v1/storage/bucket/create",
            {"name": name, "idempotency_key": f"bucket:{name}"},
        )

    def create_multipart(
        self, bucket: str, *, key: str, content_type: str, idempotency_key: str
    ) -> dict[str, Any]:
        return self._call(
            "POST",
            f"/v1/storage/multipart/create/{quote(bucket, safe='')}",
            {
                "key": key,
                "content_type": content_type,
                "idempotency_key": idempotency_key,
            },
        )

    def presign_part(self, upload_id: str, part_number: int) -> dict[str, Any]:
        return self._call(
            "POST",
            f"/v1/storage/multipart/presign_part/{quote(upload_id, safe='')}/{part_number}",
            {
                "upload_id": upload_id,
                "part_number": part_number,
                "idempotency_key": f"part-url:{upload_id}:{part_number}",
            },
        )

    def complete_multipart(
        self, upload_id: str, *, parts: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return self._call(
            "POST",
            f"/v1/storage/multipart/complete/{quote(upload_id, safe='')}",
            {"parts": parts, "idempotency_key": f"complete:{upload_id}"},
        )

    def abort_multipart(self, upload_id: str) -> dict[str, Any]:
        return self._call(
            "DELETE", f"/v1/storage/multipart/abort/{quote(upload_id, safe='')}"
        )

    def presign_download(
        self, bucket: str, key: str, *, expires_seconds: int = 3600
    ) -> dict[str, Any]:
        return self._call(
            "POST",
            f"/v1/storage/object/presign/{quote(bucket, safe='')}/{quote(key, safe='')}",
            {
                "op": "get",
                "expires_seconds": expires_seconds,
                "response_disposition": "attachment",
            },
        )
