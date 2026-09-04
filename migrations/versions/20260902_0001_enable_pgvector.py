"""Enable pgvector extension.

Revision ID: 20260902_0001
Revises:
Create Date: 2026-09-02 17:05:00
"""

from alembic import op

revision: str = "20260902_0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    # Other migrations may depend on vector, so infrastructure rollback keeps it installed.
    pass

