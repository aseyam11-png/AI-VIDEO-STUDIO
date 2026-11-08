"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from alembic import op
import sqlalchemy as sa
${imports if imports else ''}

revision = ('${up_revision}')
down_revision = ('${down_revision}')
branch_labels = ('${branch_labels}')
depends_on = ('${depends_on}')
def upgrade() -> None:
    ${upgrades if upgrades else 'pass'}


def downgrade() -> None:
    ${downgrades if downgrades else 'pass'}
