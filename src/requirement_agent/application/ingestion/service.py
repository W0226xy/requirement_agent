import hashlib
import json
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from requirement_agent.application.ingestion.dispatcher import TaskDispatcher
from requirement_agent.application.ingestion.files import sanitize_file_name, validate_file
from requirement_agent.connectors.base import SourceConnector
from requirement_agent.domain.sources.entities import AttachmentInput, RawSourceInput
from requirement_agent.infrastructure.database.models import (
    AuditLog,
    SourceAttachment,
    SourceRecord,
)
from requirement_agent.infrastructure.storage.base import ObjectStorage
from requirement_agent.shared.enums import (
    AttachmentParseStatus,
    AuditActionType,
    AuditEntityType,
    ProcessingStatus,
)
from requirement_agent.shared.errors import (
    ConnectorVerificationError,
    IdempotencyConflictError,
    ObjectStorageError,
    TaskDispatchError,
)


@dataclass(frozen=True)
class IngestionResult:
    source: SourceRecord#本次原始需求记录
    replayed: bool#是否为重复提交

#ingestion service 负责将不同来源的输入（文件、网页表单等）统一处理为源记录，并存储到数据库和对象存储中，同时派发任务给 Celery 进行后续处理。
class IngestionService:
    def __init__(
        self,
        session: AsyncSession,#异步操作 PostgreSQL 数据库
        storage: ObjectStorage,#操作对象存储，当前实现一般是 MinIO
        dispatcher: TaskDispatcher,#将解析、Agent 分析等任务投递给 Celery
        max_upload_size: int,#上传附件的最大大小限制
    ) -> None:
        self._session = session#异步操作 PostgreSQL 数据库
        self._storage = storage#操作对象存储，当前实现一般是 MinIO
        self._dispatcher = dispatcher#将解析、Agent 分析等任务投递给 Celery
        self._max_upload_size = max_upload_size#上传附件的最大大小限制

    async def ingest(
        self,
        connector: SourceConnector,
        request: object,
        *,
        dispatch: bool = True,
    ) -> IngestionResult:
        #connector代表输入渠道处理器，例如 DocumentConnector（PDF、DOCK）、ImageConnector（JPG、PNG）、WebFormConnector（纯文本网页表单） 等，
        #request 是对应的输入对象，例如 FileConnectorRequest、WebFormConnectorRequest 等

        #Connector 先统一不同来源的输入
        #校验网页表单是否有有效文本；文件 Connector 是否收到了附件；飞书事件：事件结构、签名或消息内容是否有效。
        if not await connector.verify(request):
            raise ConnectorVerificationError("connector rejected the input")

        #Connector 将输入对象转换为统一的源输入格式
        source_input = await connector.receive(request)
        attachments = await connector.download_attachments(source_input)

        #校验附件的文件名、类型、大小等，并返回标准化后的附件列表
        normalized_attachments = self._validate_attachments(attachments)

        #幂等控制：避免重复创建需求
        fingerprint = self._fingerprint(source_input, normalized_attachments)#根据本次文本和附件内容生成摘要，用于确认“重复请求的内容是否真的一致”。
        existing = await self._find_existing(source_input)#按渠道事件 ID、幂等键等查找是否已有同一条提交

        #如果已经存在相同的源记录，则检查其 payload 是否一致，如果一致则直接返回已存在的记录，并标记为 replayed=True
        if existing is not None:
            self._assert_same_payload(existing, fingerprint)
            if dispatch:
                self._dispatch(existing)
            return IngestionResult(source=existing, replayed=True)

        #新的提交，创建数据库记录和对象存储文件
        #创建原始需求记录 SourceRecord
        source = self._build_source(source_input, fingerprint)
        attachment_models = [
            self._build_attachment(source_input, attachment)
            for attachment in normalized_attachments
        ]
        source.attachments.extend(attachment_models)

        try:
            #先写数据库，再写 MinIO
            self._session.add(source)
            await self._session.flush()
            self._session.add(
                AuditLog(#记录审计日志，标记源记录已接收
                    actor_id=source.submitter_id,
                    action_type=AuditActionType.SOURCE_RECEIVED,
                    entity_type=AuditEntityType.SOURCE_RECORD,
                    entity_id=str(source.id),
                    after_data={
                        "source_key": source.source_key,
                        "channel_type": source.channel_type.value,
                    },
                )
            )
            await self._session.commit()
        except IntegrityError:#处理的是并发重复提交
            #例如用户连续点击两次提交，两个请求几乎同时发现“数据库里还没有记录”，都尝试插入。
            #一个成功，另一个会触发唯一约束错误；失败方回滚后重新查找，复用刚刚成功创建的记录，避免生成两条重复需求。
            await self._session.rollback()
            existing = await self._find_existing(source_input)
            if existing is None:
                raise
            self._assert_same_payload(existing, fingerprint)
            if dispatch:
                self._dispatch(existing)
            return IngestionResult(source=existing, replayed=True)

        try:
            #将附件内容写入对象存储（MinIO），并记录审计日志
            await self._store_attachments(
                source,
                attachment_models,
                normalized_attachments,
            )
            #如果附件MinIO存储失败，则将源记录标记为解析失败，并抛出异常
        except ObjectStorageError as exc:
            await self._mark_storage_failure(source, attachment_models, str(exc))
            raise ObjectStorageError(
                f"source {source.source_key} was saved, but its attachment could not be stored"
            ) from exc

        #派发源记录到下游处理
        #它会把 source.id 投递给 Celery Worker，进入PARSE_SOURCE_TASK，之后才进入后台流程：
        if dispatch:
            self._dispatch(source)
        return IngestionResult(source=source, replayed=False)

    def dispatch(self, source: SourceRecord) -> None:#外部调用接口，直接派发源记录到下游处理
        self._dispatch(source)

    def _validate_attachments(#校验附件的文件名、类型、大小等，并返回标准化后的附件列表
        self,
        attachments: list[AttachmentInput],
    ) -> list[AttachmentInput]:
        normalized: list[AttachmentInput] = []
        for attachment in attachments:
            safe_name = validate_file(
                attachment.file_name,
                attachment.file_type,
                attachment.content,
                self._max_upload_size,
            )
            normalized.append(
                AttachmentInput(#校验后不直接改原附件，而是重新创建一个安全版本，也就是标准化后的附件列表
                    file_name=safe_name,
                    file_type=attachment.file_type,
                    content=attachment.content,
                )
            )
        return normalized

    #查找是否已有同一条提交，按渠道事件 ID、幂等键等查找
    async def _find_existing(self, source: RawSourceInput) -> SourceRecord | None:
        result = await self._session.execute(
            select(SourceRecord)
            .options(selectinload(SourceRecord.attachments))#表示查询源记录时，把附件也一并加载出来，避免后续访问 source.attachments 时再额外查询数据库。
            .where(
                SourceRecord.channel_type == source.channel_type,
                SourceRecord.external_event_id == source.external_event_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    #检查已有源记录的 payload 是否与当前请求一致，如果不一致则抛出幂等性冲突错误
    #防止“同一个幂等键却提交了不同内容”
    def _assert_same_payload(source: SourceRecord, fingerprint: str) -> None:
        if source.raw_metadata.get("_ingestion_fingerprint") != fingerprint:
            raise IdempotencyConflictError(
                "the idempotency key was already used for a different payload"
            )

    @staticmethod
    def _fingerprint(#用源数据和附件内容生成唯一指纹，作为幂等性控制的依据
        source: RawSourceInput,
        attachments: list[AttachmentInput],
    ) -> str:
        source_payload = source.model_dump(mode="json", exclude={"received_at"})
        attachment_payload = [
            {
                "file_name": item.file_name,
                "file_type": item.file_type,
                "file_hash": hashlib.sha256(item.content).hexdigest(),
            }
            for item in attachments
        ]
        serialized = json.dumps(
            {"source": source_payload, "attachments": attachment_payload},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(serialized.encode()).hexdigest()

    @staticmethod
    #根据源数据和指纹构建源记录
    def _build_source(source: RawSourceInput, fingerprint: str) -> SourceRecord:
        metadata = dict(source.raw_metadata)
        metadata["_ingestion_fingerprint"] = fingerprint
        return SourceRecord(
            source_key=f"SRC-{uuid4().hex[:12].upper()}",
            channel_type=source.channel_type,
            external_event_id=source.external_event_id,
            submitter_id=source.submitter_id,
            submitter_name=source.submitter_name,
            raw_text=source.raw_text,
            raw_metadata=metadata,
            received_at=source.received_at,
            processing_status=ProcessingStatus.RECEIVED,
        )

    @staticmethod
    #根据源数据和附件构建附件记录
    def _build_attachment(
        source: RawSourceInput,
        attachment: AttachmentInput,
    ) -> SourceAttachment:
        file_hash = hashlib.sha256(attachment.content).hexdigest()#文件内容哈希，用于标识文件真实内容。
        event_hash = hashlib.sha256(source.external_event_id.encode()).hexdigest()[:16]
        file_name = sanitize_file_name(attachment.file_name)
        return SourceAttachment(
            file_name=file_name,
            file_type=attachment.file_type,
            file_size=len(attachment.content),
            file_hash=file_hash,
            storage_path=(#是 MinIO 中对象的存储位置。
                f"sources/{source.channel_type.value}/{event_hash}/{file_hash}/{file_name}"
            ),
            parse_status=AttachmentParseStatus.PENDING,
        )

    #将附件内容写入对象存储（MinIO），并记录审计日志
    async def _store_attachments(
        self,
        source: SourceRecord,
        attachment_models: list[SourceAttachment],
        attachment_inputs: list[AttachmentInput],
    ) -> None:
        for attachment_model, attachment_input in zip(
            attachment_models,
            attachment_inputs,
            strict=True,
        ):
            await self._storage.put(
                attachment_model.storage_path,
                attachment_input.content,
                attachment_model.file_type,
            )
            self._session.add(
                AuditLog(
                    actor_id=source.submitter_id,
                    action_type=AuditActionType.ATTACHMENT_STORED,
                    entity_type=AuditEntityType.SOURCE_ATTACHMENT,
                    entity_id=str(attachment_model.id),
                    after_data={"storage_path": attachment_model.storage_path},
                )
            )
        await self._session.commit()

    #把源记录 ID 发送到 Celery Worker，进入 PARSE_SOURCE_TASK 阶段，之后才进入后台流程
    def _dispatch(self, source: SourceRecord) -> None:
        try:
            self._dispatcher.dispatch_source(source.id)
        except Exception as exc:
            raise TaskDispatchError(
                f"source {source.source_key} was saved, but its task could not be queued"
            ) from exc

    #处理 MinIO 上传失败，当附件存储失败时，将源记录标记为解析失败，并记录错误信息到审计日志中
    async def _mark_storage_failure(
        self,
        source: SourceRecord,
        attachments: list[SourceAttachment],
        message: str,
    ) -> None:
        source.processing_status = ProcessingStatus.PARSE_FAILED
        for attachment in attachments:
            attachment.parse_status = AttachmentParseStatus.FAILED
            attachment.error_message = message
        self._session.add(
            AuditLog(
                actor_id="system",
                action_type=AuditActionType.SOURCE_PARSE_FAILED,
                entity_type=AuditEntityType.SOURCE_RECORD,
                entity_id=str(source.id),
                after_data={"error": message},
            )
        )
        await self._session.commit()
