from __future__ import annotations

import math
import os
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from asset_release_policy import decide_subscriber_update
from infrai_storage import InfraiError, InfraiStorage

PART_BYTES = 8 * 1024 * 1024
BUCKET = os.environ.get("CREATOR_MEDIA_BUCKET", "creator-media")


class CreatorUploadRequest(BaseModel):
    asset_id: str = Field(min_length=1, pattern=r"^[a-zA-Z0-9_-]+$")
    filename: str = Field(min_length=1)
    content_type: str = Field(min_length=1)
    size_bytes: int = Field(gt=0)
    publish_to_subscribers: bool = False


class UploadPart(BaseModel):
    part_number: int
    upload_url: str


class UploadSession(BaseModel):
    upload_id: str
    object_key: str
    part_bytes: int
    parts: list[UploadPart]


class CompletedPart(BaseModel):
    part_number: int = Field(gt=0)
    etag: str = Field(min_length=1)


class CompleteUploadRequest(BaseModel):
    object_key: str
    parts: list[CompletedPart] = Field(min_length=1)
    publish_to_subscribers: bool = False


class AssetDisposition(BaseModel):
    asset_state: Literal["delivery_ready"]
    processing_state: Literal["queued"]
    subscriber_update: Literal["scheduled", "not_requested"]
    download_url: str


def storage_for(request: Request) -> InfraiStorage:
    return request.app.state.storage


@asynccontextmanager
async def lifespan(app: FastAPI):
    storage = InfraiStorage()
    storage.create_bucket(BUCKET)
    app.state.storage = storage
    yield


app = FastAPI(title="Creator media multipart service", lifespan=lifespan)


@app.exception_handler(InfraiError)
async def infrai_error_handler(_request: Request, exc: InfraiError) -> JSONResponse:
    caller_status = exc.http_status if 400 <= exc.http_status < 500 else 502
    return JSONResponse(
        status_code=caller_status,
        content={"error": {"code": exc.code, "message": str(exc)}},
    )


@app.post("/creator-assets/uploads", response_model=UploadSession)
def start_upload(payload: CreatorUploadRequest, request: Request) -> UploadSession:
    storage = storage_for(request)
    object_key = f"assets/{payload.asset_id}/{payload.filename}"
    created = storage.create_multipart(
        BUCKET,
        key=object_key,
        content_type=payload.content_type,
        idempotency_key=f"creator-asset:{payload.asset_id}",
    )
    upload_id = str(created["upload_id"])
    part_count = math.ceil(payload.size_bytes / PART_BYTES)
    parts = [
        UploadPart(
            part_number=number,
            upload_url=str(storage.presign_part(upload_id, number)["url"]),
        )
        for number in range(1, part_count + 1)
    ]
    return UploadSession(
        upload_id=upload_id,
        object_key=object_key,
        part_bytes=PART_BYTES,
        parts=parts,
    )


@app.post(
    "/creator-assets/uploads/{upload_id}/complete",
    response_model=AssetDisposition,
)
def complete_upload(
    upload_id: str, payload: CompleteUploadRequest, request: Request
) -> AssetDisposition:
    storage = storage_for(request)
    storage.complete_multipart(
        upload_id,
        parts=[part.model_dump() for part in payload.parts],
    )
    signed = storage.presign_download(BUCKET, payload.object_key)
    return AssetDisposition(
        asset_state="delivery_ready",
        processing_state="queued",
        subscriber_update=decide_subscriber_update(payload.publish_to_subscribers),
        download_url=str(signed["url"]),
    )


@app.delete("/creator-assets/uploads/{upload_id}")
def cancel_upload(upload_id: str, request: Request) -> dict[str, str]:
    storage_for(request).abort_multipart(upload_id)
    return {"upload_id": upload_id, "state": "aborted"}
