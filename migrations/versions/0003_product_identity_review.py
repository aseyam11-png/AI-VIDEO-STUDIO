"""Add explicit product identity review gate to workflows.

Revision ID: 0003_product_identity_review
Revises: 0002_workflows
"""
from alembic import op

revision = "0003_product_identity_review"
down_revision = "0002_workflows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_workflow_runs_status", "workflow_runs", type_="check")
    op.create_check_constraint(
        "ck_workflow_runs_status",
        "workflow_runs",
        "status IN ('awaiting_approval', 'awaiting_identity_review', 'queued', "
        "'running', 'cancel_requested', 'succeeded', 'failed', 'cancelled')",
    )


def downgrade() -> None:
    op.execute(
        "UPDATE workflow_runs SET status = 'failed', "
        "error_message = COALESCE(error_message, 'Identity review was pending during downgrade.') "
        "WHERE status = 'awaiting_identity_review'"
    )
    op.drop_constraint("ck_workflow_runs_status", "workflow_runs", type_="check")
    op.create_check_constraint(
        "ck_workflow_runs_status",
        "workflow_runs",
        "status IN ('awaiting_approval', 'queued', 'running', "
        "'cancel_requested', 'succeeded', 'failed', 'cancelled')",
    )
