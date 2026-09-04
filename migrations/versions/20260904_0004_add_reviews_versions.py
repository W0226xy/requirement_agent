"""Add human review and immutable requirement versions.

Revision ID: 20260904_0004
Revises: 20260903_0003
Create Date: 2026-09-04 09:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260904_0004"
down_revision: str | None = "20260903_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def pg_enum(name: str, *values: str) -> postgresql.ENUM:
    return postgresql.ENUM(*values, name=name, create_type=False)


requirement_status = pg_enum("requirement_status", "active", "archived")
requirement_change_type = pg_enum("requirement_change_type", "initial", "update")
feature_status = pg_enum("feature_status", "active", "deleted")
lineage_operation_type = pg_enum(
    "lineage_operation_type", "introduced", "modified", "deleted", "restored"
)
review_status = pg_enum("review_status", "pending", "approved", "rejected", "returned")
review_decision = pg_enum("review_decision", "create", "merge")


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in (
        requirement_status,
        requirement_change_type,
        feature_status,
        lineage_operation_type,
        review_status,
        review_decision,
    ):
        enum_type.create(bind, checkfirst=True)

    op.execute("ALTER TYPE audit_action_type ADD VALUE IF NOT EXISTS 'review_task_created'")
    op.execute("ALTER TYPE audit_action_type ADD VALUE IF NOT EXISTS 'review_approved'")
    op.execute("ALTER TYPE audit_action_type ADD VALUE IF NOT EXISTS 'review_rejected'")
    op.execute("ALTER TYPE audit_action_type ADD VALUE IF NOT EXISTS 'review_returned'")
    op.execute(
        "ALTER TYPE audit_action_type ADD VALUE IF NOT EXISTS 'requirement_version_created'"
    )
    op.execute("ALTER TYPE audit_entity_type ADD VALUE IF NOT EXISTS 'review_task'")
    op.execute("ALTER TYPE audit_entity_type ADD VALUE IF NOT EXISTS 'requirement'")
    op.execute("ALTER TYPE audit_entity_type ADD VALUE IF NOT EXISTS 'requirement_version'")

    op.create_table(
        "requirement",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("requirement_key", sa.String(64), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("current_version_id", sa.BigInteger(), nullable=True),
        sa.Column("status", requirement_status, nullable=False),
        sa.Column("functional_modules", postgresql.JSONB(), nullable=False),
        sa.Column("extra_fields", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_requirement")),
        sa.UniqueConstraint("requirement_key", name=op.f("uq_requirement_requirement_key")),
    )
    op.create_index("ix_requirement_status", "requirement", ["status"])
    op.create_index("ix_requirement_title", "requirement", ["title"])
    op.create_index("ix_requirement_updated_at", "requirement", ["updated_at"])

    op.create_table(
        "requirement_version",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("requirement_id", sa.BigInteger(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("parent_version_id", sa.BigInteger(), nullable=True),
        sa.Column("change_type", requirement_change_type, nullable=False),
        sa.Column("version_title", sa.String(500), nullable=False),
        sa.Column("requirement_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("diff_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("change_reason", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("reviewed_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["parent_version_id"], ["requirement_version.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requirement_id"], ["requirement.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_requirement_version")),
        sa.UniqueConstraint(
            "requirement_id",
            "version_number",
            name="uq_requirement_version_requirement_number",
        ),
    )
    op.create_index("ix_requirement_version_requirement_id", "requirement_version", ["requirement_id"])
    op.create_index("ix_requirement_version_created_at", "requirement_version", ["created_at"])
    op.create_foreign_key(
        "fk_requirement_current_version_id_requirement_version",
        "requirement",
        "requirement_version",
        ["current_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "requirement_feature",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("feature_key", sa.String(64), nullable=False),
        sa.Column("version_id", sa.BigInteger(), nullable=False),
        sa.Column("module", sa.String(255), nullable=False),
        sa.Column("feature_title", sa.String(500), nullable=False),
        sa.Column("feature_description", sa.Text(), nullable=False),
        sa.Column("acceptance_criteria", postgresql.JSONB(), nullable=False),
        sa.Column("feature_status", feature_status, nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["version_id"], ["requirement_version.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_requirement_feature")),
        sa.UniqueConstraint("version_id", "feature_key", name="uq_requirement_feature_version_key"),
    )
    op.create_index("ix_requirement_feature_feature_key", "requirement_feature", ["feature_key"])
    op.create_index("ix_requirement_feature_module", "requirement_feature", ["module"])
    op.create_index("ix_requirement_feature_status", "requirement_feature", ["feature_status"])

    op.create_table(
        "feature_lineage",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("feature_key", sa.String(64), nullable=False),
        sa.Column("source_record_id", sa.BigInteger(), nullable=False),
        sa.Column("introduced_version_id", sa.BigInteger(), nullable=False),
        sa.Column("operation_type", lineage_operation_type, nullable=False),
        sa.Column("evidence_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["introduced_version_id"], ["requirement_version.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_record_id"], ["source_record.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_feature_lineage")),
    )
    op.create_index("ix_feature_lineage_feature_key", "feature_lineage", ["feature_key"])
    op.create_index("ix_feature_lineage_source_record_id", "feature_lineage", ["source_record_id"])
    op.create_index("ix_feature_lineage_version_id", "feature_lineage", ["introduced_version_id"])

    op.create_table(
        "review_task",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_record_id", sa.BigInteger(), nullable=False),
        sa.Column("target_requirement_id", sa.BigInteger(), nullable=True),
        sa.Column("analysis_result_id", sa.BigInteger(), nullable=False),
        sa.Column("review_status", review_status, nullable=False),
        sa.Column("decision", review_decision, nullable=True),
        sa.Column("extraction_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("candidate_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("analysis_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("approved_operations", postgresql.JSONB(), nullable=True),
        sa.Column("reviewer_id", sa.String(255), nullable=True),
        sa.Column("review_comment", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("committed_version_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["analysis_result_id"], ["analysis_result.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["committed_version_id"], ["requirement_version.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_record_id"], ["source_record.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["target_requirement_id"], ["requirement.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_task")),
        sa.UniqueConstraint("analysis_result_id", name=op.f("uq_review_task_analysis_result_id")),
        sa.UniqueConstraint("committed_version_id", name=op.f("uq_review_task_committed_version_id")),
    )
    op.create_index("ix_review_task_source_record_id", "review_task", ["source_record_id"])
    op.create_index("ix_review_task_status_created", "review_task", ["review_status", "created_at"])
    op.create_index("ix_review_task_target_requirement_id", "review_task", ["target_requirement_id"])

    op.create_foreign_key(
        "fk_requirement_embedding_requirement_id_requirement",
        "requirement_embedding",
        "requirement",
        ["requirement_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_requirement_embedding_version_id_requirement_version",
        "requirement_embedding",
        "requirement_version",
        ["version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    for table in ("requirement_version", "requirement_feature", "feature_lineage"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_immutable_row_change()"
        )


def downgrade() -> None:
    for table in ("feature_lineage", "requirement_feature", "requirement_version"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable ON {table}")
    op.drop_constraint(
        "fk_requirement_embedding_version_id_requirement_version",
        "requirement_embedding",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_requirement_embedding_requirement_id_requirement",
        "requirement_embedding",
        type_="foreignkey",
    )
    op.drop_table("review_task")
    op.drop_table("feature_lineage")
    op.drop_table("requirement_feature")
    op.drop_constraint(
        "fk_requirement_current_version_id_requirement_version",
        "requirement",
        type_="foreignkey",
    )
    op.drop_table("requirement_version")
    op.drop_table("requirement")
    for enum_type in (
        review_decision,
        review_status,
        lineage_operation_type,
        feature_status,
        requirement_change_type,
        requirement_status,
    ):
        enum_type.drop(op.get_bind(), checkfirst=True)
