"""Recruiter document viewing, decisions, collaboration and saved views."""
from alembic import op
import sqlalchemy as sa

revision = "f04d8c912e63"
down_revision = "e61c2a730b94"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("campaign_cvs", sa.Column("encrypted_original_pdf", sa.LargeBinary(), nullable=True))
    # Preserve documents still waiting for extraction; previously purged PDFs cannot be recovered.
    op.execute("UPDATE campaign_cvs SET encrypted_original_pdf = encrypted_pdf WHERE encrypted_pdf IS NOT NULL")
    op.create_table("candidate_reviews",
        sa.Column("pair_id", sa.String(64), sa.ForeignKey("campaign_pairs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False), sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False), sa.Column("verified_facts", sa.JSON(), nullable=False), sa.Column("assignee_id", sa.String(64)),
        sa.Column("version", sa.Integer(), nullable=False), sa.Column("reviewer_id", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("decision IN ('UNREVIEWED','SHORTLIST','HOLD','NOT_PROCEEDING')", name="ck_candidate_review_decision"))
    op.create_table("candidate_review_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("pair_id", sa.String(64), sa.ForeignKey("campaign_pairs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_id", sa.String(64), nullable=False), sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_candidate_review_events_pair_id", "candidate_review_events", ["pair_id"])
    op.create_table("recruiter_views", sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False), sa.Column("name", sa.String(100), nullable=False),
        sa.Column("filters", sa.JSON(), nullable=False), sa.Column("columns", sa.JSON(), nullable=False))
    op.create_index("ix_recruiter_views_owner_id", "recruiter_views", ["owner_id"])
    op.create_table("campaign_members",
        sa.Column("campaign_id", sa.String(64), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", sa.String(64), primary_key=True), sa.Column("username", sa.String(64), nullable=False))


def downgrade():
    for table in ("campaign_members", "recruiter_views", "candidate_review_events", "candidate_reviews"):
        op.drop_table(table)
    op.drop_column("campaign_cvs", "encrypted_original_pdf")
