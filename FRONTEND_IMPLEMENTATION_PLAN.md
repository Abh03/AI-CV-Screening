# Angular frontend implementation plan

## Goal and scope

Build a recruiter-facing Angular application for creating campaigns with one or more structured job descriptions (JDs), uploading a ZIP of PDF CVs, following processing, and reviewing separate results for each JD. The first production release should support the planned 1,000-CV/four-JD campaign without rendering thousands of records at once. Keep the existing single-PDF workflow as a secondary tool if the team needs it; the campaign workflow is the main path.

The upcoming 1,000-CV/four-JD ZIP is an acceptance dataset, not a prerequisite for frontend development. Use synthetic small campaigns and mock API responses until it arrives. Record measured behavior with the supplied dataset before calling the release capacity tested.

## Verified backend contract (25 September 2026)

| Workflow | Existing API | Frontend implication |
| --- | --- | --- |
| Create campaign | `POST /api/v1/campaigns` with `{job_profiles, idempotency_key?}`; returns `campaign_id`, `status`, `created`, `upload_url` | Save the returned ID before uploading; reuse a stable key for retry. |
| Upload CVs | `POST /api/v1/campaigns/{id}/archive`, raw `application/zip` body; returns accepted/rejected counts and per-file details | Send the `File` as the body, not multipart form data. Show member rejection report. |
| Campaign progress | `GET /api/v1/campaigns/{id}` returns `status`, `counts`, `stage0`, `stage3_retry_waiting`, `intake_report` | Poll while running; compute progress from pair counts. |
| JD progress | `GET /api/v1/campaigns/{id}/jds` returns JD key/title/status/cap/counts/retry wait | Show one status card/tab per JD. |
| Successful rankings | `GET /api/v1/campaigns/{id}/jds/{jd_key}/rankings?limit=&offset=` | Server pagination (1–100); score order is server defined. |
| Other outcomes | `GET /api/v1/campaigns/{id}/jds/{jd_key}/outcomes?status=&limit=&offset=` | Separate review, evaluation failure, filter rejection, processing failure, cutoff exclusion and extraction failure views. |
| Single PDF | `POST /api/v1/screening/submit-pdf`, then `GET /api/v1/screening/runs/{run_id}` | Optional secondary route; request contains base64 PDF, unlike campaign ZIP. |

The JD request contains `job_id`, `title`, `jd_category_queries`, and `hard_filter_rules`. Filter fields are `min_years_experience`, `degree_requirement`, and `require_work_authorization`. Supported degree levels and aliases come from `app/stage1_rules/contracts.py`; the UI should offer canonical values and send the nested rule object. Category query keys must be uppercase `SKILLS`, `EXPERIENCE`, `PROJECTS`, and `EDUCATION` because Stage 2 reads those exact keys; show friendly labels while editing their query text. Job IDs must be distinct; at least one nonblank query is required. The backend currently sets a Stage 3 cap of 30 per JD.

An accepted ZIP entry receives a generated candidate ID. The upload report maps source filenames to IDs. Preserve that mapping in the UI while the session is open; the results API currently returns candidate IDs but not filenames. ZIP limits default to 256 MiB compressed, 1 GiB uncompressed, 2,000 members, and 10 MiB per PDF. The frontend can check size/type early, but backend validation is authoritative. ZIP entries must be uniquely named (case-insensitive leaf name); unsupported/non-PDF entries are reported individually.

Pair statuses include `PENDING`, intermediate work states, and terminal `SUCCESS`, `REVIEW_REQUIRED`, `EVALUATION_FAILED`, `FILTER_REJECTED`, `PROCESSING_FAILED`, `CUTOFF_EXCLUDED`, `EXTRACTION_FAILED`. Do not infer completion from `SUCCESS` alone: compare `counts.terminal_pairs` with total accepted CVs × JD count and use campaign/JD status. `SUCCESS` may have `provisional=true` and verification reasons from Stage 1; it is not a final hiring decision. `REVIEW_REQUIRED` is separate from successful rankings and can carry a provisional score with no tier. `stage2_score` and `stage2_rank` describe retrieval, not the final suitability score. Evidence entries provide citation, document/chunk IDs and source location, but no text excerpt or PDF download.

## Product flows and screens

1. **Access and home.** Sign in through an approved server-side session flow; show the current user/role, service availability, recent campaigns, and a “New campaign” action. Until a campaign-list API exists, use a limited “open by campaign ID” page and locally remembered IDs with a clear browser-only label.
2. **Campaign builder.** Add/remove/reorder JD cards; enter unique ID, title, four category queries, minimum years, optional degree level/field terms and authorization requirement. Validate required fields, duplicate IDs, bounds, and conflicting terms in the browser. Provide a compact review step that summarizes all JDs and the fixed per-JD cap.
3. **ZIP intake.** Select a ZIP, show name/size and size-limit warning, create the campaign, upload the raw ZIP with progress, then show accepted/rejected entries and codes. Retain the campaign ID through reload/navigation. If upload fails, keep the ID and offer retry with the same file; explain that a campaign with zero accepted PDFs remains at intake. Handle the browser closing during upload as unknown until status is rechecked.
4. **Campaign monitor.** Show overall state, accepted CV count, expected candidate-JD pairs, terminal pairs, Stage 0 counts, per-JD counts, selected Stage 3 capacity and pending rate-limit retries. Refresh at a modest interval while active, pause polling when the tab is hidden, allow manual refresh, stop on terminal state, and show last-updated time. Do not show a fake time estimate.
5. **Per-JD results.** Each JD has paginated ranking, review and failure/exclusion tabs. Ranking rows show rank, candidate ID/filename when known, final score, tier, category scores, provisional badge, verification reasons and mock-result badge. Outcome rows show their status, stage, reason/failure code, and any available score. Keep filters/status and page in the URL; use backend pagination and sorting rather than client-side sorting a partial page.
6. **Candidate detail.** Open a row into a panel/page with stage 1 checks, category scores, reasons, verified citation IDs and source page/block locations. State clearly when the backend has no excerpt or document link. Do not present a “Verify,” “Reject,” “Approve,” or “Retry” action until corresponding audited backend APIs exist.
7. **Single-PDF tool (secondary).** Add after the campaign path works if needed: one JD, one PDF, optional verified attributes, run-status polling, and result display. It should share JD form and result components, but use its own API adapter and request model.

Use responsive desktop-first layouts, keyboard navigation, visible focus, programmatic form errors, accessible status announcements, non-color-only states, and empty/loading/error states for every screen. Never render CV or JD text as HTML. Avoid exposing PII in browser logs, analytics, error reporting, or URL parameters.

## Architecture

Create `frontend/` as a standalone Angular workspace with TypeScript strict mode, standalone components, lazy feature routes, Reactive Forms, `HttpClient` with functional interceptors, and a small state layer built from Angular signals/services. Use a component library only after a quick design check; choose one consistent set of accessible table, dialog, form and progress primitives. Avoid a global store until cross-route state becomes complex. Pin an actively supported Angular major and Node version in project files/CI at scaffold time, with lockfile committed.

Suggested structure:

```text
frontend/
  src/app/core/          # API base URL, HTTP errors, session, auth guard
  src/app/shared/        # status badges, pagination, form fields, empty states
  src/app/features/campaigns/
    create/              # JD editor, ZIP intake, review/submit
    list/                # recent campaigns when API is available
    detail/              # progress and JD overview
    results/             # ranking/outcome table and candidate panel
  src/app/features/single-screening/  # optional single-PDF workflow
  src/app/api/           # typed DTOs, endpoint clients, mappers
  src/environments/      # public config only; no secrets
```

Keep API DTOs distinct from UI view models. Use discriminated status unions where practical and explicit nullable score/tier fields. Generate types from OpenAPI where schema coverage is reliable; add typed handwritten adapters for the campaign endpoints that currently return untyped dictionaries. Add contract fixtures from actual API responses so drift is caught. Configure Angular's XHR HTTP backend for actual ZIP upload progress; its default fetch backend does not emit upload progress. Distinguish bytes sent from server processing completion. Use route-level error handling, request cancellation on navigation, and shared handling of `401`, `403/404`, `409`, `413`, `415`, `422`, and `503` without dumping response bodies or CV data to logs. Preserve `X-Correlation-ID` in support messages.

For local development, proxy `/api` to FastAPI. For production, serve the UI and API through one origin/reverse proxy or a dedicated backend-for-frontend (BFF). There is no CORS middleware in `app/main.py`; direct cross-origin calls require an explicit backend CORS decision. Keep configuration public (API path, polling intervals, upload limits displayed for guidance) and secrets server-side.

## Authentication and backend work needed for a complete production UI

The current production backend accepts static bearer tokens from `API_TOKENS_JSON` and has no login, logout, token refresh, or user-management endpoints. Never bundle one of these tokens in Angular assets or `environment.ts`, and do not ask a recruiter to paste a shared production token into local storage. Before production use, implement a server-side session/BFF that holds the backend token, uses secure `HttpOnly` cookies and CSRF protection, and maps authenticated users to distinct backend recruiter principals; alternatively replace the static-token auth with an audited user identity system. Confirm who provisions users and how roles are assigned. Existing backend owner scoping must remain the authorization source.

Backend additions are required for the full experience:

| Priority | Addition | Why |
| --- | --- | --- |
| Release gate | Authenticated `GET /campaigns?limit=&offset=` with owner scope, summary/status/timestamps | Reopen and discover campaigns across devices; locally saved IDs are insufficient. |
| Release gate | Campaign JD definition read endpoint (or include immutable JD snapshot in existing JD response) | Reload/edit review screens and show exact submitted queries/rules after creation. |
| Release gate | Candidate filename in outcome/ranking DTO, or owner-scoped candidate metadata lookup | Make generated UUIDs usable to recruiters across devices. |
| Release gate if human review is expected | Audited review action API with reviewer, timestamp, reason, explicit disposition and authorization checks | A review queue alone cannot complete human verification. Define whether review changes the screening outcome or records a separate recruiter disposition. |
| Optional | Candidate detail endpoint with redacted excerpts/source locations and policy/version metadata | Enable meaningful evidence inspection; current API supplies references/locations only. Add PDF access only with a separate privacy and retention decision. |
| Optional | Server-side search/filter/export of campaign results | Needed for operational reporting; downloading only the current paginated page is misleading. |
| Optional | Campaign cancel/retry controls | Do not show controls without idempotent backend operations and audit trails. |

These are product/API changes, not frontend workarounds. Agree the release scope for review actions and exports before implementation. A read-only results MVP can launch internally with existing endpoints plus a secure access layer and a way to reopen campaign IDs.

## Implementation sequence and deliverables

| Phase | Work | Done when |
| --- | --- | --- |
| 0. Contract and UX | Capture real OpenAPI/examples; resolve auth, category keys, filename mapping, review workflow, and screen wireframes. Create small fixtures for success, partial progress, every terminal status, 422/409/413 and 503. | Approved route map, DTOs, acceptance scenarios, and agreed backend gates. |
| 1. Foundation | Scaffold Angular, configure strict TS, routing, styles, accessible primitives, HTTP client/interceptors, environment config, development proxy, CI build/lint/test, and session integration placeholder. | App builds, route navigation works, API errors render safely, no secret is bundled. |
| 2. Campaign intake | Build JD form, validation, review step, create request/idempotency, raw ZIP upload/progress, rejected-file report and resume-by-ID. | A synthetic ZIP creates a campaign and its intake report survives navigation. |
| 3. Progress/results | Build campaign/JD polling, count reconciliation, URL-backed tabs and pagination, ranking/outcome tables, candidate detail panel, provisional and mock labels. | All statuses are visible; no non-success appears as a final ranked result. |
| 4. Production integration ( backend-dependent) | Add session/BFF and agreed API additions, campaign list/reopen, filename display, human review if in scope, production reverse proxy, security/privacy checks. | Recruiter can sign in, access only owned campaigns, resume across devices, and complete agreed review flow. |
| 5. Verification and release (backend load run) | Component/API contract tests, E2E smoke, accessibility pass, browser/device checks, small live campaign, then supplied 1,000-CV/four-JD trial. Fix measured issues and write runbook. | Release gates below pass with the target dataset and deployment. |

Phases 0 and 1 can start before the ZIP arrives. Implement campaign intake and read-only results first; add the single-PDF tool only if it is a real recruiter workflow.

## Verification and release criteria

- Unit/component tests cover JD validation, nullable score/tier rendering, provisional status, status-to-tab mapping, count math, pagination, and sanitized error messages. HTTP client tests assert raw ZIP content type and body, idempotency keys, query parameters, auth/session behavior, and response mapping. Use only synthetic CV text in fixtures.
- E2E tests cover create/upload/progress/results, refresh/reopen, duplicate or malformed JD, zero accepted ZIP files, partial member rejection, upload interruption/retry, stale/unknown campaign ID, 401/404/409/413/415/422/503, empty result sets, and a Stage 3 retry waiting state. Test recruiter owner isolation against real backend auth in an integration environment.
- Accessibility acceptance: keyboard-only completion of the main flow, labeled controls and errors, focus management for dialogs/panels, readable status announcements, contrast checks, and no essential information conveyed by color alone.
- Security/privacy acceptance: no backend bearer secret in shipped JS or browser storage; secure session and CSRF behavior checked; no raw CV, PDF bytes, or sensitive excerpts in browser logs/telemetry; network and caching policy reviewed; HTML injection tests for filenames/JD text.
- Performance acceptance: upload stays responsive for a near-limit ZIP, result pages use server pagination, polling does not pile up concurrent requests, hidden tabs stop polling, and the UI remains usable while 4,000 pairs progress. Measure on the target browser/hardware rather than declaring an unsupported time target.
- With the provided dataset, confirm 1,000 accepted CVs × four JDs yields 4,000 accounted pairs (adjust for rejected ZIP entries), each JD has at most 30 Stage 3 selections, all terminal statuses remain accessible, upload rejections are explainable, and each JD ranking is independent. Record frontend timings, errors, browser memory, and backend capacity observations alongside the backend load report.

## Source references

- Project: `app/api/campaigns.py`, `app/api/endpoints.py`, `app/api/schemas.py`, `app/stage1_rules/contracts.py`, `app/core/auth.py`, `app/config.py`, `BULK_SCREENING_PLAN.md`, and `README.md`.
- Angular release/support policy: <https://angular.dev/reference/releases>; HTTP configuration/interceptors: <https://angular.dev/api/common/http/provideHttpClient>; upload progress: <https://angular.dev/guide/http/making-requests>; HTTP tests: <https://angular.dev/guide/http/testing>.
