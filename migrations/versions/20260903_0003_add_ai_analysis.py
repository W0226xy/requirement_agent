"""Add AI analysis records and requirement embedding projection.

Revision ID: 20260903_0003
Revises: 20260903_0002
Create Date: 2026-09-03 11:25:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects import postgresql

from requirement_agent.shared.config import get_settings

revision: str = "20260903_0003"
down_revision: str | None = "20260903_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

analysis_type = postgresql.ENUM(
    "extraction",
    "conflict_risk",
    "embedding",
    name="analysis_type",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    analysis_type.create(bind, checkfirst=True)
    vector_dimension = get_settings().embedding_dimension

    op.create_table(
        "analysis_result",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_record_id", sa.BigInteger(), nullable=False),
        sa.Column("analysis_type", analysis_type, nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column(
            "input_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "result_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("raw_output", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_record_id"],
            ["source_record.id"],
            name=op.f("fk_analysis_result_source_record_id_source_record"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analysis_result")),
    )
    op.create_index(
        "ix_analysis_result_source_type",
        "analysis_result",
        ["source_record_id", "analysis_type"],
    )
    op.create_index("ix_analysis_result_model_name", "analysis_result", ["model_name"])
    op.create_index("ix_analysis_result_created_at", "analysis_result", ["created_at"])
    op.create_index("ix_analysis_result_error", "analysis_result", ["error_message"])

    op.create_table(
        "requirement_embedding",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("requirement_id", sa.BigInteger(), nullable=False),
        sa.Column("version_id", sa.BigInteger(), nullable=False),
        sa.Column("requirement_key", sa.String(length=64), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("feature_key", sa.String(length=64), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("module", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column(
            "functional_modules",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "features",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "sources",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", VECTOR(vector_dimension), nullable=False),
        sa.Column("embedding_model", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_requirement_embedding")),
        sa.UniqueConstraint(
            "version_id",
            "feature_key",
            "embedding_model",
            name="uq_requirement_embedding_version_feature_model",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        "ix_requirement_embedding_requirement_id",
        "requirement_embedding",
        ["requirement_id"],
    )
    op.create_index(
        "ix_requirement_embedding_version_id",
        "requirement_embedding",
        ["version_id"],
    )
    op.create_index("ix_requirement_embedding_module", "requirement_embedding", ["module"])
    op.create_index("ix_requirement_embedding_status", "requirement_embedding", ["status"])
    op.execute(
        """
        CREATE INDEX ix_requirement_embedding_fulltext
        ON requirement_embedding
        USING gin (to_tsvector('simple', title || ' ' || content))
        """
    )
    op.execute(
        """
        CREATE INDEX ix_requirement_embedding_vector_hnsw
        ON requirement_embedding
        USING hnsw (embedding vector_cosine_ops)
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_analysis_result_immutable
        BEFORE UPDATE OR DELETE ON analysis_result
        FOR EACH ROW EXECUTE FUNCTION reject_immutable_row_change()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_requirement_embedding_immutable
        BEFORE UPDATE OR DELETE ON requirement_embedding
        FOR EACH ROW EXECUTE FUNCTION reject_immutable_row_change()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_requirement_embedding_immutable "
        "ON requirement_embedding"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_analysis_result_immutable ON analysis_result")
    op.drop_table("requirement_embedding")
    op.drop_table("analysis_result")
    analysis_type.drop(op.get_bind(), checkfirst=True)
