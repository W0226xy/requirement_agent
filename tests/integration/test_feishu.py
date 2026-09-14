import hashlib
import json
from collections.abc import AsyncIterator

import httpx
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.api.main import app
from requirement_agent.api.feishu import get_feishu_event_dispatcher
from requirement_agent.application.ingestion.service import IngestionService
from requirement_agent.connectors.feishu import (
    FeishuConnector,
    FeishuEventRequest,
    FeishuOpenAPIClient,
)
from requirement_agent.infrastructure.database.base import Base
from requirement_agent.shared.config import Settings


class MemoryStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, object_name: str, content: bytes, content_type: str) -> None:
        self.objects[object_name] = content

    async def get(self, object_name: str) -> bytes:
        return self.objects[object_name]


class RecordingDispatcher:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []
        self.source_ids: list[int] = []

    def dispatch_feishu_event(self, payload: dict[str, object]) -> None:
        self.events.append(payload)

    def dispatch_source(self, source_record_id: int) -> None:
        self.source_ids.append(source_record_id)

    def dispatch_analysis(self, source_record_id: int) -> None:
        self.source_ids.append(source_record_id)

    def dispatch_version(self, version_id: int) -> None:
        self.source_ids.append(version_id)


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def feishu_api() -> AsyncIterator[tuple[TestClient, RecordingDispatcher, Settings]]:
    dispatcher = RecordingDispatcher()
    original_settings = app.state.settings
    settings = Settings(
        feishu_app_id="cli_test_app",
        feishu_app_secret="cli_test_secret",
        feishu_verification_token="cli_test_token",
        feishu_encrypt_key="cli_test_encrypt_key",
    )
    app.state.settings = settings
    app.dependency_overrides[get_feishu_event_dispatcher] = lambda: dispatcher
    try:
        yield TestClient(app), dispatcher, settings
    finally:
        app.dependency_overrides.clear()
        app.state.settings = original_settings


def _event(
    *,
    event_id: str = "evt-0001",
    message_type: str = "text",
    content: dict[str, str] | None = None,
) -> dict[str, object]:
    return {
        "schema": "2.0",
        "header": {
            "event_id": event_id,
            "event_type": "im.message.receive_v1",
            "create_time": "1788860260000",
            "token": "cli_test_token",
            "app_id": "cli_test_app",
            "tenant_key": "tenant-1",
        },
        "event": {
            "sender": {
                "sender_id": {"open_id": "ou-user-1"},
                "sender_type": "user",
                "tenant_key": "tenant-1",
            },
            "message": {
                "message_id": f"msg-{event_id}",
                "create_time": "1788860260000",
                "chat_id": "oc-chat-1",
                "chat_type": "p2p",
                "message_type": message_type,
                "content": json.dumps(
                    content or {"text": "Add Feishu export support"}
                ),
            },
        },
    }


def _signed_headers(body: bytes, settings: Settings) -> dict[str, str]:
    timestamp = "1788860260"
    nonce = "nonce-1"
    encrypt_key = settings.feishu_encrypt_key.get_secret_value()
    signature = hashlib.sha256(
        timestamp.encode() + nonce.encode() + encrypt_key.encode() + body
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Lark-Request-Timestamp": timestamp,
        "X-Lark-Request-Nonce": nonce,
        "X-Lark-Signature": signature,
    }


async def test_url_verification_returns_challenge(
    feishu_api: tuple[TestClient, RecordingDispatcher, Settings],
) -> None:
    client, dispatcher, settings = feishu_api
    body = json.dumps(
        {
            "challenge": "challenge-value",
            "token": "cli_test_token",
            "type": "url_verification",
        },
        separators=(",", ":"),
    ).encode()

    response = client.post(
        "/api/v1/connectors/feishu/events",
        content=body,
        headers=_signed_headers(body, settings),
    )

    assert response.status_code == 200
    assert response.json() == {"challenge": "challenge-value"}
    assert dispatcher.events == []


async def test_invalid_token_and_signature_are_rejected(
    feishu_api: tuple[TestClient, RecordingDispatcher, Settings],
) -> None:
    client, dispatcher, settings = feishu_api
    payload = _event()
    payload["header"]["token"] = "wrong"  # type: ignore[index]
    body = json.dumps(payload, separators=(",", ":")).encode()

    invalid_token = client.post(
        "/api/v1/connectors/feishu/events",
        content=body,
        headers=_signed_headers(body, settings),
    )
    invalid_signature = client.post(
        "/api/v1/connectors/feishu/events",
        content=json.dumps(_event(), separators=(",", ":")).encode(),
        headers={
            "X-Lark-Request-Timestamp": "1788860260",
            "X-Lark-Request-Nonce": "nonce-1",
            "X-Lark-Signature": "invalid",
        },
    )

    assert invalid_token.status_code == 401
    assert invalid_signature.status_code == 401
    assert dispatcher.events == []


async def test_text_event_is_queued_without_verification_token(
    feishu_api: tuple[TestClient, RecordingDispatcher, Settings],
) -> None:
    client, dispatcher, settings = feishu_api
    body = json.dumps(_event(), separators=(",", ":")).encode()

    response = client.post(
        "/api/v1/connectors/feishu/events",
        content=body,
        headers=_signed_headers(body, settings),
    )

    assert response.status_code == 200
    assert response.json() == {"code": 0}
    assert len(dispatcher.events) == 1
    assert "token" not in dispatcher.events[0]["header"]  # type: ignore[operator]


async def test_repeated_event_id_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    dispatcher = RecordingDispatcher()
    storage = MemoryStorage()
    request = FeishuEventRequest.model_validate(_event())

    async with session_factory() as session:
        service = IngestionService(session, storage, dispatcher, 1024 * 1024)
        created = await service.ingest(FeishuConnector(), request)
        replayed = await service.ingest(FeishuConnector(), request)

    assert created.replayed is False
    assert replayed.replayed is True
    assert replayed.source.id == created.source.id
    assert created.source.raw_text == "Add Feishu export support"
    assert created.source.raw_metadata["chat_id"] == "oc-chat-1"


async def test_attachment_download_uses_open_api_and_ingestion_validation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/tenant_access_token/internal"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "msg": "ok",
                    "tenant_access_token": "tenant-token",
                    "expire": 7200,
                },
            )
        assert request.headers["Authorization"] == "Bearer tenant-token"
        assert request.url.params["type"] == "file"
        return httpx.Response(
            200,
            content=b"%PDF-1.7\nattachment",
            headers={"Content-Type": "application/octet-stream"},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = FeishuOpenAPIClient(
            app_id="attachment_test_app",
            app_secret="attachment_test_secret",
            base_url="https://open.feishu.invalid",
            timeout_seconds=2,
            max_download_size=1024 * 1024 + 1,
            client=http_client,
        )
        request = FeishuEventRequest.model_validate(
            _event(
                event_id="evt-file",
                message_type="file",
                content={"file_key": "file-key-1", "file_name": "requirement.pdf"},
            )
        )
        storage = MemoryStorage()
        dispatcher = RecordingDispatcher()
        async with session_factory() as session:
            service = IngestionService(session, storage, dispatcher, 1024 * 1024)
            result = await service.ingest(FeishuConnector(client), request)
        await client.download_resource(
            message_id="msg-evt-file",
            resource_key="file-key-1",
            resource_type="file",
        )

    assert result.source.attachments[0].file_name == "requirement.pdf"
    assert list(storage.objects.values()) == [b"%PDF-1.7\nattachment"]
    assert len(requests) == 3
    assert sum(
        request.url.path.endswith("/tenant_access_token/internal")
        for request in requests
    ) == 1


async def test_image_attachment_uses_detected_jpeg_extension(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tenant_access_token/internal"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "msg": "ok",
                    "tenant_access_token": "image-token",
                    "expire": 7200,
                },
            )
        return httpx.Response(
            200,
            content=b"\xff\xd8\xff\xe0image",
            headers={"Content-Type": "image/jpeg"},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = FeishuOpenAPIClient(
            app_id="image_test_app",
            app_secret="image_test_secret",
            base_url="https://open.feishu.invalid",
            timeout_seconds=2,
            max_download_size=1024 * 1024 + 1,
            client=http_client,
        )
        request = FeishuEventRequest.model_validate(
            _event(
                event_id="evt-image",
                message_type="image",
                content={"image_key": "image-key-1"},
            )
        )
        storage = MemoryStorage()
        dispatcher = RecordingDispatcher()
        async with session_factory() as session:
            service = IngestionService(session, storage, dispatcher, 1024 * 1024)
            result = await service.ingest(FeishuConnector(client), request)

    attachment = result.source.attachments[0]
    assert attachment.file_name == "msg-evt-image.jpg"
    assert attachment.file_type == "image/jpeg"
