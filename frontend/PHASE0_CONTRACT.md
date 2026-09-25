# Phase 0 contract and route map

Captured against `app/api/campaigns.py`, `app/api/schemas.py`, `app/campaigns/intake.py`, `app/campaigns/persistence.py` and `app/core/auth.py` on 25 September 2026.

| Route | Purpose |
| --- | --- |
| `/` | Owner-scoped paginated campaign list, open by ID and browser-only recent IDs |
| `/campaigns/new` | JD builder, review, create, ZIP intake |
| `/campaigns/:id` | Overall and per-JD progress; intake report |
| `/campaigns/:id/jds/:jd?status=SUCCESS&page=1` | Rankings and each terminal outcome, server-paginated |

`POST /api/v1/campaigns` receives `job_profiles` and a stable `idempotency_key`. Each profile sends only uppercase `SKILLS`, `EXPERIENCE`, `PROJECTS`, `EDUCATION` query keys and nested `hard_filter_rules`. `POST /api/v1/campaigns/{id}/archive` receives the raw `File` with `Content-Type: application/zip`. The accepted report maps filename to candidate ID. `GET /api/v1/campaigns/{id}` returns `counts.cvs`, `counts.jds`, `counts.pairs`, `counts.terminal_pairs`, Stage 0 counts and intake report. `GET /jds` returns per-JD counts and the 30-person Stage 3 cap. Rankings return only `SUCCESS`; `/outcomes` requires one terminal non-success `status` and both routes accept `limit` 1–100 and `offset`.

Acceptance scenarios: one valid ZIP, mixed accepted/rejected entries, zero accepted entries, interrupted upload and retry, partial processing, all terminal statuses, unknown ID, 401, 409, 413, 415, 422 and 503. Scores and tiers may be null. A successful result may still be provisional. The UI renders no candidate text as HTML and exposes no write action for review.

Synthetic payloads in `fixtures/` cover partial processing, every terminal status and HTTP error states. They use no real CV content. The API remains the source of truth for limits and status transitions.

Password login, JWT cookie sessions with CSRF checks, and distinct recruiter principals are implemented. Owner-scoped `GET /api/v1/campaigns`, immutable `GET /api/v1/campaigns/{id}/jds/{jd_key}/definition`, and result `source_filename` are also implemented. An audited disposition endpoint is needed if human review actions are in release scope. HTTPS same-origin deployment and integration testing remain release gates. Current frontend displays an owner-scoped list and read-only results.
