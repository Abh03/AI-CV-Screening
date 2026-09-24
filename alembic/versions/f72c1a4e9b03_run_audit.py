"""Immutable run context and one outcome per input candidate.

Revision ID: f72c1a4e9b03
Revises: d83f11a0c642
"""
from alembic import op
import sqlalchemy as sa

revision = "f72c1a4e9b03"
down_revision = "d83f11a0c642"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("evaluation_results") as batch:
        batch.add_column(sa.Column("run_id", sa.String(64),
                                   sa.ForeignKey("screening_runs.id", name="fk_evaluation_results_run_id"),
                                   nullable=True))
        batch.create_unique_constraint("uq_evaluation_results_run_candidate", ["run_id", "candidate_id"])
    with op.batch_alter_table("screening_runs") as batch:
        batch.add_column(sa.Column("idempotency_key", sa.String(128), nullable=True))
        batch.add_column(sa.Column("request_hash", sa.String(64), nullable=False, server_default="legacy"))
        batch.add_column(sa.Column("job_snapshot", sa.JSON(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("policy_snapshot", sa.JSON(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("response_snapshot", sa.JSON(), nullable=True))
        batch.create_unique_constraint("uq_screening_runs_idempotency_key", ["idempotency_key"])
    op.create_table("candidate_outcomes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(64), sa.ForeignKey("screening_runs.id"), nullable=False),
        sa.Column("candidate_id", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("stage", sa.String(16), nullable=False),
        sa.Column("input_snapshot", sa.JSON(), nullable=False),
        sa.Column("stage_history", sa.JSON(), nullable=False),
        sa.Column("evidence_snapshot", sa.JSON(), nullable=True),
        sa.Column("citation_mapping", sa.JSON(), nullable=True),
        sa.Column("validated_output", sa.JSON(), nullable=True),
        sa.Column("result_snapshot", sa.JSON(), nullable=False),
        sa.Column("provider", sa.String(64), nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("prompt_version", sa.String(64), nullable=True),
        sa.Column("scoring_policy_version", sa.String(64), nullable=True),
        sa.UniqueConstraint("run_id", "candidate_id", name="uq_candidate_outcome_run_candidate"))


def downgrade():
    op.drop_table("candidate_outcomes")
    with op.batch_alter_table("evaluation_results") as batch:
        batch.drop_constraint("uq_evaluation_results_run_candidate", type_="unique")
        batch.drop_column("run_id")
    with op.batch_alter_table("screening_runs") as batch:
        batch.drop_constraint("uq_screening_runs_idempotency_key", type_="unique")
        for name in ("response_snapshot", "policy_snapshot", "job_snapshot", "request_hash", "idempotency_key"):
            batch.drop_column(name)
