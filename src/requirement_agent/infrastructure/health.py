import asyncio
from functools import lru_cache

from minio import Minio
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from requirement_agent.infrastructure.database.session import get_session_factory
from requirement_agent.shared.config import Settings, get_settings


class HealthChecker:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory

    async def check(self) -> dict[str, str]:
        results = await asyncio.gather(
            self._check_postgres(),
            self._check_redis(),
            self._check_minio(),
        )
        return dict(zip(("postgres", "redis", "minio"), results, strict=True))

    async def _check_postgres(self) -> str:
        try:
            async with self._session_factory() as session:
                await session.execute(text("SELECT 1"))
            return "ok"
        except Exception as exc:
            return f"error:{type(exc).__name__}"

    async def _check_redis(self) -> str:
        client = Redis.from_url(self._settings.redis_url)
        try:
            await client.ping()
            return "ok"
        except Exception as exc:
            return f"error:{type(exc).__name__}"
        finally:
            await client.aclose()

    async def _check_minio(self) -> str:
        client = Minio(
            self._settings.minio_endpoint,
            access_key=self._settings.minio_access_key,
            secret_key=self._settings.minio_secret_key,
            secure=self._settings.minio_secure,
        )
        try:
            exists = await asyncio.to_thread(
                client.bucket_exists,
                self._settings.minio_bucket,
            )
            return "ok" if exists else "error:BucketNotFound"
        except Exception as exc:
            return f"error:{type(exc).__name__}"


@lru_cache
def get_health_checker() -> HealthChecker:
    return HealthChecker(get_settings(), get_session_factory())

