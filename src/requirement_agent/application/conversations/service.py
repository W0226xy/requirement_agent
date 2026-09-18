import hashlib
import re
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from requirement_agent.application.ingestion.service import (
    IngestionResult,
    IngestionService,
)
from requirement_agent.connectors.document import DocumentConnector
from requirement_agent.connectors.image import ImageConnector
from requirement_agent.connectors.web_form import WebFormConnector
from requirement_agent.domain.sources.entities import (
    AttachmentInput,
    FileConnectorRequest,
    WebFormConnectorRequest,
)
from requirement_agent.infrastructure.database.models import (
    ConversationMessage,
    RequirementConversation,
)
from requirement_agent.shared.errors import (
    ConversationNotFoundError,
    IdempotencyConflictError,
    UnsupportedFileError,
)


class ConversationService:
    def __init__(
        self,
        session: AsyncSession,
        ingestion_service: IngestionService,
    ) -> None:
        self._session = session
        self._ingestion = ingestion_service

    async def create(
        self,
        *,
        owner_id: str,
        title: str | None,
    ) -> RequirementConversation:
        custom_title = title.strip() if title else ""
        conversation = RequirementConversation(
            conversation_key=f"CONV-{uuid4().hex[:24].upper()}",
            owner_id=owner_id,
            title=custom_title or "New conversation",
            title_is_custom=bool(custom_title),
        )
        self._session.add(conversation)
        await self._session.commit()
        await self._session.refresh(conversation)
        return conversation

    async def get(
        self,
        conversation_key: str,
        owner_id: str,
        *,
        for_update: bool = False,
    ) -> RequirementConversation:
        query = select(RequirementConversation).where(
            RequirementConversation.conversation_key == conversation_key,
            RequirementConversation.owner_id == owner_id,
            RequirementConversation.deleted_at.is_(None),
        )
        if for_update:
            query = query.with_for_update()
        conversation = (await self._session.execute(query)).scalar_one_or_none()
        if conversation is None:
            raise ConversationNotFoundError(
                f"conversation {conversation_key} was not found"
            )
        return conversation

    async def rename(
        self,
        conversation_key: str,
        owner_id: str,
        title: str,
    ) -> RequirementConversation:
        conversation = await self.get(conversation_key, owner_id, for_update=True)
        conversation.title = title.strip()
        conversation.title_is_custom = True
        conversation.updated_at = datetime.now(UTC)
        await self._session.commit()
        await self._session.refresh(conversation)
        return conversation

    async def delete(self, conversation_key: str, owner_id: str) -> None:
        conversation = await self.get(conversation_key, owner_id, for_update=True)
        now = datetime.now(UTC)
        conversation.deleted_at = now
        conversation.updated_at = now
        await self._session.commit()

    async def clear_model_context(
        self, conversation_key: str, owner_id: str
    ) -> RequirementConversation:
        """Forget prompt memory without deleting auditable business records."""
        conversation = await self.get(conversation_key, owner_id, for_update=True)
        last_sequence = await self._session.scalar(
            select(func.coalesce(func.max(ConversationMessage.sequence_number), 0)).where(
                ConversationMessage.conversation_id == conversation.id
            )
        )
        conversation.summary = ""
        conversation.business_context = {}
        conversation.memory_covered_sequence = last_sequence or 0
        conversation.memory_revision += 1
        conversation.updated_at = datetime.now(UTC)
        await self._session.commit()
        await self._session.refresh(conversation)
        return conversation

    async def add_message(
        self,
        *,
        conversation_key: str,
        owner_id: str,
        actor_name: str,
        idempotency_key: str,
        raw_text: str,
        attachment: AttachmentInput | None,
    ) -> tuple[ConversationMessage, IngestionResult]:
        conversation = await self.get(conversation_key, owner_id)
        metadata: dict[str, object] = {
            "input_surface": "conversation",
            "conversation_key": conversation.conversation_key,
        }
        external_event_id = self._external_event_id(owner_id, idempotency_key)
        received_at = datetime.now(UTC)
        if attachment is None:
            web_request = WebFormConnectorRequest(
                external_event_id=external_event_id,
                submitter_id=owner_id,
                submitter_name=actor_name,
                raw_text=raw_text,
                raw_metadata=metadata,
                received_at=received_at,
            )
            result = await self._ingestion.ingest(
                WebFormConnector(),
                web_request,
                dispatch=False,
            )
        else:
            file_request = FileConnectorRequest(
                external_event_id=external_event_id,
                submitter_id=owner_id,
                submitter_name=actor_name,
                raw_text=raw_text,
                raw_metadata=metadata,
                received_at=received_at,
                attachment=attachment,
            )
            result = await self._ingestion.ingest(
                self._file_connector(attachment.file_type),
                file_request,
                dispatch=False,
            )

        message = await self._associate_source(
            conversation_key=conversation_key,
            owner_id=owner_id,
            source_record_id=result.source.id,
            title_text=raw_text or (attachment.file_name if attachment else ""),
        )
        self._ingestion.dispatch(result.source)
        return message, result

    async def add_chat_message(
        self, *, conversation_key: str, owner_id: str, role: str, content: str,
        tool_calls: list[dict[str, object]] | None = None,
        references: list[dict[str, object]] | None = None,
        chat_status: str = "submitted", reply_to_message_id: int | None = None,
    ) -> ConversationMessage:
        """Persist a query/answer turn without creating a SourceRecord."""
        conversation = await self.get(conversation_key, owner_id, for_update=True)
        maximum = await self._session.scalar(select(func.coalesce(
            func.max(ConversationMessage.sequence_number), 0
        )).where(ConversationMessage.conversation_id == conversation.id))
        message = ConversationMessage(
            message_key=f"MSG-{uuid4().hex[:24].upper()}",
            conversation_id=conversation.id,
            sequence_number=(maximum or 0) + 1,
            role=role,
            content=content,
            tool_calls=tool_calls or [],
            references=references or [],
            chat_status=chat_status,
            reply_to_message_id=reply_to_message_id,
        )
        self._session.add(message)
        conversation.updated_at = datetime.now(UTC)
        await self._session.commit()
        await self._session.refresh(message)
        return message

    async def load_message(
        self,
        conversation_key: str,
        owner_id: str,
        message_key: str,
    ) -> ConversationMessage:
        conversation = await self.get(conversation_key, owner_id)
        message = (
            await self._session.execute(
                select(ConversationMessage)
                .options(selectinload(ConversationMessage.source_record))
                .where(
                    ConversationMessage.conversation_id == conversation.id,
                    ConversationMessage.message_key == message_key,
                )
            )
        ).scalar_one_or_none()
        if message is None:
            raise ConversationNotFoundError(f"message {message_key} was not found")
        return message

    async def _associate_source(
        self,
        *,
        conversation_key: str,
        owner_id: str,
        source_record_id: int,
        title_text: str,
    ) -> ConversationMessage:
        existing = await self._message_for_source(source_record_id)
        if existing is not None:
            conversation = await self.get(conversation_key, owner_id)
            if existing.conversation_id != conversation.id:
                raise IdempotencyConflictError(
                    "the idempotency key belongs to another conversation"
                )
            return existing

        for attempt in range(3):
            conversation = await self.get(conversation_key, owner_id, for_update=True)
            max_sequence = await self._session.scalar(
                select(func.coalesce(func.max(ConversationMessage.sequence_number), 0))
                .where(ConversationMessage.conversation_id == conversation.id)
            )
            sequence_number = (max_sequence or 0) + 1
            message = ConversationMessage(
                message_key=f"MSG-{uuid4().hex[:24].upper()}",
                conversation_id=conversation.id,
                source_record_id=source_record_id,
                sequence_number=sequence_number,
                role="user",
            )
            self._session.add(message)
            if (
                sequence_number == 1
                and not conversation.title_is_custom
                and title_text.strip()
            ):
                conversation.title = self._derived_title(title_text)
            conversation.updated_at = datetime.now(UTC)
            try:
                await self._session.commit()
                await self._session.refresh(message)
                return message
            except IntegrityError as exc:
                await self._session.rollback()
                existing = await self._message_for_source(source_record_id)
                if existing is not None:
                    if existing.conversation_id != conversation.id:
                        raise IdempotencyConflictError(
                            "the source is already associated with another conversation"
                        ) from exc
                    return existing
                if attempt == 2:
                    raise
        raise RuntimeError("unreachable")

    async def _message_for_source(
        self,
        source_record_id: int,
    ) -> ConversationMessage | None:
        return (
            await self._session.execute(
                select(ConversationMessage).where(
                    ConversationMessage.source_record_id == source_record_id
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    def _external_event_id(owner_id: str, idempotency_key: str) -> str:
        digest = hashlib.sha256(f"{owner_id}\0{idempotency_key}".encode()).hexdigest()
        return f"conversation-{digest}"

    @staticmethod
    def _derived_title(value: str) -> str:
        return " ".join(value.split())[:255]

    @staticmethod
    def _file_connector(file_type: str) -> DocumentConnector | ImageConnector:
        if file_type in {
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }:
            return DocumentConnector()
        if file_type in {"image/jpeg", "image/png"}:
            return ImageConnector()
        raise UnsupportedFileError(f"unsupported file type: {file_type}")
