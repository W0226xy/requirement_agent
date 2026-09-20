"""add asynchronous chat query state."""
from alembic import op
import sqlalchemy as sa

revision = "20260918_0009"
down_revision = "20260918_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversation_message", sa.Column("chat_status", sa.String(32), server_default="submitted", nullable=False))
    op.add_column("conversation_message", sa.Column("reply_to_message_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_conversation_message_reply_to", "conversation_message", "conversation_message", ["reply_to_message_id"], ["id"], ondelete="RESTRICT")


def downgrade() -> None:
    op.drop_constraint("fk_conversation_message_reply_to", "conversation_message", type_="foreignkey")
    op.drop_column("conversation_message", "reply_to_message_id")
    op.drop_column("conversation_message", "chat_status")
