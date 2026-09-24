"""Campaign, JD, CV and authoritative candidate-JD outcomes.

Revision ID: e52b7c9d0143
Revises: 92a1c4d06e9f
"""
from alembic import op
import sqlalchemy as sa

revision = "e52b7c9d0143"
down_revision = "92a1c4d06e9f"
branch_labels = None
depends_on = None


def timestamps():
    return [sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)]


def upgrade():
    op.create_table(
        "campaigns",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("idempotency_key", sa.String(128)),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        *timestamps(),
        sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_campaign_owner_key"),
        sa.CheckConstraint("status IN ('INTAKE','RUNNING','COMPLETED','FAILED')", name="ck_campaign_status"),
    )
    op.create_index("ix_campaigns_owner_created", "campaigns", ["owner_id", "created_at"])
    op.create_table(
        "campaign_jds",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("campaign_id", sa.String(64), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("jd_key", sa.String(64), nullable=False),
        sa.Column("job_snapshot", sa.JSON(), nullable=False),
        sa.Column("policy_snapshot", sa.JSON(), nullable=False),
        sa.Column("stage3_cap", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("campaign_id", "id", name="uq_campaign_jd_scope"),
        sa.UniqueConstraint("campaign_id", "jd_key", name="uq_campaign_jd_key"),
        sa.CheckConstraint("stage3_cap > 0", name="ck_campaign_jd_cap"),
        sa.CheckConstraint("status IN ('PENDING','PROCESSING','SHORTLISTED','COMPLETED','FAILED')", name="ck_campaign_jd_status"),
    )
    op.create_index("ix_campaign_jds_campaign_status", "campaign_jds", ["campaign_id", "status"])
    op.create_table(
        "campaign_cvs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("campaign_id", sa.String(64), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("candidate_id", sa.String(64), nullable=False),
        sa.Column("document_version", sa.Integer(), nullable=False),
        sa.Column("source_filename", sa.String(255), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("stage0_status", sa.String(24), nullable=False),
        sa.Column("redacted_text", sa.Text()),
        sa.Column("source_locations", sa.JSON()),
        sa.Column("extraction_error_code", sa.String(64)),
        sa.Column("encrypted_pdf", sa.LargeBinary()),
        *timestamps(),
        sa.UniqueConstraint("campaign_id", "id", name="uq_campaign_cv_scope"),
        sa.UniqueConstraint("campaign_id", "candidate_id", name="uq_campaign_candidate"),
        sa.CheckConstraint("document_version > 0", name="ck_campaign_cv_version"),
        sa.CheckConstraint("stage0_status IN ('PENDING','RUNNING','SUCCEEDED','FAILED')", name="ck_campaign_cv_stage0"),
        sa.CheckConstraint("stage0_status NOT IN ('SUCCEEDED','FAILED') OR encrypted_pdf IS NULL", name="ck_campaign_cv_purge_raw"),
    )
    op.create_index("ix_campaign_cvs_campaign_stage0", "campaign_cvs", ["campaign_id", "stage0_status"])
    op.create_table(
        "campaign_pairs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("campaign_id", sa.String(64), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("jd_id", sa.String(64), nullable=False),
        sa.Column("cv_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("stage1_decision", sa.String(16)),
        sa.Column("stage1_details", sa.JSON()),
        sa.Column("stage2_score", sa.Float()),
        sa.Column("stage2_rank", sa.Integer()),
        sa.Column("stage3_status", sa.String(32)),
        sa.Column("composite_score", sa.Float()),
        sa.Column("tier", sa.String(16)),
        sa.Column("verification_required", sa.Boolean(), nullable=False),
        sa.Column("verification_reasons", sa.JSON(), nullable=False),
        sa.Column("result_snapshot", sa.JSON()),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("failure_code", sa.String(64)),
        *timestamps(),
        sa.ForeignKeyConstraint(["campaign_id", "jd_id"], ["campaign_jds.campaign_id", "campaign_jds.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["campaign_id", "cv_id"], ["campaign_cvs.campaign_id", "campaign_cvs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("jd_id", "cv_id", name="uq_campaign_pair_jd_cv"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_campaign_pair_attempts"),
        sa.CheckConstraint("stage2_rank IS NULL OR stage2_rank > 0", name="ck_campaign_pair_stage2_rank"),
        sa.CheckConstraint("status IN ('PENDING','RUNNING','EXTRACTION_FAILED','FILTER_REJECTED','PROCESSING_FAILED','CUTOFF_EXCLUDED','SHORTLISTED','SUCCESS','REVIEW_REQUIRED','EVALUATION_FAILED')", name="ck_campaign_pair_status"),
    )
    op.create_index("ix_campaign_pairs_jd_status", "campaign_pairs", ["jd_id", "status"])
    op.create_index("ix_campaign_pairs_jd_rank", "campaign_pairs", ["jd_id", "status", "composite_score", "cv_id"])
    op.create_index("ix_campaign_pairs_campaign_status", "campaign_pairs", ["campaign_id", "status"])


def downgrade():
    op.drop_table("campaign_pairs")
    op.drop_table("campaign_cvs")
    op.drop_table("campaign_jds")
    op.drop_table("campaigns")
