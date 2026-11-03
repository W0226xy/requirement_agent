"""Durable, cross-process progress events backed by Redis Streams.

Streams are deliberately separate from Celery's broker keys. They are only a
delivery optimisation: PostgreSQL remains the authority for message state.
"""

import json
import logging
import re
from asyncio import AbstractEventLoop, get_running_loop
from datetime import UTC, datetime
from time import perf_counter
from typing import cast
from weakref import WeakKeyDictionary

from redis.asyncio import Redis
from redis.exceptions import RedisError

from requirement_agent.shared.config import get_settings

logger = logging.getLogger(__name__)

type EventData = dict[str, object]
type EventRow = tuple[str, str, EventData]

_STREAM_PREFIX = "requirement-agent:sse:v1"
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_DEFAULT_MAXLEN = 1_000
_DEFAULT_TTL_SECONDS = 86_400
_DEFAULT_BLOCK_MS = 15_000
_DEFAULT_BATCH_SIZE = 100


class ConversationEventService:
    """Publish and consume the event stream for one conversation message."""

    def __init__(
        self,
        redis: Redis | None = None,
        *,
        maxlen: int = _DEFAULT_MAXLEN,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    ) -> None:
        # Redis.from_url creates a client/pool but performs no network I/O. The
        # first publish/read therefore remains the connection boundary.
        self._redis = redis or Redis.from_url(
            get_settings().redis_url,
            decode_responses=True,
        )
        self._maxlen = maxlen
        self._ttl_seconds = ttl_seconds

    @staticmethod
    def key(message_key: str) -> str:
        """Return the only Redis key format used by publishers and readers."""
        _validate_identifier("message_key", message_key)
        return f"{_STREAM_PREFIX}:{message_key}"

    async def publish(
        self,
        message_key: str,
        event: str,
        payload: EventData,
        *,
        execution_id: str,
    ) -> str | None:
        """Append an event, returning its Redis Stream ID when available.

        Redis availability is deliberately best-effort: durable database state
        remains authoritative and the API already polls it as a fallback. A
        Redis failure is logged and returns ``None``; invalid local data raises
        so programming errors are not mistaken for an infrastructure outage.
        """
        key = self.key(message_key)
        _validate_identifier("event", event)
        _validate_identifier("execution_id", execution_id)
        if not isinstance(payload, dict):
            raise TypeError("event payload must be a dictionary")

        body: EventData = {
            **payload,
            "message_key": message_key,
            "execution_id": execution_id,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        try:
            encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            logger.exception(
                "conversation_event_serialization_failed event=%s message=%s",
                event,
                message_key,
            )
            raise

        started = perf_counter()
        try:
            event_id = await self._redis.xadd(
                key,
                {"event": event, "data": encoded},
                maxlen=self._maxlen,
                approximate=True,
            )
            await self._redis.expire(key, self._ttl_seconds)
        except (RedisError, OSError, TimeoutError):
            logger.exception(
                "conversation_event_publish_failed event=%s message=%s",
                event,
                message_key,
            )
            return None

        if event == "delta":
            text = payload.get("text")
            logger.info(
                "conversation_event_published execution_id=%s message_key=%s "
                "event=%s elapsed_ms=%d length=%d",
                execution_id,
                message_key,
                event,
                round((perf_counter() - started) * 1_000),
                len(text) if isinstance(text, str) else 0,
            )
        return _as_text(event_id)

    async def read(
        self,
        message_key: str,
        cursor: str,
        *,
        block_ms: int = _DEFAULT_BLOCK_MS,
    ) -> list[EventRow]:
        """Read events after ``cursor`` without a replay/live hand-off gap."""
        key = self.key(message_key)
        try:
            rows = await self._redis.xread(
                {key: cursor},
                block=block_ms,
                count=_DEFAULT_BATCH_SIZE,
            )
        except (RedisError, OSError, TimeoutError):
            logger.exception("conversation_event_read_failed message=%s", message_key)
            raise

        result: list[EventRow] = []
        for _, entries in rows:
            for event_id, fields in entries:
                try:
                    event = _as_text(fields["event"])
                    data = json.loads(_as_text(fields["data"]))
                    if not isinstance(data, dict):
                        raise TypeError("event data must be a JSON object")
                    result.append((_as_text(event_id), event, cast(EventData, data)))
                except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
                    logger.warning(
                        "conversation_event_invalid message=%s id=%s",
                        message_key,
                        _as_text(event_id),
                    )
        return result

    async def close(self) -> None:
        """Release this service's Redis client and all pooled connections."""
        await self._redis.aclose()


_publisher: ConversationEventService | None = None
_publishers: WeakKeyDictionary[AbstractEventLoop, ConversationEventService] = WeakKeyDictionary()


def _publisher_service() -> ConversationEventService:
    """Return one publisher per event loop.

    API and each Celery child normally keep one long-lived loop, so events reuse
    a connection pool. Keeping the loop in the cache key also prevents clients
    from being reused by test runners or hosts that intentionally rotate loops.
    ``_publisher`` is an explicit test override.
    """
    if _publisher is not None:
        return _publisher
    loop = get_running_loop()
    publisher = _publishers.get(loop)
    if publisher is None:
        publisher = ConversationEventService()
        _publishers[loop] = publisher
    return publisher


async def publish_event(
    message_key: str,
    event: str,
    payload: EventData,
    *,
    execution_id: str,
) -> str | None:
    """Publish an event using the process-local Redis connection pool."""
    return await _publisher_service().publish(
        message_key,
        event,
        payload,
        execution_id=execution_id,
    )


async def close_event_publisher() -> None:
    """Close the publisher associated with the current event loop."""
    global _publisher
    if _publisher is not None:
        publisher, _publisher = _publisher, None
        await publisher.close()
        return
    try:
        publisher = _publishers.pop(get_running_loop())
    except KeyError:
        return
    await publisher.close()


def _validate_identifier(name: str, value: str) -> None:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise ValueError(f"{name} contains unsupported characters")


def _as_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)
