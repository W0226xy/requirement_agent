"""allow source-free chat turns in conversations.

Revision ID: 20260918_0008
Revises: 20260918_0007
"""
from alembic import op
import sqlalchemy as sa

revision = "20260918_0008"
down_revision = "20260918_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("conversation_message", "source_record_id", nullable=True)
    op.add_column("conversation_message", sa.Column("content", sa.Text(), server_default="", nullable=False))
    op.add_column("conversation_message", sa.Column("tool_calls", sa.JSON(), server_default="[]", nullable=False))
    op.add_column("conversation_message", sa.Column("references", sa.JSON(), server_default="[]", nullable=False))


def downgrade() -> None:
    op.drop_column("conversation_message", "references")
    op.drop_column("conversation_message", "tool_calls")
    op.drop_column("conversation_message", "content")
    # Downgrade is only valid after source-free turns have been removed.
    op.alter_column("conversation_message", "source_record_id", nullable=False)
