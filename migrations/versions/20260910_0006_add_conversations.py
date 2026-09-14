"""Add owner-scoped requirement conversations and messages.

Revision ID: 20260910_0006
Revises: 20260904_0005
Create Date: 2026-09-10 10:08:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260910_0006"
down_revision: str | None = "20260904_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "requirement_conversation",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("conversation_key", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column(
            "title_is_custom",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "business_context",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("memory_revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "memory_covered_sequence",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_requirement_conversation")),
        sa.UniqueConstraint(
            "conversation_key",
            name=op.f("uq_requirement_conversation_conversation_key"),
        ),
    )
    op.create_index(
        "ix_requirement_conversation_owner_id",
        "requirement_conversation",
        ["owner_id"],
    )
    op.create_index(
        "ix_requirement_conversation_owner_updated",
        "requirement_conversation",
        ["owner_id", "updated_at"],
    )
    op.create_index(
        "ix_requirement_conversation_deleted_at",
        "requirement_conversation",
        ["deleted_at"],
    )

    op.create_table(
        "conversation_message",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("message_key", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.BigInteger(), nullable=False),
        sa.Column("source_record_id", sa.BigInteger(), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=32), server_default="user", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["requirement_conversation.id"],
            name=op.f(
                "fk_conversation_message_conversation_id_requirement_conversation"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_record_id"],
            ["source_record.id"],
            name=op.f("fk_conversation_message_source_record_id_source_record"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversation_message")),
        sa.UniqueConstraint(
            "conversation_id",
            "sequence_number",
            name="uq_conversation_message_conversation_sequence",
        ),
        sa.UniqueConstraint(
            "message_key",
            name=op.f("uq_conversation_message_message_key"),
        ),
        sa.UniqueConstraint(
            "source_record_id",
            name=op.f("uq_conversation_message_source_record_id"),
        ),
    )
    op.create_index(
        "ix_conversation_message_conversation_id",
        "conversation_message",
        ["conversation_id"],
    )
    op.create_index(
        "ix_conversation_message_created_at",
        "conversation_message",
        ["created_at"],
    )

    op.execute(
        """
        INSERT INTO requirement_conversation (
            conversation_key,
            owner_id,
            title,
            title_is_custom,
            summary,
            business_context,
            memory_revision,
            memory_covered_sequence,
            created_at,
            updated_at
        )
        SELECT
            'CONV-' || upper(substr(md5(submitter_id), 1, 24)),
            submitter_id,
            left(
                COALESCE(
                    (
                        array_agg(
                            NULLIF(btrim(raw_text), '') ORDER BY received_at, id
                        ) FILTER (WHERE NULLIF(btrim(raw_text), '') IS NOT NULL)
                    )[1],
                    'Conversation'
                ),
                255
            ),
            false,
            '',
            '{}'::jsonb,
            0,
            0,
            min(received_at),
            max(received_at)
        FROM source_record
        WHERE raw_metadata ->> 'input_surface' = 'conversation'
        GROUP BY submitter_id
        """
    )
    op.execute(
        """
        INSERT INTO conversation_message (
            message_key,
            conversation_id,
            source_record_id,
            sequence_number,
            role,
            created_at
        )
        SELECT
            'MSG-' || upper(substr(md5(source.id::text), 1, 24)),
            conversation.id,
            source.id,
            row_number() OVER (
                PARTITION BY source.submitter_id ORDER BY source.received_at, source.id
            ),
            'user',
            source.received_at
        FROM source_record AS source
        JOIN requirement_conversation AS conversation
          ON conversation.owner_id = source.submitter_id
         AND conversation.conversation_key =
             'CONV-' || upper(substr(md5(source.submitter_id), 1, 24))
        WHERE source.raw_metadata ->> 'input_surface' = 'conversation'
        """
    )


def downgrade() -> None:
    op.drop_table("conversation_message")
    op.drop_table("requirement_conversation")
