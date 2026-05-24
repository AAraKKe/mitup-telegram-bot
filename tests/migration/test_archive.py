import datetime as dt
import gzip
import json
from unittest.mock import MagicMock

import pytest

from mitup_bot.migration.archive import ArchiveWriter


def test_dry_run_does_not_call_s3_but_returns_counts():
    fake_s3 = MagicMock()
    writer = ArchiveWriter(s3_uri="s3://example/prefix/", dry_run=True, s3_client=fake_s3)
    rows = [{"id": 1, "x": "a"}, {"id": 2, "x": "b"}]
    n, byte_count = writer.write("payments", iter(rows))
    assert n == 2
    assert byte_count > 0
    fake_s3.put_object.assert_not_called()


def test_live_run_uploads_gzipped_jsonl():
    fake_s3 = MagicMock()
    writer = ArchiveWriter(s3_uri="s3://bucket/prefix/", dry_run=False, s3_client=fake_s3)
    rows = [{"id": 1, "msg": "hello"}, {"id": 2, "msg": "world"}]

    n, _ = writer.write("support_messages", iter(rows))

    assert n == 2
    fake_s3.put_object.assert_called_once()
    call_kwargs = fake_s3.put_object.call_args.kwargs
    assert call_kwargs["Bucket"] == "bucket"
    assert call_kwargs["Key"].startswith("prefix/")
    assert call_kwargs["Key"].endswith("/support_messages.jsonl.gz")

    decoded = gzip.decompress(call_kwargs["Body"]).decode().splitlines()
    assert [json.loads(line) for line in decoded] == rows


def test_datetime_values_are_serialized_as_iso_strings():
    fake_s3 = MagicMock()
    writer = ArchiveWriter(s3_uri="s3://bucket/", dry_run=False, s3_client=fake_s3)
    when = dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.UTC)
    writer.write("chats", iter([{"id": 1, "created_at": when}]))
    body = gzip.decompress(fake_s3.put_object.call_args.kwargs["Body"]).decode()
    lines = body.splitlines()
    assert len(lines) == 1  # single row written → single JSONL line
    parsed = json.loads(lines[0])
    assert parsed["created_at"] == when.isoformat()


def test_live_run_without_s3_uri_raises_value_error():
    writer = ArchiveWriter(s3_uri=None, dry_run=False, s3_client=MagicMock())
    with pytest.raises(ValueError, match="S3 URI"):
        writer.write("payments", iter([{"id": 1}]))
