"""Resize requirement embeddings to the configured 1024 dimensions.

Revision ID: 20260904_0005
Revises: 20260904_0004
Create Date: 2026-09-04 11:45:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260904_0005"
down_revision: str | None = "20260904_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("TRUNCATE TABLE requirement_embedding")
    op.drop_index(
        "ix_requirement_embedding_vector_hnsw",
        table_name="requirement_embedding",
    )
    op.execute(
        """
        ALTER TABLE requirement_embedding
        ALTER COLUMN embedding TYPE vector(1024)
        USING embedding::vector(1024)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_requirement_embedding_vector_hnsw
        ON requirement_embedding
        USING hnsw (embedding vector_cosine_ops)
        """
    )


def downgrade() -> None:
    op.execute("TRUNCATE TABLE requirement_embedding")
    op.drop_index(
        "ix_requirement_embedding_vector_hnsw",
        table_name="requirement_embedding",
    )
    op.execute(
        """
        ALTER TABLE requirement_embedding
        ALTER COLUMN embedding TYPE vector(1536)
        USING embedding::vector(1536)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_requirement_embedding_vector_hnsw
        ON requirement_embedding
        USING hnsw (embedding vector_cosine_ops)
        """
    )
