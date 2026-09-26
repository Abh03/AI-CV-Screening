# Phase 6 measured capacity report (2026-09-26)

**Campaign RUNNING; completion is not yet accepted.**
Campaign ID: `c2f3123a-ebec-4e69-a26b-ad123c82fb98`. Started `2026-09-26T10:33:32.249411+00:00`.
Latest measured runtime: 6954.516 seconds. Intake: 7.516 seconds.

## Exact inputs and deployment

Archive: `C:\Abhyudit_Files\Infinite\CV-Benchmark-Dataset-Generator\output\cvs\CV_1000.zip`.
7,174,953 bytes; 1,000 unique PDFs; 8,816,430 expanded bytes; 1,634 pages, including 43 image-only pages in 30 scanned PDFs.
JD inputs: `C:\Abhyudit_Files\Infinite\CV-Benchmark-Dataset-Generator\output\jds\JD01.pdf` through `JD04.pdf`.
Roles: Java Backend, C#/.NET Backend, QA Automation, Data Engineering. Minimum years: 3, 3, 2, 3. Explicit OR alternatives were corrected before approval; no hard degree filter.
The approved immutable IDs and complete extraction/approval JSON are in `artifacts/phase6/jobs.json` and `jd01`–`jd04` artifacts. Dataset labels were not used in screening.
Local Docker Compose on WSL2: PostgreSQL 16/pgvector, Redis 7, API on loopback:8000. Docker reports 16 CPUs and 8,130,854,912 bytes of RAM (7.572 GiB).
Initially one OCR, evaluation, control and legacy process, plus beat/web; three retrieval processes with four OpenMP/MKL threads each. Evaluation was increased to two processes during the OpenRouter phase, and that setting persists for Gemini. No concurrent active evaluation campaigns.
Secrets are read from `docker/.env`; credentials and prompts are excluded from this report.
Initial provider: Groq free plan, model `openai/gpt-oss-120b`. Configured admission: 30 requests/minute, 8,000 tokens/minute, global Stage 3 inflight <=2. Reservation initially 6,000 tokens/request; changed to 4,000 at 2026-09-26 10:51:11 UTC after observed responses used 2,901–3,418 tokens. Only evaluation-worker was gracefully recreated; this tuning time is included in measured runtime.
At 2026-09-26 11:28 UTC the user switched both env files to OpenRouter, model openrouter/free, to resume the 64 unfinished selections. The evaluation worker was recreated. Compose initially omitted the model-variable mapping and used an obsolete default: two HTTP 404 attempts were retained as evidence and their existing pair rows requeued after fixing GROQ_MODEL/OPENROUTER_MODEL forwarding. Existing Groq results and all 4,000 pair rows were preserved. Evidence: provider-change-resume.json.
OpenRouter account metadata confirms is_free_tier=true, model openrouter/free, and usage_daily=0 at the account probe. The API-key USD limit field is a spending cap, not a daily request allowance. This free tier is documented at 20 RPM/50 requests per day, with failed requests consuming that allowance. The router chooses compatible free models; it is not one fixed model. [Free router](https://openrouter.ai/openrouter/free), [free request limits](https://openrouter.ai/blog/tutorials/how-to-get-the-lowest-cost-llm-inference-on-openrouter/).
After OpenRouter exhausted its daily free quota, the user configured Gemini and authorized switching the fourteen unfinished selections. At 2026-09-26 12:17 UTC, live settings confirmed gemini-3.6-flash, a 180-second deadline and two worker processes. Twelve waiting selections and two interrupted leases were released after stopping the prior worker, preserving all 106 terminal outcomes and attempt counts. Evidence: gemini-provider-resume.json and gemini-worker-settings.jsonl. GEMINI_MODEL is now forwarded through Compose; safe request/response telemetry includes thinking tokens, and SDK automatic retries are disabled so the campaign controls attempts.
Gemini 3.6 Flash is officially eligible for free-tier standard inference; account-specific billing and quotas are unavailable from this key. No billing upgrade was performed. Gemini cost is not returned by the SDK. The list-price equivalent uses current standard paid rates $0.75/M input and $3.75/M output including thinking tokens; this is not billed campaign spend. [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing), [quota guidance](https://ai.google.dev/gemini-api/docs/rate-limits). Gemini adapter validation: 38 relevant tests passed in 12.71 seconds, followed by eight deadline/measurement checks in 0.60 seconds.
A live quota-header probe returned 1,000 requests/day and 8,000 tokens/minute. Official published free-plan daily token quota is 200,000; actual remaining daily tokens were not exposed. [Groq rate limits](https://console.groq.com/docs/rate-limits).

## Pair accounting and rankings

Accepted 1000 CVs; rejected 0. Expected 4000; stored 4000; terminal 3993; selected 120.
Reconciliation: `True`. Status counts: `{"CUTOFF_EXCLUDED": 3880, "EVALUATION_FAILED": 10, "REVIEW_REQUIRED": 95, "SHORTLISTED": 7, "SUCCESS": 8}`.

| JD key | Status | Pairs | Stage 3 selected | Cap | Reconciled |
|---|---|---:|---:|---:|---|
| 42a7509a9ecd4fc3876a6367e93225fc | COMPLETED | 1000 | 30 | 30 | True |
| a038c776c96041c49e171040fdf8fd1a | COMPLETED | 1000 | 30 | 30 | True |
| cefbdecad0d84d969d6747a6846371fe | COMPLETED | 1000 | 30 | 30 | True |
| d89c10d5f4c84639a46000c04324d301 | SHORTLISTED | 1000 | 30 | 30 | True |

Final ranking checks: `{}`.
Independent database check: 4000 unique pair IDs; extracted-page/OCR totals `{"extracted_pages": 1634, "ocr_pages": 43}`. It verifies every JD has ranks 1–1,000, descending score/candidate-ID ordering and exact top-30 selection.
Actual shortlisted-candidate overlaps: `{"a038c776c96041c49e171040fdf8fd1a|42a7509a9ecd4fc3876a6367e93225fc": 1, "a038c776c96041c49e171040fdf8fd1a|cefbdecad0d84d969d6747a6846371fe": 0, "a038c776c96041c49e171040fdf8fd1a|d89c10d5f4c84639a46000c04324d301": 2, "cefbdecad0d84d969d6747a6846371fe|42a7509a9ecd4fc3876a6367e93225fc": 0, "cefbdecad0d84d969d6747a6846371fe|d89c10d5f4c84639a46000c04324d301": 0, "d89c10d5f4c84639a46000c04324d301|42a7509a9ecd4fc3876a6367e93225fc": 0}`.
Review reasons (one result can have several): `{"HIGH_OR_CRITICAL_FLAG_REQUIRES_REVIEW": 1, "INVALID_CITATIONS:experience": 4, "INVALID_CITATIONS:flags[0]": 1, "MISSING_CITATIONS:education": 1, "MISSING_CITATIONS:flags[0]": 2, "MISSING_CITATIONS:flags[1]": 3, "MISSING_CITATIONS:flags[2]": 1, "MISSING_CITATIONS:projects": 4, "MISSING_EVIDENCE:EDUCATION": 1, "MISSING_INFORMATION": 67}`. Failure codes: `{"INVALID_LLM_OUTPUT": 2, "PROVIDER_ERROR": 1}`. A terminal review/failure is accounted for but is not a successful ranking result.
Actual ranked-candidate overlaps: `{}`. Empty/unavailable rankings do not demonstrate actual overlap.
A separate controlled live PostgreSQL/API fixture used two shared candidates with JD-A scores 90/70 and JD-B scores 60/80. Both candidates appeared in both lists, with reversed order, exactly four stored pairs, and no raw evidence in API responses. This tests ranking isolation, not model quality; zero provider calls. `live-overlap-results.json` contains exact inputs/outputs.
Live API replay: reusing capacity-final-20260926-v4 returned the same campaign with created=false; re-uploading the same ZIP returned the saved intake report. Counts remained 1,000 CVs and 4,000 pairs. Exact inputs/outputs: `api-idempotency-results.json`.
Duplicate stored pair groups at latest sample: `[{"duplicate_groups": 0}]`.

## Runtime, queue waits and resource measurements

Stage milestones (poll resolution ~30 seconds): `{"all_jds_shortlisted_seconds": 1491.453, "stage0_terminal_seconds": 814.75}`.
Peak sampled queue depths: `{"control": 113, "evaluation": 3, "ocr": 972, "retrieval": 1, "screening": 0}`; unacked deliveries: 132.
Total sampled container memory MiB: `{"count": 215, "max": 6926.8, "mean": 6564.973, "p50": 6614.9, "p95": 6801.785}`.

| Container | CPU p95 / max (%) | Memory p95 / max (MiB) |
|---|---:|---:|
| docker-worker-1 | 0.46 / 0.6 | 407.05 / 490.8 |
| docker-ocr-worker-1 | 97.633 / 368.56 | 617.9 / 713.1 |
| docker-retrieval-worker-1 | 866.404 / 950.77 | 3636.531 / 3658.752 |
| docker-evaluation-worker-1 | 63.98 / 247.83 | 773.575 / 1180.672 |
| docker-control-worker-1 | 56.381 / 164.79 | 534.38 / 598.7 |
| docker-beat-1 | 0.0 / 2.98 | 384.61 / 463.7 |
| docker-web-1 | 1.778 / 10.7 | 893.22 / 1051.648 |
| docker-postgres-1 | 66.517 / 113.46 | 270.96 / 306.5 |
| docker-redis-1 | 7.044 / 15.62 | 21.893 / 24.89 |

| Task | Queue wait p50 / p95 / max (ms) | Runtime p50 / p95 / max (ms) |
|---|---:|---:|
| campaign.stage2_pair | 177.817 / 841.603 / 2053.744 | 891.576 / 1938.778 / 5946.919 |
| campaign.coordinate | 140.016 / 57369.741 / 3636776.612 | 179.017 / 247.309 / 6436.613 |
| campaign.stage0 | 544586.722 / 720411.118 / 775724.514 | 460.98 / 1821.196 / 7979.564 |
| screening.recover_runs | 5.111 / 155.811 / 4106.223 | 48.12 / 104.539 / 10460.479 |
| campaign.recover_stage0 | 3.479 / 303.6 / 10526.313 | 47.755 / 126.759 / 152.935 |
| campaign.stage3_pair | 17.062 / 3389.252 / 327081.903 | 100.509 / 5261.837 / 327164.586 |
| jd.expire_drafts | 6.944 / 6.944 / 6.944 | 48.074 / 48.074 / 48.074 |

Database telemetry window: 2026-09-26T10:33:56.321427+00:00–2026-09-26T12:29:39.269922+00:00; peak connections 9.
Global counter deltas: `{"blks_hit": 61542889, "blks_read": 151536, "deadlocks": 0, "temp_bytes": 0, "xact_commit": 91623, "xact_rollback": 7311}`. Rollbacks include read-only session closures; they are not a count of failed candidate evaluations.

## Provider requests, throttling and cost

Captured provider events: `{"200": 111, "404": 2, "429": 28, "503": 12, "504": 1, "requests": 155, "responses_with_cost": 49, "responses_with_usage": 111}`.
Captured token usage: `{"input_tokens": 175098, "output_tokens": 298276, "total_tokens": 473374}`. Latency ms: `{"count": 154, "max": 327035.691, "mean": 22224.676, "p50": 4261.208, "p95": 89334.343}`.
Provider/model breakdown: `[{"counts": {"200": 56, "429": 19, "requests": 75, "responses_with_usage": 56}, "latency_ms": {"count": 75, "max": 7051.45, "mean": 2835.535, "p50": 3352.598, "p95": 4773.176}, "provider": "groq", "reported_cost_sum": null, "requested_model": "openai/gpt-oss-120b", "resolved_model_counts": {}, "tokens": {"input_tokens": 104448, "output_tokens": 77831, "total_tokens": 182279}, "uncached_list_price_estimate_usd": 0.062366}, {"counts": {"404": 2, "requests": 2}, "latency_ms": {"count": 2, "max": 447.06, "mean": 263.334, "p50": 263.334, "p95": 428.687}, "provider": "openrouter", "reported_cost_sum": null, "requested_model": "meta-llama/llama-3.3-70b-instruct:free", "resolved_model_counts": {}, "tokens": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}, "uncached_list_price_estimate_usd": null}, {"counts": {"200": 49, "429": 9, "504": 1, "requests": 60, "responses_with_cost": 49, "responses_with_usage": 49}, "latency_ms": {"count": 59, "max": 327035.691, "mean": 52050.028, "p50": 45920.46, "p95": 107102.039}, "provider": "openrouter", "reported_cost_sum": 0, "requested_model": "openrouter/free", "resolved_model_counts": {"dots-studio/dots-3-note-preview:free": 15, "liquid/lfm-2.5-2.6b:free": 11, "nvidia/nemotron-3-super-120b-a12b:free": 17, "qwen/qwen3.8-27b:free": 1}, "tokens": {"input_tokens": 62881, "output_tokens": 210162, "total_tokens": 273043}, "uncached_list_price_estimate_usd": 0}, {"counts": {"200": 6, "503": 12, "requests": 18, "responses_with_usage": 6}, "latency_ms": {"count": 18, "max": 20493.214, "mean": 7692.035, "p50": 6639.844, "p95": 13793.826}, "provider": "gemini", "reported_cost_sum": null, "requested_model": "gemini-3.6-flash", "resolved_model_counts": {"gemini-3.6-flash": 6}, "tokens": {"input_tokens": 7769, "output_tokens": 10283, "total_tokens": 18052}, "uncached_list_price_estimate_usd": 0.044388}]`.
Provider-reported partial cost sum: `0`; a complete actual bill remains unavailable if any successful response omits cost. OpenRouter free responses can report zero; Groq does not expose actual billing here.
Provider-reported actual cost: `None` (not available when null). Uncached list-price equivalent for captured usage: `0.106754` USD.
The list-price estimate uses $0.15/M input and $0.60/M output; it is not billed free-plan spend. Missing usage on failed requests is not estimated. [Groq model pricing](https://console.groq.com/docs/model/openai/gpt-oss-120b).
This provider window covers the full campaign Stage 3 events. Prior JD extraction, diagnostic/probe usage and other account consumers are not included; a total account bill is unavailable.
A representative throttle probe confirmed HTTP 429 on tokens per day (TPD): limit 200,000, used 199,796, requested 3,658, Retry-After 1,493 seconds. Its TPM bucket was full at 8,000. A preceding tiny 97-token probe succeeded; this does not imply enough daily allowance for a full evaluation. Exact input/output: `provider-campaign-limit-results.json`, `provider-quota-limit-results.json`. Probe usage is separate from worker totals.
OpenRouter subsequently confirmed HTTP 429 free-models-per-day with x-ratelimit-limit=50, remaining=0 and reset=1790467200000 (2026-09-27 00:00 UTC / 05:45 Nepal time). Fourteen selected pairs were unfinished when this limit was confirmed. Exact synthetic input/output: openrouter-daily-limit-results.json. An earlier tiny probe used 51 tokens at reported cost zero; both probes are separate from worker totals.
The initial 6,000-token reservation allowed about one evaluation/minute; the tuned 4,000-token reservation permits two, with unchanged 8,000 TPM budget. Reservations estimate usage, so longer outputs can still cause provider 429s. At roughly 3,000+ tokens/request, 120 responses can exceed a 200,000-token daily free quota. No paid upgrade or spending was authorized.

## Recovery, authorization and changes validated

Live isolated retry/auth probe: missing/invalid token ->401; valid recruiter /auth/me ->200; cross-owner campaign ->404; other owner approved JDs ->empty. Injected 429 Retry-After=120 left the pair SHORTLISTED with 119.855 seconds remaining. Duplicate delivery ->LEASE_LOST. Simulated elapsed-cooldown retry ->SUCCESS; duplicate ->LEASE_LOST; one stored pair, two attempts/two injected calls. Fixture and limiter keys removed; zero real provider calls. Exact evidence: `live-fault-auth-results.json`.
Fixed bottlenecks: CV readability no longer depends on a generic vocabulary or capitalization. Source audit reduced pages requiring OCR from 1,539 to 43. Corrupt/image-only text still triggers bounded OCR.
Stage 0 recovery now excludes broker queued/unacked IDs and recent reservations, preventing repeated re-publication of healthy backlog. Terminal updates retain fenced leases and uniqueness constraints.
Retrieval was increased from one process to three with bounded thread counts. An isolated 12-pair benchmark found eight threads 0.5951s, two 0.7510s, one 1.0964s with identical first score; the campaign improvement comes from process parallelism, not a claim that fewer threads alone are faster.
Provider admission was reduced from the incorrect 120,000 TPM default to the measured free-plan 8,000 TPM. Safe task/provider events capture queue wait, runtime, status and usage without raw messages, credentials or CV text.
At 2026-09-26 10:59:30 UTC the evaluation worker was gracefully recreated with Redis-backed delayed-wakeup coalescing. Measured control-queue bursts near 90 tasks came from per-pair cooldown timers. The fix publishes one timer per campaign/window and retains the 30-second beat recovery path; publication failure releases the coalescing key. Pair retry deadlines and attempt counts are unchanged.
The deployed isolated retry/auth probe was repeated successfully after coalescing. A 120-notification burst against live Redis produced exactly one scheduled timer with a 60-second TTL; retry and duplicate-delivery results remained correct. Evidence: `live-fault-auth-coalescing-results.json`. Its first invocation immediately after recreation hit transient Docker DNS resolution failure; a service-DNS check and rerun passed. No campaign processing failure was observed from that probe failure.
Privacy fixes already applied: repeated page-header names, phone extensions, and complete first-page contact lines; retrieval cache identity advanced to pii-mask-v4. Earlier trial derivatives with name/phone leaks were repaired and affected cached documents removed.
At 2026-09-26 11:43:33 UTC the evaluation worker was recreated with a whole-request 180-second deadline, an 8,192-token OpenRouter completion cap, and resolved-model metadata. A prior HTTP 200 took 327.036 seconds because the old inactivity timeout did not bound wall time. Two interrupted leases were requeued after stopping the worker; existing results and attempt counts were preserved. Evidence: deadline-lease-resume.json. Thirty-eight relevant deadline/evidence/Stage 3 tests passed in 19.91 seconds; six deadline/measurement checks subsequently passed in 0.58 seconds.
At 2026-09-26 11:49:01 UTC the live evaluation pool was grown from one to two processes without interrupting requests. The shared global inflight cap remains two, with unchanged token/request admission. Compose now exposes EVALUATION_WORKER_CONCURRENCY and docker/.env sets two for future recreations. Evidence: evaluation-pool-grow.json. The original one-process pool serialized slow free-router responses despite two claimed leases.
At 2026-09-26 12:22:31 UTC the worker resumed with persisted retries for HTTP 500/502/503/504 and request timeouts/transport failures. These now park selections with PROVIDER_TRANSIENT_FAILURE and exhaust at five attempts with PROVIDER_TRANSIENT_RETRY_EXHAUSTED. HTTP 429 keeps its separate rate-limit codes. Two observed Gemini 503 failures were requeued after preserving their failed snapshots in gemini-transient-resume.json; earlier 106 terminal outcomes and all attempt counts were retained. Forty-six relevant checks passed in 12.04 seconds after updating the expected transient exception type.
The deployed isolated 503/auth fixture returned TRANSIENT_RETRY, a 119.837-second persisted cooldown, then SUCCESS on the second injected call; duplicate deliveries returned LEASE_LOST both times. Exactly one pair was stored, 120 wakeups produced one timer, and all fixture records were removed. Zero real provider requests. Evidence: live-transient-auth-results.json. This fixes the earlier non-429 transient retry gap; malformed model output and non-retryable 4xx responses still become terminal failures.
Regression evidence before wakeup coalescing: 258 passed, 11 skipped, one existing deprecation warning, 98.30 seconds. After coalescing, 10 relevant wakeup/Stage 3/measurement checks passed in 28.24 seconds. The duplicate-log measurement check also passed. Host tests overlapped portions of the campaign and may add CPU contention. No formatting errors from git diff --check.

## Superseded trials and remaining limits

Diagnostic 0dcf5e5a-2dcb-4042-9e1f-51288a1687ba ran with mid-flight fixes/restarts and original OCR failures; runtime 1,526.775s is not an accepted capacity baseline. It recorded real provider 429s.
Trials 9b5edd3c-36d5-4d3a-b0c1-4dae0d9d61e1 (names), ec8bbc11-e38c-4d06-99ec-adeecf426c33 (phone extensions), and f6486035-18da-4f53-a4ab-26dd4290ee9a (contact locations) were deliberately closed before Stage 3. Each retained exactly 4,000 accounted terminal outcomes. Their timings must not be combined with the full run.
Per the user instruction, deeper privacy/anonymization investigation is deferred. Historical source-location retention and residual indirect identifiers need follow-up. Do not interpret source-contact checks as complete anonymization or a privacy certification.
API exposure is loopback-only. PostgreSQL currently publishes 5432 on all host interfaces; password authentication is configured, but firewall/network isolation was not tested. Tighten that binding before external deployment.
One campaign on this host is measured; multi-campaign capacity, strict SLA, horizontal scale, OCR-heavy real-world archives, and sustained soak/restart failure rates are not established.
Daily free-plan quota has been reached during the full campaign. A 1,493-second Retry-After replenishes enough allowance for that request, not necessarily the entire remaining pool. Completion of every selected evaluation may require waiting into the next day. Five-attempt exhaustion can otherwise leave explicit quota-failure outcomes; successful full-pool model evaluation is not accepted while pairs remain pending.
The resumed phase uses a free auto-routing provider with different output and latency behavior. Resolved models are captured only from the deadline deployment onward. Requests now have a 180-second whole-request deadline; timeout, invalid output and completion-cap failures remain explicit outcomes. Interpret the total as a mixed-provider measured campaign, not a clean single-provider SLA.
Peaks are sampled and may miss spikes. Queue wait includes countdown/redelivery time. DB metrics are global and include API/observer probes. Admission reservations are conservative and do not refund actual usage; daily account quota is not centrally tracked.
Provider latency/429s/invalid or unsupported output may dominate turnaround and produce review/failure outcomes. Rankings contain SUCCESS only; REVIEW_REQUIRED and failures remain visible as separate outcomes.
OpenRouter daily-limit responses expose a millisecond x-ratelimit-reset header without Retry-After. Current retries honor Retry-After when present and otherwise use exponential backoff; they do not interpret that reset header or open a shared daily-quota circuit breaker. Repeated daily-limit retries can therefore consume the five-attempt policy without new model inference. Waiting for reset or switching to another authorized free provider is required for additional successful responses.
Evidence: `full-campaign.json`, `.intake.json`, `.rankings.json` when finalized; `full-telemetry.jsonl`; `full-worker-events.jsonl` and `full-evaluation-tuned-events.jsonl`; `full-measurements.json`; live fixture JSON. Artifacts may contain protected candidate identifiers and should remain access-controlled.
