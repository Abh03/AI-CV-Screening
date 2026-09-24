"""Versioned redacted documents and PostgreSQL retrieval.

Revision ID: c4b8e6a2f901
Revises: b82f1e6a0902
"""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import TSVECTOR

revision = "c4b8e6a2f901"
down_revision = "b82f1e6a0902"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("Stage 2 retrieval requires PostgreSQL 16 with pgvector")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table("candidates",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("document_versions",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("candidate_id", sa.String(64), sa.ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("redaction_version", sa.String(32), nullable=False),
        sa.Column("chunking_version", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_document_versions_candidate", "document_versions", ["candidate_id", "created_at"])
    op.create_table("source_chunks",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("document_id", sa.String(80), sa.ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("candidate_id", sa.String(64), sa.ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("section", sa.String(32), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("source_location", sa.JSON(), nullable=False),
        sa.Column("tsv_content", TSVECTOR(),
                  sa.Computed("to_tsvector('english'::regconfig, text)", persisted=True)))
    op.create_index("ix_source_chunks_scope", "source_chunks", ["candidate_id", "document_id", "category"])
    op.create_index("ix_source_chunks_fts", "source_chunks", ["tsv_content"], postgresql_using="gin")
    op.create_table("chunk_embeddings",
        sa.Column("chunk_id", sa.String(80), sa.ForeignKey("source_chunks.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("model_name", sa.String(128), primary_key=True),
        sa.Column("model_version", sa.String(64), primary_key=True),
        sa.Column("vector", Vector(384), nullable=False))
    # Candidate/category filters are very selective. Exact vector ordering avoids
    # an unscoped ANN scan and guarantees stable results for small CV documents.
    op.create_table("job_query_embeddings",
        sa.Column("job_id", sa.String(64), primary_key=True),
        sa.Column("category", sa.String(16), primary_key=True),
        sa.Column("query_hash", sa.String(64), primary_key=True),
        sa.Column("model_name", sa.String(128), primary_key=True),
        sa.Column("model_version", sa.String(64), primary_key=True),
        sa.Column("vector", Vector(384), nullable=False))
    op.create_table("screening_runs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("job_id", sa.String(64), sa.ForeignKey("job_profiles.id"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table("screening_runs")
    op.drop_table("job_query_embeddings")
    op.drop_table("chunk_embeddings")
    op.drop_index("ix_source_chunks_fts", table_name="source_chunks")
    op.drop_index("ix_source_chunks_scope", table_name="source_chunks")
    op.drop_table("source_chunks")
    op.drop_index("ix_document_versions_candidate", table_name="document_versions")
    op.drop_table("document_versions")
    op.drop_table("candidates")
