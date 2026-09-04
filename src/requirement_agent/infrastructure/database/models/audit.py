from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, Enum, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from requirement_agent.infrastructure.database.base import Base
from requirement_agent.shared.enums import AuditActionType, AuditEntityType

BIGINT_PK = BigInteger().with_variant(Integer, "sqlite")
JSON_DATA = JSON().with_variant(JSONB, "postgresql")


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_actor_id", "actor_id"),
        Index("ix_audit_log_entity", "entity_type", "entity_id"),
        Index("ix_audit_log_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    action_type: Mapped[AuditActionType] = mapped_column(
        Enum(
            AuditActionType,
            name="audit_action_type",
            values_callable=lambda enum_type: [item.value for item in enum_type],
            validate_strings=True,
        ),
        nullable=False,
    )
    entity_type: Mapped[AuditEntityType] = mapped_column(
        Enum(
            AuditEntityType,
            name="audit_entity_type",
            values_callable=lambda enum_type: [item.value for item in enum_type],
            validate_strings=True,
        ),
        nullable=False,
    )
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    before_data: Mapped[dict[str, object] | None] = mapped_column(JSON_DATA)
    after_data: Mapped[dict[str, object] | None] = mapped_column(JSON_DATA)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
