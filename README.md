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
existing outcome JSON. Durable storage of all Stage 1-only outcomes remains part
of the run-audit phase. No new database migration is required for Phase 4.
# PDF ingestion and privacy policy

Production screening accepts PDFs through `POST /api/v1/screening/run-pdf`.
Send JSON with `job_profile`, `candidate_id`, and `pdf_base64`; candidate rule
facts (`work_authorized`, `authorization_source`, `parsed_attributes`, and
`recruiter_overrides`) are optional. The response includes `status` (`success`,
`review`, or `failure`) and a stable `code`. On success, `screening` contains
the ordinary screening response. The existing `/run` raw-text route is for
internal development and tests and returns 404 in production.
`POST /api/v1/screening/ingest-pdf` also accepts a raw `application/pdf` body
and returns only redacted page/block text with source locations.

Raw PDF bytes and extracted text are processed in memory and discarded after
the request. Only redacted blocks enter retrieval and LLM evaluation. OCR uses
English (`eng`) and runs only on pages that fail the text integrity check,
with page count, image pixel, and per-page time limits. With
`STAGE2_BACKEND=postgres`, redacted text is stored in source chunks and indexed
for PostgreSQL full-text search.

Set `ENCRYPTION_SECRET_KEY` to a valid Fernet key before starting in
production. Production startup rejects a missing or invalid key. For example,
generate a key with `cryptography.fernet.Fernet.generate_key()` and supply it
through the deployment secret manager.
