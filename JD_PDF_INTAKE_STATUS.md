# JD PDF intake implementation status

Plan steps 1–5 are implemented. Step 6 has passed automated offline verification
and a small live infrastructure/provider check on the local deployment.
Recruiter quality review and capacity validation remain.

## Implemented

- A versioned `jd-v1` contract uses `title`, required/preferred skill clusters,
  nested hard filters, all four category requirements, and uncertainty notes.
  Unspecified authorization/experience/education create no additional hard filter.
- Bounded JD PDF extraction reuses reading order, integrity assessment and OCR,
  without CV PII masking. A separate schema-constrained JD provider path supports
  Gemini, Groq, OpenRouter and explicitly synthetic development mock output.
- Owner/PDF-hash deduplication caches successful extraction. Failed attempts retry
  only on explicit request; interrupted requests become retryable after a bounded
  deadline. Attempt fencing prevents an old request overwriting a newer result.
- Separate drafts and immutable approved records retain ownership, PDF hash,
  extraction/schema/prompt/provider/model provenance and approval time. Draft
  source text is cleared on approval. Unapproved contents expire after seven days;
  hourly control/beat cleanup clears them. Revised PDF bytes create a new version.
- Owner-scoped extraction, draft read, approval and approved-list APIs. Campaigns
  accept approved IDs and atomically snapshot approved profiles. Production single
  PDF screening also requires an approved ID. Internal development text/profile
  calls remain supported. Existing campaign snapshots and results remain readable.
- Frontend supports multiple PDF uploads, prefilled editable review, source text
  by page, uncertainty notes, skill aliases/substitutes and explicit approval.
  Every JD must be approved before campaign creation and existing CV ZIP intake.
- Both screening paths require evidence for every mandatory skill. Canonical,
  alias and approved substitute mentions pass the skill check; absent or negated
  mentions require review. Checks retain matched term, excerpt and source.
  Preferences never reject. Existing verified hard filters preserve definite FAILs.
  Stage 2 and Stage 3 consume the same approved profile; Stage 3 sees skill lists.
- Migration `d91a2b3c4e50`, readiness revision, smoke/folder/load tools and README
  now use the approved-profile flow.

## Verification

- Follow-up fix, 2026-09-26: a readable two-page AI-engineering JD was falsely
  rejected at dictionary density 0.34 against a 0.35 cutoff. JD intake now checks
  entropy, readable word content and decoding artifacts without the small CV
  vocabulary cutoff; CV integrity behavior is unchanged. The actual PDF passed
  both pages without OCR and the deployed Groq extraction returned `REVIEW`
  with a profile and no error. Targeted backend checks: 13 passed; frontend: 2
  passed. Failed cached drafts require the explicit Retry extraction action.
- Full offline backend suite: 232 passed, 11 infrastructure/live-provider skips.
- Frontend: 10 tests passed; production Angular build passed.
- Small PDF campaign test drives upload, edited approval, saved version, ZIP intake,
  Stage 0, Stage 1, retrieval worker, shortlist coordinator, mock Stage 3 and campaign
  completion. It uses SQLite, synthetic retrieval and Redis admission stubs.
- Additional coverage includes owner isolation, idempotency, approval immutability,
  explicit provider retry, expiry cleanup, interrupted extraction recovery, production
  single-PDF approved references, scanned-page OCR fallback with an OCR stub,
  injection encapsulation, required-skill evidence and migration upgrade/downgrade.

## Remaining before rollout

1. **Completed locally, 2026-09-26.** Rebuilt and started the production Compose
   stack using `docker/.env`. Its migration service applied
   `python -m alembic upgrade head`; `alembic current` confirmed
   `d91a2b3c4e50 (head)`. Web, all five workers and beat are running;
   PostgreSQL, Redis and web health checks are healthy. This verifies the local
   Docker deployment, not any separately hosted installation.
2. **Completed locally, 2026-09-26.** `python -m scripts.production_smoke` passed
   authenticated submission, queue, worker, persistence and readiness checks.
   `python -m scripts.jd_rollout_smoke` extracted and approved one synthetic text
   JD PDF, uploaded a one-CV ZIP, and completed its campaign using PostgreSQL,
   Redis, Celery, the configured live Groq provider and real retrieval models.
   One pair was selected and finished as `REVIEW_REQUIRED`; expected, actual and
   terminal pair counts all equal one. See
   [campaign report](docs/jd-rollout-smoke-report.json). Synthetic fixtures verify
   plumbing, not recruiter quality or capacity.
3. Review real text, scanned and ambiguous JD PDFs with recruiters. Real Tesseract
   scanned-PDF and live-provider extraction quality have not been validated here.
4. Perform the planned capacity trial after the small live campaign passes.

Items 3 and 4 are deferred as requested. The remaining items are live validation;
they are not additional
backend/frontend implementation steps for the specified recruiter flow.
