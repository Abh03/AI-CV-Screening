"""Persist ZIP intake idempotency and rejection report.

Revision ID: fe903a71bd42
Revises: e52b7c9d0143
"""
from alembic import op
import sqlalchemy as sa

revision = "fe903a71bd42"
down_revision = "e52b7c9d0143"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("campaigns", sa.Column("archive_hash", sa.String(64), nullable=True))
    op.add_column("campaigns", sa.Column("intake_report", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("campaigns", "intake_report")
    op.drop_column("campaigns", "archive_hash")
