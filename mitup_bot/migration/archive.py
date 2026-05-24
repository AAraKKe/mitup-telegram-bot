"""Streaming gzipped-JSONL S3 writer for tables we don't migrate but want to keep."""

import datetime as dt
import gzip
import io
import json
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlparse

import boto3
from mypy_boto3_s3 import S3Client


class ArchiveWriter:
    """Writes one gzipped JSONL file per table into `s3://bucket/prefix/` (or computes
    sizes without uploading when `dry_run=True`)."""

    def __init__(self, s3_uri: str | None, *, dry_run: bool, s3_client: S3Client | None = None):
        self._s3_uri = s3_uri
        self._dry_run = dry_run
        self._s3 = s3_client

    @staticmethod
    def _default_path(s3_uri: str, table: str) -> str:
        parsed = urlparse(s3_uri)
        if parsed.scheme != "s3" or not parsed.netloc:
            raise ValueError(f"Invalid S3 URI: {s3_uri!r}")
        timestamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
        key_prefix = parsed.path.lstrip("/")
        if key_prefix and not key_prefix.endswith("/"):
            key_prefix += "/"
        key = f"{key_prefix}{timestamp}/{table}.jsonl.gz"
        return f"s3://{parsed.netloc}/{key}"

    def write(self, table: str, rows: Iterable[dict[str, Any]]) -> tuple[int, int]:
        """Stream `rows` as gzipped JSONL; returns (row_count, byte_count)."""
        buffer = io.BytesIO()
        row_count = 0
        with gzip.GzipFile(fileobj=buffer, mode="wb") as gz:
            for row in rows:
                gz.write((json.dumps(row, default=json_default) + "\n").encode())
                row_count += 1
        body = buffer.getvalue()

        if not self._dry_run:
            if self._s3_uri is None:
                raise ValueError("Live archive requires an S3 URI; got None")
            destination = self._default_path(self._s3_uri, table)
            parsed = urlparse(destination)
            client = self._s3 or boto3.client("s3")
            client.put_object(
                Bucket=parsed.netloc,
                Key=parsed.path.lstrip("/"),
                Body=body,
                ContentType="application/gzip",
            )
        return row_count, len(body)


def json_default(value: object) -> object:
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.decode(errors="replace")
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
