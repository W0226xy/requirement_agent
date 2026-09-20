"""add module overview current and revision tables."""
from alembic import op
import sqlalchemy as sa

revision = "20260920_0010"
down_revision = "20260918_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("module_overview",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("module_name", sa.String(length=255), nullable=False, unique=True),
        sa.Column("overview", sa.Text()), sa.Column("core_capabilities", sa.JSON(), nullable=False),
        sa.Column("pending_items", sa.JSON(), nullable=False), sa.Column("requirement_count", sa.Integer(), nullable=False),
        sa.Column("source_snapshot", sa.JSON(), nullable=False), sa.Column("source_fingerprint", sa.String(length=64)),
        sa.Column("status", sa.String(length=32), nullable=False), sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table("module_overview_revision",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("module_overview_id", sa.BigInteger(), sa.ForeignKey("module_overview.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("overview", sa.Text(), nullable=False), sa.Column("core_capabilities", sa.JSON(), nullable=False),
        sa.Column("pending_items", sa.JSON(), nullable=False), sa.Column("source_snapshot", sa.JSON(), nullable=False), sa.Column("source_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("trigger_requirement_key", sa.String(length=64)), sa.Column("trigger_version_number", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_module_overview_revision_overview_id", "module_overview_revision", ["module_overview_id"])


def downgrade() -> None:
    op.drop_index("ix_module_overview_revision_overview_id", table_name="module_overview_revision")
    op.drop_table("module_overview_revision")
    op.drop_table("module_overview")
