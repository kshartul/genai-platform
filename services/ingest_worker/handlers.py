"""Event-driven ingestion entry points. Same pipeline, two clouds.

AWS:   S3 ObjectCreated -> EventBridge -> SQS -> Lambda ``aws_handler`` (container image)
Azure: Blob Created -> Event Grid -> Azure Function ``azure_handler`` (Container Apps job for backfill)
Backfill: Step Functions Map / Durable Functions fan out the same handler over a listing.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from genai_platform.platform import Platform

_platform: Platform | None = None


def _get() -> Platform:
    global _platform
    if _platform is None:  # reuse across warm invocations
        _platform = Platform.from_profile(os.environ["GENAI_PROFILE"], os.environ.get("GENAI_ROOT", "."))
    return _platform


def aws_handler(event, context):
    import boto3

    s3 = boto3.client("s3")
    files = []
    for rec in event.get("Records", []):
        body = json.loads(rec["body"]) if "body" in rec else rec
        detail = body.get("detail", body)
        bucket = detail["bucket"]["name"]
        key = detail["object"]["key"]
        dest = Path(tempfile.mkdtemp()) / Path(key).name
        s3.download_file(bucket, key, str(dest))
        files.append(str(dest))
    stats = _get().pipeline.ingest_paths(files)
    return {"statusCode": 200, "body": json.dumps(stats.__dict__)}


def azure_handler(event: dict) -> dict:
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobClient

    url = event["data"]["url"]
    dest = Path(tempfile.mkdtemp()) / url.rsplit("/", 1)[-1]
    with open(dest, "wb") as fh:
        BlobClient.from_blob_url(url, credential=DefaultAzureCredential()).download_blob().readinto(fh)
    return _get().pipeline.ingest_paths([str(dest)]).__dict__
