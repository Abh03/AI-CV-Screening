# Frontend release checks

The campaign UI includes the recruiter workspace described in [RECRUITER_WORKSPACE.md](../docs/RECRUITER_WORKSPACE.md). The backend provides authenticated CV viewing, full-pool filtering, private saved views, versioned human decisions and reviewer membership. API responses use `Cache-Control: no-store`. Before production release, migrate to `f04d8c912e63` and verify HTTPS same-origin deployment, account provisioning and recovery, cookie/CSRF checks, and owned/shared campaign isolation.

## Local smoke

1. Start PostgreSQL, Redis, workers and FastAPI as described in the root README. The nonproduction backend uses its local principal.
2. In `frontend/`, run `npm ci`, `npm run lint`, `npm run build`, `npm test`, then `npm start`.
3. Create one or more synthetic JDs. Upload a small ZIP with valid synthetic PDFs and one non-PDF entry. Confirm the rejection code, saved campaign ID, and per-JD counts.
4. Reopen the campaign; for an `INTAKE` campaign, choose another ZIP. Open a role workspace, search across the full pool, filter requirements, and compare candidates. Open original CVs and extracted evidence; confirm null scores, unassessed requirements, verification and demonstration badges. Search a rejected candidate and verify its stage history and reason. Restore a legacy CV and verify only the matching original is accepted.
5. In an HTTPS integration deployment, sign in as two provisioned recruiters. Verify unshared campaigns and CVs return 404, then grant and revoke campaign membership. Save a decision, verified facts, notes and tags; verify history, saved views and exports. Edit the same decision in two sessions and confirm the stale write returns 409. Confirm sign-out revokes the session, expired tokens require sign-in, and a cookie-authenticated write without the CSRF header is rejected.
6. Test keyboard flow, narrow viewport, 401/404/409/413/415/422/503 messages, interrupted upload, hidden-tab polling, and a Stage 3 retry waiting state in an integration environment.

## Capacity trial

With the supplied dataset, record browser and backend versions, hardware, upload duration, page responsiveness, browser memory, errors and correlation IDs. Confirm accepted CVs × four JDs equals accounted pairs, each JD selects at most 15 for Stage 3, rejected entries have reason codes, and all terminal outcomes remain accessible. The 1,000-CV/four-JD trial has not been run; do not label capacity verified until it has.
