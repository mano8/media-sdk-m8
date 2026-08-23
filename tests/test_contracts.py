"""Tests for media_sdk_m8.contracts job payloads."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from media_sdk_m8 import (
    ExportArchiveEntry,
    ExportArchiveJobPayload,
    OutboxEventPayload,
    ScanJobPayload,
    VariantJobPayload,
    VariantSpec,
)


def test_scan_job_payload_round_trips():
    oid, uid = uuid4(), uuid4()
    payload = ScanJobPayload(
        object_id=oid, bucket="private-media", object_key="k", owner_user_id=uid
    )
    restored = ScanJobPayload.model_validate_json(payload.model_dump_json())
    assert restored == payload
    assert restored.object_id == oid
    assert restored.owner_user_id == uid


def test_scan_job_payload_rejects_blank_bucket():
    with pytest.raises(ValidationError):
        ScanJobPayload(
            object_id=uuid4(), bucket="", object_key="k", owner_user_id=uuid4()
        )


def test_scan_job_payload_rejects_bad_uuid():
    with pytest.raises(ValidationError):
        ScanJobPayload(
            object_id="not-a-uuid",
            bucket="b",
            object_key="k",
            owner_user_id=uuid4(),
        )


def _spec() -> VariantSpec:
    return VariantSpec(
        variant_name="thumb_webp",
        output_options={"name": "thumb_webp", "ext": "WEBP", "quality": 80},
        target_bucket="public-media",
        target_key="p/img/1/variants/thumb_webp/thumb.webp",
    )


def test_variant_spec_round_trips():
    spec = _spec()
    restored = VariantSpec.model_validate_json(spec.model_dump_json())
    assert restored == spec
    assert restored.output_options["ext"] == "WEBP"


def test_variant_spec_rejects_blank_name():
    with pytest.raises(ValidationError):
        VariantSpec(
            variant_name="",
            output_options={},
            target_bucket="b",
            target_key="k",
        )


def test_variant_job_payload_round_trips():
    job = VariantJobPayload(
        job_id=uuid4(),
        media_object_id=uuid4(),
        source_bucket="private-media",
        source_object_key="k",
        specs=[_spec()],
    )
    restored = VariantJobPayload.model_validate_json(job.model_dump_json())
    assert restored == job
    assert len(restored.specs) == 1


def test_variant_job_payload_requires_at_least_one_spec():
    with pytest.raises(ValidationError):
        VariantJobPayload(
            job_id=uuid4(),
            media_object_id=uuid4(),
            source_bucket="b",
            source_object_key="k",
            specs=[],
        )


def test_payloads_are_frozen():
    spec = _spec()
    with pytest.raises(ValidationError):
        spec.variant_name = "other"


def _archive_entry() -> ExportArchiveEntry:
    object_id = uuid4()
    return ExportArchiveEntry(
        object_id=object_id,
        source_bucket="private-media",
        source_object_key=f"users/u/document/{object_id}/original/report.pdf",
        archive_path=f"files/{object_id}/report.pdf",
        size_bytes=1024,
    )


def test_export_archive_job_payload_round_trips():
    payload = ExportArchiveJobPayload(
        job_id=uuid4(),
        manifest_json='{"category_tree":[],"objects":[]}',
        objects=[_archive_entry()],
        target_bucket="temp-media",
        target_object_key="users/u/exports/result.zip",
        stream_chunk_size=1024,
        presigned_expire_seconds=300,
    )

    restored = ExportArchiveJobPayload.model_validate_json(payload.model_dump_json())

    assert restored == payload
    assert restored.objects[0].size_bytes == 1024


@pytest.mark.parametrize(
    "archive_path",
    [
        "/absolute/file.pdf",
        "../file.pdf",
        "files/../file.pdf",
        "files\\file.pdf",
        "C:/absolute/file.pdf",
        "other/object/file.pdf",
    ],
)
def test_export_archive_entry_rejects_unsafe_paths(archive_path: str):
    with pytest.raises(ValidationError):
        ExportArchiveEntry(
            object_id=uuid4(),
            source_bucket="private-media",
            source_object_key="source",
            archive_path=archive_path,
            size_bytes=1,
        )


def test_export_archive_payload_allows_an_empty_collection():
    payload = ExportArchiveJobPayload(
        job_id=uuid4(),
        manifest_json='{"category_tree":[],"objects":[]}',
        target_bucket="temp-media",
        target_object_key="users/u/exports/empty.zip",
        stream_chunk_size=1,
        presigned_expire_seconds=1,
    )

    assert payload.objects == []


def _outbox_event() -> OutboxEventPayload:
    return OutboxEventPayload(
        event_id=uuid4(),
        event_type="object.ready",
        object_id=uuid4(),
        payload={"status": "ready", "size_bytes": 1024},
        created_at=datetime(2026, 6, 15, 12, 0, tzinfo=UTC),
    )


def test_outbox_event_payload_round_trips():
    event = _outbox_event()
    restored = OutboxEventPayload.model_validate_json(event.model_dump_json())
    assert restored == event
    assert restored.event_type == "object.ready"
    assert restored.payload["size_bytes"] == 1024


def test_outbox_event_payload_rejects_blank_event_type():
    with pytest.raises(ValidationError):
        OutboxEventPayload(
            event_id=uuid4(),
            event_type="",
            object_id=uuid4(),
            payload={},
            created_at=datetime.now(UTC),
        )


def test_outbox_event_payload_rejects_bad_uuid():
    with pytest.raises(ValidationError):
        OutboxEventPayload(
            event_id="not-a-uuid",
            event_type="object.ready",
            object_id=uuid4(),
            payload={},
            created_at=datetime.now(UTC),
        )


def test_outbox_event_payload_is_frozen():
    event = _outbox_event()
    with pytest.raises(ValidationError):
        event.event_type = "object.deleted"
