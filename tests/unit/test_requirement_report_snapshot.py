from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from requirement_agent.ai.tools.schemas import GetRequirementReportSnapshotInput
from requirement_agent.application.chat_tools import ChatToolService
from requirement_agent.infrastructure.database.base import Base
from requirement_agent.infrastructure.database.models import (
    AnalysisResult, ConversationMessage, FeatureLineage, Requirement,
    RequirementConversation, RequirementVersion, ReviewTask, SourceRecord,
)
from requirement_agent.shared.enums import (
    AnalysisType, ChannelType, LineageOperationType, RequirementChangeType, RequirementStatus,
    ReviewStatus,
)


@pytest_asyncio.fixture
async def report_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
    await engine.dispose()


async def _seed(session: AsyncSession) -> tuple[RequirementConversation, SourceRecord]:
    now = datetime.now(UTC)
    source = SourceRecord(source_key="SRC-REPORT-1", channel_type=ChannelType.WEB_FORM,
        external_event_id="report-1", submitter_id="owner-1", submitter_name="Owner", raw_text="播放器需求",
        raw_metadata={}, received_at=now)
    foreign = SourceRecord(source_key="SRC-REPORT-2", channel_type=ChannelType.WEB_FORM,
        external_event_id="report-2", submitter_id="owner-2", submitter_name="Other", raw_text="不可见",
        raw_metadata={}, received_at=now)
    session.add_all([source, foreign]); await session.flush()
    requirement = Requirement(requirement_key="REQ-PLAYER-1", title="播放列表", status=RequirementStatus.ACTIVE,
        functional_modules=["音乐播放器"], extra_fields={})
    hidden = Requirement(requirement_key="REQ-HIDDEN-1", title="其他用户需求", status=RequirementStatus.ACTIVE,
        functional_modules=["音乐播放器"], extra_fields={})
    session.add_all([requirement, hidden]); await session.flush()
    version = RequirementVersion(requirement_id=requirement.id, version_number=1, change_type=RequirementChangeType.INITIAL,
        version_title=requirement.title, requirement_snapshot={}, diff_snapshot={}, change_reason="初始", created_by="owner-1", reviewed_by="owner-1")
    hidden_version = RequirementVersion(requirement_id=hidden.id, version_number=1, change_type=RequirementChangeType.INITIAL,
        version_title=hidden.title, requirement_snapshot={}, diff_snapshot={}, change_reason="初始", created_by="owner-2", reviewed_by="owner-2")
    session.add_all([version, hidden_version]); await session.flush()
    requirement.current_version_id = version.id; hidden.current_version_id = hidden_version.id
    session.add_all([
        FeatureLineage(feature_key="F-1", source_record_id=source.id, introduced_version_id=version.id, operation_type=LineageOperationType.INTRODUCED, evidence_text="播放列表"),
        FeatureLineage(feature_key="F-2", source_record_id=foreign.id, introduced_version_id=hidden_version.id, operation_type=LineageOperationType.INTRODUCED, evidence_text="不可见"),
        AnalysisResult(source_record_id=source.id, analysis_type=AnalysisType.CONFLICT_RISK, model_name="test", prompt_version="test", input_snapshot={},
            result_json={"conflict_status": "duplicate", "risks": [{"level": "high", "description": "版权待确认"}]}, duration_ms=1, attempt_number=1),
    ])
    await session.flush()
    analysis = await session.scalar(select(AnalysisResult).where(AnalysisResult.source_record_id == source.id))
    assert analysis is not None
    session.add(ReviewTask(source_record_id=source.id, analysis_result_id=analysis.id, review_status=ReviewStatus.PENDING,
        extraction_snapshot={}, candidate_snapshot=[], analysis_snapshot={}))
    conversation = RequirementConversation(conversation_key="CONV-REPORT", owner_id="owner-1", title="报告会话", summary="已确认需求：播放列表\n待确认问题：版权", business_context={})
    session.add(conversation); await session.flush()
    session.add(ConversationMessage(message_key="MSG-REPORT", conversation_id=conversation.id, source_record_id=source.id, sequence_number=1))
    await session.commit()
    return conversation, source


async def test_report_snapshot_is_owner_scoped_and_counts_real_records(report_session: AsyncSession) -> None:
    await _seed(report_session)
    snapshot = await ChatToolService(report_session, None, "owner-1").get_requirement_report_snapshot(GetRequirementReportSnapshotInput(module="音乐播放器"))  # type: ignore[arg-type]
    assert snapshot is not None
    assert snapshot.requirement_counts["total"] == 1
    assert snapshot.requirement_counts["by_status"] == {"active": 1}
    assert snapshot.review_summary["pending_count"] == 1
    assert snapshot.risk_conflict_summary["conflict_count"] == 1
    assert snapshot.requirements[0]["requirement_key"] == "REQ-PLAYER-1"


async def test_report_snapshot_supports_conversation_empty_and_inaccessible_scopes(report_session: AsyncSession) -> None:
    await _seed(report_session)
    service = ChatToolService(report_session, None, "owner-1")  # type: ignore[arg-type]
    conversation = await service.get_requirement_report_snapshot(GetRequirementReportSnapshotInput(conversation_key="CONV-REPORT"))
    absent_module = await service.get_requirement_report_snapshot(GetRequirementReportSnapshotInput(module="不存在模块"))
    denied = await ChatToolService(report_session, None, "owner-2").get_requirement_report_snapshot(GetRequirementReportSnapshotInput(conversation_key="CONV-REPORT"))  # type: ignore[arg-type]
    assert conversation is not None and conversation.conversation_summary is not None
    assert conversation.conversation_summary.pending_questions == ["版权"]
    assert absent_module is not None and absent_module.requirement_counts["total"] == 0
    assert denied is None
