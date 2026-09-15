from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from requirement_agent.ai.llm.fake import FakeLLM
from requirement_agent.ai.retrieval.hybrid import RetrievalWeights
from requirement_agent.ai.schemas.retrieval import RequirementCandidate
from requirement_agent.infrastructure.database.base import Base
from requirement_agent.infrastructure.database.models import (
    AnalysisResult,
    ConversationMessage,
    FeatureLineage,
    Requirement,
    RequirementConversation,
    RequirementFeature,
    RequirementVersion,
    ReviewTask,
    SourceRecord,
)
from requirement_agent.shared.enums import (
    AnalysisType,
    ChannelType,
    FeatureStatus,
    LineageOperationType,
    ProcessingStatus,
    RequirementChangeType,
    RequirementStatus,
)
from requirement_agent.shared.errors import CandidateScopeError, LLMServiceError
from requirement_agent.workflows.requirement_analysis import RequirementAnalysisWorkflow


class FakeRetriever:
    def __init__(self, candidates: list[RequirementCandidate]) -> None:
        self.candidates = candidates

    async def search(
        self,
        query: str,
        *,
        source_record_id: int | None,
        query_modules: list[str],
        filters: object = None,
    ) -> list[RequirementCandidate]:
        return self.candidates


class FailingEmbeddingModel:
    @property
    def model_name(self) -> str:
        return "failed-embedding"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise LLMServiceError("embedding service unavailable")


@pytest_asyncio.fixture
async def workflow_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        session.add(
            SourceRecord(
                id=1,
                source_key="SRC-WORKFLOW",
                channel_type=ChannelType.WEB_FORM,
                external_event_id="workflow-event-1",
                submitter_id="user-1",
                submitter_name="Tester",
                raw_text="Users need to export reports as PDF.",
                raw_metadata={},
                received_at=datetime.now(UTC),
                processing_status=ProcessingStatus.PARSING,
            )
        )
        await session.commit()
        yield session
    await engine.dispose()


def extraction() -> dict[str, object]:
    return {
        "requirement_summary": "Export reports",
        "requirement_description": "Users need to export reports as PDF.",
        "functional_modules": ["reporting"],
        "acceptance_criteria": ["A PDF is downloaded."],
        "clarification_questions": [],
        "entities": {
            "platform": "web",
            "page": "reports",
            "target": "report",
            "actors": ["analyst"],
        },
    }


def conflict(requirement_id: str = "REQ-001") -> dict[str, object]:
    return {
        "conflict_status": "duplicate",
        "related_requirement_ids": [requirement_id],
        "conflicts": [
            {
                "requirement_id": requirement_id,
                "type": "duplicate",
                "description": "Same export behavior.",
                "evidence": "Both require PDF report export.",
                "confidence": 0.95,
            }
        ],
        "risks": [],
        "proposed_operations": [],
        "clarification_questions": [],
    }


def candidate(
    key: str = "REQ-001", *, similarity_score: float = 0.95
) -> RequirementCandidate:
    return RequirementCandidate.model_validate(
        {
            "requirement_key": key,
            "version_number": 2,
            "title": "Export reports",
            "functional_modules": ["reporting"],
            "features": [],
            "similarity_score": similarity_score,
            "matched_text": "Export reports as PDF.",
            "sources": [],
        }
    )


def insufficient_info_conflict() -> dict[str, object]:
    return {
        "conflict_status": "insufficient_info",
        "related_requirement_ids": [],
        "conflicts": [],
        "risks": [],
        "proposed_operations": [],
        "clarification_questions": [],
    }


async def test_workflow_reaches_pending_review(
    workflow_session: AsyncSession,
) -> None:
    chat_model = FakeLLM([extraction(), conflict()], model_name="fake-chat")
    embedding_model = FakeLLM([], model_name="fake-embedding")
    workflow = RequirementAnalysisWorkflow(
        workflow_session,
        chat_model,
        embedding_model,
        max_retries=2,
        retrieval_weights=RetrievalWeights(keyword=0.4, vector=0.4, business=0.2),
        candidate_limit=20,
        retriever=FakeRetriever([candidate()]),
    )

    state = await workflow.run(1)
    source = await workflow_session.get(SourceRecord, 1)
    records = (
        await workflow_session.execute(
            select(AnalysisResult).order_by(AnalysisResult.id)
        )
    ).scalars().all()
    review_tasks = (
        await workflow_session.execute(select(ReviewTask))
    ).scalars().all()

    assert source is not None
    assert source.processing_status == ProcessingStatus.PENDING_REVIEW
    assert state["conflict_analysis"]["conflict_status"] == "duplicate"
    assert len(records) == 2
    assert len(review_tasks) == 1
    assert review_tasks[0].analysis_snapshot["conflict_status"] == "duplicate"


async def test_workflow_rejects_requirement_outside_candidates(
    workflow_session: AsyncSession,
) -> None:
    chat_model = FakeLLM(
        [extraction(), conflict("REQ-INVENTED")],
        model_name="fake-chat",
    )
    embedding_model = FakeLLM([], model_name="fake-embedding")
    workflow = RequirementAnalysisWorkflow(
        workflow_session,
        chat_model,
        embedding_model,
        max_retries=2,
        retrieval_weights=RetrievalWeights(keyword=0.4, vector=0.4, business=0.2),
        candidate_limit=20,
        retriever=FakeRetriever([candidate()]),
    )

    with pytest.raises(CandidateScopeError):
        await workflow.run(1)

    source = await workflow_session.get(SourceRecord, 1)
    assert source is not None
    assert source.processing_status == ProcessingStatus.ANALYSIS_FAILED


async def test_conflict_snapshot_filters_low_scores_before_limit(
    workflow_session: AsyncSession,
) -> None:
    workflow = RequirementAnalysisWorkflow(
        workflow_session,
        FakeLLM([extraction(), insufficient_info_conflict()], model_name="fake-chat"),
        FakeLLM([], model_name="fake-embedding"),
        max_retries=2,
        retrieval_weights=RetrievalWeights(keyword=0.4, vector=0.4, business=0.2),
        candidate_limit=2,
        retrieval_min_similarity_score=0.40,
        retriever=FakeRetriever(
            [
                candidate("REQ-MUSIC", similarity_score=0.39),
                candidate("REQ-HABIT", similarity_score=0.40),
                candidate("REQ-RELATED", similarity_score=0.72),
                candidate("REQ-LIMITED", similarity_score=0.91),
            ]
        ),
    )

    state = await workflow.run(1)
    conflict_record = (
        await workflow_session.execute(
            select(AnalysisResult)
            .where(AnalysisResult.analysis_type == AnalysisType.CONFLICT_RISK)
            .order_by(AnalysisResult.id.desc())
        )
    ).scalar_one()
    candidates = conflict_record.input_snapshot["candidates"]

    assert [item["requirement_key"] for item in state["candidates"]] == [
        "REQ-LIMITED",
        "REQ-RELATED",
    ]
    assert [item["requirement_key"] for item in candidates] == [
        "REQ-LIMITED",
        "REQ-RELATED",
    ]
    assert all(item["similarity_score"] >= 0.40 for item in candidates)


async def test_workflow_allows_empty_candidates_after_threshold(
    workflow_session: AsyncSession,
) -> None:
    workflow = RequirementAnalysisWorkflow(
        workflow_session,
        FakeLLM([extraction(), insufficient_info_conflict()], model_name="fake-chat"),
        FakeLLM([], model_name="fake-embedding"),
        max_retries=2,
        retrieval_weights=RetrievalWeights(keyword=0.4, vector=0.4, business=0.2),
        candidate_limit=20,
        retrieval_min_similarity_score=0.40,
        retriever=FakeRetriever([candidate("REQ-LOW", similarity_score=0.39)]),
    )

    state = await workflow.run(1)
    conflict_record = (
        await workflow_session.execute(
            select(AnalysisResult)
            .where(AnalysisResult.analysis_type == AnalysisType.CONFLICT_RISK)
            .order_by(AnalysisResult.id.desc())
        )
    ).scalar_one()

    assert state["candidates"] == []
    assert conflict_record.input_snapshot["candidates"] == []


async def test_context_is_isolated_bounded_and_updates_memory(
    workflow_session: AsyncSession,
) -> None:
    same_source = SourceRecord(
        id=2,
        source_key="SRC-SAME-CONTEXT",
        channel_type=ChannelType.WEB_FORM,
        external_event_id="same-context-event",
        submitter_id="user-1",
        submitter_name="Tester",
        raw_text="The export format must preserve dashboard filters.",
        raw_metadata={"input_surface": "conversation"},
        received_at=datetime.now(UTC),
        processing_status=ProcessingStatus.PENDING_REVIEW,
    )
    other_source = SourceRecord(
        id=3,
        source_key="SRC-OTHER-CONTEXT",
        channel_type=ChannelType.WEB_FORM,
        external_event_id="other-context-event",
        submitter_id="user-1",
        submitter_name="Tester",
        raw_text="SECRET FROM ANOTHER CONVERSATION",
        raw_metadata={"input_surface": "conversation"},
        received_at=datetime.now(UTC),
        processing_status=ProcessingStatus.PENDING_REVIEW,
    )
    conversation = RequirementConversation(
        conversation_key="CONV-CONTEXT",
        owner_id="user-1",
        title="Context",
        summary="Reporting preferences",
        business_context={"modules": ["reporting"]},
    )
    other_conversation = RequirementConversation(
        conversation_key="CONV-OTHER",
        owner_id="user-1",
        title="Other",
    )
    workflow_session.add_all(
        [same_source, other_source, conversation, other_conversation]
    )
    await workflow_session.flush()
    workflow_session.add_all(
        [
            ConversationMessage(
                message_key="MSG-SAME",
                conversation_id=conversation.id,
                source_record_id=same_source.id,
                sequence_number=1,
            ),
            ConversationMessage(
                message_key="MSG-CURRENT",
                conversation_id=conversation.id,
                source_record_id=1,
                sequence_number=2,
            ),
            ConversationMessage(
                message_key="MSG-OTHER",
                conversation_id=other_conversation.id,
                source_record_id=other_source.id,
                sequence_number=1,
            ),
        ]
    )
    await workflow_session.commit()
    workflow = RequirementAnalysisWorkflow(
        workflow_session,
        FakeLLM([extraction(), conflict()], model_name="fake-chat"),
        FakeLLM([], model_name="fake-embedding"),
        max_retries=2,
        retrieval_weights=RetrievalWeights(keyword=0.4, vector=0.4, business=0.2),
        candidate_limit=20,
        retriever=FakeRetriever([candidate()]),
        context_message_limit=1,
        context_char_limit=500,
        memory_summary_limit=80,
    )

    state = await workflow.run(1)
    refreshed = await workflow_session.get(RequirementConversation, conversation.id)
    extraction_record = (
        await workflow_session.execute(
            select(AnalysisResult)
            .where(AnalysisResult.source_record_id == 1)
            .order_by(AnalysisResult.id)
        )
    ).scalars().first()

    assert "dashboard filters" in state["conversation_context"]
    assert "SECRET FROM ANOTHER CONVERSATION" not in state["conversation_context"]
    assert len(state["conversation_context"]) <= 500
    assert extraction_record is not None
    assert extraction_record.input_snapshot["conversation_context"] == state[
        "conversation_context"
    ]
    assert refreshed is not None
    assert refreshed.memory_revision == 1
    assert refreshed.memory_covered_sequence == 2
    assert refreshed.business_context["modules"] == ["reporting"]
    assert "Export reports" in refreshed.summary


async def test_embedding_failure_preserves_original_source(
    workflow_session: AsyncSession,
) -> None:
    original_text = "Users need to export reports as PDF."
    workflow = RequirementAnalysisWorkflow(
        workflow_session,
        FakeLLM([extraction()], model_name="fake-chat"),
        FailingEmbeddingModel(),
        max_retries=2,
        retrieval_weights=RetrievalWeights(keyword=0.4, vector=0.4, business=0.2),
        candidate_limit=20,
    )

    with pytest.raises(LLMServiceError, match="embedding service unavailable"):
        await workflow.run(1)

    source = await workflow_session.get(SourceRecord, 1)
    records = (
        await workflow_session.execute(
            select(AnalysisResult).order_by(AnalysisResult.id)
        )
    ).scalars().all()

    assert source is not None
    assert source.raw_text == original_text
    assert source.processing_status == ProcessingStatus.ANALYSIS_FAILED
    assert [record.analysis_type.value for record in records] == [
        "extraction",
        "embedding",
    ]
    assert records[1].error_message == "LLMServiceError: embedding service unavailable"


async def test_exact_formal_source_is_injected_as_duplicate_candidate(
    workflow_session: AsyncSession,
) -> None:
    source = await workflow_session.get(SourceRecord, 1)
    assert source is not None
    historical_source = SourceRecord(
        id=2,
        source_key="SRC-HISTORICAL",
        channel_type=ChannelType.WEB_FORM,
        external_event_id="historical-event",
        submitter_id="user-2",
        submitter_name="Historical user",
        raw_text=source.raw_text,
        raw_metadata={},
        received_at=datetime.now(UTC),
        processing_status=ProcessingStatus.VERSIONED,
    )
    requirement = Requirement(
        requirement_key="REQ-EXACT",
        title="Export reports",
        status=RequirementStatus.ACTIVE,
        functional_modules=["reporting"],
        extra_fields={},
    )
    workflow_session.add_all([historical_source, requirement])
    await workflow_session.flush()
    version = RequirementVersion(
        requirement_id=requirement.id,
        version_number=1,
        change_type=RequirementChangeType.INITIAL,
        version_title=requirement.title,
        requirement_snapshot={},
        diff_snapshot={},
        change_reason="Initial",
        created_by="reviewer",
        reviewed_by="reviewer",
    )
    workflow_session.add(version)
    await workflow_session.flush()
    requirement.current_version_id = version.id
    workflow_session.add_all(
        [
            RequirementFeature(
                feature_key="FEAT-001",
                version_id=version.id,
                module="reporting",
                feature_title="Export reports",
                feature_description=source.raw_text,
                acceptance_criteria=[],
                feature_status=FeatureStatus.ACTIVE,
                sort_order=1,
            ),
            FeatureLineage(
                feature_key="FEAT-001",
                source_record_id=historical_source.id,
                introduced_version_id=version.id,
                operation_type=LineageOperationType.INTRODUCED,
                evidence_text="Original request",
            ),
        ]
    )
    await workflow_session.commit()
    workflow = RequirementAnalysisWorkflow(
        workflow_session,
        FakeLLM(
            [
                extraction(),
                {
                    "conflict_status": "insufficient_info",
                    "related_requirement_ids": [],
                    "conflicts": [],
                    "risks": [],
                    "proposed_operations": [],
                    "clarification_questions": [],
                },
            ],
            model_name="fake-chat",
        ),
        FakeLLM([], model_name="fake-embedding"),
        max_retries=2,
        retrieval_weights=RetrievalWeights(keyword=0.4, vector=0.4, business=0.2),
        candidate_limit=20,
        retriever=FakeRetriever([]),
    )

    state = await workflow.run(1)

    assert state["candidates"][0]["requirement_key"] == "REQ-EXACT"
    assert state["conflict_analysis"]["conflict_status"] == "duplicate"
    assert state["conflict_analysis"]["related_requirement_ids"] == ["REQ-EXACT"]
    assert state["conflict_analysis"]["proposed_operations"] == []
