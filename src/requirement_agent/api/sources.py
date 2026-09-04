from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, Query, Response, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from requirement_agent.api.dependencies import get_ingestion_service
from requirement_agent.api.schemas.sources import (
    IngestionRequest,
    IngestionResponse,
    SourceRecordListResponse,
    SourceRecordResponse,
)
from requirement_agent.application.ingestion.service import IngestionService
from requirement_agent.connectors.document import DocumentConnector
from requirement_agent.connectors.image import ImageConnector
from requirement_agent.connectors.web_form import WebFormConnector
from requirement_agent.domain.sources.entities import (
    AttachmentInput,
    FileConnectorRequest,
    WebFormConnectorRequest,
)
from requirement_agent.infrastructure.database.models import SourceRecord
from requirement_agent.infrastructure.database.session import get_session
from requirement_agent.shared.config import get_settings
from requirement_agent.shared.enums import ChannelType, ProcessingStatus
from requirement_agent.shared.errors import (
    FileTooLargeError,
    SourceNotFoundError,
    UnsupportedFileError,
)

router = APIRouter(prefix="/api/v1", tags=["sources"])


@router.post(
    "/ingestions",
    response_model=IngestionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_ingestion(
    payload: IngestionRequest,
    response: Response,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=255),
    ],
    service: Annotated[IngestionService, Depends(get_ingestion_service)],
) -> IngestionResponse:
    request = WebFormConnectorRequest(
        external_event_id=idempotency_key,
        submitter_id=payload.submitter_id,
        submitter_name=payload.submitter_name,
        raw_text=payload.raw_text,
        raw_metadata=payload.raw_metadata,
        received_at=datetime.now(UTC),
    )
    result = await service.ingest(WebFormConnector(), request)
    if result.replayed:
        response.status_code = status.HTTP_200_OK
    return IngestionResponse(
        source=SourceRecordResponse.from_model(result.source, include_attachments=False),
        replayed=result.replayed,
    )


@router.post(
    "/files",
    response_model=IngestionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_file(
    response: Response,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=255),
    ],
    service: Annotated[IngestionService, Depends(get_ingestion_service)],
    file: Annotated[UploadFile, File()],
    submitter_id: Annotated[str, Form(min_length=1, max_length=255)],
    submitter_name: Annotated[str, Form(min_length=1, max_length=255)],
    raw_text: Annotated[str, Form(max_length=200_000)] = "",
) -> IngestionResponse:
    settings = get_settings()
    content = await file.read(settings.max_upload_size_bytes + 1)
    if len(content) > settings.max_upload_size_bytes:
        raise FileTooLargeError(
            f"file exceeds maximum size of {settings.max_upload_size_bytes} bytes"
        )
    file_type = file.content_type or "application/octet-stream"
    file_name = file.filename or "unnamed"
    attachment = AttachmentInput(
        file_name=file_name,
        file_type=file_type,
        content=content,
    )
    connector = _connector_for(file_type)
    request = FileConnectorRequest(
        external_event_id=idempotency_key,
        submitter_id=submitter_id,
        submitter_name=submitter_name,
        raw_text=raw_text,
        raw_metadata={},
        received_at=datetime.now(UTC),
        attachment=attachment,
    )
    result = await service.ingest(connector, request)
    if result.replayed:
        response.status_code = status.HTTP_200_OK
    return IngestionResponse(
        source=SourceRecordResponse.from_model(result.source, include_attachments=True),
        replayed=result.replayed,
    )


@router.get("/source-records", response_model=SourceRecordListResponse)
async def list_source_records(
    session: Annotated[AsyncSession, Depends(get_session)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    channel_type: ChannelType | None = None,
    processing_status: ProcessingStatus | None = None,
    submitter_id: str | None = None,
) -> SourceRecordListResponse:
    filters = []
    if channel_type is not None:
        filters.append(SourceRecord.channel_type == channel_type)
    if processing_status is not None:
        filters.append(SourceRecord.processing_status == processing_status)
    if submitter_id is not None:
        filters.append(SourceRecord.submitter_id == submitter_id)

    count_result = await session.execute(
        select(func.count(SourceRecord.id)).where(*filters)
    )
    query = (
        select(SourceRecord)
        .where(*filters)
        .order_by(SourceRecord.received_at.desc(), SourceRecord.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    records = (await session.execute(query)).scalars().all()
    return SourceRecordListResponse(
        items=[
            SourceRecordResponse.from_model(record, include_attachments=False)
            for record in records
        ],
        total=count_result.scalar_one(),
        page=page,
        page_size=page_size,
    )


@router.get("/source-records/{source_record_id}", response_model=SourceRecordResponse)
async def get_source_record(
    source_record_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SourceRecordResponse:
    result = await session.execute(
        select(SourceRecord)
        .options(selectinload(SourceRecord.attachments))
        .where(SourceRecord.id == source_record_id)
    )
    source = result.scalar_one_or_none()
    if source is None:
        raise SourceNotFoundError(f"source record {source_record_id} was not found")
    return SourceRecordResponse.from_model(source, include_attachments=True)


def _connector_for(file_type: str) -> DocumentConnector | ImageConnector:
    if file_type in {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }:
        return DocumentConnector()
    if file_type in {"image/jpeg", "image/png"}:
        return ImageConnector()
    raise UnsupportedFileError(f"unsupported file type: {file_type}")

