"""Persist approved multi-step workflows and their job lineage.

Revision ID: 0002_workflows
Revises: 0001_phase1
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_workflows"
down_revision = "0001_phase1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("asset_id", sa.String(36), nullable=False),
        sa.Column("brief", sa.Text(), nullable=False),
        sa.Column("language", sa.String(12), nullable=False),
        sa.Column("plan_json", sa.JSON(), nullable=False),
        sa.Column("options_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("current_step_index", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id", "asset_id"],
            ["assets.project_id", "assets.id"],
            name="fk_workflow_runs_project_asset_scope",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('awaiting_approval', 'queued', 'running', "
            "'cancel_requested', 'succeeded', 'failed', 'cancelled')",
            name="ck_workflow_runs_status",
        ),
    )
    op.create_index(
        "ix_workflow_runs_project_created", "workflow_runs", ["project_id", "created_at"]
    )
    op.create_index(
        "ix_workflow_runs_status_updated", "workflow_runs", ["status", "updated_at"]
    )
    op.create_table(
        "workflow_steps",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "workflow_id",
            sa.String(36),
            sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("tool_id", sa.String(80), nullable=False),
        sa.Column(
            "asset_id",
            sa.String(36),
            sa.ForeignKey("assets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("workflow_id", "sequence", name="uq_workflow_steps_sequence"),
        sa.UniqueConstraint("job_id", name="uq_workflow_steps_job"),
        sa.CheckConstraint(
            "status IN ('planned', 'queued', 'running', 'succeeded', "
            "'failed', 'cancel_requested', 'cancelled')",
            name="ck_workflow_steps_status",
        ),
    )
    op.create_index(
        "ix_workflow_steps_workflow_status", "workflow_steps", ["workflow_id", "status"]
    )


def downgrade() -> None:
    op.drop_index("ix_workflow_steps_workflow_status", table_name="workflow_steps")
    op.drop_table("workflow_steps")
    op.drop_index("ix_workflow_runs_status_updated", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_project_created", table_name="workflow_runs")
    op.drop_table("workflow_runs")
