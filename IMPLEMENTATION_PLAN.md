  ## Phase 1 — Restore imports, configuration, and a runnable baseline

  Goal: Make a clean checkout importable and testable without changing screening policy.

  Files/modules likely to change

  - .gitignore
  - app/models/database.py — existing local file, currently ignored
  - app/config.py
  - app/core/database.py
  - app/stage3_evaluation/llm_client.py
  - app/orchestrator.py
  - alembic/env.py
  - Tests importing removed interfaces

  Concrete changes

  - Narrow the models/ ignore rule so application source is tracked while downloaded model artifacts remain
    excluded.

  - Track the existing async ORM implementation.
  - Restore the three removed LLM interfaces from your supplied implementation; reconcile them with the current
    provider wrapper.

  - Establish settings.DATABASE_URL as the shared configuration source for the application and Alembic.
  - Remove import-time database connections.
  - Inspect remaining consumers of the synchronous database helper. Retire it if unnecessary; otherwise isolate its
    synchronous URL handling. Do not maintain two competing application persistence layers.

  - Make provider initialization explicit/lazy enough that imports and unit tests do not require external
    credentials.

  - Repair stale test signatures. Resolve missing scoring interfaces in Phase 2 rather than adding compatibility
    functions with ambiguous behavior.

  Prerequisite

  - Your previous LLM implementation is required before editing that integration.

  Verification

  - Import application, orchestrator, evaluator, and Alembic environment from a clean checkout.
  - Collect the entire test suite.
  - Run existing independent tests with Mock and offline cached models.
  - Confirm no database connections or provider calls occur merely from importing modules.

  Acceptance criteria

  - Application starts with Mock configured.
  - No missing-symbol or missing-module collection failures.
  - The tracked repository contains every required application module.
  - Application and migrations target the same configured database.

  Decision to settle

  - Whether any synchronous database access still has a justified role. Async SQLAlchemy remains the default.

  ———

  ## Phase 2 — Establish one deterministic scoring and outcome contract

  Goal: Prevent inconsistent policy and separate candidate suitability from system failure.

  Files/modules likely to change

  - app/stage3_evaluation/scoring.py
  - app/stage3_evaluation/schemas.py
  - app/stage3_evaluation/evaluator.py
  - app/stage3_evaluation/prompts.py
  - app/orchestrator.py
  - app/api/schemas.py
  - Stage 3 and orchestrator tests

  Concrete changes

  - Implement the currently empty scoring.py as the single home for weighting and decision rules.
  - Apply the requested initial Stage 3 weights:

   Category                    Weight
  ━━━━━━━━━━━━━━━━━━━━━━━━━━  ━━━━━━━━
   Core Skills                    40%
  ──────────────────────────  ────────
   Experience Relevance           30%
  ──────────────────────────  ────────
   Project Complexity             20%
  ──────────────────────────  ────────
   Education/Certifications       10%

  - Retain existing internal category keys where practical; document their meaning.
  - Require finite category scores within 0–100 and define rounding consistently.
  - Separate execution status from decision tier: successful evaluation, review required, and evaluation failure
    must be distinguishable.

  - Provider/schema failures must not automatically become a zero-score rejection.
  - Preserve Stage 1 REVIEW as a review outcome.
  - Define deterministic final sorting and a stable tie-breaker.
  - Version the scoring policy and include that version in evaluation results.
  - Update conflicting tests to the approved policy.

  Dependency

  - Phase 1 interfaces restored.

  Verification

  - Exact weighted-score calculations and threshold boundaries.
  - Invalid/nonfinite scores.
  - Provider failure versus valid low-scoring candidate.
  - Stable sorting and ties.
  - Review outcomes retained through orchestration.

  Acceptance criteria

  - Every final score and tier comes from one Python policy implementation.
  - No operational failure is represented as a suitability judgment.
  - Tests and runtime agree on weights and tier behavior.

  Decisions to settle

  - Retain current 75/55 tier thresholds initially?
  - Is a minimum Core Skills score required?
  - What consequence should each verified flag type/severity have?
  - Recommended starting point: do not introduce the stale tests’ low-skill gate without explicit agreement; missing
    information should trigger uncertainty/review, not an automatic critical penalty.

  - Stage 2 ranking weights remain separate from Stage 3 weights unless deliberately aligned.

  ———

  ## Phase 3 — Repair evidence verification and prompt boundaries

  Goal: Make evidence references structurally trustworthy and prevent unsupported output from producing an automatic
  decision.

  Files/modules likely to change

  - app/stage3_evaluation/prompts.py
  - app/stage3_evaluation/schemas.py
  - app/stage3_evaluation/evaluator.py
  - app/stage3_evaluation/llm_client.py
  - app/stage2_retrieval/evidence_extractor.py
  - app/stage0_extraction/injection_guard.py
  - Evidence, injection, and Stage 3 tests

  Concrete changes

  - Build a Python citation registry from the exact evidence supplied to the LLM.
  - Map each prompt citation to candidate, category, persistent chunk identity, and source location.
  - Validate citation identifiers by exact lookup; reject empty, malformed, nonexistent, and wrong-category
    references.

  - Escape interpolated prompt content and identifiers.
  - Use provider-supported system instruction channels separately from candidate evidence.
  - Reuse existing injection heuristics as signals; do not treat regex detection as complete protection.
  - Enforce the approved policy for missing or invalid citations before scoring/tiering.
  - Validate flags against permitted evidence references and apply deterministic flag consequences.
  - Make Mock behavior clearly identifiable and prevent accidental production use.

  Dependencies

  - Phase 2 outcome policy.
  - Persistent source identifiers are completed in Phases 5–6; use the same contract from this phase onward.

  Verification

  - Forged tags in CV/JD text, XML delimiter injection, empty citations, and cross-category references.
  - High scores with no evidence or invalid citations.
  - Unsupported HIGH/CRITICAL flags.
  - Equivalent prompt/schema behavior across provider adapters using mocked HTTP responses.

  Acceptance criteria

  - Substring membership is no longer used for citation verification.
  - Unsupported evaluations cannot receive an automatic favorable or adverse decision.
  - Every accepted citation resolves to supplied evidence.

  Important limit

  Exact references establish provenance, not semantic truth. Python can verify source ownership, identifiers, and
  quoted text; it cannot prove that every interpretation is justified. Ambiguous judgments need an explicit review
  policy.

  ———

  ## Phase 4 — Harden Stage 1 inputs and preserve review cases

  Goal: Make hard filters validated, JD-specific, and explicit about uncertainty.

  Files/modules likely to change

  - app/api/schemas.py
  - app/stage1_rules/jd_profiler.py
  - app/stage1_rules/rules_engine.py
  - app/stage1_rules/jd_matcher.py — only where shared contracts require it
  - app/orchestrator.py
  - Rules/API/orchestrator tests

  Concrete changes

  - Replace unrestricted hard-filter dictionaries with validated contracts.
  - Reject unknown rule names, invalid degree levels, and negative/nonfinite experience values.
  - Represent authorization as eligible, ineligible, or unknown rather than defaulting to authorized.
  - Define whether authorization is mandatory for each JD.
  - Preserve PASS/FAIL/REVIEW and structured reasons throughout the pipeline.
  - Identify the source of experience and authorization attributes: trusted recruiter input versus extracted CV
    claims.

  - Fix demonstrated education parsing failures without replacing the existing rules engine.
  - Keep multi-JD matching optional; do not add routing complexity to the existing single-JD screening flow.

  Verification

  - NaN, infinity, malformed values, unknown authorization, invalid requirements.
  - Completed/in-progress degrees and ambiguous education sections.
  - Trusted attribute overrides and review routing.

  Acceptance criteria

  - Invalid inputs cannot silently disable filters.
  - Unknown information is handled according to policy.
  - Review candidates remain distinguishable from rejected candidates.

  Decision to settle

  For the first repaired release, should experience remain a validated trusted input, or must it be derived from CV
  employment intervals? The latter requires date extraction, overlap handling, and confidence reporting; the
  existing SQL interval function alone does not provide that pipeline.

  ———

  ## Phase 5 — Connect the existing PDF extraction and privacy components

  Goal: Turn Stage 0 utilities into a controlled PDF ingestion path.

  Files/modules likely to change

  - app/stage0_extraction/parser.py
  - app/stage0_extraction/integrity.py
  - app/stage0_extraction/pii_masker.py
  - app/stage0_extraction/injection_guard.py
  - Proposed app/stage0_extraction/pipeline.py
  - app/core/security.py
  - app/api/endpoints.py
  - app/api/schemas.py
  - app/orchestrator.py
  - Dockerfile
  - Stage 0/privacy tests

  Concrete changes

  - Add PDF upload ingestion with content validation, size/page limits, bounded processing, and explicit malformed/
    encrypted-document outcomes.

  - Connect extraction → integrity assessment → conditional OCR → reassessment → privacy masking.
  - Retain the current parser and fix reading-order cases demonstrated by representative fixtures.
  - Add OCR only for pages requiring it, with timeouts and clear failure/review outcomes.
  - Preserve page/block provenance through redaction and downstream chunking.
  - Validate encryption configuration; eliminate accidental fallback behavior.
  - Ensure only approved redacted views reach retrieval and LLM calls.
  - Replace duplicated code in test_privacy.py with tests exercising the production masker.
  - Keep a text-input path for internal tests if useful, clearly separated from production PDF ingestion.

  Verification

  - Single/two-column PDFs, intermediate headings, footers, scanned pages, malformed files, oversized input, and OCR
    failure.

  - Redaction coverage and preservation of technical terms/dates needed for screening.
  - Encryption round trips and invalid/missing-key configuration.
  - No raw CV content in ordinary logs.

  Acceptance criteria

  - Every PDF reaches a structured success, review, or failure state.
  - OCR cannot run without bounds.
  - Downstream evidence retains traceable source locations.
  - Production configuration fails clearly when required encryption is unavailable.

  Decisions to settle

  - Raw PDF/text retention and deletion policy.
  - Approved OCR language coverage.
  - Whether redacted text may be persisted for FTS. PostgreSQL FTS requires an approved searchable textual
    representation; runtime-only redaction and persistent FTS need an explicit reconciliation.

  ———

  ## Phase 6 — Unify persistence and implement PostgreSQL retrieval

  Goal: Use PostgreSQL + pgvector + FTS in production while preserving chunking, RRF, reranking, and aggregation.

  Files/modules likely to change

  - app/models/database.py
  - alembic/versions/ — new migrations
  - sql/01_init_extensions.sql
  - sql/02_create_tables.sql
  - sql/03_stored_procedures.sql
  - app/stage2_retrieval/chunker.py
  - app/stage2_retrieval/embeddings.py
  - app/stage2_retrieval/hybrid_search.py
  - app/stage2_retrieval/evidence_extractor.py
  - Proposed retrieval repository module
  - docker/docker-compose.yml
  - PostgreSQL integration tests

  Concrete changes

  - Make Alembic the schema source of truth; reconcile the standalone SQL definitions rather than maintaining
    competing schemas.

  - Model candidates, document versions, source chunks, category metadata, embeddings, jobs, and screening runs.
  - Persist stable chunk IDs, source provenance, redaction version, embedding model/version, and content hashes.
  - Implement scoped FTS and vector retrieval with candidate/category isolation.
  - Feed both ranked result lists into the existing RRF function, then existing CrossEncoder reranking.
  - Generate JD query embeddings once per applicable job/model version.
  - Reuse document embeddings until content or processing versions change.
  - Enforce chunk size bounds without discarding source traceability.
  - Retain the in-memory implementation as a unit-test/reference backend if useful; production selection must be
    explicit.

  - Add indexes appropriate to measured query patterns.

  Dependencies

  - Evidence/provenance and privacy contracts from Phases 3 and 5.

  Verification

  - Actual PostgreSQL 16 + pgvector tests for migrations, FTS, vector dimensions, filtering, and index/query
    behavior.

  - Candidate/category isolation.
  - Cache invalidation after document/model changes.
  - Retrieval-quality fixtures and cutoff stability.
  - Representative query plans.

  Acceptance criteria

  - Production Stage 2 executes both retrieval branches in PostgreSQL.
  - Retrieved evidence remains correctly scoped and traceable.
  - Repeated screening reuses compatible embeddings.
  - A fresh database is fully provisioned through the documented migration path.

  Decisions to settle

  - Preserve per-candidate category retrieval initially; avoid changing it into global candidate retrieval without
    evidence.

  - Explicit policy for Experience fallback into Skills/Projects.
  - Calibration of CrossEncoder aggregation and zero clipping before relying on cutoff quality at volume.

  ———

  ## Phase 7 — Complete orchestration, persistence, and audit records

  Goal: Make each screening run reproducible and account for every candidate.

  Files/modules likely to change

  - app/orchestrator.py
  - app/api/endpoints.py
  - app/api/schemas.py
  - app/models/database.py
  - New Alembic migrations
  - Proposed run/audit service module
  - Orchestrator and persistence tests

  Concrete changes

  - Persist immutable JD/rule/scoring snapshots for each run.
  - Record every stage outcome, including review, extraction failure, filter rejection, cutoff exclusion, and
    evaluation failure.

  - Store the evidence snapshot, citation mapping, provider/model identifiers, prompt version, policy versions, and
    validated output.

  - Add run IDs and idempotency keys.
  - Prevent changed requirements from being associated only with an older stored job profile.
  - Use short transactions around persistence; do not hold transactions across model/API calls.
  - Ensure metrics reconcile with candidate outcomes.
  - Return a deterministically sorted leaderboard separately from review/error lists.

  Verification

  - Duplicate submissions, changed JD versions, partial failures, persistence interruption, and safe replay.
  - Recompute stored weighted scores/tiers without another LLM call.
  - Ensure each input candidate has an outcome.

  Acceptance criteria

  - Every decision can be traced to its inputs, evidence, and policy.
  - Retries do not create duplicate logical results.
  - No candidates disappear between stages.

  ———

  ## Phase 8 — Activate Celery and bounded background execution

  Goal: Support high-volume batches without blocking API request workers.

  Files/modules likely to change

  - app/workers/celery_app.py
  - app/workers/tasks.py
  - app/orchestrator.py
  - app/stage3_evaluation/llm_client.py
  - app/api/endpoints.py
  - app/config.py
  - docker/docker-compose.yml
  - Worker/integration tests

  Concrete changes

  - Configure Redis-backed Celery and worker services.
  - Submit screening runs asynchronously; expose status/results endpoints.
  - Start with coarse task boundaries consistent with current orchestration, splitting further only where retry or
    throughput requirements justify it.

  - Bound OCR, embedding, reranking, and provider concurrency independently.
  - Replace blocking Gemini calls with supported async execution.
  - Reuse provider clients appropriately.
  - Add retryable/nonretryable error classification, timeouts, backoff, and retry-safe persistence.
  - Handle worker interruption and restart using persisted run state.

  Dependencies

  - Phase 7 idempotency and durable state.

  Verification

  - Worker restart, retry/redelivery, rate limits, timeouts, and concurrent batches.
  - Event-loop responsiveness and model memory use.
  - No duplicate finalized results after task redelivery.

  Acceptance criteria

  - Submission requests finish quickly.
  - Expensive work runs in workers.
  - Concurrency and retries remain bounded.
  - Interrupted runs recover or expose an actionable failure state.

  ———

  ## Phase 9 — Deployment, CI, and production acceptance

  Goal: Make deployment reproducible and establish measured readiness.

  Files/modules likely to change

  - .dockerignore
  - Dockerfile
  - docker/docker-compose.yml
  - .github/workflows/ci.yml
  - pytest.ini
  - requirements.txt
  - app/main.py
  - app/core/logging.py
  - Authentication/authorization module
  - README.md
  - Proposed .env.example
  - Integration/load-test modules

  Concrete changes

  - Exclude secrets, virtual environments, local data, and caches from image context.
  - Add required OCR/runtime dependencies and a reproducible model provisioning strategy.
  - Validate dependency installation from a clean environment.
  - Provision PostgreSQL/pgvector and Redis in integration CI.
  - Separate unit, database, worker, model, and opt-in live-provider tests.
  - Add authentication and job/run access controls; introduce tenant scoping before shared multi-tenant deployment.
  - Separate liveness from readiness.
  - Add privacy-safe structured logs, run correlation IDs, stage latency/error metrics, and operational
    documentation.

  - Document migrations, startup, secrets, retention, backup/restore, and worker recovery.

  Verification

  - Clean-checkout build and deployment.
  - Migration execution and restore exercise.
  - Authorization boundaries and image-content checks.
  - Representative batch/load tests and a labeled retrieval/evaluation benchmark.

  Acceptance criteria

  - Deployment requires no untracked local files.
  - CI verifies the intended production database and worker paths.
  - Throughput, latency, retrieval quality, and review/error rates meet agreed targets.
  - Production cannot silently operate with Mock or incomplete critical configuration.

  Decisions to settle

  - Expected batch size, concurrent users, latency target, hardware budget, and retention period.
  - Single-organization deployment versus multi-tenant operation.
  - Quality thresholds for shortlisting and human review.

  ———

  ## Proposed final implementation sequence

  1. Recovery boundary — Phase 1: obtain your removed LLM code, restore interfaces, track the ORM, fix
     configuration, and achieve clean imports/test collection.

  2. Correctness boundary — Phases 2–4: approve scoring policy, enforce evidence verification, distinguish failures/
     reviews, and validate hard filters.

  3. Data boundary — Phase 5: connect bounded PDF ingestion, OCR, privacy, and source provenance.
  4. Retrieval boundary — Phase 6: migrate the unified schema and switch production retrieval to PostgreSQL FTS +
     pgvector.

  5. Audit boundary — Phase 7: complete durable run records, idempotency, candidate accounting, and reproducible
     decisions.

  6. Execution boundary — Phase 8: activate Celery with bounded concurrency and recoverable processing.
  7. Release boundary — Phase 9: complete deployment controls, production integration CI, quality evaluation, and
     load acceptance.

  The Docker secret-exclusion fix should be brought forward before any image build. Each boundary should finish with
  its acceptance checks passing before work proceeds to the next.