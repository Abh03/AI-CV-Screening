# Automated CV Screening Engine

PDF-focused FastAPI screening pipeline. Phase 1 repairs restore the runnable
baseline; production retrieval, ingestion, decision hardening, and workers
remain subsequent phases.

## Local configuration

Use Python 3.11 and install `requirements.txt` in a virtual environment. The
embedding and reranking tests require the configured SentenceTransformer models
(downloaded on first use, or already cached for offline runs).

Application ORM and Alembic both read `app.config.settings`, including `.env`.
Set `DATABASE_URL` to a PostgreSQL async URL such as
`postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/cv_engine`.
Set `LLM_PROVIDER=mock` for local development. Importing the application does not
connect to PostgreSQL or initialize provider credentials. Real providers are
validated on first evaluation.

With PostgreSQL available, run `python -m alembic upgrade head`, then
`python -m uvicorn app.main:app`. The health route is `/health`.
This baseline exposes text-input screening; secure PDF ingestion is planned.

## Verification

- `python -m pytest tests --collect-only -q`: collect all tests without service connections.
- `python -m pytest tests -q`: ordinary tests use Mock; live provider and infrastructure tests are skipped.
- `python -m pytest tests/test_phase1.py --run-infrastructure -q`: explicitly test running PostgreSQL/pgvector and Redis.
- `python -m pytest tests/test_live_stage3_evaluation.py --run-live-llm -q`: explicitly allow configured live LLM calls and associated costs.
- `python -m alembic upgrade head --sql`: inspect migration SQL without connecting.

Use `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` to prevent model downloads when
cached models are available. API persistence tests use isolated SQLite databases;
they do not establish PostgreSQL retrieval readiness.

Phase 1 deliberately preserves the existing scoring/failure behavior. Phase 2
will adopt Skills/Experience/Projects/Education weights of 40/30/20/10 and separate
operational failures from candidate decisions. Citation hardening follows in Phase 3.
