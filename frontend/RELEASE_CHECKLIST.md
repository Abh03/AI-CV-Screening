# Frontend release checks

The campaign UI is implemented for internal read-only use. The backend now provides an owner-scoped campaign list, immutable JD definition read API, filenames in result rows, and password sign-in with JWT cookies and CSRF checks. API responses use `Cache-Control: no-store`. Before production release, verify HTTPS same-origin deployment, account provisioning and recovery, and login/owner-isolation behavior in the integration environment. If recruiters must record human decisions, define and implement an audited review disposition API before adding controls.

## Local smoke

1. Start PostgreSQL, Redis, workers and FastAPI as described in the root README. The nonproduction backend uses its local principal.
2. In `frontend/`, run `npm ci`, `npm run lint`, `npm run build`, `npm test`, then `npm start`.
3. Create one or more synthetic JDs. Upload a small ZIP with valid synthetic PDFs and one non-PDF entry. Confirm the rejection code, saved campaign ID, and per-JD counts.
4. Reopen the campaign by ID; for an `INTAKE` campaign, choose another ZIP. Confirm rankings contain only `SUCCESS` and each other terminal outcome appears on its own status tab. Open details and check null scores, provisional results, mock badges and citation locations.
5. In an HTTPS integration deployment, sign in as two provisioned recruiters. Verify each sees only their own campaigns; try another recruiter's campaign ID and expect 404. Confirm sign-out revokes the session, expired tokens require sign-in, and a cookie-authenticated write without the CSRF header is rejected.
6. Test keyboard flow, narrow viewport, 401/404/409/413/415/422/503 messages, interrupted upload, hidden-tab polling, and a Stage 3 retry waiting state in an integration environment.

## Capacity trial

With the supplied dataset, record browser and backend versions, hardware, upload duration, page responsiveness, browser memory, errors and correlation IDs. Confirm accepted CVs × four JDs equals accounted pairs, each JD selects at most 30 for Stage 3, rejected entries have reason codes, and all terminal outcomes remain accessible. The 1,000-CV/four-JD trial has not been run; do not label capacity verified until it has.
