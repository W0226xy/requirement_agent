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
    def dispatch_source(self, source_record_id: int) -> None:
        pass

    def dispatch_analysis(self, source_record_id: int) -> None:
        pass

    def dispatch_version(self, version_id: int) -> None:
        pass


@pytest_asyncio.fixture
async def conversation_client() -> AsyncIterator[TestClient]:
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


async def test_conversation_crud_and_owner_isolation(
    conversation_client: TestClient,
) -> None:
    owner = {"X-Actor-ID": "owner-1"}
    other = {"X-Actor-ID": "owner-2"}
    created = conversation_client.post(
        "/api/v1/conversations",
        json={"title": "Release planning"},
        headers=owner,
    )
    key = created.json()["conversation_key"]

    assert created.status_code == 201
    assert created.json()["title_is_custom"] is True
    assert conversation_client.get(
        f"/api/v1/conversations/{key}", headers=other
    ).status_code == 404

    renamed = conversation_client.patch(
        f"/api/v1/conversations/{key}",
        json={"title": "Q4 release"},
        headers=owner,
    )
    listed = conversation_client.get("/api/v1/conversations", headers=owner)
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Q4 release"
    assert listed.json()["items"][0]["conversation_key"] == key

    deleted = conversation_client.delete(
        f"/api/v1/conversations/{key}",
        headers=owner,
    )
    assert deleted.status_code == 204
    assert conversation_client.get(
        f"/api/v1/conversations/{key}", headers=owner
    ).status_code == 404


async def test_text_file_association_idempotency_and_message_projection(
    conversation_client: TestClient,
) -> None:
    headers = {"X-Actor-ID": "owner-1"}
    created = conversation_client.post(
        "/api/v1/conversations",
        headers=headers,
    )
    key = created.json()["conversation_key"]
    message_headers = {
        **headers,
        "Idempotency-Key": "conversation-text-0001",
    }
    text_message = conversation_client.post(
        f"/api/v1/conversations/{key}/messages",
        data={"raw_text": "Add scheduled report exports", "actor_name": "产品经理"},
        headers=message_headers,
    )
    replay = conversation_client.post(
        f"/api/v1/conversations/{key}/messages",
        data={"raw_text": "Add scheduled report exports", "actor_name": "产品经理"},
        headers=message_headers,
    )
    file_message = conversation_client.post(
        f"/api/v1/conversations/{key}/messages",
        data={"raw_text": "Use this specification"},
        files={"file": ("spec.pdf", b"%PDF-1.7\n", "application/pdf")},
        headers={
            **headers,
            "Idempotency-Key": "conversation-file-0001",
        },
    )
    listed = conversation_client.get(
        f"/api/v1/conversations/{key}/messages",
        headers=headers,
    )
    reanalyzed = conversation_client.post(
        f"/api/v1/conversations/{key}/messages/"
        f"{text_message.json()['message']['message_key']}/reanalyze",
        headers=headers,
    )
    detail = conversation_client.get(
        f"/api/v1/conversations/{key}",
        headers=headers,
    )

    assert text_message.status_code == 202
    assert text_message.json()["message"]["source"]["submitter_name"] == "产品经理"
    assert replay.status_code == 200
    assert replay.json()["message"]["message_key"] == text_message.json()["message"][
        "message_key"
    ]
    assert file_message.status_code == 202
    assert file_message.json()["message"]["source"]["attachments"][0]["file_name"] == (
        "spec.pdf"
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 2
    assert [item["sequence_number"] for item in listed.json()["items"]] == [1, 2]
    assert listed.json()["items"][0]["latest_extraction"] is None
    assert listed.json()["items"][0]["review_task"] is None
    assert reanalyzed.status_code == 202
    assert reanalyzed.json()["queued"] is True
    assert detail.json()["title"] == "Add scheduled report exports"
    assert detail.json()["title_is_custom"] is False


async def test_message_rejects_wrong_owner(conversation_client: TestClient) -> None:
    created = conversation_client.post(
        "/api/v1/conversations",
        json={},
        headers={"X-Actor-ID": "owner-1"},
    )
    response = conversation_client.post(
        f"/api/v1/conversations/{created.json()['conversation_key']}/messages",
        data={"raw_text": "Unauthorized"},
        headers={
            "X-Actor-ID": "owner-2",
            "Idempotency-Key": "wrong-owner-0001",
        },
    )
    assert response.status_code == 404
