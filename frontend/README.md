# Recruiter frontend

Angular 22 workspace for campaign intake, monitoring and read-only per-JD results. Requires Node 22.22.3 or another version in `package.json` engines.

```sh
npm ci
npm start
```

The dev server proxies `/api` to FastAPI at `127.0.0.1:8000`. In nonproduction backend mode, the API uses its local principal until a recruiter signs in. The home page lists campaigns owned by the current principal. Production sign-in uses an expiring JWT in a Secure, HttpOnly cookie and a CSRF header on writes; no token or filename is stored in browser storage. Deploy UI and API on one HTTPS origin with a reverse proxy. Session-only display hints are cleared on sign-out.

`npm run build`, `npm run lint`, and `npm test` are CI gates. The campaign path is primary; the optional single-PDF tool is deferred until its recruiter use case is confirmed.
