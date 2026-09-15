from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from requirement_agent.ai.llm.factory import get_embedding_model
from requirement_agent.ai.retrieval.hybrid import (
    HybridRetriever,
    RetrievalWeights,
    SearchFilters,
)
from requirement_agent.api.dependencies import get_reviewer
from requirement_agent.api.schemas.analysis import (
    AnalysisResultListResponse,
    AnalysisResultResponse,
    ReanalyzeResponse,
    SearchRequest,
    SearchResponse,
)
from requirement_agent.application.ingestion.dispatcher import (
    TaskDispatcher,
    get_task_dispatcher,
)
from requirement_agent.infrastructure.database.models import AnalysisResult, SourceRecord
from requirement_agent.infrastructure.database.session import get_session
from requirement_agent.shared.config import get_settings
from requirement_agent.shared.enums import AnalysisType, ProcessingStatus
from requirement_agent.shared.errors import (
    AnalysisNotFoundError,
    ReviewStateError,
    SourceNotFoundError,
)

router = APIRouter(prefix="/api/v1", tags=["analysis"])


@router.get("/analysis-results", response_model=AnalysisResultListResponse)
async def list_analysis_results(
    session: Annotated[AsyncSession, Depends(get_session)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    source_record_id: int | None = None,
    analysis_type: AnalysisType | None = None,
    failed_only: bool = False,
) -> AnalysisResultListResponse:
    filters = []
    if source_record_id is not None:
        filters.append(AnalysisResult.source_record_id == source_record_id)
    if analysis_type is not None:
        filters.append(AnalysisResult.analysis_type == analysis_type)
    if failed_only:
        filters.append(AnalysisResult.error_message.is_not(None))
    total = (
        await session.execute(select(func.count(AnalysisResult.id)).where(*filters))
    ).scalar_one()
    results = (
        await session.execute(
            select(AnalysisResult)
            .where(*filters)
            .order_by(AnalysisResult.created_at.desc(), AnalysisResult.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars()
    return AnalysisResultListResponse(
        items=[AnalysisResultResponse.model_validate(item) for item in results],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/failed-jobs", response_model=AnalysisResultListResponse)
async def list_failed_jobs(
    session: Annotated[AsyncSession, Depends(get_session)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> AnalysisResultListResponse:
    failed_filter = AnalysisResult.error_message.is_not(None)
    total = (
        await session.execute(
            select(func.count(AnalysisResult.id)).where(failed_filter)
        )
    ).scalar_one()
    results = (
        await session.execute(
            select(AnalysisResult)
            .where(failed_filter)
            .order_by(AnalysisResult.created_at.desc(), AnalysisResult.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars()
    return AnalysisResultListResponse(
        items=[AnalysisResultResponse.model_validate(item) for item in results],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/failed-jobs/{analysis_result_id}/retry",
    response_model=ReanalyzeResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_failed_job(
    analysis_result_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    dispatcher: Annotated[TaskDispatcher, Depends(get_task_dispatcher)],
    _reviewer_id: Annotated[str, Depends(get_reviewer)],
) -> ReanalyzeResponse:
    result = await session.get(AnalysisResult, analysis_result_id)
    if result is None:
        raise AnalysisNotFoundError(
            f"analysis result {analysis_result_id} was not found"
        )
    if result.error_message is None:
        raise ReviewStateError("only failed analysis jobs can be retried")
    source = await session.get(SourceRecord, result.source_record_id)
    if source is None:
        raise SourceNotFoundError(
            f"source record {result.source_record_id} was not found"
        )
    source.processing_status = ProcessingStatus.PARSING
    await session.commit()
    dispatcher.dispatch_source(source.id)
    return ReanalyzeResponse(source_record_id=source.id, queued=True)


@router.post(
    "/source-records/{source_record_id}/reanalyze",
    response_model=ReanalyzeResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reanalyze_source(
    source_record_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    dispatcher: Annotated[TaskDispatcher, Depends(get_task_dispatcher)],
) -> ReanalyzeResponse:
    source = await session.get(SourceRecord, source_record_id)
    if source is None:
        raise SourceNotFoundError(f"source record {source_record_id} was not found")
    source.processing_status = ProcessingStatus.PARSING
    await session.commit()
    dispatcher.dispatch_source(source_record_id)
    return ReanalyzeResponse(source_record_id=source_record_id, queued=True)


@router.post("/search", response_model=SearchResponse)
async def search_requirements(
    payload: SearchRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SearchResponse:
    settings = get_settings()
    retriever = HybridRetriever(
        session,
        get_embedding_model(),
        RetrievalWeights(
            keyword=settings.retrieval_keyword_weight,
            vector=settings.retrieval_vector_weight,
            business=settings.retrieval_business_weight,
        ),
        candidate_limit=settings.retrieval_candidate_limit,
        min_similarity_score=settings.retrieval_min_similarity_score,
    )
    candidates = await retriever.search(
        payload.query,
        source_record_id=payload.source_record_id,
        query_modules=payload.query_modules,
        filters=SearchFilters(
            modules=tuple(payload.filter_modules),
            statuses=tuple(payload.statuses),
        ),
    )
    return SearchResponse(items=candidates)
