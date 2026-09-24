"""Preserve distinct evaluation outcomes and scoring policy versions.

Revision ID: b82f1e6a0902
Revises: 6c46fc90bc73
"""
from alembic import op
import sqlalchemy as sa

revision = "b82f1e6a0902"
down_revision = "6c46fc90bc73"
branch_labels = None
depends_on = None


def upgrade():
    # Old rows may include disguised provider failures: do not label them SUCCESS.
    with op.batch_alter_table("evaluation_results") as batch:
        batch.add_column(sa.Column("evaluation_status", sa.String(32), nullable=False,
                                   server_default="LEGACY_UNCLASSIFIED"))
        batch.add_column(sa.Column("scoring_policy_version", sa.String(32), nullable=False,
                                   server_default="legacy-unversioned"))
        batch.alter_column("composite_score", existing_type=sa.Float(), nullable=True)
        batch.alter_column("tier", existing_type=sa.String(16), nullable=True)
    with op.batch_alter_table("evaluation_results") as batch:
        batch.alter_column("evaluation_status", existing_type=sa.String(32), server_default=None)
        batch.alter_column("scoring_policy_version", existing_type=sa.String(32), server_default=None)


def downgrade():
    # Never coerce review/error outcomes into adverse candidate decisions.
    if op.get_context().as_sql:
        raise RuntimeError("Outcome downgrade requires an online data safety check")
    count = op.get_bind().execute(sa.text(
        "SELECT COUNT(*) FROM evaluation_results WHERE composite_score IS NULL OR tier IS NULL"
    )).scalar_one()
    if count:
        raise RuntimeError("Cannot downgrade while review/failed outcomes have null scores or tiers")
    with op.batch_alter_table("evaluation_results") as batch:
        batch.alter_column("composite_score", existing_type=sa.Float(), nullable=False)
        batch.alter_column("tier", existing_type=sa.String(16), nullable=False)
        batch.drop_column("evaluation_status")
        batch.drop_column("scoring_policy_version")
