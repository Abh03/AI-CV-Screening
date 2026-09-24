from pathlib import Path
import runpy

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_run_audit_upgrade_preserves_existing_runs():
    migration = runpy.run_path(str(Path(__file__).resolve().parents[1] / "alembic" /
                                   "versions" / "f72c1a4e9b03_run_audit.py"))
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE job_profiles (id VARCHAR(64) PRIMARY KEY)"))
        conn.execute(sa.text("CREATE TABLE screening_runs (id VARCHAR(64) PRIMARY KEY, job_id VARCHAR(64), "
                             "status VARCHAR(24), metrics JSON, created_at DATETIME)"))
        conn.execute(sa.text("CREATE TABLE evaluation_results (id INTEGER PRIMARY KEY, job_id VARCHAR(64), "
                             "candidate_id VARCHAR(64))"))
        conn.execute(sa.text("INSERT INTO screening_runs (id, job_id, status, metrics, created_at) "
                             "VALUES ('old', 'job', 'COMPLETED', '{}', CURRENT_TIMESTAMP)"))
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        migration["upgrade"]()
        row = conn.execute(sa.text("SELECT request_hash, job_snapshot, policy_snapshot "
                                   "FROM screening_runs WHERE id='old'")).one()
        assert row[0] == "legacy"
        assert row[1] == row[2] == "{}"
        assert "run_id" in {column["name"] for column in sa.inspect(conn).get_columns("evaluation_results")}
        assert "candidate_outcomes" in sa.inspect(conn).get_table_names()
    engine.dispose()
