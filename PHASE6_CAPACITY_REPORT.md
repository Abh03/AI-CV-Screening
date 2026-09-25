# Phase 6 operating report (2026-09-25)

## Environment and evidence

Local Docker Compose on WSL2 with PostgreSQL 16/pgvector, Redis 7, one process
per OCR, retrieval, evaluation, control, and legacy worker, plus beat and web.
The containers share a reported 7.572 GiB memory limit. Alembic was upgraded
from `fe903a71bd42` to `ab925e1c3d70`; `/ready` and the existing authenticated
single-PDF production smoke passed afterward. The ordinary regression suite
passed (219 passed, 11 skipped); two capacity-report accounting tests passed.

The live synthetic 2-CV/2-JD trial accepted both PDFs and reached `COMPLETED`
in 16.765 seconds. All four expected pair rows were terminal and all four were
selected for Stage 3. Intake took 0.094 seconds; Stage 0 was terminal by the
2.531-second poll; both JDs were shortlisted by the 6.593-second poll. All four
Stage 3 worker attempts were recorded, one per pair. All four
Stage 3 results were `REVIEW_REQUIRED` because the first fixture lacked
Education and Projects evidence. A later synthetic 1-CV/1-JD trial completed
in 10.578 seconds with one selected review outcome (`MISSING_INFORMATION`).
Review outcomes were correctly absent from final rankings. The tests cover
ranked-success ordering and provisional flags; the live smoke did not produce
a successful ranked result.

One idle or lightly loaded `docker stats` sample, taken after those trials,
showed approximately 531 MiB OCR worker, 572 MiB retrieval worker, 520 MiB
evaluation worker, 508 MiB control worker, 461 MiB beat, 515 MiB legacy worker,
918 MiB web, 55 MiB PostgreSQL, and 6 MiB Redis. CPU was below 1.2% per
container at that sample. These are baseline readings, not peak trial memory.

## Capacity boundary

No representative 1,000-PDF/four-JD archive was supplied, and no target host
or provider quota was specified. The required 4,000-pair/at-most-120-Stage-3
target has **not** been measured or accepted. The synthetic smoke uses tiny,
one-page PDFs and cannot establish OCR throughput, Stage 2 retrieval load,
provider 429 behavior, peak memory, DB load, cost, or turnaround at company
scale. Do not extrapolate the 16.765-second runtime to 1,000 PDFs.

The repeatable command is documented in `README.md` and implemented by
`scripts/campaign_load_test.py`. Run it with a representative ZIP and four
structured JDs on the intended deployment. Supply `DATABASE_URL` and
`REDIS_URL` to collect aggregate attempt, DB, and queue samples; add
`--docker-stats` for worker CPU/memory samples. Its report checks pair totals,
per-JD shortlist caps, terminal counts, stage milestones, and ranking totals.
Poll-based stage timings have poll-interval resolution. The current data model
does not persist exact queue wait, provider token usage, or billing, so those
values require separate broker/provider telemetry before an operational SLA or
cost estimate is published.

## Operations and recovery

Build **all** service images, including `migrate`, before `compose up`: a stale
migration image can leave the database behind the web revision and make `/ready`
return 503. Check `compose ps` for all five workers and beat; the Celery workers
disable the web image's inherited HTTP health probe. Keep PostgreSQL backups,
Redis append-only data, and the encryption key available during recovery.
Control and beat requeue expired work; inspect campaign status, per-JD statuses,
queue depth, and worker logs when progress stalls. Exhausted provider rate
limits appear as `PROVIDER_RATE_LIMIT_EXHAUSTED` and need operator action.
