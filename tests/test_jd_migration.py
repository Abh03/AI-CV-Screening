import runpy
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_jd_migration_upgrade_and_downgrade():
    revision = runpy.run_path(str(Path(__file__).resolve().parents[1] /
        "alembic/versions/d91a2b3c4e50_jd_approval.py"))
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            revision["upgrade"]()
        inspector = sa.inspect(conn)
        assert set(inspector.get_table_names()) == {"jd_drafts", "approved_jds"}
        assert inspector.get_foreign_keys("approved_jds")[0]["referred_table"] == "jd_drafts"
        assert inspector.get_unique_constraints("jd_drafts")[0]["column_names"] == ["owner_id", "pdf_hash"]
        with Operations.context(MigrationContext.configure(conn)):
            revision["downgrade"]()
        assert sa.inspect(conn).get_table_names() == []
    engine.dispose()
