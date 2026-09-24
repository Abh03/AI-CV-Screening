"""Persist queued inputs and worker leases.

Revision ID: a814e0c9b257
Revises: f72c1a4e9b03
"""
from alembic import op
import sqlalchemy as sa

revision = "a814e0c9b257"
down_revision = "f72c1a4e9b03"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("screening_runs") as batch:
        batch.add_column(sa.Column("request_snapshot", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("lease_owner", sa.String(64), nullable=True))
        batch.add_column(sa.Column("failure_code", sa.String(64), nullable=True))


def downgrade():
    with op.batch_alter_table("screening_runs") as batch:
        for name in ("failure_code", "lease_owner", "lease_until", "attempt_count", "request_snapshot"):
            batch.drop_column(name)
