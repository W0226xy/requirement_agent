import asyncio
import json
import logging
from collections.abc import Mapping
from typing import Any, cast

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from requirement_agent.api.conversations import _sse
from requirement_agent.infrastructure import events
from requirement_agent.infrastructure.events import ConversationEventService, publish_event


class FakeRedis:
    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.expirations: dict[str, int] = {}
        self.closed = False

    async def xadd(
        self,
        key: str,
        fields: Mapping[str, str],
        *,
        maxlen: int,
        approximate: bool,
    ) -> str:
        del maxlen, approximate
        entries = self.streams.setdefault(key, [])
        event_id = f"{len(entries) + 1}-0"
        entries.append((event_id, dict(fields)))
        return event_id

    async def expire(self, key: str, ttl_seconds: int) -> bool:
        self.expirations[key] = ttl_seconds
        return True

    async def xread(
        self,
        streams: Mapping[str, str],
        *,
        block: int,
        count: int,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        del block
        key, cursor = next(iter(streams.items()))
        entries = [
            row
            for row in self.streams.get(key, [])
            if _stream_id(row[0]) > _stream_id(cursor)
        ]
        return [(key, entries[:count])] if entries else []

    async def aclose(self) -> None:
        self.closed = True


class FailingRedis(FakeRedis):
    async def xadd(
        self,
        key: str,
        fields: Mapping[str, str],
        *,
        maxlen: int,
        approximate: bool,
    ) -> str:
        del key, fields, maxlen, approximate
        raise RedisConnectionError("redis unavailable")


class BlockingRedis(FakeRedis):
    def __init__(self) -> None:
        super().__init__()
        self.read_started = asyncio.Event()

    async def xread(
        self,
        streams: Mapping[str, str],
        *,
        block: int,
        count: int,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        del streams, block, count
        self.read_started.set()
        await asyncio.Future()
        return []


def _redis(fake: FakeRedis) -> Redis:
    return cast(Redis, cast(Any, fake))


def _stream_id(value: str) -> tuple[int, int]:
    first, second = value.split("-", maxsplit=1)
    return int(first), int(second)


@pytest.mark.asyncio
async def test_publish_event_round_trip_preserves_pev_and_async_task_events() -> None:
    fake = FakeRedis()
    publisher = ConversationEventService(_redis(fake))
    previous = events._publisher
    events._publisher = publisher
    reader = ConversationEventService(_redis(fake))
    try:
        await publish_event(
            "MSG-REQUEST-A",
            "agent_planning",
            {"message": "正在生成执行计划", "run_id": "run-7"},
            execution_id="run-7",
        )
        await publish_event(
            "MSG-REQUEST-A",
            "status",
            {"stage": "extracting", "message": "正在提取结构化需求"},
            execution_id="source-42",
        )
        await publish_event(
            "MSG-REQUEST-A",
            "delta",
            {"text": "普通会话增量"},
            execution_id="chat-42",
        )

        rows = await reader.read("MSG-REQUEST-A", "0-0", block_ms=0)
    finally:
        events._publisher = previous
        await reader.close()

    assert [row[1] for row in rows] == ["agent_planning", "status", "delta"]
    assert rows[0][2]["message_key"] == "MSG-REQUEST-A"
    assert rows[0][2]["execution_id"] == "run-7"
    assert rows[0][2]["run_id"] == "run-7"
    assert isinstance(rows[0][2]["timestamp"], str)
    assert rows[2][2]["text"] == "普通会话增量"
    assert fake.expirations[ConversationEventService.key("MSG-REQUEST-A")] == 86_400


@pytest.mark.asyncio
async def test_streams_are_isolated_by_message_request() -> None:
    fake = FakeRedis()
    service = ConversationEventService(_redis(fake))

    await service.publish("MSG-CONVERSATION-A", "agent_completed", {}, execution_id="run-a")
    await service.publish(
        "MSG-CONVERSATION-B",
        "tool_call_completed",
        {"tool_name": "search"},
        execution_id="run-b",
    )

    first = await service.read("MSG-CONVERSATION-A", "0-0", block_ms=0)
    second = await service.read("MSG-CONVERSATION-B", "0-0", block_ms=0)

    assert [row[1] for row in first] == ["agent_completed"]
    assert [row[1] for row in second] == ["tool_call_completed"]
    assert first[0][2]["execution_id"] == "run-a"
    assert second[0][2]["execution_id"] == "run-b"


def test_sse_uses_named_event_json_and_double_newline() -> None:
    rendered = _sse("agent_planning", "17-0", {"message": "规划中"})

    assert rendered.startswith("id: 17-0\nevent: agent_planning\ndata: ")
    assert rendered.endswith("\n\n")
    assert json.loads(rendered.split("data: ", maxsplit=1)[1]) == {"message": "规划中"}


@pytest.mark.asyncio
async def test_cancelled_subscription_can_be_closed() -> None:
    fake = BlockingRedis()
    service = ConversationEventService(_redis(fake))
    task = asyncio.create_task(service.read("MSG-CANCEL", "0-0"))
    await fake.read_started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await service.close()

    assert fake.closed is True


@pytest.mark.asyncio
async def test_redis_publish_failure_is_logged_and_degrades(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = ConversationEventService(_redis(FailingRedis()))

    with caplog.at_level(logging.ERROR):
        event_id = await service.publish(
            "MSG-FAIL",
            "status",
            {"stage": "queued"},
            execution_id="source-1",
        )

    assert event_id is None
    assert "conversation_event_publish_failed" in caplog.text


@pytest.mark.asyncio
async def test_non_json_payload_fails_loudly(caplog: pytest.LogCaptureFixture) -> None:
    service = ConversationEventService(_redis(FakeRedis()))

    with caplog.at_level(logging.ERROR), pytest.raises(TypeError):
        await service.publish(
            "MSG-INVALID",
            "status",
            {"unsafe": object()},
            execution_id="source-1",
        )

    assert "conversation_event_serialization_failed" in caplog.text


def test_api_worker_and_task_modules_import_event_api() -> None:
    from apps.api.main import app
    from apps.worker.celery_app import celery_app
    from requirement_agent.application.conversations import tasks as conversation_tasks
    from requirement_agent.application.ingestion import tasks as ingestion_tasks

    assert app is not None
    assert celery_app is not None
    assert conversation_tasks.publish_event is publish_event
    assert ingestion_tasks.publish_event is publish_event
