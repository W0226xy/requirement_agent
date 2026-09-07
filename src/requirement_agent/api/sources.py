#“原始需求输入模块”的 API 路由文件
#接收纯文本需求；
#接收 PDF、Word、图片等文件；
#分页查询所有原始输入；
#查询某一条原始输入及其附件解析状态。
from datetime import UTC, datetime
from typing import Annotated


from fastapi import APIRouter, Depends, File, Form, Header, Query, Response, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from requirement_agent.api.dependencies import get_ingestion_service
from requirement_agent.api.schemas.sources import (
    IngestionRequest,
    IngestionResponse,
    SourceRecordListResponse,
    SourceRecordResponse,
)
from requirement_agent.application.ingestion.service import IngestionService
from requirement_agent.connectors.document import DocumentConnector
from requirement_agent.connectors.image import ImageConnector
from requirement_agent.connectors.web_form import WebFormConnector
from requirement_agent.domain.sources.entities import (
    AttachmentInput,
    FileConnectorRequest,
    WebFormConnectorRequest,
)
from requirement_agent.infrastructure.database.models import SourceRecord
from requirement_agent.infrastructure.database.session import get_session
from requirement_agent.shared.config import get_settings
from requirement_agent.shared.enums import ChannelType, ProcessingStatus
from requirement_agent.shared.errors import (
    FileTooLargeError,
    SourceNotFoundError,
    UnsupportedFileError,
)
#创建路由分组
#本文件里的所有接口自动以 /api/v1 开头；
#所有接口自动打上 sources 标签，方便在 Swagger UI 中查看。
router = APIRouter(prefix="/api/v1", tags=["sources"])

#提取纯文本需求
@router.post(
    "/ingestions",
    response_model=IngestionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_ingestion(
    payload: IngestionRequest,#是请求 Body 中的 JSON
    response: Response,#是 FastAPI 的 Response 对象，用于设置响应的状态码和头部信息
    idempotency_key: Annotated[#幂等控制：同一用户由于网络超时、双击提交等原因重复发送请求时，不会重复创建多条需求
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=255),
    ],
    service: Annotated[IngestionService, Depends(get_ingestion_service)],#数据库 Session + MinIO 对象存储 + Celery 任务派发器 + 文件大小配置

) -> IngestionResponse:
    request = WebFormConnectorRequest(#把 API 请求转换成内部的“Web 表单输入对象”。
        external_event_id=idempotency_key,
        submitter_id=payload.submitter_id,
        submitter_name=payload.submitter_name,
        raw_text=payload.raw_text,
        raw_metadata=payload.raw_metadata,
        received_at=datetime.now(UTC),
    )
    result = await service.ingest(WebFormConnector(), request)
    if result.replayed:#如果是重复请求，则返回 200 OK，而不是 202 Accepted
        response.status_code = status.HTTP_200_OK
    return IngestionResponse(#返回原始需求记录和是否重复提交的标记。
        source=SourceRecordResponse.from_model(result.source, include_attachments=False),
        replayed=result.replayed,
    )

#上传 PDF、Word、图片等文件
@router.post(
    "/files",
    response_model=IngestionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_file(
    response: Response,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=255),
    ],
    service: Annotated[IngestionService, Depends(get_ingestion_service)],
    file: Annotated[UploadFile, File()],#上传的文件
    submitter_id: Annotated[str, Form(min_length=1, max_length=255)],#上传者 ID
    submitter_name: Annotated[str, Form(min_length=1, max_length=255)],#上传者姓名
    raw_text: Annotated[str, Form(max_length=200_000)] = "",#上传者提供的文本内容，通常是对文件的描述或补充信息
) -> IngestionResponse:
    settings = get_settings()
    content = await file.read(settings.max_upload_size_bytes + 1)#读取上传的文件内容，最多读取 max_upload_size_bytes + 1 字节，以便检查文件是否超过最大限制。
    if len(content) > settings.max_upload_size_bytes:#如果文件内容超过最大限制，则抛出 FileTooLargeError 异常，提示用户文件过大。
        raise FileTooLargeError(
            f"file exceeds maximum size of {settings.max_upload_size_bytes} bytes"
        )
    file_type = file.content_type or "application/octet-stream"#获取上传文件的 MIME 类型，如果无法获取，则默认为 application/octet-stream。（MIME类型包括pdf，dock，jpg，png）
    file_name = file.filename or "unnamed"#获取上传文件的文件名，如果无法获取，则默认为 unnamed。
    attachment = AttachmentInput(#把上传文件转换成内部附件对象。
        file_name=file_name,
        file_type=file_type,
        content=content,
    )
    connector = _connector_for(file_type)#根据文件类型选择合适的处理器（DocumentConnector 或 ImageConnector），如果文件类型不支持，则抛出 UnsupportedFileError 异常。
    request = FileConnectorRequest(#把 API 请求转换成内部的“文件输入对象”。
        external_event_id=idempotency_key,
        submitter_id=submitter_id,
        submitter_name=submitter_name,
        raw_text=raw_text,
        raw_metadata={},
        received_at=datetime.now(UTC),
        attachment=attachment,
    )
    result = await service.ingest(connector, request)#调用 IngestionService 的 ingest 方法处理文件上传请求，返回处理结果。
    #表单、PDF、Word、图片虽然入口不同，但进入业务层后会走统一的“原始输入入库和异步处理”流程
    if result.replayed:
        response.status_code = status.HTTP_200_OK
    return IngestionResponse(#文件上传接口会把附件信息一并返回，方便前端立即显示文件名、类型、解析状态等
        source=SourceRecordResponse.from_model(result.source, include_attachments=True),
        replayed=result.replayed,
    )

#查询原始需求列表
@router.get("/source-records", response_model=SourceRecordListResponse)
async def list_source_records(
    session: Annotated[AsyncSession, Depends(get_session)],#注入异步数据库连接。
    page: Annotated[int, Query(ge=1)] = 1,#分页参数
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,#每页条目数20，最大 100
    channel_type: ChannelType | None = None,#渠道类型过滤
    processing_status: ProcessingStatus | None = None,#处理状态过滤
    submitter_id: str | None = None,#上传者 ID 过滤
) -> SourceRecordListResponse:
    filters = []#添加过滤条件（渠道、处理状态、上传者 ID）
    if channel_type is not None:
        filters.append(SourceRecord.channel_type == channel_type)
    if processing_status is not None:
        filters.append(SourceRecord.processing_status == processing_status)
    if submitter_id is not None:
        filters.append(SourceRecord.submitter_id == submitter_id)

    count_result = await session.execute(
        select(func.count(SourceRecord.id)).where(*filters)
    )
    query = (
        select(SourceRecord)
        .where(*filters)
        .order_by(SourceRecord.received_at.desc(), SourceRecord.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    records = (await session.execute(query)).scalars().all()
    return SourceRecordListResponse(
        items=[
            SourceRecordResponse.from_model(record, include_attachments=False)
            for record in records
        ],
        total=count_result.scalar_one(),
        page=page,
        page_size=page_size,
    )


@router.get("/source-records/{source_record_id}", response_model=SourceRecordResponse)
async def get_source_record(
    source_record_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SourceRecordResponse:
    result = await session.execute(
        select(SourceRecord)
        .options(selectinload(SourceRecord.attachments))
        .where(SourceRecord.id == source_record_id)
    )
    source = result.scalar_one_or_none()
    if source is None:
        raise SourceNotFoundError(f"source record {source_record_id} was not found")
    return SourceRecordResponse.from_model(source, include_attachments=True)


def _connector_for(file_type: str) -> DocumentConnector | ImageConnector:
    if file_type in {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }:
        return DocumentConnector()
    if file_type in {"image/jpeg", "image/png"}:
        return ImageConnector()
    raise UnsupportedFileError(f"unsupported file type: {file_type}")

