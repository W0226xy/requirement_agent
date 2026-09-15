from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from requirement_agent.api.dependencies import get_reviewer
from requirement_agent.api.schemas.reviews import (
    ApprovalResponse,
    ApproveReviewRequest,
    ReviewActionRequest,
    ReviewTaskListResponse,
    ReviewTaskResponse,
)
from requirement_agent.application.ingestion.dispatcher import (
    TaskDispatcher,
    get_task_dispatcher,
)
from requirement_agent.application.versions.service import VersionCommitService
from requirement_agent.infrastructure.database.models import (
    AuditLog,
    ReviewTask,
    SourceRecord,
)
from requirement_agent.infrastructure.database.session import get_session
from requirement_agent.shared.enums import (
    AuditActionType,
    AuditEntityType,
    ProcessingStatus,
    ReviewStatus,
)
from requirement_agent.shared.errors import ReviewStateError, ReviewTaskNotFoundError

router = APIRouter(prefix="/api/v1/review-tasks", tags=["reviews"])


@router.get("", response_model=ReviewTaskListResponse)
async def list_review_tasks(
    session: Annotated[AsyncSession, Depends(get_session)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    review_status: ReviewStatus | None = None,
    source_record_id: int | None = None,
) -> ReviewTaskListResponse:
    filters = []
    if review_status is not None:
        filters.append(ReviewTask.review_status == review_status)
    if source_record_id is not None:
        filters.append(ReviewTask.source_record_id == source_record_id)
    total = (
        await session.execute(select(func.count(ReviewTask.id)).where(*filters))
    ).scalar_one()
    tasks = (
        await session.execute(
            select(ReviewTask)
            .where(*filters)
            .order_by(ReviewTask.created_at.desc(), ReviewTask.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars()
    return ReviewTaskListResponse(
        items=[ReviewTaskResponse.model_validate(item) for item in tasks],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{review_task_id}", response_model=ReviewTaskResponse)
async def get_review_task(
    review_task_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReviewTaskResponse:
    task = await session.get(ReviewTask, review_task_id)
    if task is None:
        raise ReviewTaskNotFoundError(f"review task {review_task_id} was not found")
    return ReviewTaskResponse.model_validate(task)


@router.post("/{review_task_id}/approve", response_model=ApprovalResponse)
async def approve_review(
    review_task_id: int,
    payload: ApproveReviewRequest,
    reviewer_id: Annotated[str, Depends(get_reviewer)],
    session: Annotated[AsyncSession, Depends(get_session)],
    dispatcher: Annotated[TaskDispatcher, Depends(get_task_dispatcher)],
) -> ApprovalResponse:
    version = await VersionCommitService(session).approve(
        review_task_id=review_task_id,
        reviewer_id=reviewer_id,
        decision=payload.decision,
        title=payload.title,
        target_requirement_key=payload.target_requirement_key,
        expected_requirement_id=payload.expected_requirement_id,
        expected_current_version=payload.expected_current_version,
        operations=[operation.to_domain() for operation in payload.operations],
        comment=payload.comment,
    )
    dispatcher.dispatch_version(version.id)
    return ApprovalResponse(
        review_task_id=review_task_id,
        requirement_id=version.requirement_id,
        version_id=version.id,
        version_number=version.version_number,
    )


@router.post("/{review_task_id}/reject", response_model=ReviewTaskResponse)
async def reject_review(
    review_task_id: int,
    payload: ReviewActionRequest,
    reviewer_id: Annotated[str, Depends(get_reviewer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReviewTaskResponse:
    return await _complete_without_version(
        session,
        review_task_id,
        reviewer_id,
        payload.comment,
        ReviewStatus.REJECTED,
    )


@router.post("/{review_task_id}/return", response_model=ReviewTaskResponse)
async def return_review(
    review_task_id: int,
    payload: ReviewActionRequest,
    reviewer_id: Annotated[str, Depends(get_reviewer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReviewTaskResponse:
    return await _complete_without_version(
        session,
        review_task_id,
        reviewer_id,
        payload.comment,
        ReviewStatus.RETURNED,
    )


@router.post("/{review_task_id}/reanalyze", response_model=ReviewTaskResponse)
async def reanalyze_review(
    review_task_id: int,
    reviewer_id: Annotated[str, Depends(get_reviewer)],
    session: Annotated[AsyncSession, Depends(get_session)],
    dispatcher: Annotated[TaskDispatcher, Depends(get_task_dispatcher)],
) -> ReviewTaskResponse:
    task = await _pending_task(session, review_task_id)
    source = await session.get(SourceRecord, task.source_record_id)
    if source is None:
        raise ReviewStateError("review source no longer exists")
    task.review_status = ReviewStatus.RETURNED
    task.reviewer_id = reviewer_id
    task.review_comment = "reanalyze requested"
    task.reviewed_at = datetime.now(UTC)
    source.processing_status = ProcessingStatus.ANALYZING
    await session.commit()
    dispatcher.dispatch_analysis(source.id)
    return ReviewTaskResponse.model_validate(task)


async def _complete_without_version(
    session: AsyncSession,
    review_task_id: int,
    reviewer_id: str,
    comment: str,
    new_status: ReviewStatus,
) -> ReviewTaskResponse:
    task = await _pending_task(session, review_task_id)
    source = await session.get(SourceRecord, task.source_record_id)
    if source is None:
        raise ReviewStateError("review source no longer exists")
    task.review_status = new_status
    task.reviewer_id = reviewer_id
    task.review_comment = comment
    task.reviewed_at = datetime.now(UTC)
    source.processing_status = (
        ProcessingStatus.REJECTED
        if new_status == ReviewStatus.REJECTED
        else ProcessingStatus.RETURNED
    )
    action = (
        AuditActionType.REVIEW_REJECTED
        if new_status == ReviewStatus.REJECTED
        else AuditActionType.REVIEW_RETURNED
    )
    session.add(
        AuditLog(
            actor_id=reviewer_id,
            action_type=action,
            entity_type=AuditEntityType.REVIEW_TASK,
            entity_id=str(task.id),
            after_data={"status": new_status.value, "comment": comment},
        )
    )
    await session.commit()
    return ReviewTaskResponse.model_validate(task)


async def _pending_task(session: AsyncSession, review_task_id: int) -> ReviewTask:
    task = await session.scalar(
        select(ReviewTask).where(ReviewTask.id == review_task_id).with_for_update()
    )
    if task is None:
        raise ReviewTaskNotFoundError(f"review task {review_task_id} was not found")
    if task.review_status != ReviewStatus.PENDING:
        raise ReviewStateError(f"review task is already {task.review_status.value}")
    return task
