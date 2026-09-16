import asyncio
from functools import lru_cache
from io import BytesIO

from minio import Minio

from requirement_agent.shared.config import get_settings
from requirement_agent.shared.errors import ObjectStorageError

#将 MinIO 的文件上传、下载能力统一包装成异步方法，供 IngestionService、附件解析任务等业务代码调用。
class MinioObjectStorage:
    def __init__(self, client: Minio, bucket: str) -> None:
        self._client = client#MinIO 客户端实例，用于真正调用 put_object()、get_object()
        self._bucket = bucket#存储桶名称

    #上传附件到 MinIO
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

    #从 MinIO 下载附件
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


#创建并缓存 MinIO 存储对象
@lru_cache
def get_object_storage() -> MinioObjectStorage:
    settings = get_settings()
    client = Minio(
        settings.minio_endpoint,#MinIO 服务地址
        access_key=settings.minio_access_key,#MinIO 用户名
        secret_key=settings.minio_secret_key,#MinIO 密钥
        secure=settings.minio_secure,#是否使用 HTTPS
    )
    return MinioObjectStorage(client, settings.minio_bucket)

