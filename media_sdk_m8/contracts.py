"""
Producer↔consumer job contracts shared between media-service and the worker.

Self-contained Pydantic v2 models with no database, preset, or imgtools
knowledge. media-service-m8 builds these payloads and enqueues them; the worker
deserializes and acts on them — so the worker needs no preset/key awareness.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ScanJobPayload(BaseModel):
    """Antivirus-scan job: locate the uploaded object and scan its bytes."""

    model_config = ConfigDict(frozen=True)

    object_id: UUID
    bucket: str = Field(min_length=1)
    object_key: str = Field(min_length=1)
    owner_user_id: UUID


class VariantSpec(BaseModel):
    """
    A single image-variant unit of work.

    ``output_options`` is the imgtools-shaped dict (one format plus a ``name``)
    built by media-service, so the worker writes the result to
    ``target_bucket``/``target_key`` without any preset or key knowledge.
    """

    model_config = ConfigDict(frozen=True)

    variant_name: str = Field(min_length=1)
    output_options: dict[str, Any]
    target_bucket: str = Field(min_length=1)
    target_key: str = Field(min_length=1)


class VariantJobPayload(BaseModel):
    """Image-variant job: render every spec from one source object."""

    model_config = ConfigDict(frozen=True)

    job_id: UUID
    media_object_id: UUID
    source_bucket: str = Field(min_length=1)
    source_object_key: str = Field(min_length=1)
    specs: list[VariantSpec] = Field(min_length=1)


class ExportArchiveEntry(BaseModel):
    """One storage object embedded in an exported collection archive."""

    model_config = ConfigDict(frozen=True)

    object_id: UUID
    source_bucket: str = Field(min_length=1)
    source_object_key: str = Field(min_length=1)
    archive_path: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)

    @field_validator("archive_path")
    @classmethod
    def _archive_path_is_safe(cls, value: str) -> str:
        """Reject absolute, ambiguous, or traversal-capable ZIP entry paths."""
        parts = value.split("/")
        if (
            value.startswith("/")
            or "\\" in value
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise ValueError("archive_path must be a safe relative POSIX path")
        return value

    @model_validator(mode="after")
    def _archive_path_matches_object(self) -> "ExportArchiveEntry":
        """Keep every entry in its immutable ``files/<object-id>/`` namespace."""
        parts = self.archive_path.split("/")
        if len(parts) != 3 or parts[:2] != ["files", str(self.object_id)]:
            raise ValueError("archive_path must match files/<object_id>/<filename>")
        return self


class ExportArchiveJobPayload(BaseModel):
    """
    Archive-export job resolved by media-service and run by media-worker.

    The producer performs every authorization and database query before enqueueing
    this immutable snapshot. The DB-free worker only reads the listed storage
    objects, writes the supplied manifest, uploads the finished ZIP, presigns it,
    and reports the result through media-service's internal callback surface.
    """

    model_config = ConfigDict(frozen=True)

    job_id: UUID
    manifest_json: str = Field(min_length=2)
    objects: list[ExportArchiveEntry] = Field(default_factory=list)
    target_bucket: str = Field(min_length=1)
    target_object_key: str = Field(min_length=1)
    stream_chunk_size: int = Field(gt=0)
    presigned_expire_seconds: int = Field(gt=0)


class OutboxEventPayload(BaseModel):
    """
    A single outbound webhook event delivered to a subscriber URL.

    media-service-m8 writes one row per state change to its transactional outbox
    table, then its maintenance worker serializes the row into this model and
    POSTs the JSON body — HMAC-signed with the subscription secret — to every
    matching subscriber. The contract is self-contained: no database, framework,
    or service knowledge, so a subscriber needs only this shape to verify and
    consume an event.

    ``event_type`` is a dotted name (e.g. ``object.ready``, ``object.deleted``,
    ``scan.failed``, ``variant.ready``); ``object_id`` is the media object the
    event concerns; ``payload`` carries event-specific detail; ``event_id`` is a
    stable id a subscriber can dedupe on (delivery is at-least-once).
    """

    model_config = ConfigDict(frozen=True)

    event_id: UUID
    event_type: str = Field(min_length=1)
    object_id: UUID
    payload: dict[str, Any]
    created_at: datetime
