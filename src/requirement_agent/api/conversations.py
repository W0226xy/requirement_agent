from typing import Annotated

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    Header,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from requirement_agent.api.dependencies import get_actor_id, get_ingestion_service
from requirement_agent.api.schemas.analysis import ReanalyzeResponse
from requirement_agent.api.schemas.conversations import (
    ConversationListResponse,
    ConversationMessageListResponse,
    ConversationMessageResponse,
    ConversationResponse,
    CreateConversationMessageResponse,
    CreateConversationRequest,
    UpdateConversationRequest,
)
from requirement_agent.api.schemas.reviews import ReviewTaskResponse
from requirement_agent.api.schemas.sources import SourceRecordResponse
from requirement_agent.application.conversations import ConversationService
from requirement_agent.application.ingestion.dispatcher import (
    TaskDispatcher,
    get_task_dispatcher,
)
from requirement_agent.application.ingestion.service import IngestionService
from requirement_agent.domain.sources.entities import AttachmentInput
from requirement_agent.infrastructure.database.models import (
    AnalysisResult,
    ConversationMessage,
    RequirementConversation,
    ReviewTask,
    SourceRecord,
)
from requirement_agent.infrastructure.database.session import get_session
from requirement_agent.shared.config import get_settings
from requirement_agent.shared.enums import AnalysisType, ProcessingStatus
from requirement_agent.shared.errors import FileTooLargeError

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])


def _service(
    session: AsyncSession,
    ingestion_service: IngestionService,
) -> ConversationService:
    return ConversationService(session, ingestion_service)


@router.post("", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    payload: Annotated[
        CreateConversationRequest,
        Body(default_factory=CreateConversationRequest),
    ],
    actor_id: Annotated[str, Depends(get_actor_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
    ingestion_service: Annotated[IngestionService, Depends(get_ingestion_service)],
) -> ConversationResponse:
    conversation = await _service(session, ingestion_service).create(
        owner_id=actor_id,
        title=payload.title,
    )
    return ConversationResponse.model_validate(conversation)


@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    actor_id: Annotated[str, Depends(get_actor_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ConversationListResponse:
    owner_filter = (
        RequirementConversation.owner_id == actor_id,
        RequirementConversation.deleted_at.is_(None),
    )
    total = await session.scalar(
        select(func.count(RequirementConversation.id)).where(*owner_filter)
    )
    conversations = (
        await session.execute(
            select(RequirementConversation)
            .where(*owner_filter)
            .order_by(
                RequirementConversation.updated_at.desc(),
                RequirementConversation.id.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars()
    return ConversationListResponse(
        items=[ConversationResponse.model_validate(item) for item in conversations],
        total=total or 0,
        page=page,
        page_size=page_size,
    )


@router.get("/{conversation_key}", response_model=ConversationResponse)
async def get_conversation(
    conversation_key: str,
    actor_id: Annotated[str, Depends(get_actor_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
    ingestion_service: Annotated[IngestionService, Depends(get_ingestion_service)],
) -> ConversationResponse:
    conversation = await _service(session, ingestion_service).get(
        conversation_key,
        actor_id,
    )
    return ConversationResponse.model_validate(conversation)


@router.patch("/{conversation_key}", response_model=ConversationResponse)
async def update_conversation(
    conversation_key: str,
    payload: UpdateConversationRequest,
    actor_id: Annotated[str, Depends(get_actor_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
    ingestion_service: Annotated[IngestionService, Depends(get_ingestion_service)],
) -> ConversationResponse:
    conversation = await _service(session, ingestion_service).rename(
        conversation_key,
        actor_id,
        payload.title,
    )
    return ConversationResponse.model_validate(conversation)


@router.delete("/{conversation_key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_key: str,
    actor_id: Annotated[str, Depends(get_actor_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
    ingestion_service: Annotated[IngestionService, Depends(get_ingestion_service)],
) -> Response:
    await _service(session, ingestion_service).delete(conversation_key, actor_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{conversation_key}/messages",
    response_model=ConversationMessageListResponse,
)
async def list_conversation_messages(
    conversation_key: str,
    actor_id: Annotated[str, Depends(get_actor_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
    ingestion_service: Annotated[IngestionService, Depends(get_ingestion_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 100,
) -> ConversationMessageListResponse:
    service = _service(session, ingestion_service)
    conversation = await service.get(conversation_key, actor_id)
    total = await session.scalar(
        select(func.count(ConversationMessage.id)).where(
            ConversationMessage.conversation_id == conversation.id
        )
    )
    messages = (
        await session.execute(
            select(ConversationMessage)
            .options(
                selectinload(ConversationMessage.source_record).selectinload(
                    SourceRecord.attachments
                )
            )
            .where(ConversationMessage.conversation_id == conversation.id)
            .order_by(ConversationMessage.sequence_number, ConversationMessage.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    projected = await _project_messages(session, list(messages))
    return ConversationMessageListResponse(
        items=projected,
        total=total or 0,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/{conversation_key}/messages",
    response_model=CreateConversationMessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_conversation_message(
    conversation_key: str,
    response: Response,
    actor_id: Annotated[str, Depends(get_actor_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
    ingestion_service: Annotated[IngestionService, Depends(get_ingestion_service)],
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=255),
    ],
    actor_name: Annotated[str | None, Form(min_length=1, max_length=255)] = None,
    raw_text: Annotated[str, Form(max_length=200_000)] = "",
    file: Annotated[UploadFile | None, File()] = None,
) -> CreateConversationMessageResponse:
    text = raw_text.strip()
    attachment = await _attachment_input(file)
    if not text and attachment is None:
        from requirement_agent.shared.errors import ConnectorVerificationError

        raise ConnectorVerificationError("a message requires raw_text or a file")
    service = _service(session, ingestion_service)
    message, result = await service.add_message(
        conversation_key=conversation_key,
        owner_id=actor_id,
        actor_name=actor_name or actor_id,
        idempotency_key=idempotency_key,
        raw_text=text,
        attachment=attachment,
    )
    loaded = (
        await session.execute(
            select(ConversationMessage)
            .options(
                selectinload(ConversationMessage.source_record).selectinload(
                    SourceRecord.attachments
                )
            )
            .where(ConversationMessage.id == message.id)
        )
    ).scalar_one()
    if result.replayed:
        response.status_code = status.HTTP_200_OK
    projected = await _project_messages(session, [loaded])
    return CreateConversationMessageResponse(
        message=projected[0],
        replayed=result.replayed,
    )


@router.post(
    "/{conversation_key}/messages/{message_key}/reanalyze",
    response_model=ReanalyzeResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reanalyze_conversation_message(
    conversation_key: str,
    message_key: str,
    actor_id: Annotated[str, Depends(get_actor_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
    ingestion_service: Annotated[IngestionService, Depends(get_ingestion_service)],
    dispatcher: Annotated[TaskDispatcher, Depends(get_task_dispatcher)],
) -> ReanalyzeResponse:
    message = await _service(session, ingestion_service).load_message(
        conversation_key,
        actor_id,
        message_key,
    )
    source = message.source_record
    source.processing_status = ProcessingStatus.PARSING
    await session.commit()
    dispatcher.dispatch_source(source.id)
    return ReanalyzeResponse(source_record_id=source.id, queued=True)


async def _attachment_input(file: UploadFile | None) -> AttachmentInput | None:
    if file is None:
        return None
    settings = get_settings()
    content = await file.read(settings.max_upload_size_bytes + 1)
    if len(content) > settings.max_upload_size_bytes:
        raise FileTooLargeError(
            f"file exceeds maximum size of {settings.max_upload_size_bytes} bytes"
        )
    return AttachmentInput(
        file_name=file.filename or "unnamed",
        file_type=file.content_type or "application/octet-stream",
        content=content,
    )


async def _project_messages(
    session: AsyncSession,
    messages: list[ConversationMessage],
) -> list[ConversationMessageResponse]:
    if not messages:
        return []
    source_ids = [message.source_record_id for message in messages]
    analyses = (
        await session.execute(
            select(AnalysisResult)
            .where(
                AnalysisResult.source_record_id.in_(source_ids),
                AnalysisResult.error_message.is_(None),
                AnalysisResult.result_json.is_not(None),
            )
            .order_by(AnalysisResult.id.desc())
        )
    ).scalars().all()
    reviews = (
        await session.execute(
            select(ReviewTask)
            .where(ReviewTask.source_record_id.in_(source_ids))
            .order_by(ReviewTask.id.desc())
        )
    ).scalars().all()
    latest_analysis: dict[tuple[int, AnalysisType], dict[str, object]] = {}
    for analysis in analyses:
        key = (analysis.source_record_id, analysis.analysis_type)
        if key not in latest_analysis and analysis.result_json is not None:
            latest_analysis[key] = analysis.result_json
    latest_review: dict[int, ReviewTask] = {}
    for review in reviews:
        latest_review.setdefault(review.source_record_id, review)

    return [
        ConversationMessageResponse(
            message_key=message.message_key,
            sequence_number=message.sequence_number,
            role=message.role,
            created_at=message.created_at,
            source=SourceRecordResponse.from_model(
                message.source_record,
                include_attachments=True,
            ),
            latest_extraction=latest_analysis.get(
                (message.source_record_id, AnalysisType.EXTRACTION)
            ),
            latest_conflict_analysis=latest_analysis.get(
                (message.source_record_id, AnalysisType.CONFLICT_RISK)
            ),
            review_task=(
                ReviewTaskResponse.model_validate(latest_review[message.source_record_id])
                if message.source_record_id in latest_review
                else None
            ),
        )
        for message in messages
    ]
