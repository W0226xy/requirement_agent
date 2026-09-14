from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from requirement_agent.infrastructure.database.base import Base

if TYPE_CHECKING:
    from requirement_agent.infrastructure.database.models.source import SourceRecord

BIGINT_PK = BigInteger().with_variant(Integer, "sqlite")
JSON_DATA = JSON().with_variant(JSONB, "postgresql")


#会话记忆功能的两张核心表
#requirement_conversation  一次完整会话
#conversation_message  会话中的一条消息
#source_record         该消息对应的原始需求、附件、OCR/解析文本

#RequirementConversation：会话主表，一条记录代表一个 GPT 式会话
class RequirementConversation(Base):
    __tablename__ = "requirement_conversation"
    __table_args__ = (
        Index(
            "ix_requirement_conversation_owner_updated",
            "owner_id",
            "updated_at",
        ),
        Index("ix_requirement_conversation_deleted_at", "deleted_at"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)#数据库内部主键。
    conversation_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)#面向接口和前端的会话唯一标识，例如：conv-9f821
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)#会话所属用户
    title: Mapped[str] = mapped_column(String(255), nullable=False)#会话标题
    title_is_custom: Mapped[bool] = mapped_column(#系统自动生成会话标题时，title_is_custom 为 False；用户手动修改过标题时，title_is_custom 为 True。
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    summary: Mapped[str] = mapped_column(#会话摘要，通常是对话的简短总结，便于快速了解会话内容。
        Text,
        nullable=False,
        default="",
        server_default="",
    )
    business_context: Mapped[dict[str, object]] = mapped_column(#会话级业务背景，JSON 格式，用于存储与会话相关的业务信息，例如项目、模块、需求类型等。后续可以根据业务背景进行更智能的对话处理。
        JSON_DATA,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    memory_revision: Mapped[int] = mapped_column(#会话记忆的修订版本号，每次记忆更新时递增，用于跟踪记忆的变化。
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    memory_covered_sequence: Mapped[int] = mapped_column(#会话记忆覆盖的消息序号，表示记忆中包含了哪些消息的内容。
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    created_at: Mapped[datetime] = mapped_column(#会话创建时间
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(#会话更新时间

        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))#会话删除时间，软删除标记，deleted_at 不为空表示会话已被删除。

    messages: Mapped[list["ConversationMessage"]] = relationship(#会话中的消息列表
        back_populates="conversation",
        order_by="ConversationMessage.sequence_number",
    )

# ConversationMessage：会话消息表，一条记录代表一次对话中的一条消息
class ConversationMessage(Base):
    __tablename__ = "conversation_message"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "sequence_number",
            name="uq_conversation_message_conversation_sequence",
        ),
        Index("ix_conversation_message_conversation_id", "conversation_id"),
        Index("ix_conversation_message_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)#数据库内部主键。
    message_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)#面向接口和前端的消息唯一标识，例如：msg-9f821
    conversation_id: Mapped[int] = mapped_column(#关联到 RequirementConversation，表示该消息属于哪个会话。
        BIGINT_PK,
        ForeignKey("requirement_conversation.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_record_id: Mapped[int] = mapped_column(#关联到已有的 SourceRecord，复用原有的需求文本、附件、OCR 结果和解析结果。
        BIGINT_PK,
        ForeignKey("source_record.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)#消息在会话中的顺序号，从 1 开始递增，确保消息按时间顺序排列。
    role: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="user",
        server_default="user",
    )
    created_at: Mapped[datetime] = mapped_column(#消息创建时间
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    conversation: Mapped[RequirementConversation] = relationship(back_populates="messages")
    source_record: Mapped["SourceRecord"] = relationship(back_populates="conversation_message")
