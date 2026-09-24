"""Single organization job and run ownership.

Revision ID: 92a1c4d06e9f
Revises: a814e0c9b257
"""
from alembic import op
import sqlalchemy as sa

revision = "92a1c4d06e9f"
down_revision = "a814e0c9b257"
branch_labels = None
depends_on = None


def upgrade():
    # Existing records require administrator access until assigned to a named owner.
    with op.batch_alter_table("job_profiles") as batch:
        batch.add_column(sa.Column("owner_id", sa.String(64), nullable=False, server_default="local"))
    with op.batch_alter_table("screening_runs") as batch:
        batch.add_column(sa.Column("owner_id", sa.String(64), nullable=False, server_default="local"))
        batch.create_index("ix_screening_runs_owner_id", ["owner_id"])


def downgrade():
    with op.batch_alter_table("screening_runs") as batch:
        batch.drop_index("ix_screening_runs_owner_id")
        batch.drop_column("owner_id")
    with op.batch_alter_table("job_profiles") as batch:
        batch.drop_column("owner_id")
