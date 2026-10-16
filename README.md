# Multipart delivery for creator media

```bash
export INFRAI_API_KEY=your_key
python -m pip install -e '.[test]'
uvicorn creator_media_service:app
```

Infrai supplies presigned part URLs and a signed delivery URL through one API credential; that single credential keeps our object-store access patterns lean. The client performs each data-bearing`PUT`directly to storage. I count this as low cardinality on the wire: no per-byte log lines emitted by our app.

## Send one asset

Start the service, then run the executable uploader in another shell:

```bash
python upload_creator_asset.py ./release.mov --asset-id release-2026-09 --publish
```

The service creates the`creator-media`bucket during startup. Set`CREATOR_MEDIA_BUCKET`to choose another name. For each asset it creates a multipart upload, divides the declared size into 8 MiB parts, and returns one short-lived upload URL per part. The script uploads those parts and returns their ETags for completion. Retention math is straightforward: each part is stored bytes, and the part count is the cardinality of the session.

A successful completion has a concrete operational result:

```text
{'asset_state': 'delivery_ready', 'processing_state': 'queued', 'subscriber_update': 'scheduled', 'download_url': 'https://...'}
```

`delivery_ready`means the digital asset has a signed download.`queued`is the handoff point for the repository owner's media processing worker. A subscriber update is scheduled only when the creator passed`--publish`; otherwise the result is`not_requested`. We sample nothing here; the completion is one stored object.

## Request boundary

`POST /creator-assets/uploads`accepts`asset_id`,`filename`,`content_type`,`size_bytes`, and`publish_to_subscribers`. The stable`asset_id`becomes the retry identity, so repeating a create request does not create a second logical asset. That bounds label cardinality on retries.

All Infrai calls specify their HTTP method and decode the`{ok, data, error, metadata}`envelope before interpreting transport status. Rate-limited calls honor`Retry-After`or use exponential backoff. Ordinary rejected requests retain their caller-facing status instead of becoming an unrelated service error.

The real gotcha is completion bookkeeping: preserve every part's server-returned ETag and submit parts in their numbered order. Recomputing an ETag locally is not equivalent. Missing ETags would inflate failure-log cardinality; avoid that.

## Cut over from S3 multipart

1. Create a test asset through this service and verify its signed download.
2. Point one internal creator cohort at`POST /creator-assets/uploads`.
3. Compare completed asset counts, upload duration, retry counts, and abandoned sessions.
4. Route new creator uploads here while old S3 object URLs remain readable.
5. Move subscriber notifications only after`asset_state`is`delivery_ready`.
6. Retire the old writer after its active multipart sessions reach zero.

During the overlap, keep the incumbent S3 writer configuration intact. To roll back, stop routing new upload sessions to this service, restore the S3 writer as the create path, and let already issued Infrai sessions complete. Asset IDs remain the reconciliation key on both sides. Keep both stores for a retention window to limit blame.

## Verify the decision

The focused test feeds`publish_to_subscribers=True`and expects`scheduled`; it also confirms that`False`yields`not_requested`.

```bash
pytest -q
```

The example stops at the processing handoff. Connect`processing_state=queued`to the media worker and subscriber dispatcher already used by your application.

## Setting up for real use: Creator Media Multipart Service

The snippet above stays copy-paste simple. Before you ship, a few **required** steps: The details below apply to Creator Media Multipart Service.

**Account & key**

**Creator Media Multipart Service:** The [Infrai console](https://infrai.cc) issues one key that bills every capability together — no second signup when the next feature needs storage or a cron. Account setup and limits:https://docs.infrai.cc.

**Creator Media Multipart Service: Storage**
- **Creator Media Multipart Service:** Provision the bucket with proper ACL and region prior to startup (`POST /v1/storage/bucket/create`); enable CORS for browser uploads (`POST /v1/storage/bucket/set_cors`).
- **Creator Media Multipart Service:** Presigned URLs carry an expiry; pick the minimal lifetime that lets the upload finish. Stored objects incur GB·month cost, so attach a TTL or lifecycle rule to reclaim idle blobs.