from datetime import datetime
from enum import StrEnum

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
from requirement_agent.shared.enums import (
    FeatureStatus,
    LineageOperationType,
    RequirementChangeType,
    RequirementStatus,
)

BIGINT_PK = BigInteger().with_variant(Integer, "sqlite")
BIGINT_FK = BigInteger().with_variant(Integer, "sqlite")
JSON_DATA = JSON().with_variant(JSONB, "postgresql")


def enum_values[EnumType: StrEnum](enum_type: type[EnumType]) -> list[str]:
    return [item.value for item in enum_type]


class Requirement(Base):
    __tablename__ = "requirement"
    __table_args__ = (
        Index("ix_requirement_status", "status"),
        Index("ix_requirement_updated_at", "updated_at"),
        Index("ix_requirement_title", "title"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    requirement_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    current_version_id: Mapped[int | None] = mapped_column(
        BIGINT_FK,
        ForeignKey(
            "requirement_version.id",
            name="fk_requirement_current_version_id_requirement_version",
            ondelete="RESTRICT",
            use_alter=True,
        ),
    )
    status: Mapped[RequirementStatus] = mapped_column(
        Enum(
            RequirementStatus,
            name="requirement_status",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
        default=RequirementStatus.ACTIVE,
    )
    functional_modules: Mapped[list[str]] = mapped_column(JSON_DATA, nullable=False)
    extra_fields: Mapped[dict[str, object]] = mapped_column(JSON_DATA, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    versions: Mapped[list["RequirementVersion"]] = relationship(
        back_populates="requirement",
        foreign_keys="RequirementVersion.requirement_id",
        order_by="RequirementVersion.version_number",
    )


class RequirementVersion(Base):
    __tablename__ = "requirement_version"
    __table_args__ = (
        UniqueConstraint(
            "requirement_id",
            "version_number",
            name="uq_requirement_version_requirement_number",
        ),
        Index("ix_requirement_version_requirement_id", "requirement_id"),
        Index("ix_requirement_version_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    requirement_id: Mapped[int] = mapped_column(
        BIGINT_FK,
        ForeignKey("requirement.id", ondelete="RESTRICT"),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_version_id: Mapped[int | None] = mapped_column(
        BIGINT_FK,
        ForeignKey("requirement_version.id", ondelete="RESTRICT"),
    )
    change_type: Mapped[RequirementChangeType] = mapped_column(
        Enum(
            RequirementChangeType,
            name="requirement_change_type",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
    )
    version_title: Mapped[str] = mapped_column(String(500), nullable=False)
    requirement_snapshot: Mapped[dict[str, object]] = mapped_column(JSON_DATA, nullable=False)
    diff_snapshot: Mapped[dict[str, object]] = mapped_column(JSON_DATA, nullable=False)
    change_reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    reviewed_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    requirement: Mapped[Requirement] = relationship(
        back_populates="versions",
        foreign_keys=[requirement_id],
    )
    features: Mapped[list["RequirementFeature"]] = relationship(
        back_populates="version",
        order_by="RequirementFeature.sort_order",
    )


class RequirementFeature(Base):
    __tablename__ = "requirement_feature"
    __table_args__ = (
        UniqueConstraint("version_id", "feature_key", name="uq_requirement_feature_version_key"),
        Index("ix_requirement_feature_feature_key", "feature_key"),
        Index("ix_requirement_feature_module", "module"),
        Index("ix_requirement_feature_status", "feature_status"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    feature_key: Mapped[str] = mapped_column(String(64), nullable=False)
    version_id: Mapped[int] = mapped_column(
        BIGINT_FK,
        ForeignKey("requirement_version.id", ondelete="RESTRICT"),
        nullable=False,
    )
    module: Mapped[str] = mapped_column(String(255), nullable=False)
    feature_title: Mapped[str] = mapped_column(String(500), nullable=False)
    feature_description: Mapped[str] = mapped_column(Text, nullable=False)
    acceptance_criteria: Mapped[list[str]] = mapped_column(JSON_DATA, nullable=False)
    feature_status: Mapped[FeatureStatus] = mapped_column(
        Enum(
            FeatureStatus,
            name="feature_status",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    version: Mapped[RequirementVersion] = relationship(back_populates="features")


class FeatureLineage(Base):
    __tablename__ = "feature_lineage"
    __table_args__ = (
        Index("ix_feature_lineage_feature_key", "feature_key"),
        Index("ix_feature_lineage_source_record_id", "source_record_id"),
        Index("ix_feature_lineage_version_id", "introduced_version_id"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    feature_key: Mapped[str] = mapped_column(String(64), nullable=False)
    source_record_id: Mapped[int] = mapped_column(
        BIGINT_FK,
        ForeignKey("source_record.id", ondelete="RESTRICT"),
        nullable=False,
    )
    introduced_version_id: Mapped[int] = mapped_column(
        BIGINT_FK,
        ForeignKey("requirement_version.id", ondelete="RESTRICT"),
        nullable=False,
    )
    operation_type: Mapped[LineageOperationType] = mapped_column(
        Enum(
            LineageOperationType,
            name="lineage_operation_type",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
    )
    evidence_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
