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
    source: SourceRecord
    replayed: bool


class IngestionService:
    def __init__(
        self,
        session: AsyncSession,
        storage: ObjectStorage,
        dispatcher: TaskDispatcher,
        max_upload_size: int,
    ) -> None:
        self._session = session
        self._storage = storage
        self._dispatcher = dispatcher
        self._max_upload_size = max_upload_size

    async def ingest(self, connector: SourceConnector, request: object) -> IngestionResult:
        if not await connector.verify(request):
            raise ConnectorVerificationError("connector rejected the input")
        source_input = await connector.receive(request)
        attachments = await connector.download_attachments(source_input)
        normalized_attachments = self._validate_attachments(attachments)
        fingerprint = self._fingerprint(source_input, normalized_attachments)

        existing = await self._find_existing(source_input)
        if existing is not None:
            self._assert_same_payload(existing, fingerprint)
            self._dispatch(existing)
            return IngestionResult(source=existing, replayed=True)

        source = self._build_source(source_input, fingerprint)
        attachment_models = [
            self._build_attachment(source_input, attachment)
            for attachment in normalized_attachments
        ]
        source.attachments.extend(attachment_models)

        try:
            self._session.add(source)
            await self._session.flush()
            self._session.add(
                AuditLog(
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
        except IntegrityError:
            await self._session.rollback()
            existing = await self._find_existing(source_input)
            if existing is None:
                raise
            self._assert_same_payload(existing, fingerprint)
            self._dispatch(existing)
            return IngestionResult(source=existing, replayed=True)

        try:
            await self._store_attachments(
                source,
                attachment_models,
                normalized_attachments,
            )
        except ObjectStorageError as exc:
            await self._mark_storage_failure(source, attachment_models, str(exc))
            raise ObjectStorageError(
                f"source {source.source_key} was saved, but its attachment could not be stored"
            ) from exc

        self._dispatch(source)
        return IngestionResult(source=source, replayed=False)

    def _validate_attachments(
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
                AttachmentInput(
                    file_name=safe_name,
                    file_type=attachment.file_type,
                    content=attachment.content,
                )
            )
        return normalized

    async def _find_existing(self, source: RawSourceInput) -> SourceRecord | None:
        result = await self._session.execute(
            select(SourceRecord)
            .options(selectinload(SourceRecord.attachments))
            .where(
                SourceRecord.channel_type == source.channel_type,
                SourceRecord.external_event_id == source.external_event_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _assert_same_payload(source: SourceRecord, fingerprint: str) -> None:
        if source.raw_metadata.get("_ingestion_fingerprint") != fingerprint:
            raise IdempotencyConflictError(
                "the idempotency key was already used for a different payload"
            )

    @staticmethod
    def _fingerprint(
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
    def _build_attachment(
        source: RawSourceInput,
        attachment: AttachmentInput,
    ) -> SourceAttachment:
        file_hash = hashlib.sha256(attachment.content).hexdigest()
        event_hash = hashlib.sha256(source.external_event_id.encode()).hexdigest()[:16]
        file_name = sanitize_file_name(attachment.file_name)
        return SourceAttachment(
            file_name=file_name,
            file_type=attachment.file_type,
            file_size=len(attachment.content),
            file_hash=file_hash,
            storage_path=(
                f"sources/{source.channel_type.value}/{event_hash}/{file_hash}/{file_name}"
            ),
            parse_status=AttachmentParseStatus.PENDING,
        )

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

    def _dispatch(self, source: SourceRecord) -> None:
        try:
            self._dispatcher.dispatch_source(source.id)
        except Exception as exc:
            raise TaskDispatchError(
                f"source {source.source_key} was saved, but its task could not be queued"
            ) from exc

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
