from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.api.main import app
from requirement_agent.application.ingestion.dispatcher import get_task_dispatcher
from requirement_agent.infrastructure.database.base import Base
from requirement_agent.infrastructure.database.models import (
    AnalysisResult,
    ReviewTask,
    SourceRecord,
)
from requirement_agent.infrastructure.database.session import get_session
from requirement_agent.shared.enums import (
    AnalysisType,
    ChannelType,
    ProcessingStatus,
    ReviewStatus,
)


class RecordingDispatcher:
    def __init__(self) -> None:
        self.version_ids: list[int] = []
        self.source_ids: list[int] = []

    def dispatch_source(self, source_record_id: int) -> None:
        self.source_ids.append(source_record_id)

    def dispatch_analysis(self, source_record_id: int) -> None:
        pass

    def dispatch_version(self, version_id: int) -> None:
        self.version_ids.append(version_id)


@pytest_asyncio.fixture
async def review_api_client() -> AsyncIterator[tuple[TestClient, RecordingDispatcher]]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        source = SourceRecord(
            source_key="SRC-REVIEW",
            channel_type=ChannelType.WEB_FORM,
            external_event_id="review-event",
            submitter_id="user-1",
            submitter_name="Tester",
            raw_text="Add PDF export",
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
        failed_analysis = AnalysisResult(
            source_record_id=source.id,
            analysis_type=AnalysisType.EMBEDDING,
            model_name="fake",
            prompt_version="test",
            input_snapshot={},
            result_json=None,
            duration_ms=1,
            error_message="embedding unavailable",
            attempt_number=1,
        )
        session.add(failed_analysis)
        session.add(
            ReviewTask(
                source_record_id=source.id,
                analysis_result_id=analysis.id,
                review_status=ReviewStatus.PENDING,
                extraction_snapshot={"requirement_summary": "PDF export"},
                candidate_snapshot=[],
                analysis_snapshot={"conflict_status": "none"},
            )
        )
        await session.commit()

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    dispatcher = RecordingDispatcher()
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_task_dispatcher] = lambda: dispatcher
    try:
        yield TestClient(app), dispatcher
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def test_approve_and_query_requirement(
    review_api_client: tuple[TestClient, RecordingDispatcher],
) -> None:
    client, dispatcher = review_api_client
    headers = {"X-Actor-ID": "reviewer-1", "X-Actor-Role": "reviewer"}
    payload = {
        "decision": "create",
        "title": "Report exports",
        "target_requirement_id": None,
        "operations": [
            {
                "operation": "add",
                "feature_key": None,
                "content": {
                    "module": "reporting",
                    "feature_title": "Export PDF",
                    "feature_description": "Export reports as PDF",
                    "acceptance_criteria": ["A PDF file is downloaded"],
                },
                "source_record_id": 1,
                "reason": "New requested capability",
            }
        ],
        "comment": "Approved",
    }

    approved = client.post(
        "/api/v1/review-tasks/1/approve",
        json=payload,
        headers=headers,
    )
    replayed = client.post(
        "/api/v1/review-tasks/1/approve",
        json=payload,
        headers=headers,
    )
    requirement_id = approved.json()["requirement_id"]
    detail = client.get(f"/api/v1/requirements/{requirement_id}")
    versions = client.get(f"/api/v1/requirements/{requirement_id}/versions")
    diff = client.get(f"/api/v1/requirements/{requirement_id}/diff")

    assert approved.status_code == 200
    assert replayed.status_code == 200
    assert replayed.json()["version_id"] == approved.json()["version_id"]
    assert detail.status_code == 200
    assert detail.json()["features"][0]["feature_key"] == "FEAT-001"
    assert detail.json()["features"][0]["lineage"][0]["source_record_id"] == 1
    assert len(versions.json()["items"]) == 1
    assert diff.json()["diff"]["operations"][0]["operation"] == "add"
    assert dispatcher.version_ids == [
        approved.json()["version_id"],
        approved.json()["version_id"],
    ]


async def test_approval_requires_reviewer_role(
    review_api_client: tuple[TestClient, RecordingDispatcher],
) -> None:
    client, _ = review_api_client
    response = client.post(
        "/api/v1/review-tasks/1/approve",
        headers={"X-Actor-ID": "user-1", "X-Actor-Role": "viewer"},
        json={
            "decision": "create",
            "title": "Report exports",
            "operations": [
                {
                    "operation": "add",
                    "feature_key": None,
                    "content": {
                        "module": "reporting",
                        "feature_title": "Export PDF",
                        "feature_description": "Export reports",
                        "acceptance_criteria": [],
                    },
                    "source_record_id": 1,
                    "reason": "Requested",
                }
            ],
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"


async def test_list_and_retry_failed_job(
    review_api_client: tuple[TestClient, RecordingDispatcher],
) -> None:
    client, dispatcher = review_api_client
    failed = client.get("/api/v1/failed-jobs")
    job_id = failed.json()["items"][0]["id"]
    retried = client.post(
        f"/api/v1/failed-jobs/{job_id}/retry",
        headers={"X-Actor-ID": "reviewer-1", "X-Actor-Role": "reviewer"},
    )

    assert failed.status_code == 200
    assert failed.json()["total"] == 1
    assert retried.status_code == 202
    assert retried.json() == {"source_record_id": 1, "queued": True}
    assert dispatcher.source_ids == [1]


async def test_filter_review_tasks_by_source(
    review_api_client: tuple[TestClient, RecordingDispatcher],
) -> None:
    client, _ = review_api_client

    found = client.get("/api/v1/review-tasks?source_record_id=1")
    missing = client.get("/api/v1/review-tasks?source_record_id=999")

    assert found.status_code == 200
    assert found.json()["total"] == 1
    assert found.json()["items"][0]["source_record_id"] == 1
    assert missing.status_code == 200
    assert missing.json()["total"] == 0
