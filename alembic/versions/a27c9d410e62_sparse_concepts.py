"""Backfill versioned technical lexical text without changing source evidence."""
from alembic import op, context
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TSVECTOR
from app.stage2_retrieval.sparse import normalize_lexical_v1

revision = "a27c9d410e62"
down_revision = "d91a2b3c4e50"
branch_labels = None
depends_on = None


def upgrade():
    if context.is_offline_mode():
        raise RuntimeError("Sparse lexical backfill requires an online Alembic upgrade against PostgreSQL")
    op.add_column("source_chunks", sa.Column("lexical_text", sa.Text(), nullable=False, server_default=""))
    op.add_column("source_chunks", sa.Column("lexical_version", sa.String(32), nullable=False, server_default="concept-v1"))
    connection = op.get_bind()
    # Keyset batches avoid loading all historical CV chunks into memory.
    last_id = ""
    while True:
        rows = connection.execute(sa.text(
            "SELECT id, text FROM source_chunks WHERE id > :last ORDER BY id LIMIT 1000"),
            {"last": last_id}).all()
        if not rows:
            break
        connection.execute(sa.text("UPDATE source_chunks SET lexical_text=:lexical WHERE id=:id"),
            [{"id": row.id, "lexical": normalize_lexical_v1(row.text)} for row in rows])
        last_id = rows[-1].id
    op.add_column("source_chunks", sa.Column("tsv_lexical", TSVECTOR(),
        sa.Computed("to_tsvector('simple'::regconfig, lexical_text)", persisted=True)))
    op.create_index("ix_source_chunks_lexical", "source_chunks", ["tsv_lexical"], postgresql_using="gin")


def downgrade():
    op.drop_index("ix_source_chunks_lexical", table_name="source_chunks")
    op.drop_column("source_chunks", "tsv_lexical")
    op.drop_column("source_chunks", "lexical_version")
    op.drop_column("source_chunks", "lexical_text")
