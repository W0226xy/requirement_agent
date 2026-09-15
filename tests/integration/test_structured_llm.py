from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from requirement_agent.ai.llm.fake import FakeLLM
from requirement_agent.ai.schemas.analysis import ConflictAnalysis, RequirementExtraction
from requirement_agent.ai.structured import StructuredLLM
from requirement_agent.infrastructure.database.base import Base
from requirement_agent.infrastructure.database.models import AnalysisResult, SourceRecord
from requirement_agent.shared.enums import AnalysisType, ChannelType, ProcessingStatus
from requirement_agent.shared.errors import (
    LLMOutputBudgetExceededError,
    StructuredOutputError,
)


@pytest_asyncio.fixture
async def analysis_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            SourceRecord(
                id=1,
                source_key="SRC-TEST",
                channel_type=ChannelType.WEB_FORM,
                external_event_id="event-1",
                submitter_id="user-1",
                submitter_name="Tester",
                raw_text="Export reports",
                raw_metadata={},
                received_at=datetime.now(UTC),
                processing_status=ProcessingStatus.PARSING,
            )
        )
        await session.commit()
        yield session
    await engine.dispose()


def valid_extraction() -> dict[str, object]:
    return {
        "requirement_summary": "Export reports",
        "requirement_description": "Users export reports as PDF.",
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


class OutputBudgetExhaustedModel:
    model_name = "budget-exhausted-model"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        analysis_type: str | None = None,
    ) -> str:
        self.calls += 1
        raise LLMOutputBudgetExceededError(
            "model output budget exhausted before final content was generated"
        )


async def test_invalid_output_is_recorded_then_retried(
    analysis_session: AsyncSession,
) -> None:
    model = FakeLLM(['{"functional_modules": null}', valid_extraction()])
    runner = StructuredLLM(model, analysis_session, max_retries=2)

    result = await runner.generate(
        source_record_id=1,
        analysis_type=AnalysisType.EXTRACTION,
        prompt_version="test-v1",
        schema=RequirementExtraction,
        messages=[{"role": "user", "content": "extract"}],
        input_snapshot={"source": "Export reports"},
    )
    rows = (
        await analysis_session.execute(
            select(AnalysisResult).order_by(AnalysisResult.attempt_number)
        )
    ).scalars().all()

    assert result.requirement_summary == "Export reports"
    assert len(model.calls) == 2
    assert len(rows) == 2
    assert rows[0].error_message is not None
    assert rows[1].result_json is not None


async def test_invalid_output_fails_after_three_attempts(
    analysis_session: AsyncSession,
) -> None:
    model = FakeLLM(["not-json", "[]", '{"functional_modules": null}'])
    runner = StructuredLLM(model, analysis_session, max_retries=2)

    with pytest.raises(StructuredOutputError, match="after 3 attempts"):
        await runner.generate(
            source_record_id=1,
            analysis_type=AnalysisType.EXTRACTION,
            prompt_version="test-v1",
            schema=RequirementExtraction,
            messages=[{"role": "user", "content": "extract"}],
            input_snapshot={"source": "Export reports"},
        )

    rows = (await analysis_session.execute(select(AnalysisResult))).scalars().all()
    assert len(rows) == 3
    assert all(row.error_message for row in rows)


async def test_empty_content_error_is_recorded_without_json_correction_retry(
    analysis_session: AsyncSession,
) -> None:
    model = OutputBudgetExhaustedModel()
    runner = StructuredLLM(model, analysis_session, max_retries=2)

    with pytest.raises(LLMOutputBudgetExceededError, match="output budget exhausted"):
        await runner.generate(
            source_record_id=1,
            analysis_type=AnalysisType.CONFLICT_RISK,
            prompt_version="test-v1",
            schema=ConflictAnalysis,
            messages=[{"role": "user", "content": "analyze"}],
            input_snapshot={"candidates": []},
        )

    rows = (await analysis_session.execute(select(AnalysisResult))).scalars().all()
    assert model.calls == 1
    assert len(rows) == 1
    assert rows[0].raw_output is None
    assert rows[0].error_message is not None
    assert "LLMOutputBudgetExceededError" in rows[0].error_message
