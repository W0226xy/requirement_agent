from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from requirement_agent.ai.llm.fake import FakeLLM
from requirement_agent.ai.schemas.analysis import ProposedOperation
from requirement_agent.application.versions.indexing import index_requirement_version
from requirement_agent.application.versions.service import VersionCommitService
from requirement_agent.infrastructure.database.base import Base
from requirement_agent.infrastructure.database.models import (
    AnalysisResult,
    FeatureLineage,
    Requirement,
    RequirementEmbedding,
    RequirementFeature,
    RequirementVersion,
    ReviewTask,
    SourceRecord,
)
from requirement_agent.shared.enums import (
    AnalysisType,
    ChangeOperation,
    ChannelType,
    FeatureStatus,
    ProcessingStatus,
    ReviewDecision,
    ReviewStatus,
)
from requirement_agent.shared.errors import VersionOperationError


@pytest_asyncio.fixture
async def version_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
    await engine.dispose()


def operation(
    source_id: int,
    action: str,
    *,
    feature_key: str | None = None,
    title: str = "Export PDF",
) -> ProposedOperation:
    content = (
        {
            "module": "reporting",
            "feature_title": title,
            "feature_description": f"{title} description",
            "acceptance_criteria": [f"{title} works"],
        }
        if action != "delete"
        else None
    )
    return ProposedOperation.model_validate(
        {
            "operation": ChangeOperation(action),
            "feature_key": feature_key,
            "content": content,
            "source_record_id": source_id,
            "reason": f"{action} requested",
        }
    )


async def add_review(session: AsyncSession, source_id: int) -> ReviewTask:
    source = SourceRecord(
        source_key=f"SRC-{source_id:04d}",
        channel_type=ChannelType.WEB_FORM,
        external_event_id=f"event-{source_id}",
        submitter_id="user-1",
        submitter_name="Tester",
        raw_text="Requirement input",
        raw_metadata={},
        received_at=datetime.now(UTC),
        processing_status=ProcessingStatus.PENDING_REVIEW,
    )
    session.add(source)
    await session.flush()
    analysis = AnalysisResult(
        source_record_id=source.id,
        analysis_type=AnalysisType.CONFLICT_RISK,
        model_name="fake",
        prompt_version="test",
        input_snapshot={},
        result_json={},
        duration_ms=1,
        attempt_number=1,
    )
    session.add(analysis)
    await session.flush()
    review = ReviewTask(
        source_record_id=source.id,
        analysis_result_id=analysis.id,
        review_status=ReviewStatus.PENDING,
        extraction_snapshot={},
        candidate_snapshot=[],
        analysis_snapshot={},
    )
    session.add(review)
    await session.commit()
    return review


async def approve(
    session: AsyncSession,
    review: ReviewTask,
    operation_value: ProposedOperation,
    *,
    requirement_id: int | None = None,
) -> RequirementVersion:
    return await VersionCommitService(session).approve(
        review_task_id=review.id,
        reviewer_id="reviewer-1",
        decision=(
            ReviewDecision.MERGE if requirement_id is not None else ReviewDecision.CREATE
        ),
        title="Report exports" if requirement_id is None else None,
        target_requirement_id=requirement_id,
        operations=[operation_value],
        comment="approved",
    )


async def test_create_and_all_feature_operations_are_versioned(
    version_session: AsyncSession,
) -> None:
    create_review = await add_review(version_session, 1)
    version1 = await approve(
        version_session, create_review, operation(1, "add")
    )
    requirement_id = version1.requirement_id

    modify_review = await add_review(version_session, 2)
    version2 = await approve(
        version_session,
        modify_review,
        operation(2, "modify", feature_key="FEAT-001", title="Export PDF/A"),
        requirement_id=requirement_id,
    )
    delete_review = await add_review(version_session, 3)
    version3 = await approve(
        version_session,
        delete_review,
        operation(3, "delete", feature_key="FEAT-001"),
        requirement_id=requirement_id,
    )
    restore_review = await add_review(version_session, 4)
    version4 = await approve(
        version_session,
        restore_review,
        operation(4, "restore", feature_key="FEAT-001", title="Export PDF/A"),
        requirement_id=requirement_id,
    )

    requirement = await version_session.get(Requirement, requirement_id)
    versions = (
        await version_session.execute(
            select(RequirementVersion)
            .where(RequirementVersion.requirement_id == requirement_id)
            .order_by(RequirementVersion.version_number)
        )
    ).scalars().all()
    features = (
        await version_session.execute(
            select(RequirementFeature)
            .where(
                RequirementFeature.version_id.in_(
                    [version1.id, version2.id, version3.id, version4.id]
                )
            )
            .order_by(RequirementFeature.version_id)
        )
    ).scalars().all()
    lineage = (
        await version_session.execute(
            select(FeatureLineage).order_by(FeatureLineage.id)
        )
    ).scalars().all()

    assert requirement is not None
    assert requirement.current_version_id == version4.id
    assert [item.version_number for item in versions] == [1, 2, 3, 4]
    assert [item.feature_status for item in features] == [
        FeatureStatus.ACTIVE,
        FeatureStatus.ACTIVE,
        FeatureStatus.DELETED,
        FeatureStatus.ACTIVE,
    ]
    assert [item.operation_type.value for item in lineage] == [
        "introduced",
        "modified",
        "deleted",
        "restored",
    ]
    assert version1.requirement_snapshot["features"] != version2.requirement_snapshot[
        "features"
    ]


async def test_repeated_approval_returns_same_version(
    version_session: AsyncSession,
) -> None:
    review = await add_review(version_session, 1)
    service = VersionCommitService(version_session)
    first = await service.approve(
        review_task_id=review.id,
        reviewer_id="reviewer-1",
        decision=ReviewDecision.CREATE,
        title="Report exports",
        target_requirement_id=None,
        operations=[operation(1, "add")],
        comment=None,
    )
    second = await service.approve(
        review_task_id=review.id,
        reviewer_id="reviewer-1",
        decision=ReviewDecision.CREATE,
        title="Ignored replay title",
        target_requirement_id=None,
        operations=[operation(1, "add")],
        comment=None,
    )
    count = await version_session.scalar(select(func.count(RequirementVersion.id)))

    assert second.id == first.id
    assert count == 1


async def test_version_indexing_is_idempotent_and_contains_sources(
    version_session: AsyncSession,
) -> None:
    review = await add_review(version_session, 1)
    version = await approve(version_session, review, operation(1, "add"))
    embedding_model = FakeLLM([], model_name="fake-embedding")

    await index_requirement_version(version_session, embedding_model, version.id)
    await index_requirement_version(version_session, embedding_model, version.id)
    projections = (
        await version_session.execute(
            select(RequirementEmbedding).order_by(RequirementEmbedding.feature_key)
        )
    ).scalars().all()

    assert len(projections) == 2
    assert {item.feature_key for item in projections} == {None, "FEAT-001"}
    assert projections[0].sources[0]["source_key"] == "SRC-0001"


async def test_invalid_operation_rolls_back_entire_commit(
    version_session: AsyncSession,
) -> None:
    review = await add_review(version_session, 1)
    review_id = review.id

    with pytest.raises(VersionOperationError, match="does not exist"):
        await VersionCommitService(version_session).approve(
            review_task_id=review.id,
            reviewer_id="reviewer-1",
            decision=ReviewDecision.CREATE,
            title="Report exports",
            target_requirement_id=None,
            operations=[
                operation(1, "add"),
                operation(1, "modify", feature_key="FEAT-999"),
            ],
            comment=None,
        )

    requirement_count = await version_session.scalar(select(func.count(Requirement.id)))
    version_count = await version_session.scalar(
        select(func.count(RequirementVersion.id))
    )
    persisted_review = await version_session.get(ReviewTask, review_id)
    assert requirement_count == 0
    assert version_count == 0
    assert persisted_review is not None
    assert persisted_review.review_status == ReviewStatus.PENDING
