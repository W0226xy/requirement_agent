from collections.abc import AsyncIterator

import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.api.main import app
from requirement_agent.application.ingestion.dispatcher import get_task_dispatcher
from requirement_agent.infrastructure.database.base import Base
from requirement_agent.infrastructure.database.session import get_session
from requirement_agent.infrastructure.storage.minio import get_object_storage


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

    def dispatch_analysis(self, source_record_id: int) -> None:
        self.source_ids.append(source_record_id)

    def dispatch_version(self, version_id: int) -> None:
        self.source_ids.append(version_id)


@pytest_asyncio.fixture
async def api_client() -> AsyncIterator[TestClient]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_object_storage] = MemoryStorage
    app.dependency_overrides[get_task_dispatcher] = RecordingDispatcher
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def test_create_replay_list_and_get_source(api_client: TestClient) -> None:
    request = {
        "submitter_id": "user-1",
        "submitter_name": "Tester",
        "raw_text": "Add export support",
        "raw_metadata": {"module": "reporting"},
    }
    headers = {"Idempotency-Key": "web-event-0001"}

    created = api_client.post("/api/v1/ingestions", json=request, headers=headers)
    replayed = api_client.post("/api/v1/ingestions", json=request, headers=headers)
    listed = api_client.get("/api/v1/source-records")
    source_id = created.json()["source"]["id"]
    detail = api_client.get(f"/api/v1/source-records/{source_id}")

    assert created.status_code == 202
    assert created.json()["replayed"] is False
    assert replayed.status_code == 200
    assert replayed.json()["replayed"] is True
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert detail.status_code == 200
    assert detail.json()["raw_metadata"] == {"module": "reporting"}


async def test_upload_pdf(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/v1/files",
        headers={"Idempotency-Key": "file-event-0001"},
        data={"submitter_id": "user-1", "submitter_name": "Tester"},
        files={"file": ("requirement.pdf", b"%PDF-1.7\n", "application/pdf")},
    )

    assert response.status_code == 202
    attachment = response.json()["source"]["attachments"][0]
    assert attachment["file_name"] == "requirement.pdf"
    assert attachment["parse_status"] == "pending"
