from pathlib import Path
import runpy

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_upgrade_preserves_legacy_rows_and_downgrade_refuses_null_outcomes():
    root = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    original = runpy.run_path(str(root / "6c46fc90bc73_initial_schema_for_job_profiles_and_.py"))
    outcome = runpy.run_path(str(root / "b82f1e6a0902_evaluation_outcomes.py"))
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        original["upgrade"]()
        conn.execute(sa.text("""
            INSERT INTO job_profiles (id, title, category_queries, created_at)
            VALUES ('job', 'Engineer', '{}', CURRENT_TIMESTAMP)
        """))
        conn.execute(sa.text("""
            INSERT INTO evaluation_results
            (job_id, candidate_id, composite_score, tier, category_scores,
             verified_citations, invalid_citations, has_critical_flags, llm_raw_output, created_at)
            VALUES ('job', 'legacy', 0, 'TIER_3', '{}', '[]', '[]', 0, '{}', CURRENT_TIMESTAMP)
        """))
        outcome["upgrade"]()
        row = conn.execute(sa.text("SELECT * FROM evaluation_results")).mappings().one()
        assert row["evaluation_status"] == "LEGACY_UNCLASSIFIED"
        assert row["scoring_policy_version"] == "legacy-unversioned"
        assert row["composite_score"] == 0 and row["tier"] == "TIER_3"
        conn.execute(sa.text("UPDATE evaluation_results SET tier = NULL, evaluation_status = 'REVIEW_REQUIRED'"))
        with pytest.raises(RuntimeError, match="Cannot downgrade"):
            outcome["downgrade"]()
    engine.dispose()
