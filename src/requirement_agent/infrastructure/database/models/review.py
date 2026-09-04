from datetime import datetime

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
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from requirement_agent.infrastructure.database.base import Base
from requirement_agent.shared.enums import ReviewDecision, ReviewStatus

BIGINT_PK = BigInteger().with_variant(Integer, "sqlite")
BIGINT_FK = BigInteger().with_variant(Integer, "sqlite")
JSON_DATA = JSON().with_variant(JSONB, "postgresql")


class ReviewTask(Base):
    __tablename__ = "review_task"
    __table_args__ = (
        Index("ix_review_task_status_created", "review_status", "created_at"),
        Index("ix_review_task_source_record_id", "source_record_id"),
        Index("ix_review_task_target_requirement_id", "target_requirement_id"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    source_record_id: Mapped[int] = mapped_column(
        BIGINT_FK,
        ForeignKey("source_record.id", ondelete="RESTRICT"),
        nullable=False,
    )
    target_requirement_id: Mapped[int | None] = mapped_column(
        BIGINT_FK,
        ForeignKey("requirement.id", ondelete="RESTRICT"),
    )
    analysis_result_id: Mapped[int] = mapped_column(
        BIGINT_FK,
        ForeignKey("analysis_result.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    review_status: Mapped[ReviewStatus] = mapped_column(
        Enum(
            ReviewStatus,
            name="review_status",
            values_callable=lambda enum_type: [item.value for item in enum_type],
            validate_strings=True,
        ),
        nullable=False,
        default=ReviewStatus.PENDING,
    )
    decision: Mapped[ReviewDecision | None] = mapped_column(
        Enum(
            ReviewDecision,
            name="review_decision",
            values_callable=lambda enum_type: [item.value for item in enum_type],
            validate_strings=True,
        )
    )
    extraction_snapshot: Mapped[dict[str, object]] = mapped_column(JSON_DATA, nullable=False)
    candidate_snapshot: Mapped[list[dict[str, object]]] = mapped_column(
        JSON_DATA, nullable=False
    )
    analysis_snapshot: Mapped[dict[str, object]] = mapped_column(JSON_DATA, nullable=False)
    approved_operations: Mapped[list[dict[str, object]] | None] = mapped_column(JSON_DATA)
    reviewer_id: Mapped[str | None] = mapped_column(String(255))
    review_comment: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    committed_version_id: Mapped[int | None] = mapped_column(
        BIGINT_FK,
        ForeignKey("requirement_version.id", ondelete="RESTRICT"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
