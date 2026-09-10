import asyncio
from collections.abc import Coroutine
from typing import Any

from celery import Celery
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from requirement_agent.ai.llm.factory import get_embedding_model, get_llm
from requirement_agent.ai.retrieval.hybrid import RetrievalWeights
from requirement_agent.application.ingestion.dispatcher import (
    ANALYZE_SOURCE_TASK,
    FEISHU_EVENT_TASK,
    INDEX_VERSION_TASK,
    PARSE_SOURCE_TASK,
    get_task_dispatcher,
)
from requirement_agent.application.ingestion.service import IngestionService
from requirement_agent.application.versions.indexing import index_requirement_version
from requirement_agent.connectors.feishu import (
    FeishuConnector,
    FeishuEventRequest,
    FeishuOpenAPIClient,
)
from requirement_agent.infrastructure.database.models import (
    AuditLog,
    SourceAttachment,
    SourceRecord,
)
from requirement_agent.infrastructure.database.session import get_session_factory
from requirement_agent.infrastructure.storage.base import ObjectStorage
from requirement_agent.infrastructure.storage.minio import get_object_storage
from requirement_agent.parsers.registry import ParserRegistry, get_parser_registry
from requirement_agent.shared.config import get_settings
from requirement_agent.shared.enums import (
    AttachmentParseStatus,
    AuditActionType,
    AuditEntityType,
    ProcessingStatus,
)
from requirement_agent.shared.errors import (
    ApplicationError,
    AttachmentProcessingError,
    FeishuAPIError,
    LLMServiceError,
    SourceNotFoundError,
)
from requirement_agent.workflows.requirement_analysis import RequirementAnalysisWorkflow

_worker_loop: asyncio.AbstractEventLoop | None = None


def run_worker_coroutine[Result](
    coroutine: Coroutine[Any, Any, Result],
) -> Result:
    global _worker_loop
    if _worker_loop is None or _worker_loop.is_closed():
        _worker_loop = asyncio.new_event_loop()
    return _worker_loop.run_until_complete(coroutine)


async def parse_source_attachments(
    source_record_id: int,
    session_factory: async_sessionmaker[AsyncSession],
    storage: ObjectStorage,
    parsers: ParserRegistry,
) -> None:
    async with session_factory() as session:
        source = await _load_source(session, source_record_id)
        if source.attachments and all(
            attachment.parse_status == AttachmentParseStatus.PARSED
            for attachment in source.attachments
        ):
            return

        source.processing_status = ProcessingStatus.PARSING
        session.add(
            AuditLog(
                actor_id="system",
                action_type=AuditActionType.SOURCE_PARSING_STARTED,
                entity_type=AuditEntityType.SOURCE_RECORD,
                entity_id=str(source.id),
                after_data={"status": ProcessingStatus.PARSING.value},
            )
        )
        await session.commit()

        for attachment in source.attachments:
            if attachment.parse_status == AttachmentParseStatus.PARSED:
                continue
            await _parse_attachment(session, source, attachment, storage, parsers)


async def _load_source(session: AsyncSession, source_record_id: int) -> SourceRecord:
    result = await session.execute(
        select(SourceRecord)
        .options(selectinload(SourceRecord.attachments))
        .where(SourceRecord.id == source_record_id)
        .with_for_update()
    )
    source = result.scalar_one_or_none()
    if source is None:
        raise SourceNotFoundError(f"source record {source_record_id} was not found")
    return source


async def _parse_attachment(
    session: AsyncSession,
    source: SourceRecord,
    attachment: SourceAttachment,
    storage: ObjectStorage,
    parsers: ParserRegistry,
) -> None:
    attachment.parse_status = AttachmentParseStatus.PARSING
    attachment.error_message = None
    await session.commit()

    try:
        content = await storage.get(attachment.storage_path)
        result = await parsers.for_file_type(attachment.file_type).parse(content)
    except ApplicationError as exc:
        message = str(exc)
        attachment.parse_status = AttachmentParseStatus.FAILED
        attachment.error_message = message
        source.processing_status = ProcessingStatus.PARSE_FAILED
        session.add(
            AuditLog(
                actor_id="system",
                action_type=AuditActionType.SOURCE_PARSE_FAILED,
                entity_type=AuditEntityType.SOURCE_ATTACHMENT,
                entity_id=str(attachment.id),
                after_data={"error": message},
            )
        )
        await session.commit()
        raise AttachmentProcessingError(
            f"failed to parse attachment {attachment.id}"
        ) from exc

    attachment.parsed_text = result.parsed_text
    attachment.ocr_text = result.ocr_text
    attachment.parse_status = AttachmentParseStatus.PARSED
    session.add(
        AuditLog(
            actor_id="system",
            action_type=AuditActionType.ATTACHMENT_PARSED,
            entity_type=AuditEntityType.SOURCE_ATTACHMENT,
            entity_id=str(attachment.id),
            after_data={"parse_status": AttachmentParseStatus.PARSED.value},
        )
    )
    await session.commit()


def register_tasks(celery_app: Celery) -> None:
    def parse_source_task(source_record_id: int) -> None:
        run_worker_coroutine(
            parse_source_attachments(
                source_record_id,
                get_session_factory(),
                get_object_storage(),
                get_parser_registry(),
            )
        )
        celery_app.send_task(ANALYZE_SOURCE_TASK, args=[source_record_id])

    def analyze_source_task(source_record_id: int) -> None:
        run_worker_coroutine(analyze_source(source_record_id))

    def index_version_task(version_id: int) -> None:
        run_worker_coroutine(index_version(version_id))

    def process_feishu_event_task(payload: dict[str, object]) -> None:
        run_worker_coroutine(process_feishu_event(payload))

    celery_app.task(
        name=PARSE_SOURCE_TASK,
        autoretry_for=(AttachmentProcessingError,),
        retry_backoff=True,
        retry_jitter=True,
        retry_kwargs={"max_retries": 3},
    )(parse_source_task)
    celery_app.task(name=ANALYZE_SOURCE_TASK)(analyze_source_task)
    celery_app.task(
        name=INDEX_VERSION_TASK,
        autoretry_for=(LLMServiceError, SQLAlchemyError),
        retry_backoff=True,
        retry_jitter=True,
        retry_kwargs={"max_retries": 3},
    )(index_version_task)
    celery_app.task(
        name=FEISHU_EVENT_TASK,
        autoretry_for=(FeishuAPIError,),
        retry_backoff=True,
        retry_jitter=True,
        retry_kwargs={"max_retries": 3},
    )(process_feishu_event_task)


async def analyze_source(source_record_id: int) -> None:
    settings = get_settings()
    async with get_session_factory()() as session:
        workflow = RequirementAnalysisWorkflow(
            session,
            get_llm(),
            get_embedding_model(),
            max_retries=settings.llm_max_retries,
            retrieval_weights=RetrievalWeights(
                keyword=settings.retrieval_keyword_weight,
                vector=settings.retrieval_vector_weight,
                business=settings.retrieval_business_weight,
            ),
            candidate_limit=settings.retrieval_candidate_limit,
        )
        await workflow.run(source_record_id)


async def index_version(version_id: int) -> None:
    async with get_session_factory()() as session:
        await index_requirement_version(session, get_embedding_model(), version_id)


async def process_feishu_event(payload: dict[str, object]) -> None:
    settings = get_settings()
    request = FeishuEventRequest.model_validate(payload)
    async with FeishuOpenAPIClient(
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret.get_secret_value(),
        base_url=settings.feishu_base_url,
        timeout_seconds=settings.feishu_timeout_seconds,
        max_download_size=settings.max_upload_size_bytes,
    ) as client:
        async with get_session_factory()() as session:
            service = IngestionService(
                session=session,
                storage=get_object_storage(),
                dispatcher=get_task_dispatcher(),
                max_upload_size=settings.max_upload_size_bytes,
            )
            await service.ingest(FeishuConnector(client), request)
