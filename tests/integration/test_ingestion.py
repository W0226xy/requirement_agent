from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from requirement_agent.application.ingestion.service import IngestionService
from requirement_agent.application.ingestion.tasks import parse_source_attachments
from requirement_agent.connectors.document import DocumentConnector
from requirement_agent.connectors.web_form import WebFormConnector
from requirement_agent.domain.sources.entities import (
    AttachmentInput,
    FileConnectorRequest,
    WebFormConnectorRequest,
)
from requirement_agent.infrastructure.database.base import Base
from requirement_agent.infrastructure.database.models import (
    AuditLog,
    SourceAttachment,
    SourceRecord,
)
from requirement_agent.parsers.document import PdfParser
from requirement_agent.parsers.registry import ParserRegistry
from requirement_agent.shared.enums import AttachmentParseStatus, ProcessingStatus
from requirement_agent.shared.errors import IdempotencyConflictError, ObjectStorageError


class MemoryStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, object_name: str, content: bytes, content_type: str) -> None:
        self.objects[object_name] = content

    async def get(self, object_name: str) -> bytes:
        return self.objects[object_name]


class RecordingDispatcher:
    def __init__(self) -> None:
        self.source_ids: list[int] = []

    def dispatch_source(self, source_record_id: int) -> None:
        self.source_ids.append(source_record_id)


class FailedStorage(MemoryStorage):
    async def put(self, object_name: str, content: bytes, content_type: str) -> None:
        raise ObjectStorageError("storage unavailable")


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def make_request(raw_text: str = "Add export support") -> WebFormConnectorRequest:
    return WebFormConnectorRequest(
        external_event_id="idempotency-key-1",
        submitter_id="user-1",
        submitter_name="Tester",
        raw_text=raw_text,
        received_at=datetime.now(UTC),
    )


async def test_web_form_ingestion_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    dispatcher = RecordingDispatcher()
    async with session_factory() as session:
        service = IngestionService(session, MemoryStorage(), dispatcher, 1024)
        first = await service.ingest(WebFormConnector(), make_request())
        replay = await service.ingest(WebFormConnector(), make_request())

    assert first.replayed is False
    assert replay.replayed is True
    assert replay.source.id == first.source.id
    assert dispatcher.source_ids == [first.source.id, first.source.id]


async def test_reused_key_with_different_payload_is_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        service = IngestionService(session, MemoryStorage(), RecordingDispatcher(), 1024)
        await service.ingest(WebFormConnector(), make_request())

        with pytest.raises(IdempotencyConflictError):
            await service.ingest(WebFormConnector(), make_request("Different requirement"))


async def test_parse_task_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    import pymupdf

    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Stored requirement")
    content = pdf.tobytes()
    pdf.close()
    storage = MemoryStorage()

    async with session_factory() as session:
        service = IngestionService(session, storage, RecordingDispatcher(), 1024 * 1024)
        ingestion = await service.ingest(
            DocumentConnector(),
            FileConnectorRequest(
                external_event_id="document-task-1",
                submitter_id="user-1",
                submitter_name="Tester",
                received_at=datetime.now(UTC),
                attachment=AttachmentInput(
                    file_name="requirement.pdf",
                    file_type="application/pdf",
                    content=content,
                ),
            ),
        )

    parsers = ParserRegistry([PdfParser()])
    await parse_source_attachments(ingestion.source.id, session_factory, storage, parsers)
    await parse_source_attachments(ingestion.source.id, session_factory, storage, parsers)

    async with session_factory() as session:
        attachment = (
            await session.execute(
                select(SourceAttachment).where(
                    SourceAttachment.source_record_id == ingestion.source.id
                )
            )
        ).scalar_one()
        parsed_audits = (
            await session.execute(
                select(func.count(AuditLog.id)).where(
                    AuditLog.entity_id == str(attachment.id),
                    AuditLog.action_type == "attachment_parsed",
                )
            )
        ).scalar_one()

    assert attachment.parse_status == AttachmentParseStatus.PARSED
    assert attachment.parsed_text is not None
    assert "Stored requirement" in attachment.parsed_text
    assert parsed_audits == 1


async def test_text_source_moves_to_parsing_without_attachment(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    storage = MemoryStorage()
    async with session_factory() as session:
        service = IngestionService(session, storage, RecordingDispatcher(), 1024)
        ingestion = await service.ingest(WebFormConnector(), make_request())

    await parse_source_attachments(
        ingestion.source.id,
        session_factory,
        storage,
        ParserRegistry([PdfParser()]),
    )

    async with session_factory() as session:
        source = await session.get(SourceRecord, ingestion.source.id)

    assert source is not None
    assert source.processing_status == ProcessingStatus.PARSING


async def test_storage_failure_keeps_original_source(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        service = IngestionService(session, FailedStorage(), RecordingDispatcher(), 1024)
        with pytest.raises(ObjectStorageError, match="was saved"):
            await service.ingest(
                DocumentConnector(),
                FileConnectorRequest(
                    external_event_id="failed-storage-1",
                    submitter_id="user-1",
                    submitter_name="Tester",
                    received_at=datetime.now(UTC),
                    attachment=AttachmentInput(
                        file_name="requirement.pdf",
                        file_type="application/pdf",
                        content=b"%PDF-1.7\n",
                    ),
                ),
            )

        source = (
            await session.execute(
                select(SourceRecord).where(
                    SourceRecord.external_event_id == "failed-storage-1"
                )
            )
        ).scalar_one()

    assert source.processing_status == ProcessingStatus.PARSE_FAILED
