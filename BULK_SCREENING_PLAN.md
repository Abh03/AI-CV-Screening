# Bulk, multi-JD screening plan

## Purpose and agreed behavior

Build a backend workflow for a company that receives a changing number of CVs (roughly 1,000 in a typical campaign) and has a changing number of openings (one or more). A campaign accepts PDF CVs and structured job descriptions (JDs), runs screening for **every candidate against every JD**, and produces one ranked result per JD. The same candidate may rank for several JDs, including all of them. Technical and nontechnical JDs must use the same workflow.

For each candidate, run PDF extraction, integrity checks, and PII redaction (Stage 0) **once per document version**. For each candidate-JD pair, run Stage 1 hard rules and Stage 2 retrieval/ranking. After all Stage 2 work for a JD is accounted for, select **up to 30 candidates for that JD** for Stage 3 LLM evaluation. Thirty is the default and intended campaign cap, not a required minimum: if fewer qualify, evaluate fewer. The cap applies separately to each JD. Thus four JDs can produce at most 120 Stage 3 evaluations, and one candidate can consume a place in several JDs.

Stage 1 `FAIL` means a definite hard-rule failure and stops that candidate-JD pair. Stage 1 `REVIEW` means a fact is missing, ambiguous, or unverified: let the pair continue through Stages 2 and 3, retaining the exact rule checks and reasons. If Stage 3 returns a valid `SUCCESS` score, include the candidate in the normal score-ordered JD ranking with a **provisional / human verification required** flag. A Stage 3 score never verifies work authorization, years of experience, or another Stage 1 fact. Stage 3 `REVIEW_REQUIRED` (for example unsupported evidence) and `EVALUATION_FAILED` remain distinct from a scored success and must not be presented as final ranked decisions. Show them in separate review/failure views with any available provisional score. A shortlist place is a Stage 3 *attempt*, not a guarantee of 30 successful ranked results. Do not silently backfill failed or review outcomes unless a separate, explicit policy is later agreed.

The first implementation is backend and API work. A UI will be built later against these APIs. This plan does not authorize coding by itself; execute a phase only when asked. Each phase below is written to be usable as a standalone task in a new session.

## Current codebase and constraints

- `app/api/endpoints.py` exposes production `POST /api/v1/screening/submit-pdf` and `GET /api/v1/screening/runs/{run_id}`. A production PDF run contains **one CV and one JD**, so it cannot choose the best 30 from a whole pool. Development text `/submit` accepts a candidate list for one JD, and `app/orchestrator.py` already has a run-wide Stage 2 top-N cutoff, but this is not a production bulk-PDF campaign.
- `app/api/schemas.py` accepts one structured `job_profile` with `job_id`, `title`, `jd_category_queries`, and `hard_filter_rules`. It does not accept multiple JDs in a campaign.
- `app/orchestrator.py` currently stops Stage 1 `REVIEW` pairs before Stage 2. `app/stage1_rules/rules_engine.py` defaults to requiring work authorization, and nonzero experience rules require recruiter-verified data; CV-only intake may therefore produce many Stage 1 reviews. Keep the distinction between unknown and definite failure.
- `app/stage3_evaluation/scoring.py` owns evidence-based score and tier policy. Stage 3 `REVIEW_REQUIRED` currently has no final tier. Preserve its evidence safeguards. Keep Stage 1 verification metadata separate from Stage 3 evidence status.
- `app/models/database.py`, `app/run_audit.py`, and Alembic migrations store per-run and per-candidate outcomes. `app/workers/tasks.py` and Celery/Redis run durable asynchronous jobs. Stage 2 production retrieval uses PostgreSQL and caches document/chunk embeddings in `app/stage2_retrieval/repository.py`.
- Production currently accepts PDFs, with `PDF_MAX_BYTES` and OCR/page limits in `app/config.py`. The README's workload assumption is 50 CV submissions, not a demonstrated 1,000-CV campaign. A representative load trial is required before claiming capacity or turnaround time.
- `docker/docker-compose.yml` already runs the FastAPI `web` service separately from a single Celery worker, so heavy Celery work cannot occupy HTTP request workers. The actual queue risk for campaigns is that many OCR/retrieval jobs on one worker queue delay lightweight coordination/recovery and Stage 3 work. `app/stage3_evaluation/llm_client.py` already retries provider 429 errors briefly (three attempts with short waits), but that is not a provider-wide rate limit and an exhausted 429 currently becomes a generic Stage 3 provider failure.
- Keep existing single-PDF and development routes working unless a specific migration plan deliberately replaces them. Apply normal authentication/ownership checks, privacy handling, and idempotency to campaign APIs.

## Phase 1 — Candidate-JD outcome policy

**Goal:** Change the existing pipeline semantics so a Stage 1 review can continue and a successfully scored result carries a separate human-verification flag. This phase establishes the rules the later campaign workflow will reuse.

**Files/modules likely to change:** `app/orchestrator.py`; `app/stage1_rules/contracts.py` and `rules_engine.py` only if the review output needs a more stable machine-readable reason; `app/stage3_evaluation/schemas.py` and `scoring.py` only if a result field is needed; `app/api/schemas.py`; `app/run_audit.py`; `tests/test_end_to_end_orchestrator.py`, `tests/test_rules_engine.py`, `tests/test_scoring_contract.py`, and `tests/test_db_persistence.py`.

**Concrete changes:**

1. Route Stage 1 `PASS` and `REVIEW` pairs into Stage 2; route `FAIL` out. Carry immutable Stage 1 check codes, source/provenance, and review reasons into the Stage 3 payload and stored outcome.
2. Represent `verification_required` and `verification_reasons` separately from Stage 3 `evaluation_status`, score, tier, and evidence review reasons. Do not change the LLM's evidence decision merely because Stage 1 needs verification.
3. A valid Stage 3 `SUCCESS` goes into the normal leaderboard regardless of the Stage 1 review flag. Its rank/score/tier are labeled provisional until human verification. A Stage 3 `REVIEW_REQUIRED` or `EVALUATION_FAILED` remains in its existing separate collection. Keep the current deterministic score/tie ordering.
4. Update metrics and audit accounting to count a Stage 1 review that continued; the old invariant that reviews stop at Stage 1 must be removed without double-counting the pair. Version any changed policy/result contract and keep old stored runs readable.

**Verification:** Focused pipeline tests for Stage 1 pass, review, definite fail, and processing error, each followed by Stage 3 success/review/failure as applicable. Assert Stage 1 reasons survive into the API/database result; assert a Stage 1 review cannot be mistaken for verified eligibility. Run existing audit and scoring tests to catch regressions.

**Acceptance criteria:** A review-only Stage 1 candidate can be shortlisted and ranked after a valid Stage 3 score, with explicit verification reasons. A definite Stage 1 fail never reaches Stage 2. Stage 3 evidence safeguards, separate review/failure outcomes, historical reads, and outcome counts remain correct.

## Phase 2 — Campaign data model and migrations

**Goal:** Store a campaign, its variable JD set, each imported CV/version, and the result of every candidate-JD pair so work is resumable and results are queryable by JD.

**Files/modules likely to change:** `app/models/database.py`; new `alembic/versions/*.py` migration(s); `app/run_audit.py` or a new `app/campaigns/` persistence module; `app/main.py` readiness revision handling; `tests/test_db_persistence.py`, migration tests, and infrastructure tests.

**Concrete changes:**

1. Add campaign and campaign-JD records with owner, status, timestamps, immutable JD/rule/policy snapshots, and the default Stage 3 cap of 30 per JD. Use campaign-local JD IDs or stable references so two JDs cannot overwrite each other.
2. Add campaign CV/document records with stable campaign-local candidate identity, source filename, content hash, Stage 0 status, redacted text/source locations or a protected reference to them, and extraction error information. Do not use a filename alone as identity or assume equal PDF hashes prove two named candidates are the same person.
3. Add a unique campaign-JD-candidate work/result record for Stage 1 decision, Stage 2 score/cutoff rank, Stage 3 status/score/tier, verification reasons, attempt state, and failure code. Tie results to the document version and policy snapshot. Existing per-run audit records can be reused or linked if that is simpler, but there must be one authoritative pair outcome.
4. Add database constraints/indexes for uniqueness, campaign/JD ranking queries, idempotent retries, and ownership. Define terminal states and campaign/JD counts so every accepted CV is accounted for against every JD, including extraction failure.
5. Define a retention boundary: temporary raw PDF bytes are encrypted while pending and deleted after Stage 0; retained redacted text and outcomes remain sensitive and follow the project's access controls. Do not log PDF/CV content.

**Verification:** Upgrade a clean PostgreSQL database and a database with existing screening records; check reads/writes, constraints, owner scoping, retries, and migration head/readiness. Test that two JDs can store different outcomes for one candidate and that a failed Stage 0 CV remains visible in the campaign accounting.

**Acceptance criteria:** A database query can list all JDs and CVs in a campaign, every pair's progress/outcome, and a single JD's ranked successful results. Retries cannot create duplicate pair results; existing runs remain readable.

## Phase 3 — Bulk intake and Stage 0 reuse

**Goal:** Let an operator submit a changing number of PDFs and one or more structured JDs in a manageable way, then extract/redact each accepted PDF only once.

**Files/modules likely to change:** `app/api/endpoints.py` and `schemas.py` or a new campaign router; `app/stage0_extraction/pipeline.py`; `app/config.py`; `app/workers/tasks.py` and `celery_app.py`; `docker/docker-compose.yml`; `app/core/security.py`; new `app/campaigns/intake.py` and optional `scripts/import_campaign_folder.py`; API, privacy, and worker tests; README.

**Concrete changes:**

1. Add a campaign creation/submission API that accepts one or more structured JDs and a bulk CV upload. A ZIP of PDFs is the primary UI-friendly path. Add a small folder-import CLI for files already on the server; it calls the same intake service rather than creating a second screening policy. A browser cannot directly give the server an arbitrary local folder path.
2. Validate that the JD list is nonempty and IDs are distinct; validate each JD's title, category queries, and hard rules. Do not require a fixed CV count or JD count. Set bounded upload/archive limits and return a clear error if exceeded; keep the individual PDF limits. Use streaming/staging rather than placing the whole archive and all extracted CVs into one request JSON or database row.
3. Validate ZIP members safely: PDFs only, no path traversal, no nested archive extraction, bounded uncompressed total/member count, and deterministic reporting of duplicate names/files. Reject invalid members individually where possible and report accepted/rejected counts. Do not silently merge candidates by content hash.
4. Assign stable candidate IDs, reserve campaign/CV records idempotently, queue Stage 0 per CV or in bounded chunks, and persist the redacted view plus page/block locations needed for later evidence citation. Release/delete encrypted raw bytes after extraction or terminal failure. A retry must reuse completed Stage 0 output, not extract again.
5. Return `campaign_id` promptly and expose initial status; intake/extraction continues asynchronously. Keep existing bearer-token ownership rules and redaction/logging behavior.
6. Route new Stage 0 jobs to a bounded OCR worker queue. Keep lightweight campaign coordination/recovery jobs on a separate control queue that OCR workers do not consume. Configure a worker for each subscribed queue in Compose, preserving the existing legacy screening task route. Size worker pools for the actual RAM/CPU budget instead of multiplying model-loaded processes without measurement. API status reads remain direct database reads in the web service, not Celery tasks.

**Verification:** API and worker tests for one CV/one JD, many CVs/many JDs, invalid PDFs, duplicate names and hashes, oversized archive/member, malformed JDs, archive path attacks, Stage 0 retry, and cross-owner access. Use a representative PDF ZIP in an end-to-end intake test. Check task routing and worker subscriptions, including recovery after an OCR backlog.

**Acceptance criteria:** One submission or folder import creates a campaign with variable CV/JD counts; every CV is accepted or explicitly rejected/failed; each accepted document has exactly one reusable Stage 0 result; no unredacted PDF appears in logs or final results. An OCR backlog does not prevent control/recovery jobs or API status reads from running.

## Phase 4 — Per-JD Stage 1/2 processing and global cutoff

**Goal:** Assess the full candidate pool against each JD and choose the best 30 for each JD before *any* Stage 3 selection for that JD.

**Files/modules likely to change:** `app/orchestrator.py`; `app/workers/tasks.py` and `celery_app.py`; `app/stage2_retrieval/evidence_extractor.py` and `repository.py`; new `app/campaigns/coordinator.py`; `app/config.py`; `docker/docker-compose.yml`; integration tests.

**Concrete changes:**

1. Schedule bounded candidate-JD work units; do not create one 1,000-CV request/run or load all pair results into a worker's memory. Reuse each CV's redacted Stage 0 output and existing document/embedding caches. Stage 1 `REVIEW` pairs continue as specified in Phase 1.
2. Persist each pair's Stage 1 outcome and Stage 2 score/failure. Keep candidate-JD separation: a failure or rejection for one JD must not affect other JDs. Review candidates compete for the same shortlist places as passed candidates.
3. Add a durable per-JD barrier: shortlist only when all accepted CVs for that JD have terminal Stage 0/1/2 outcomes or explicit failures. Sort Stage 2 scores with a deterministic tie-breaker, persist ranks, select up to 30, and mark other survivors `CUTOFF_EXCLUDED`. Never take 30 per worker chunk.
4. Make coordinator and worker retries idempotent. Recover abandoned pair work and avoid a second shortlist when multiple workers finish together. Bound OCR, embedding, reranking, and database concurrency. Make the work queue independent of campaign size and JD count, subject to configured resource limits.
5. Route new Stage 2 jobs to a bounded retrieval queue with its own subscribed worker pool, separate from OCR and lightweight control work. Keep the per-JD shortlist barrier on the control queue. Ensure retries return to their original queue; verify deployed worker subscriptions so jobs cannot sit on an unconsumed queue. Keep the legacy single-PDF run route available.

**Verification:** Deterministic end-to-end tests for 1 JD and several JDs, under/at/over 30 eligible candidates, overlapping shortlists, tie scores, Stage 1 reviews, failed extraction/retrieval, chunking, reordering, worker crash/retry, and simultaneous completion. Use PostgreSQL integration tests for cache reuse. Simulate a retrieval backlog and check that control tasks still advance other JDs.

**Acceptance criteria:** For each JD, at most 30 *global* Stage 2 survivors are selected; their IDs/ranks are the same regardless of batch size or task order. Every accepted candidate has one recorded outcome for each JD, and a candidate may be selected for several JDs. Heavy retrieval work does not block control/recovery work.

## Phase 5 — Stage 3 execution, ranking, and result APIs

**Goal:** Evaluate only each JD's shortlist and expose separate, understandable rankings and review information to a future UI.

**Files/modules likely to change:** `app/workers/tasks.py` and `celery_app.py`; `app/stage3_evaluation/llm_client.py`, `prompts.py`, `schemas.py`, and `scoring.py` where needed; `app/api/endpoints.py` and `schemas.py` or campaign router; `app/run_audit.py`/campaign persistence; `app/config.py`; `docker/docker-compose.yml`; API and evaluation tests.

**Concrete changes:**

1. Queue at most 30 Stage 3 evaluations per JD, using that JD's immutable snapshot and the pair's verified retrieval evidence. Preserve existing citation checks and score/tier policy; only version the LLM/scoring policy if its behavior actually changes. Check prompts and category queries with a nontechnical JD; avoid technical-role assumptions in user-visible explanations.
2. Persist each evaluation and Stage 1 verification metadata together. Score-sort Stage 3 `SUCCESS` results per JD; include provisional candidates in that list with a prominent flag and exact verification reasons. Keep Stage 3 review-required, failed, Stage 1 failed, Stage 2 excluded, and extraction failed cases accessible separately.
3. Expose `GET` campaign status/progress, JD list, one JD's paginated ranked results, and its review/failure/exclusion details. Include candidate IDs, score/tier, rank, provisional status, relevant reasons, and evidence links/locations. Apply owner scoping and avoid disclosing raw CV text by default.
4. Treat a campaign as complete only after every JD's selected Stage 3 pair is terminal and all nonselected pairs are accounted for. Permit partial progress reads and failed-task retry without changing already completed rankings silently.
5. Apply a provider-wide Stage 3 dispatch limit across all JDs and workers, not just `LLM_CONCURRENCY_LIMIT` within one batch. Make request/token budgets configurable for the chosen provider and deployment; do not hard-code a presumed provider quota. Keep the Stage 3 worker pool bounded and separate from heavy OCR/retrieval work.
6. Improve exhausted 429 handling: recognize provider rate-limit responses, respect `Retry-After` or equivalent when valid, and schedule delayed task retries with bounded exponential backoff and jitter. Do not occupy a worker with a long sleep or turn a temporarily rate-limited evaluation immediately into final `PROVIDER_ERROR`. Keep per-candidate attempt state durable so retries do not create duplicate stored evaluations; after the configured retry budget is exhausted, record a clear retryable/provider-rate-limit failure for operator action. Preserve distinct handling for invalid output and nonretryable errors.

**Verification:** Mock-provider integration tests for multi-JD overlap, success with Stage 1 provisional flag, Stage 3 evidence review, evaluation failure, pagination/order, owner access, retry/idempotency, and response privacy. Simulate bursts across multiple JDs/workers, provider 429 with and without `Retry-After`, exhausted retries, and a nonretryable 4xx; assert global admission limits and delayed retry behavior. Run one live-provider smoke test only when configured and explicitly suitable; it is not needed for deterministic unit tests.

**Acceptance criteria:** A UI can show one complete ordered list per JD, one candidate on multiple lists, progress, and explicit provisional verification. Stage 3 evaluates no more than 30 per JD; no scored Stage 3 review/failure is displayed as a final success. Concurrent JDs stay within configured provider limits; transient 429s are retried later without blocking workers, and exhausted 429s have an explicit recorded outcome.

## Phase 6 — End-to-end capacity, operations, and documentation

**Goal:** Verify that the workflow handles a representative company campaign safely and record its measured operating limits.

**Files/modules likely to change:** `scripts/load_test.py` or a new campaign load script; `docker/docker-compose.yml`; `app/config.py`; `README.md`; deployment/operations scripts and tests as findings require.

**Concrete changes:**

1. Exercise campaigns with changing CV and JD counts, including approximately 1,000 PDFs and four JDs. Measure intake time, Stage 0/1/2 duration, total Stage 3 calls, overall completion, queue wait time/depth by stage, worker memory/CPU, DB load, provider 429s/retry delays, and approximate provider usage. Verify the intended 4,000 candidate-JD pair accounting and at most 120 selected Stage 3 candidate-JD pairs in the 1,000-by-4 example; retry attempts may add provider requests.
2. Tune task size, indexes, worker concurrency, queue recovery, and timeouts based on observed bottlenecks. Do not claim a fixed turnaround target until it is measured on the intended infrastructure.
3. Document API examples for ZIP and folder import, one and multiple JDs, status polling, ranking reads, verification flags, failures, limits, costs, backup/retention, and worker recovery. Update the production smoke path and readiness checks for the new migration revision.
4. Run the existing regression suite plus campaign integration tests against a migrated PostgreSQL/Redis environment. Report remaining capacity, quality, or provider limitations explicitly.

**Verification:** Repeatable load report with counts that reconcile at each stage; migration/ready check; existing single-PDF smoke; new campaign smoke; privacy/auth checks. Test both a small campaign and the target-size campaign.

**Acceptance criteria:** A representative 1,000-CV/four-JD campaign reaches a terminal state with all expected pair outcomes accounted for, no duplicate Stage 3 calls from retries, separate JD rankings, and measured runtime/resource/provider usage. Operational docs describe the tested limits and failure recovery.

## Completion boundary

The backend is ready for UI implementation when all six phases meet their acceptance criteria and the campaign APIs can provide upload/submission status plus paginated per-JD rankings. A future UI can then offer ZIP/folder selection, JD entry, progress, four-or-more ranking tabs, and human-verification queues without defining new screening policy.

This is a substantial change. The earlier rough estimate was 40–70 focused hours for backend work and a representative trial, with capacity tuning as the main uncertainty; the UI is separate. Re-estimate after Phase 2 if the chosen storage/intake approach or infrastructure requires more work.
