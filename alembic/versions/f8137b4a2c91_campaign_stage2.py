"""Durable Stage 2 completion and fenced pair leases.

Revision ID: f8137b4a2c91
Revises: fe903a71bd42
"""
from alembic import op
import sqlalchemy as sa

revision = "f8137b4a2c91"
down_revision = "fe903a71bd42"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("campaign_pairs", sa.Column("lease_owner", sa.String(64)))
    op.drop_constraint("ck_campaign_pair_status", "campaign_pairs", type_="check")
    op.create_check_constraint("ck_campaign_pair_status", "campaign_pairs",
        "status IN ('PENDING','RUNNING','STAGE2_READY','EXTRACTION_FAILED','FILTER_REJECTED','PROCESSING_FAILED','CUTOFF_EXCLUDED','SHORTLISTED','SUCCESS','REVIEW_REQUIRED','EVALUATION_FAILED')")
    op.create_index("ix_campaign_pairs_dispatch", "campaign_pairs", ["campaign_id", "status", "lease_until"])


def downgrade():
    op.drop_index("ix_campaign_pairs_dispatch", table_name="campaign_pairs")
    op.execute("UPDATE campaign_pairs SET status='PENDING', stage2_score=NULL, stage2_rank=NULL WHERE status='STAGE2_READY'")
    op.drop_constraint("ck_campaign_pair_status", "campaign_pairs", type_="check")
    op.create_check_constraint("ck_campaign_pair_status", "campaign_pairs",
        "status IN ('PENDING','RUNNING','EXTRACTION_FAILED','FILTER_REJECTED','PROCESSING_FAILED','CUTOFF_EXCLUDED','SHORTLISTED','SUCCESS','REVIEW_REQUIRED','EVALUATION_FAILED')")
    op.drop_column("campaign_pairs", "lease_owner")
