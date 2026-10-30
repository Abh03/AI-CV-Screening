# AI CV Screening System

A recruiter workspace for screening PDF CVs against approved job descriptions. It records how each candidate was handled, shows the evidence behind assessments, and leaves uncertain cases for human review. A score is a job-specific aid to review, not a hiring decision.

## How it works

1. **Approve the job.** Extract a draft profile from a JD PDF, then review and approve its requirements. Approved versions are retained for reproducibility.
2. **Read CVs.** Upload a campaign of PDF CVs or evaluate one CV. Text extraction uses OCR when needed and reports unreadable documents separately.
3. **Filter and retrieve.** Explicit eligibility rules run first. Hybrid search combines SentenceTransformer embeddings in PostgreSQL/pgvector with full-text search, reciprocal rank fusion, and CrossEncoder reranking. Evidence coverage selects at most 15 candidates per JD for model assessment.
4. **Assess and review.** An LLM returns structured, cited assessments. Python checks citation quotes and references, computes versioned scores, and routes unsupported or incomplete results to review. Recruiters can inspect the full candidate pool, save decisions, and export results.

FastAPI serves the API; Angular and TypeScript provide the recruiter UI. PostgreSQL stores campaign, document, and audit data. Redis and Celery run and recover long-lived OCR, retrieval, and assessment work. Alembic manages schema changes. Docker Compose provides the production-style service stack.

## Run locally

The backend targets Python 3.11. The frontend requires a Node version listed in [frontend/package.json](frontend/package.json). PostgreSQL with pgvector, Redis, Tesseract, and Poppler are needed for the full pipeline. Model downloads are required on first use unless already cached.

For the full stack, copy [.env.example](.env.example) to `docker/.env`, set the database URL, encryption and JWT secrets, and a supported LLM provider key. Then run:

```sh
docker compose --env-file docker/.env -f docker/docker-compose.yml up --build -d
```

The API listens at `http://127.0.0.1:8000`; `/health` reports liveness and `/ready` checks dependencies. Compose applies Alembic migrations before starting the API and workers. The first image build provisions the embedding and reranking models, so it needs network access and can take time.

Start the UI separately:

```sh
cd frontend
npm ci
npm start
```

The development server proxies `/api` to the backend. For backend-only development, install [requirements.txt](requirements.txt), configure `.env`, run `python -m alembic upgrade head`, then start `python -m uvicorn app.main:app`. Set `LLM_PROVIDER=mock` for local tests that should not call a live provider.

## Checks

```sh
python -m pytest tests -q -m "not infrastructure and not integration and not worker and not model"
python -m pytest tests -q -m model
python -m pytest tests/test_phase1.py tests/test_postgres_retrieval.py -q --run-infrastructure
cd frontend && npm run build && npm run lint && npm test
```

Infrastructure tests require PostgreSQL/pgvector and Redis. Model tests need downloaded or cached models; CI runs them inside the provisioned Docker image. Live-provider tests need external credentials. [GitHub Actions](.github/workflows/ci.yml) also tests database migrations and a queued worker run.

## Notes

- PDF text and extracted evidence contain candidate data. Restrict database and export access and apply a retention policy.
- Citation checks verify that quoted text exists in the supplied CV; they do not prove the CV's claims or replace recruiter judgment.
- The 15-candidate cap is a per-JD limit on model assessments, not a claim about screening accuracy or measured cost savings.

See the [architecture guide](docs/SIMPLIFIED_ARCH_DOC.md) for a walkthrough of the design and the [frontend guide](frontend/README.md) for UI setup.
