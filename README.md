# Automated CV Screening Engine

PDF-focused FastAPI screening pipeline with versioned scoring, evidence verification,
PostgreSQL retrieval, and PDF ingestion.

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
Set `STAGE2_BACKEND=postgres` for persistent retrieval. Production requires this
setting. `STAGE2_BACKEND=memory` retains the reference backend for local tests.
Docker Compose runs Alembic before starting the web service. The standalone
`sql/*.sql` files are historical notes and do not provision the database.

Stage 2 stores redacted CV chunks as searchable text and 384-dimensional vectors.
Treat the PostgreSQL database as sensitive candidate data: restrict access and
apply the same retention controls as other screening records. Raw PDF bytes and
unredacted extracted text are not written to retrieval tables. Document identity
includes candidate ID, redacted content and source locations, redaction version,
and chunking version. Chunk embeddings are reused for the same model version;
JD embeddings are reused per job, category, query text, and model version.
Changing model or processing versions creates new cache entries. Search remains
scoped to one candidate, one document version, and one category. Only Skills and
Projects fall back to Experience when their own category is absent; Experience
and Education have no fallback. Both SQL branches feed the existing RRF and
CrossEncoder path. The category metadata table records this policy.

Candidate/category filters are selective, so vector search uses exact ordering
over that scope. The B-tree scope index and GIN FTS index are checked in the
PostgreSQL integration test. CrossEncoder score averaging and zero clipping are
retained from the reference path; cutoff calibration at volume remains necessary
before using Stage 2 scores as a decision rule.

## Verification

- `python -m pytest tests --collect-only -q`: collect all tests without service connections.
- `python -m pytest tests -q`: ordinary tests use Mock; live provider and infrastructure tests are skipped.
- `python -m pytest tests/test_phase1.py --run-infrastructure -q`: explicitly test running PostgreSQL/pgvector and Redis.
- `python -m pytest tests/test_postgres_retrieval.py --run-infrastructure -q`: create a fresh PostgreSQL database, run all migrations, and exercise scoped retrieval and embedding caches.
- `python -m pytest tests/test_live_stage3_evaluation.py --run-live-llm -q`: explicitly allow configured live LLM calls and associated costs.
- `python -m alembic upgrade head --sql`: inspect migration SQL without connecting.

Use `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` to prevent model downloads when
cached models are available. API persistence tests use isolated SQLite databases;
use the PostgreSQL integration test to verify retrieval readiness.

## Stage 3 scoring and outcomes

`app/stage3_evaluation/scoring.py` owns policy `stage3-v1.1.0`:

| Internal category | Meaning | Weight |
| --- | --- | --- |
| skills | Core Skills | 40% |
| experience | Experience Relevance | 30% |
| projects | Project Complexity | 20% |
| education | Education/Certifications | 10% |

Scores must be finite and within 0-100. Compute the weighted total with decimal
arithmetic, round to two decimals using ROUND_HALF_UP, then apply thresholds:
Tier 1 >= 75; Tier 2 >= 55 and < 75; Tier 3 < 55. There is no independent minimum
skills gate. Stage 2 retrieval weights are unchanged and are not suitability scores.

- SUCCESS: validated assessment, composite score, and final tier.
- REVIEW_REQUIRED: a completed assessment with a provisional composite, review
  reasons, and no final tier. Any MISSING_INFORMATION flag requires review without
  an additional score penalty or critical-flag designation. HIGH/CRITICAL flags of
  other types also require review rather than automatic rejection. LOW/MEDIUM
  flags of other types do not alter the score or tier in this policy.
- EVALUATION_FAILED: provider/response failure with an error code and safe message;
  score, tier, and validated LLM output are null, category scores are empty, and
  there are no candidate flags. A valid zero-score assessment still receives Tier 3.

`has_critical_flags` records a HIGH/CRITICAL non-missing-information flag only
when all of that flag's references pass verification. It is not proof of the
semantic truth of the claim. Such flags still require human review.

Missing evidence, missing citations, invalid references, and injection signals
require REVIEW_REQUIRED with no final tier. Scores remain explicitly provisional;
unsupported evidence cannot trigger an automatic favorable or adverse decision.

Batch order is SUCCESS, REVIEW_REQUIRED, EVALUATION_FAILED, then descending score,
then case-sensitive candidate ID ascending. The API leaderboard contains only
SUCCESS results (including valid Tier 3 decisions). `review_candidates` contains
Stage 1 and Stage 3 reviews, identified by `stage`; `failed_candidates` contains
Stage 3 errors. Stage 1 reviews stop before retrieval and are not rejections.

`stage3_evaluated` counts attempted evaluations and equals `stage3_succeeded` +
`stage3_review_required` + `stage3_failed`. Stage 1 processed candidates split into
passed, rejected, and `stage1_review_required`. Cutoff exclusions and durable
Stage 1 review records remain part of the later run-audit phase.

### Database upgrade

Run `python -m alembic upgrade head` against your configured database before using
this API version. Migration `b82f1e6a0902` permits null scores/tiers and adds status
and policy-version columns. Existing records retain their original score/tier and
are labeled LEGACY_UNCLASSIFIED / legacy-unversioned, since earlier zero scores
may represent provider failures. Historical decisions are not recomputed.

The endpoint persists all Stage 3 outcomes, including reviews/errors, with the
complete outcome envelope in its existing JSON audit column. The migration
refuses downgrade when null score/tier outcomes exist; it does not turn operational
failures into rejections to satisfy the previous schema. Offline downgrade is
unsupported because that safety check needs database access.

## Evidence and prompt boundaries (Phase 3)

Before a provider call, Python creates an immutable registry from the retrieved
redacted evidence and builds escaped XML from that same snapshot. Each CATEGORY:N
reference maps to the candidate, assessment category, original source category,
chunk identity, document identity when available, source location, and exact text.
Source fields are not inferred from the LLM response. Empty snippets and NONE
placeholders are never citable. IDs embedded inside CV or JD text cannot create
registry entries. Inconsistent ownership or conflicting chunk identities fail
before a provider call.

Category scores must cite their own category. Career-gap flags must cite
EXPERIENCE; other flags may cite any supplied category. Every supplied citation
must pass exact matching. Missing references require review, including flags
without citations. Per-field checks distinguish a tag valid in one assessment
from the same tag misused in another. Results include `evidence_verification`
with the registry snapshot, checks, reasons, and injection signals; the existing
JSON audit column persists it. There is no additional Phase 3 migration.

The memory Stage 2 chunk/document IDs are deterministic content-derived identifiers.
For older text-only payloads, snapshot IDs are derived from candidate, source
category, location, and text. These are not claims of database persistence. The
PostgreSQL backend persists page/block metadata when supplied; missing coordinates
remain null for text-only input. Experience fallback into Skills or Projects
retains its original source category.

Gemini receives a dedicated system_instruction; Groq/OpenRouter receive separate
system and user messages. User data cannot create XML elements or override the
system channel. Injection regexes are review signals, not a complete defense.
Exact references prove provenance, not that a rationale is semantically correct.
The model still interprets evidence; Python verifies references and controls tiers.

Mock results have `is_mock=true` and synthetic wording. Mock is allowed only in
ENVIRONMENT=development, test, or testing, including explicitly injected Mock
providers. Other environments return an operational error rather than synthetic
candidate decisions. Live providers require their configured credentials.

Policy version advanced to stage3-v1.1.0 because evidence gating changed outcome
behavior; 40/30/20/10 weights and 75/55 thresholds remain unchanged. Existing
historical evaluation records are not relabeled or recomputed.

## Stage 1 hard filters (Phase 4)

Policy `stage1-v1.0.0` uses validated recruiter input for experience; this release
does not infer years from employment dates. API and direct pipeline calls share
`app/stage1_rules/contracts.py`. Invalid rules/attributes are rejected before
masking or retrieval. API validation errors return 422 with field locations and
reasons, without echoing input values (including NaN/infinity or CV content).

The supported hard-filter keys are `min_years_experience` (finite, nonnegative
number; default 0), `degree_requirement` (validated degree level, aliases and
fields), and `require_work_authorization` (boolean; default true). Unknown keys,
unsupported degree levels, blank terms, mismatched level aliases, negative or
nonfinite years, and numeric strings/booleans are invalid. NONE cannot accompany
field requirements. Rule conflicts between nested/flattened/explicit inputs are
errors, not silent overrides. Multi-JD matching remains optional and unchanged.

Authorization is `eligible`, `ineligible`, or `unknown`; omitted/null values mean
unknown, not eligible. Actual legacy JSON booleans are normalized, but strings
such as "false" are rejected. `authorization_source` and
`parsed_attributes.experience_source` use `recruiter_verified`, `cv_extracted`,
or `unknown` (default). Missing experience is distinct from zero.

For an enabled filter, missing or unverified experience/authorization requires
REVIEW regardless of whether the claim appears favorable. Verified insufficient
experience or ineligible authorization yields FAIL. A zero minimum experience or
`require_work_authorization=false` disables that particular filter explicitly.
Any definite FAIL takes precedence over REVIEW; all individual checks are retained.

Example candidate input:

```json
{
  "candidate_id": "candidate-001",
  "raw_cv_text": "EDUCATION\nBachelor of Computer Science",
  "work_authorized": "eligible",
  "authorization_source": "recruiter_verified",
  "parsed_attributes": {
    "experience_years": 5,
    "experience_source": "recruiter_verified"
  }
}
```

For an explicit correction to extracted claims, use
`recruiter_overrides: {"experience_years": 3, "work_authorized": "eligible"}`.
Only supplied non-null override fields replace reported values, with effective
source recruiter_verified. Reported values and overrides are recorded separately.
Nested legacy authorization attributes remain supported when the top-level
field is omitted. An explicitly unknown top-level value stays unknown; conflicting
known top-level/nested values require an override.

These provenance labels and override fields are assertions from the trusted
intake caller, not authentication. Restrict this API to trusted intake services;
recruiter authorization/tenant enforcement remains the planned deployment phase.

Education matching retains the deterministic parser with bounded section scope,
B.S./M.S./Ph.D./+2 recognition and adjacent-line field/status context. Missing or
ambiguous entries, unspecified fields, and in-progress/incomplete qualifications
require REVIEW. Clear degree-level or documented-field mismatches fail. Multiple
degree levels on one line are treated as ambiguous instead of combining the
higher level with a different degree's field. The legacy parsed `degree` value is
informational only and cannot bypass CV evidence checks.

All results contain rule checks with stable codes, messages, policy version, and
attribute sources. Stage 1 reviews stop before Stage 2 and remain separate from
rejections. Stage 3 outcomes also carry `stage1_filter_details`, persisted in the
existing outcome JSON. Run audit records now persist Stage 1 reviews and
rejections alongside every later candidate outcome.
# PDF ingestion and privacy policy

Production screening accepts PDFs through `POST /api/v1/screening/run-pdf`
or `POST /api/v1/screening/submit-pdf`. Both return HTTP 202 with a `run_id`
and `result_url` after reserving the run and sending it to Redis/Celery.
Send JSON with `job_profile`, `candidate_id`, and `pdf_base64`; candidate rule
facts (`work_authorized`, `authorization_source`, `parsed_attributes`, and
`recruiter_overrides`) are optional. Poll `GET /api/v1/screening/runs/{run_id}`
for `status`, `failure_code`, and the completed `result`. The existing `/run` raw-text route is for
internal development and tests and returns 404 in production.
`POST /api/v1/screening/ingest-pdf` also accepts a raw `application/pdf` body
and returns only redacted page/block text with source locations.

Each `/run` response includes `run_id` and `idempotency_key`. Supply an
`idempotency_key` in the request to name a logical submission. Reusing it with
the same request returns the stored response; reusing it with changed inputs
returns HTTP 409. Without a supplied key, a hash of the request and policy is
used, so identical submissions replay. The `screening_runs` row stores the
job/rule and policy snapshots. `candidate_outcomes` records every input from
reservation through its final result, including reviews, rejections, cutoff
exclusions, extraction errors, and evaluation errors. A failed PDF ingestion
also returns a `run_id` and stores a Stage 0 outcome. The audit stores redacted
CV text or a PDF hash, never raw PDF bytes. `recompute_stored_decision` in
`app/run_audit.py` checks a stored Stage 3 score and tier without a provider call.

Raw PDF bytes are encrypted before reservation and discarded from API memory.
The worker decrypts them for ingestion; the encrypted run request snapshot is
cleared on completion. Only redacted blocks enter retrieval and LLM evaluation. OCR uses
English (`eng`) and runs only on pages that fail the text integrity check,
with page count, image pixel, and per-page time limits. With
`STAGE2_BACKEND=postgres`, redacted text is stored in source chunks and indexed
for PostgreSQL full-text search.

Set `ENCRYPTION_SECRET_KEY` to a valid Fernet key before starting in
production. Production startup rejects a missing or invalid key. For example,
generate a key with `cryptography.fernet.Fernet.generate_key()` and supply it
through the deployment secret manager.

For development text batches, `POST /api/v1/screening/submit` provides the
same asynchronous contract. Set `ENCRYPTION_SECRET_KEY` for this route too;
queued text candidate inputs are encrypted in the run snapshot. The synchronous `/run` route remains available in
development. The compose stack starts separate `worker` and `beat` services;
beat revisits queued runs and expired worker leases every minute. Tasks carry
only run IDs, acknowledge after completion, and use late acknowledgment and
worker-loss redelivery. The default worker concurrency is one process to keep
the embedding and reranker models in one memory footprint per worker. Provider
requests are bounded within each run by `LLM_CONCURRENCY_LIMIT`; OCR, embedding,
and reranking have separate local limits. A failed run reports a stable
`failure_code` and can be resubmitted with the same idempotency key while its
attempt budget remains. Apply Alembic migrations before starting workers.

## Phase 9 deployment and operations

This release supports one organization. Each production API call needs a bearer
token. `API_TOKENS_JSON` is a JSON list of `{id,role,token}` with roles `admin`
or `recruiter`; tokens must contain at least 32 characters. Recruiters can read
their own runs and update only their own jobs. Admins can access all jobs and
runs, including records created before ownership was introduced. The system has
no tenant isolation, so do not share an installation between organizations.

Supply the variables listed in `.env.example` from a deployment secret manager:
a Fernet key, PostgreSQL password and URL, API credentials, and a live LLM
provider key. URL encode special characters in the database password. No local
secret file is needed. Run from the repository root:

```sh
docker compose -f docker/docker-compose.yml build
docker compose -f docker/docker-compose.yml up -d
docker compose -f docker/docker-compose.yml ps
```

When using a private `docker/.env` file instead of exported variables, add
`--env-file docker/.env` to each Compose command. Check values without printing
secrets with `python -m scripts.check_deployment_env docker/.env`. After startup,
run `python -m scripts.production_smoke` for a synthetic authenticated request
through the queue and worker.

The image installs OCR dependencies and downloads fixed revisions of the
embedding and reranker models during build. Runtime model loading is offline.
`/health` reports process liveness. `/ready` returns 503 until PostgreSQL is
reachable at the current Alembic revision, Redis responds, and both model
directories exist in production. Migration runs before web and worker startup.
Production startup rejects mock LLMs, missing provider credentials, an invalid
Fernet key, missing API credentials, and non-PostgreSQL retrieval.

For a manual migration or rollback exercise, stop web and workers, back up the
database, then run `docker compose -f docker/docker-compose.yml
run --rm migrate`. Back up PostgreSQL with
`pg_dump -Fc -h HOST -U postgres cv_engine > backup.dump` and restore to a new
empty PostgreSQL 16/pgvector database with
`pg_restore --clean --if-exists -d cv_engine_restore backup.dump`. Run
`alembic current` against the restored URL, start a disposable web/worker pair,
and check `/ready` and a test run before swapping traffic. Back up encryption
keys separately; losing the key makes pending encrypted input unreadable. Redis
contains the queue and transient task state; keep its append-only volume, but
the database is the source of truth for reserved runs. The beat process scans
expired leases every minute and requeues eligible runs. After a worker crash,
check `failure_code`, `attempt_count`, and beat logs before manual retry.

Logs are JSON events with route templates, correlation IDs, latency, stage
counts, and stable error codes. Send `X-Correlation-ID` to trace an API call.
Logs omit CV text, PDF bytes, provider output, and credentials. Protect the
database, backups, and logs with restricted access. Input PDFs are encrypted
while queued and cleared from successful runs. The proposed retention targets
are 30 days for pending encrypted input and 90 days for structured outcomes;
automatic deletion is not enabled because historical outcomes may need manual
retention. Decide and implement the final erasure policy before claiming a
retention service-level commitment.

Acceptance assumptions are 50 CVs per batch, five concurrent API users,
submission p95 under 2 seconds, and a rough 120 second processing target on
4 CPU cores and 8 GB RAM. The production PDF API currently creates one run per
CV, so a 50 CV workload means 50 submissions. Run a representative trial with
`python scripts/load_test.py --pdf sample.pdf --job job.json --count 50
--concurrency 5` and `API_TOKEN` in the environment. The script reports p95
submission time, total elapsed time, unfinished runs, and request errors; it
does not store PDF contents in its report. Run the labeled quality report with
`python scripts/acceptance_benchmark.py labels.json --cutoff 30`. The JSON
input contains query `relevant_ids` and `ranked_ids`, plus candidate `label`
and `decision`; see the script header. It reports recall@k, shortlist precision
and recall, review rate, and error rate. No quality gate is set until labeled
data and thresholds are agreed. CI separates unit, PostgreSQL/Redis and worker,
image/model, and manually dispatched live-provider checks.
## Campaign intake (Phase 3)

Create a campaign with one or more structured JDs, then upload a ZIP of PDFs. Stage 0 and per-JD Stage 1/2 screening run asynchronously. The Stage 3 evaluation and final ranking APIs are added in Phase 5.

```bash
curl -H "Authorization: Bearer $API_TOKEN" -H "Content-Type: application/json" \
  -d '{"idempotency_key":"autumn-2026","job_profiles":[{"job_id":"ops","title":"Operations Manager","jd_category_queries":{"EXPERIENCE":"operations management"}}]}' \
  http://localhost:8000/api/v1/campaigns
curl -H "Authorization: Bearer $API_TOKEN" -H "Content-Type: application/zip" \
  --data-binary @cvs.zip http://localhost:8000/api/v1/campaigns/CAMPAIGN_ID/archive
curl -H "Authorization: Bearer $API_TOKEN" \
  http://localhost:8000/api/v1/campaigns/CAMPAIGN_ID
```

The upload response reports accepted candidates and rejected ZIP members in archive order. Repeating the same upload returns the stored report. PDF bytes are encrypted while queued and removed after Stage 0. The default limits are 256 MiB compressed, 1 GiB uncompressed, 2,000 members, 100 JDs, and the existing 10 MiB per PDF; configure `CAMPAIGN_*` and `PDF_MAX_BYTES` to change them. The ZIP upload uses a temporary spool that is deleted when the request ends.

For files already on the server, put the structured JD array in `jobs.json` and run `python scripts/import_campaign_folder.py /path/to/pdfs jobs.json --owner OWNER_ID --idempotency-key campaign-key` from the project root. The owner must match an API credential ID for subsequent owner-scoped reads.

Compose runs legacy screening on `screening`, PDF extraction on `ocr`, retrieval on `retrieval`, and coordination/recovery on `control`, each with a single worker process. Status reads query PostgreSQL directly. Keep the control worker and beat service running to recover interrupted Stage 0 and pair tasks. Pair dispatch is bounded by `CAMPAIGN_RETRIEVAL_INFLIGHT` per campaign and `CAMPAIGN_RETRIEVAL_GLOBAL_INFLIGHT` overall. A JD becomes `SHORTLISTED` only when all its accepted CVs have a terminal extraction/Stage 1/Stage 2 outcome; Stage 2 scores are ranked globally by descending score and candidate ID, and at most the JD cap (30 by default) is selected. Retrieval failures are retried up to `RUN_MAX_ATTEMPTS`; failed attempts remain visible as `PROCESSING_FAILED`.
