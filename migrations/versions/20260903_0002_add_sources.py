"""Add immutable source records, attachments, and audit logs.

Revision ID: 20260903_0002
Revises: 20260902_0001
Create Date: 2026-09-03 09:48:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260903_0002"
down_revision: str | None = "20260902_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

channel_type = postgresql.ENUM(
    "web_form", "document", "image", "feishu", name="channel_type", create_type=False
)
processing_status = postgresql.ENUM(
    "received",
    "parsing",
    "extracted",
    "retrieving",
    "analyzing",
    "pending_review",
    "approved",
    "rejected",
    "returned",
    "versioned",
    "parse_failed",
    "extraction_failed",
    "analysis_failed",
    "version_failed",
    name="processing_status",
    create_type=False,
)
attachment_parse_status = postgresql.ENUM(
    "pending", "parsing", "parsed", "failed", name="attachment_parse_status", create_type=False
)
audit_action_type = postgresql.ENUM(
    "source_received",
    "attachment_stored",
    "source_parsing_started",
    "attachment_parsed",
    "source_parse_failed",
    name="audit_action_type",
    create_type=False,
)
audit_entity_type = postgresql.ENUM(
    "source_record", "source_attachment", name="audit_entity_type", create_type=False
)


def upgrade() -> None:
    bind = op.get_bind()
    channel_type.create(bind, checkfirst=True)
    processing_status.create(bind, checkfirst=True)
    attachment_parse_status.create(bind, checkfirst=True)
    audit_action_type.create(bind, checkfirst=True)
    audit_entity_type.create(bind, checkfirst=True)

    op.create_table(
        "source_record",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_key", sa.String(length=32), nullable=False),
        sa.Column("channel_type", channel_type, nullable=False),
        sa.Column("external_event_id", sa.String(length=255), nullable=False),
        sa.Column("submitter_id", sa.String(length=255), nullable=False),
        sa.Column("submitter_name", sa.String(length=255), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("raw_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processing_status", processing_status, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_record")),
        sa.UniqueConstraint("source_key", name=op.f("uq_source_record_source_key")),
        sa.UniqueConstraint(
            "channel_type",
            "external_event_id",
            name="uq_source_record_channel_external_event",
        ),
    )
    op.create_index(
        "ix_source_record_channel_received",
        "source_record",
        ["channel_type", "received_at"],
    )
    op.create_index(
        "ix_source_record_processing_status", "source_record", ["processing_status"]
    )
    op.create_index("ix_source_record_submitter_id", "source_record", ["submitter_id"])

    op.create_table(
        "source_attachment",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_record_id", sa.BigInteger(), nullable=False),
        sa.Column("file_name", sa.String(length=512), nullable=False),
        sa.Column("file_type", sa.String(length=128), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("storage_path", sa.String(length=1024), nullable=False),
        sa.Column("ocr_text", sa.Text(), nullable=True),
        sa.Column("parsed_text", sa.Text(), nullable=True),
        sa.Column("parse_status", attachment_parse_status, nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_record_id"],
            ["source_record.id"],
            name=op.f("fk_source_attachment_source_record_id_source_record"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_attachment")),
        sa.UniqueConstraint(
            "source_record_id",
            "file_hash",
            name="uq_source_attachment_source_hash",
        ),
        sa.UniqueConstraint("storage_path", name=op.f("uq_source_attachment_storage_path")),
    )
    op.create_index("ix_source_attachment_file_hash", "source_attachment", ["file_hash"])
    op.create_index(
        "ix_source_attachment_parse_status", "source_attachment", ["parse_status"]
    )
    op.create_index(
        "ix_source_attachment_source_record_id", "source_attachment", ["source_record_id"]
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("action_type", audit_action_type, nullable=False),
        sa.Column("entity_type", audit_entity_type, nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("before_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index("ix_audit_log_actor_id", "audit_log", ["actor_id"])
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])
    op.create_index("ix_audit_log_entity", "audit_log", ["entity_type", "entity_id"])

    op.execute(
        """
        CREATE FUNCTION reject_source_record_content_change() RETURNS trigger AS $$
        BEGIN
          IF NEW.source_key IS DISTINCT FROM OLD.source_key
             OR NEW.channel_type IS DISTINCT FROM OLD.channel_type
             OR NEW.external_event_id IS DISTINCT FROM OLD.external_event_id
             OR NEW.submitter_id IS DISTINCT FROM OLD.submitter_id
             OR NEW.submitter_name IS DISTINCT FROM OLD.submitter_name
             OR NEW.raw_text IS DISTINCT FROM OLD.raw_text
             OR NEW.raw_metadata IS DISTINCT FROM OLD.raw_metadata
             OR NEW.received_at IS DISTINCT FROM OLD.received_at
             OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION 'source_record original content is immutable';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_source_record_immutable
        BEFORE UPDATE ON source_record
        FOR EACH ROW EXECUTE FUNCTION reject_source_record_content_change()
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_source_attachment_identity_change() RETURNS trigger AS $$
        BEGIN
          IF NEW.source_record_id IS DISTINCT FROM OLD.source_record_id
             OR NEW.file_name IS DISTINCT FROM OLD.file_name
             OR NEW.file_type IS DISTINCT FROM OLD.file_type
             OR NEW.file_size IS DISTINCT FROM OLD.file_size
             OR NEW.file_hash IS DISTINCT FROM OLD.file_hash
             OR NEW.storage_path IS DISTINCT FROM OLD.storage_path
             OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION 'source_attachment identity is immutable';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_source_attachment_immutable
        BEFORE UPDATE ON source_attachment
        FOR EACH ROW EXECUTE FUNCTION reject_source_attachment_identity_change()
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_immutable_row_change() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION '% rows are immutable', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_source_record_no_delete
        BEFORE DELETE ON source_record
        FOR EACH ROW EXECUTE FUNCTION reject_immutable_row_change()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_source_attachment_no_delete
        BEFORE DELETE ON source_attachment
        FOR EACH ROW EXECUTE FUNCTION reject_immutable_row_change()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_log_immutable
        BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION reject_immutable_row_change()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_log_immutable ON audit_log")
    op.execute("DROP TRIGGER IF EXISTS trg_source_attachment_no_delete ON source_attachment")
    op.execute("DROP TRIGGER IF EXISTS trg_source_record_no_delete ON source_record")
    op.execute("DROP TRIGGER IF EXISTS trg_source_attachment_immutable ON source_attachment")
    op.execute("DROP TRIGGER IF EXISTS trg_source_record_immutable ON source_record")
    op.execute("DROP FUNCTION IF EXISTS reject_immutable_row_change()")
    op.execute("DROP FUNCTION IF EXISTS reject_source_attachment_identity_change()")
    op.execute("DROP FUNCTION IF EXISTS reject_source_record_content_change()")
    op.drop_table("audit_log")
    op.drop_table("source_attachment")
    op.drop_table("source_record")
    bind = op.get_bind()
    audit_entity_type.drop(bind, checkfirst=True)
    audit_action_type.drop(bind, checkfirst=True)
    attachment_parse_status.drop(bind, checkfirst=True)
    processing_status.drop(bind, checkfirst=True)
    channel_type.drop(bind, checkfirst=True)
