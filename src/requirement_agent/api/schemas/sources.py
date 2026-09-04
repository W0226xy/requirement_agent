from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from requirement_agent.infrastructure.database.models import SourceRecord
from requirement_agent.shared.enums import AttachmentParseStatus, ChannelType, ProcessingStatus


class IngestionRequest(BaseModel):
    submitter_id: str = Field(min_length=1, max_length=255)
    submitter_name: str = Field(min_length=1, max_length=255)
    raw_text: str = Field(min_length=1, max_length=200_000)
    raw_metadata: dict[str, object] = Field(default_factory=dict)


class AttachmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    file_name: str
    file_type: str
    file_size: int
    file_hash: str
    storage_path: str
    ocr_text: str | None
    parsed_text: str | None
    parse_status: AttachmentParseStatus
    error_message: str | None
    created_at: datetime


class SourceRecordResponse(BaseModel):
    id: int
    source_key: str
    channel_type: ChannelType
    external_event_id: str
    submitter_id: str
    submitter_name: str
    raw_text: str
    raw_metadata: dict[str, object]
    received_at: datetime
    processing_status: ProcessingStatus
    created_at: datetime
    attachments: list[AttachmentResponse] = Field(default_factory=list)

    @classmethod
    def from_model(
        cls,
        source: SourceRecord,
        *,
        include_attachments: bool,
    ) -> "SourceRecordResponse":
        metadata = {
            key: value
            for key, value in source.raw_metadata.items()
            if key != "_ingestion_fingerprint"
        }
        attachments = (
            [AttachmentResponse.model_validate(item) for item in source.attachments]
            if include_attachments
            else []
        )
        return cls(
            id=source.id,
            source_key=source.source_key,
            channel_type=source.channel_type,
            external_event_id=source.external_event_id,
            submitter_id=source.submitter_id,
            submitter_name=source.submitter_name,
            raw_text=source.raw_text,
            raw_metadata=metadata,
            received_at=source.received_at,
            processing_status=source.processing_status,
            created_at=source.created_at,
            attachments=attachments,
        )


class IngestionResponse(BaseModel):
    source: SourceRecordResponse
    replayed: bool


class SourceRecordListResponse(BaseModel):
    items: list[SourceRecordResponse]
    total: int
    page: int
    page_size: int

