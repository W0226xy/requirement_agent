from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from requirement_agent.infrastructure.database.base import Base
from requirement_agent.shared.enums import AttachmentParseStatus, ChannelType, ProcessingStatus

if TYPE_CHECKING:
    from requirement_agent.infrastructure.database.models.conversation import (
        ConversationMessage,
    )

BIGINT_PK = BigInteger().with_variant(Integer, "sqlite")
JSON_DATA = JSON().with_variant(JSONB, "postgresql")


def enum_values(
    enum_type: type[ChannelType | ProcessingStatus | AttachmentParseStatus],
) -> list[str]:
    return [item.value for item in enum_type]


class SourceRecord(Base):
    __tablename__ = "source_record"
    __table_args__ = (
        UniqueConstraint(
            "channel_type",
            "external_event_id",
            name="uq_source_record_channel_external_event",
        ),
        Index("ix_source_record_channel_received", "channel_type", "received_at"),
        Index("ix_source_record_submitter_id", "submitter_id"),
        Index("ix_source_record_processing_status", "processing_status"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    source_key: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    channel_type: Mapped[ChannelType] = mapped_column(
        Enum(
            ChannelType,
            name="channel_type",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
    )
    external_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    submitter_id: Mapped[str] = mapped_column(String(255), nullable=False)
    submitter_name: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    raw_metadata: Mapped[dict[str, object]] = mapped_column(JSON_DATA, nullable=False, default=dict)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processing_status: Mapped[ProcessingStatus] = mapped_column(
        Enum(
            ProcessingStatus,
            name="processing_status",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
        default=ProcessingStatus.RECEIVED,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    attachments: Mapped[list["SourceAttachment"]] = relationship(
        back_populates="source_record",
        cascade="save-update, merge",
        passive_deletes=True,
    )
    conversation_message: Mapped["ConversationMessage | None"] = relationship(
        back_populates="source_record",
        uselist=False,
    )


class SourceAttachment(Base):
    __tablename__ = "source_attachment"
    __table_args__ = (
        UniqueConstraint(
            "source_record_id",
            "file_hash",
            name="uq_source_attachment_source_hash",
        ),
        Index("ix_source_attachment_source_record_id", "source_record_id"),
        Index("ix_source_attachment_file_hash", "file_hash"),
        Index("ix_source_attachment_parse_status", "parse_status"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    source_record_id: Mapped[int] = mapped_column(
        ForeignKey("source_record.id", ondelete="RESTRICT"),
        nullable=False,
    )
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    file_type: Mapped[str] = mapped_column(String(128), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    ocr_text: Mapped[str | None] = mapped_column(Text)
    parsed_text: Mapped[str | None] = mapped_column(Text)
    parse_status: Mapped[AttachmentParseStatus] = mapped_column(
        Enum(
            AttachmentParseStatus,
            name="attachment_parse_status",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
        default=AttachmentParseStatus.PENDING,
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    source_record: Mapped[SourceRecord] = relationship(back_populates="attachments")
