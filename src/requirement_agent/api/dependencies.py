from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from requirement_agent.application.ingestion.dispatcher import (
    TaskDispatcher,
    get_task_dispatcher,
)
from requirement_agent.application.ingestion.service import IngestionService
from requirement_agent.infrastructure.database.session import get_session
from requirement_agent.infrastructure.storage.base import ObjectStorage
from requirement_agent.infrastructure.storage.minio import get_object_storage
from requirement_agent.shared.config import get_settings
from requirement_agent.shared.errors import PermissionDeniedError


def get_reviewer(#用于获取请求头中的 X-Actor-ID，作为当前操作的用户 ID，并检查其角色是否为 reviewer 或 admin。
    actor_id: Annotated[str, Header(alias="X-Actor-ID", min_length=1, max_length=255)],
    actor_role: Annotated[str, Header(alias="X-Actor-Role")] = "reviewer",
) -> str:
    if actor_role not in {"reviewer", "admin"}:
        raise PermissionDeniedError("reviewer or admin role is required")
    return actor_id


def get_actor_id(#用于获取请求头中的 X-Actor-ID，作为当前操作的用户 ID。
    actor_id: Annotated[str, Header(alias="X-Actor-ID", min_length=1, max_length=255)],
) -> str:
    return actor_id


def get_ingestion_service(#用于创建 IngestionService 实例，注入数据库 Session、对象存储和任务派发器。
    session: Annotated[AsyncSession, Depends(get_session)],
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
    dispatcher: Annotated[TaskDispatcher, Depends(get_task_dispatcher)],
) -> IngestionService:
    settings = get_settings()
    return IngestionService(
        session=session,
        storage=storage,
        dispatcher=dispatcher,
        max_upload_size=settings.max_upload_size_bytes,
    )
