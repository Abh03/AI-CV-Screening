"""Recruiter identities for password login and later OIDC mapping.

Revision ID: c3e9a72d1860
Revises: ab925e1c3d70
"""
from alembic import op
import sqlalchemy as sa

revision = "c3e9a72d1860"
down_revision = "ab925e1c3d70"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("recruiter_users",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("token_version", sa.Integer(), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('admin','recruiter')", name="ck_recruiter_role"))


def downgrade():
    op.drop_table("recruiter_users")
