"""JD drafts and immutable approved versions."""
from alembic import op
import sqlalchemy as sa

revision = "d91a2b3c4e50"
down_revision = "c3e9a72d1860"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("jd_drafts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("pdf_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("pages", sa.JSON(), nullable=False),
        sa.Column("profile", sa.JSON()), sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(64)), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_id", "pdf_hash", name="uq_jd_owner_pdf"))
    op.create_table("approved_jds",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("draft_id", sa.String(64), sa.ForeignKey("jd_drafts.id"), nullable=False, unique=True),
        sa.Column("profile", sa.JSON(), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table("approved_jds")
    op.drop_table("jd_drafts")
