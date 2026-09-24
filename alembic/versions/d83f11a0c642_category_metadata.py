"""Record category isolation and fallback policy.

Revision ID: d83f11a0c642
Revises: c4b8e6a2f901
"""
from alembic import op
import sqlalchemy as sa

revision = "d83f11a0c642"
down_revision = "c4b8e6a2f901"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("category_metadata",
        sa.Column("category", sa.String(16), primary_key=True),
        sa.Column("fallback_category", sa.String(16), nullable=True),
        sa.Column("policy_version", sa.String(32), nullable=False))
    op.execute("""INSERT INTO category_metadata (category, fallback_category, policy_version) VALUES
        ('SKILLS', 'EXPERIENCE', 'category-v1'),
        ('EXPERIENCE', NULL, 'category-v1'),
        ('PROJECTS', 'EXPERIENCE', 'category-v1'),
        ('EDUCATION', NULL, 'category-v1')""")


def downgrade():
    op.drop_table("category_metadata")
