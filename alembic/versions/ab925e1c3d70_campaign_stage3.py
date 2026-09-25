"""Stage 3 pair lease and attempt accounting.

Revision ID: ab925e1c3d70
Revises: f8137b4a2c91
"""
from alembic import op
import sqlalchemy as sa

revision = "ab925e1c3d70"
down_revision = "f8137b4a2c91"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("campaign_pairs", sa.Column("stage3_attempt_count", sa.Integer(), nullable=False, server_default="0"))
    op.create_check_constraint("ck_campaign_pair_stage3_attempts", "campaign_pairs", "stage3_attempt_count >= 0")
    op.drop_constraint("ck_campaign_pair_status", "campaign_pairs", type_="check")
    op.create_check_constraint("ck_campaign_pair_status", "campaign_pairs",
        "status IN ('PENDING','RUNNING','STAGE2_READY','EXTRACTION_FAILED','FILTER_REJECTED','PROCESSING_FAILED','CUTOFF_EXCLUDED','SHORTLISTED','STAGE3_RUNNING','SUCCESS','REVIEW_REQUIRED','EVALUATION_FAILED')")


def downgrade():
    op.execute("UPDATE campaign_pairs SET status='SHORTLISTED', lease_owner=NULL, lease_until=NULL WHERE status='STAGE3_RUNNING'")
    op.drop_constraint("ck_campaign_pair_status", "campaign_pairs", type_="check")
    op.create_check_constraint("ck_campaign_pair_status", "campaign_pairs",
        "status IN ('PENDING','RUNNING','STAGE2_READY','EXTRACTION_FAILED','FILTER_REJECTED','PROCESSING_FAILED','CUTOFF_EXCLUDED','SHORTLISTED','SUCCESS','REVIEW_REQUIRED','EVALUATION_FAILED')")
    op.drop_constraint("ck_campaign_pair_stage3_attempts", "campaign_pairs", type_="check")
    op.drop_column("campaign_pairs", "stage3_attempt_count")
