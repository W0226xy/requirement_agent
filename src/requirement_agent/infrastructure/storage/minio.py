import asyncio
from functools import lru_cache
from io import BytesIO

from minio import Minio

from requirement_agent.shared.config import get_settings
from requirement_agent.shared.errors import ObjectStorageError


class MinioObjectStorage:
    def __init__(self, client: Minio, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    async def put(self, object_name: str, content: bytes, content_type: str) -> None:
        try:
            await asyncio.to_thread(
                self._client.put_object,
                self._bucket,
                object_name,
                BytesIO(content),
                len(content),
                content_type,
            )
        except Exception as exc:
            raise ObjectStorageError(f"failed to store object {object_name}") from exc

    async def get(self, object_name: str) -> bytes:
        try:
            response = await asyncio.to_thread(
                self._client.get_object,
                self._bucket,
                object_name,
            )
            try:
                return await asyncio.to_thread(response.read)
            finally:
                response.close()
                response.release_conn()
        except Exception as exc:
            raise ObjectStorageError(f"failed to read object {object_name}") from exc


@lru_cache
def get_object_storage() -> MinioObjectStorage:
    settings = get_settings()
    client = Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )
    return MinioObjectStorage(client, settings.minio_bucket)

