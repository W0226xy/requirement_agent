import asyncio
from collections.abc import Coroutine
from typing import Any

from celery import Celery
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from requirement_agent.ai.llm.factory import get_embedding_model, get_llm
from requirement_agent.ai.retrieval.hybrid import RetrievalWeights
from requirement_agent.application.ingestion.dispatcher import (
    ANALYZE_SOURCE_TASK,
    FEISHU_EVENT_TASK,
    INDEX_VERSION_TASK,
    PARSE_SOURCE_TASK,
    get_task_dispatcher,
)
from requirement_agent.application.ingestion.service import IngestionService
from requirement_agent.application.versions.indexing import index_requirement_version
from requirement_agent.connectors.feishu import (
    FeishuConnector,
    FeishuEventRequest,
    FeishuOpenAPIClient,
)
from requirement_agent.infrastructure.database.models import (
    AuditLog,
    SourceAttachment,
    SourceRecord,
)
from requirement_agent.infrastructure.database.session import get_session_factory
from requirement_agent.infrastructure.storage.base import ObjectStorage
from requirement_agent.infrastructure.storage.minio import get_object_storage
from requirement_agent.parsers.registry import ParserRegistry, get_parser_registry
from requirement_agent.shared.config import get_settings
from requirement_agent.shared.enums import (
    AttachmentParseStatus,
    AuditActionType,
    AuditEntityType,
    ProcessingStatus,
)
from requirement_agent.shared.errors import (
    ApplicationError,
    AttachmentProcessingError,
    FeishuAPIError,
    LLMServiceError,
    SourceNotFoundError,
)
from requirement_agent.workflows.requirement_analysis import RequirementAnalysisWorkflow

_worker_loop: asyncio.AbstractEventLoop | None = None


#Celery Worker 的任务入口和异步编排层。
#它把 Celery 的同步任务，连接到项目内部的异步数据库、MinIO、解析器、LangGraph 工作流和飞书接入逻辑。

#整体流程：
#需求提交
# → PARSE_SOURCE_TASK：解析 PDF / Word / 图片
# → ANALYZE_SOURCE_TASK：运行 LangGraph Agent
# → 人工审核
# → INDEX_VERSION_TASK：向量化正式需求并更新 HNSW 索引
#
# 飞书事件
# → FEISHU_EVENT_TASK
# → 统一进入 IngestionService
# → 继续走相同的解析和分析流程

#让 Celery 的同步任务能执行项目里的异步函数
def run_worker_coroutine[Result](
    coroutine: Coroutine[Any, Any, Result],#接收一个异步函数的协程对象，并在 Celery Worker 的同步上下文中运行它
    #这里接收的协程对象可以是任何异步函数的调用结果，比如 parse_source_attachments(...) 或 analyze_source(...)，它们都是 async def 定义的函数，返回一个 Coroutine 对象。
) -> Result:
    global _worker_loop#使用文件级的全局变量 _worker_loop，让同一个 Celery Worker 进程尽量复用同一个事件循环。
    if _worker_loop is None or _worker_loop.is_closed():#如果当前没有事件循环，或者事件循环已经关闭，就创建一个新的事件循环。
        _worker_loop = asyncio.new_event_loop()
    return _worker_loop.run_until_complete(coroutine)#启动协程，等待协程全部完成，再把结果返回给同步 Celery 任务。


#解析解析某一条需求记录下的所有附件
#例如一条需求同时上传了 PDF、Word 和截图，它会逐个调用对应解析器，把解析文本保存到附件记录中
async def parse_source_attachments(
    source_record_id: int,#需求记录的 ID
    session_factory: async_sessionmaker[AsyncSession],#异步数据库会话工厂，用于创建数据库会话
    storage: ObjectStorage,#对象存储，用于读取附件文件内容
    parsers: ParserRegistry,#解析器注册表，用于按文件类型选择 PDF、Word、图片解析器
) -> None:
    async with session_factory() as session:
        #加载原始需求SourceRecord,并一起加载它的附件列表。
        #使用 SELECT ... FOR UPDATE 锁定该行，防止并发任务同时解析同一条需求记录。
        source = await _load_source(session, source_record_id)
        #如果所有附件都已经解析成功，就直接返回，不再重复解析。
        #即使 Celery 任务被重复投递，也不会重复调用 PDF 解析或 OCR。
        if source.attachments and all(
            attachment.parse_status == AttachmentParseStatus.PARSED#PARSED（解析完成）状态
            for attachment in source.attachments
        ):
            return
        #如果有附件未解析，更新需求记录的 processing_status 为 PARSING（解析中），并写入审计日志。
        source.processing_status = ProcessingStatus.PARSING
        session.add(#写入一条审计日志
            AuditLog(
                actor_id="system",#系统自动操作
                action_type=AuditActionType.SOURCE_PARSING_STARTED,#需求解析开始#
                entity_type=AuditEntityType.SOURCE_RECORD,#需求记录实体类型
                entity_id=str(source.id),#需求记录 ID
                after_data={"status": ProcessingStatus.PARSING.value},#更新后的状态
            )
        )
        await session.commit()#把“状态变为 PARSING”和审计日志真正写入数据库。

        #逐个解析附件
        for attachment in source.attachments:
            if attachment.parse_status == AttachmentParseStatus.PARSED:#如果附件已经解析成功，就跳过它，继续解析下一个附件。
                continue
            #如果附件未解析，调用 _parse_attachment 解析它，并更新数据库。
            await _parse_attachment(session, source, attachment, storage, parsers)


#根据 source_record_id 从数据库加载一条原始需求及其附件；如果不存在就报错。
async def _load_source(session: AsyncSession, source_record_id: int) -> SourceRecord:
    result = await session.execute(#执行 SQL 查询
        select(SourceRecord)
        .options(selectinload(SourceRecord.attachments))#同时预加载这条需求关联的附件
        .where(SourceRecord.id == source_record_id)#限定只查指定 ID
        .with_for_update()#表示查询时给这条需求记录加数据库行锁，减少多个Celery Worker 同时修改它的风险。
    )
    source = result.scalar_one_or_none()#如果查询结果只有一条，就返回它；如果没有结果，就返回 None；如果有多条结果，就抛出异常。
    #由于 id 是主键，正常情况下最多只会查到一条。
    if source is None:#如果没有查到，就抛出 SourceNotFoundError 异常，提示调用者这条需求记录不存在。
        raise SourceNotFoundError(f"source record {source_record_id} was not found")
    return source

#解析单个附件，并将解析结果或失败原因写回数据库。
async def _parse_attachment(
    session: AsyncSession,
    source: SourceRecord,
    attachment: SourceAttachment,
    storage: ObjectStorage,
    parsers: ParserRegistry,
) -> None:
    #更新附件状态为 PARSING（解析中），清空错误信息，并提交事务。
    attachment.parse_status = AttachmentParseStatus.PARSING
    attachment.error_message = None#任务重试，清空上次的错误信息
    await session.commit()

    try:
        #从 MinIO 读取文件，再选择对应解析器
        content = await storage.get(attachment.storage_path)
        #根据文件类型选择解析器，并解析内容
        #PdfParser：PyMuPDF 提取每页文本
        #DockParser：python-docx 提取段落文本
        #ImageParser：PaddleOCR 提取图片文字
        result = await parsers.for_file_type(attachment.file_type).parse(content)
    except ApplicationError as exc:#如果解析器抛出 ApplicationError 异常，说明解析失败（比如 PDF 损坏、Word 格式不支持、图片无法识别等），就把错误信息写入数据库，并更新附件状态为 FAILED。
        message = str(exc)#获取错误信息
        attachment.parse_status = AttachmentParseStatus.FAILED#更新附件状态为 FAILED
        attachment.error_message = message
        source.processing_status = ProcessingStatus.PARSE_FAILED#更新需求记录状态为 PARSE_FAILED
        session.add(#写入一条审计日志，记录解析失败的原因
            AuditLog(
                actor_id="system",
                action_type=AuditActionType.SOURCE_PARSE_FAILED,
                entity_type=AuditEntityType.SOURCE_ATTACHMENT,
                entity_id=str(attachment.id),
                after_data={"error": message},
            )
        )
        await session.commit()
        raise AttachmentProcessingError(#包装成 Celery 可重试异常
            #上层注册 Celery 任务时配置了：autoretry_for=(AttachmentProcessingError,)
            #Celery 会自动重试这个任务，最多 3 次，间隔指数退避。
            f"failed to parse attachment {attachment.id}"
        ) from exc

    #如果解析成功，就把解析文本写入数据库，并更新附件状态为 PARSED。
    attachment.parsed_text = result.parsed_text
    attachment.ocr_text = result.ocr_text
    attachment.parse_status = AttachmentParseStatus.PARSED
    session.add(#写入一条审计日志，记录解析成功
        AuditLog(
            actor_id="system",
            action_type=AuditActionType.ATTACHMENT_PARSED,
            entity_type=AuditEntityType.SOURCE_ATTACHMENT,
            entity_id=str(attachment.id),
            after_data={"parse_status": AttachmentParseStatus.PARSED.value},
        )
    )
    await session.commit()


#把项目内部的普通 Python 函数注册成 Celery 可投递、可由 Worker 消费的任务。
#PARSE_SOURCE_TASK
# → parse_source_task()
# → 解析 PDF / Word / 图片
# → 成功后投递 ANALYZE_SOURCE_TASK
#
# ANALYZE_SOURCE_TASK
# → analyze_source_task()
# → 启动 LangGraph Agent
#
# INDEX_VERSION_TASK
# → index_version_task()
# → 正式需求向量化、写入 HNSW 索引
#
# FEISHU_EVENT_TASK
# → process_feishu_event_task()
# → 接收飞书消息和附件，统一进入需求入库流程
def register_tasks(celery_app: Celery) -> None:#注册 Celery 任务
    def parse_source_task(source_record_id: int) -> None:#附件解析任务
        run_worker_coroutine(
            parse_source_attachments(
                source_record_id,
                get_session_factory(),#异步数据库会话工厂
                get_object_storage(),#获取 MinIO 对象存储
                get_parser_registry(),#获取解析器注册表
            )
        )
        #解析成功后，投递分析任务
        celery_app.send_task(ANALYZE_SOURCE_TASK, args=[source_record_id])

    def analyze_source_task(source_record_id: int) -> None:#Agent 分析任务
        run_worker_coroutine(analyze_source(source_record_id))

    def index_version_task(version_id: int) -> None:#正式需求向量化任务
        run_worker_coroutine(index_version(version_id))

    def process_feishu_event_task(payload: dict[str, object]) -> None:#飞书事件处理任务
        run_worker_coroutine(process_feishu_event(payload))

    celery_app.task(#解析任务的重试配置
        name=PARSE_SOURCE_TASK,#解析任务的名称
        autoretry_for=(AttachmentProcessingError,),#当出现 AttachmentProcessingError时，Celery 会自动重试该任务
        retry_backoff=True,#避免持续高频请求 MinIO、OCR 服务等
        retry_jitter=True,#避免多个 Celery Worker 同时重试，导致雪崩效应
        retry_kwargs={"max_retries": 3},
    )(parse_source_task)
    celery_app.task(name=ANALYZE_SOURCE_TASK)(analyze_source_task)
    celery_app.task(
        name=INDEX_VERSION_TASK,
        autoretry_for=(LLMServiceError, SQLAlchemyError),
        retry_backoff=True,
        retry_jitter=True,
        retry_kwargs={"max_retries": 3},
    )(index_version_task)
    celery_app.task(
        name=FEISHU_EVENT_TASK,
        autoretry_for=(FeishuAPIError,),
        retry_backoff=True,
        retry_jitter=True,
        retry_kwargs={"max_retries": 3},
    )(process_feishu_event_task)

#针对一条已经完成附件解析的原始需求，创建 Agent 工作流并运行完整分析。
#它本身不直接写 Prompt、不直接检索数据库，而是组装运行 RequirementAnalysisWorkflow 所需要的依赖和配置。
async def analyze_source(source_record_id: int) -> None:
    settings = get_settings()#读取项目配置，通常来自 .env 或配置类。
    async with get_session_factory()() as session:#创建异步数据库会话
        workflow = RequirementAnalysisWorkflow(#创建需求分析 Agent 工作流，并注入三个核心依赖
            session,#异步数据库会话，用于读取原始需求、附件、历史分析结果等
            get_llm(),#聊天模型，用于需求提取和冲突/风险分析
            get_embedding_model(),#向量化模型，用于检索相似需求和功能点
            max_retries=settings.llm_max_retries,#LLM 调用失败时的最大重试次数
            retrieval_weights=RetrievalWeights(#混合检索权重
                keyword=settings.retrieval_keyword_weight,
                vector=settings.retrieval_vector_weight,
                business=settings.retrieval_business_weight,
            ),
            candidate_limit=settings.retrieval_candidate_limit,#检索候选项限制
        )
        await workflow.run(source_record_id)#运行工作流，分析指定的原始需求记录 ID。工作流内部会调用 LLM、检索数据库、生成结构化结果，并写入数据库。

#把一条已经审核通过的正式需求版本加入长期 RAG 知识库
async def index_version(version_id: int) -> None:#参数 version_id 是人工审核后提交形成的正式需求版本的 ID
    async with get_session_factory()() as session:#创建一个异步数据库会话，执行结束后自动关闭会话
        await index_requirement_version(session, get_embedding_model(), version_id)#将指定的正式需求版本加入长期 RAG 知识库


async def process_feishu_event(payload: dict[str, object]) -> None:
    settings = get_settings()
    request = FeishuEventRequest.model_validate(payload)
    async with FeishuOpenAPIClient(
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret.get_secret_value(),
        base_url=settings.feishu_base_url,
        timeout_seconds=settings.feishu_timeout_seconds,
        max_download_size=settings.max_upload_size_bytes,
    ) as client:
        async with get_session_factory()() as session:
            service = IngestionService(
                session=session,
                storage=get_object_storage(),
                dispatcher=get_task_dispatcher(),
                max_upload_size=settings.max_upload_size_bytes,
            )
            await service.ingest(FeishuConnector(client), request)
