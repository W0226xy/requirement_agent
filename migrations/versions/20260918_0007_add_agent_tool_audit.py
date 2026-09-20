"""add agent tool audit enum values

Revision ID: 20260918_0007
Revises: 20260910_0006
Create Date: 2026-09-18
"""
from alembic import op

revision = "20260918_0007"
down_revision = "20260910_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL enum additions are intentionally additive; SQLite stores these as text.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE audit_action_type ADD VALUE IF NOT EXISTS 'agent_tool_called'")
        op.execute("ALTER TYPE audit_entity_type ADD VALUE IF NOT EXISTS 'conversation'")


def downgrade() -> None:
    # PostgreSQL cannot remove enum values safely without recreating dependent types.
    pass
