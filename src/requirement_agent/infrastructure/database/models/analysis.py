from datetime import datetime

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Enum,
    Float,
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
from sqlalchemy.orm import Mapped, mapped_column

from requirement_agent.infrastructure.database.base import Base
from requirement_agent.shared.config import get_settings
from requirement_agent.shared.enums import AnalysisType

BIGINT_PK = BigInteger().with_variant(Integer, "sqlite")
JSON_DATA = JSON().with_variant(JSONB, "postgresql")
VECTOR_DATA = VECTOR(get_settings().embedding_dimension).with_variant(JSON(), "sqlite")


class AnalysisResult(Base):
    __tablename__ = "analysis_result"
    __table_args__ = (
        Index("ix_analysis_result_source_type", "source_record_id", "analysis_type"),
        Index("ix_analysis_result_model_name", "model_name"),
        Index("ix_analysis_result_created_at", "created_at"),
        Index("ix_analysis_result_error", "error_message"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    source_record_id: Mapped[int] = mapped_column(
        ForeignKey("source_record.id", ondelete="RESTRICT"),
        nullable=False,
    )
    analysis_type: Mapped[AnalysisType] = mapped_column(
        Enum(
            AnalysisType,
            name="analysis_type",
            values_callable=lambda enum_type: [item.value for item in enum_type],
            validate_strings=True,
        ),
        nullable=False,
    )
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_snapshot: Mapped[dict[str, object]] = mapped_column(JSON_DATA, nullable=False)
    result_json: Mapped[dict[str, object] | None] = mapped_column(JSON_DATA)
    raw_output: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class RequirementEmbedding(Base):
    __tablename__ = "requirement_embedding"
    __table_args__ = (
        UniqueConstraint(
            "version_id",
            "feature_key",
            "embedding_model",
            name="uq_requirement_embedding_version_feature_model",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_requirement_embedding_requirement_id", "requirement_id"),
        Index("ix_requirement_embedding_version_id", "version_id"),
        Index("ix_requirement_embedding_module", "module"),
        Index("ix_requirement_embedding_status", "status"),
        Index(
            "ix_requirement_embedding_fulltext",
            text(
                "to_tsvector('simple'::regconfig, "
                "(title::text || ' '::text) || content)"
            ),
            postgresql_using="gin",
        ).ddl_if(dialect="postgresql"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    requirement_id: Mapped[int] = mapped_column(
        ForeignKey("requirement.id", ondelete="RESTRICT"), nullable=False
    )
    version_id: Mapped[int] = mapped_column(
        ForeignKey("requirement_version.id", ondelete="RESTRICT"), nullable=False
    )
    requirement_key: Mapped[str] = mapped_column(String(64), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    feature_key: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    module: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    functional_modules: Mapped[list[str]] = mapped_column(JSON_DATA, nullable=False)
    features: Mapped[list[dict[str, object]]] = mapped_column(JSON_DATA, nullable=False)
    sources: Mapped[list[dict[str, object]]] = mapped_column(JSON_DATA, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(VECTOR_DATA, nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


Index(
    "ix_requirement_embedding_vector_hnsw",
    RequirementEmbedding.embedding,
    postgresql_using="hnsw",
    postgresql_ops={"embedding": "vector_cosine_ops"},
).ddl_if(dialect="postgresql")
