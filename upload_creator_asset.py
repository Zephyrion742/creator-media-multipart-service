from __future__ import annotations

import argparse
import mimetypes
from pathlib import Path
from typing import BinaryIO

import httpx

from creator_media_service import PART_BYTES


def put_parts(
    source: BinaryIO, parts: list[dict[str, object]]
) -> list[dict[str, object]]:
    completed: list[dict[str, object]] = []
    with httpx.Client(timeout=120.0) as client:
        for part in parts:
            chunk = source.read(PART_BYTES)
            response = client.request("PUT", str(part["upload_url"]), content=chunk)
            response.raise_for_status()
            completed.append(
                {
                    "part_number": int(part["part_number"]),
                    "etag": response.headers["ETag"],
                }
            )
    return completed


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload one creator asset in parts")
    parser.add_argument("file", type=Path)
    parser.add_argument("--asset-id", required=True)
    parser.add_argument("--service", default="http://127.0.0.1:8000")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()

    content_type = mimetypes.guess_type(args.file.name)[0] or "application/octet-stream"
    with httpx.Client(timeout=30.0) as service:
        started = service.request(
            "POST",
            f"{args.service}/creator-assets/uploads",
            json={
                "asset_id": args.asset_id,
                "filename": args.file.name,
                "content_type": content_type,
                "size_bytes": args.file.stat().st_size,
                "publish_to_subscribers": args.publish,
            },
        )
        started.raise_for_status()
        session = started.json()

        with args.file.open("rb") as source:
            completed = put_parts(source, session["parts"])

        finished = service.request(
            "POST",
            f"{args.service}/creator-assets/uploads/{session['upload_id']}/complete",
            json={
                "object_key": session["object_key"],
                "parts": completed,
                "publish_to_subscribers": args.publish,
            },
        )
        finished.raise_for_status()
        print(finished.json())


if __name__ == "__main__":
    main()
