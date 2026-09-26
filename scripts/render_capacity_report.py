"""Render measured campaign evidence without inferring successful completion."""
import json
from datetime import datetime
from pathlib import Path


def read(path):
    data=Path(path).read_bytes()
    return json.loads(data.decode('utf-16' if data.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig'))


def main():
    root=Path('artifacts/phase6')
    run=read(root/'full-campaign.json')
    measured=read(root/'full-measurements.json')
    accounting=run['accounting']
    status=run.get('status',run['samples'][-1]['status'])
    complete=status=='COMPLETED' and accounting['terminal_pairs']==4000
    db=measured['database_and_broker']
    provider=measured['provider']
    verification_path=root/'database-final-verification.json'
    if not verification_path.exists():
        verification_path=root/'database-progress-verification.json'
    verification=read(verification_path)['output'] if verification_path.exists() else {}
    campaign_times=verification.get('campaign',{})
    database_runtime=((datetime.fromisoformat(campaign_times['completed_at'])-datetime.fromisoformat(campaign_times['created_at'])).total_seconds()
                      if campaign_times.get('completed_at') else None)
    lines=['# Phase 6 measured capacity report (2026-09-26)','',
        f"**Campaign {status}; {'all 4,000 pairs terminal' if complete else 'completion is not yet accepted'}.**",
        f"Campaign ID: `{run['campaign_id']}`. Started `{run['started_at']}`.",
        f"Latest measured runtime: {measured['elapsed_seconds']:.3f} seconds. Intake: {run['intake_seconds']:.3f} seconds.",'',
        f"Database campaign runtime: `{database_runtime}` seconds (unavailable while pending); database completion timestamp: `{campaign_times.get('completed_at')}`. Observer elapsed time includes polling resolution and all provider switches/operator pauses.",'',
        '## Exact inputs and deployment','',
        r'Archive: `C:\Abhyudit_Files\Infinite\CV-Benchmark-Dataset-Generator\output\cvs\CV_1000.zip`.',
        '7,174,953 bytes; 1,000 unique PDFs; 8,816,430 expanded bytes; 1,634 pages, including 43 image-only pages in 30 scanned PDFs.',
        r'JD inputs: `C:\Abhyudit_Files\Infinite\CV-Benchmark-Dataset-Generator\output\jds\JD01.pdf` through `JD04.pdf`.',
        'Roles: Java Backend, C#/.NET Backend, QA Automation, Data Engineering. Minimum years: 3, 3, 2, 3. Explicit OR alternatives were corrected before approval; no hard degree filter.',
        'The approved immutable IDs and complete extraction/approval JSON are in `artifacts/phase6/jobs.json` and `jd01`–`jd04` artifacts. Dataset labels were not used in screening.',
        'Local Docker Compose on WSL2: PostgreSQL 16/pgvector, Redis 7, API on loopback:8000. Docker reports 16 CPUs and 8,130,854,912 bytes of RAM (7.572 GiB).',
        'Initially one OCR, evaluation, control and legacy process, plus beat/web; three retrieval processes with four OpenMP/MKL threads each. Evaluation was increased to two processes during the OpenRouter phase, and that setting persists for Gemini. No concurrent active evaluation campaigns.',
        'Secrets are read from `docker/.env`; credentials and prompts are excluded from this report.',
        'Groq free plan only, model `openai/gpt-oss-120b`. Configured admission: 30 requests/minute, 8,000 tokens/minute, global Stage 3 inflight <=2. Reservation initially 6,000 tokens/request; changed to 4,000 at 2026-09-26 10:51:11 UTC after observed responses used 2,901–3,418 tokens. Only evaluation-worker was gracefully recreated; this tuning time is included in measured runtime.',
        'At 2026-09-26 11:28 UTC the user switched both env files to OpenRouter, model openrouter/free, to resume the 64 unfinished selections. The evaluation worker was recreated. Compose initially omitted the model-variable mapping and used an obsolete default: two HTTP 404 attempts were retained as evidence and their existing pair rows requeued after fixing GROQ_MODEL/OPENROUTER_MODEL forwarding. Existing Groq results and all 4,000 pair rows were preserved. Evidence: provider-change-resume.json.',
        'OpenRouter account metadata confirms is_free_tier=true, model openrouter/free, and usage_daily=0 at the account probe. The API-key USD limit field is a spending cap, not a daily request allowance. This free tier is documented at 20 RPM/50 requests per day, with failed requests consuming that allowance. The router chooses compatible free models; it is not one fixed model. [Free router](https://openrouter.ai/openrouter/free), [free request limits](https://openrouter.ai/blog/tutorials/how-to-get-the-lowest-cost-llm-inference-on-openrouter/).',
        'After OpenRouter exhausted its daily free quota, the user configured Gemini and authorized switching the fourteen unfinished selections. At 2026-09-26 12:17 UTC, live settings confirmed gemini-3.6-flash, a 180-second deadline and two worker processes. Twelve waiting selections and two interrupted leases were released after stopping the prior worker, preserving all 106 terminal outcomes and attempt counts. Evidence: gemini-provider-resume.json and gemini-worker-settings.jsonl. GEMINI_MODEL is now forwarded through Compose; safe request/response telemetry includes thinking tokens, and SDK automatic retries are disabled so the campaign controls attempts.',
        'Gemini 3.6 Flash is officially eligible for free-tier standard inference; account-specific billing and quotas are unavailable from this key. No billing upgrade was performed. Gemini cost is not returned by the SDK. The list-price equivalent uses current standard paid rates $0.75/M input and $3.75/M output including thinking tokens; this is not billed campaign spend. [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing), [quota guidance](https://ai.google.dev/gemini-api/docs/rate-limits). Gemini adapter validation: 38 relevant tests passed in 12.71 seconds, followed by eight deadline/measurement checks in 0.60 seconds.',
        'The Gemini quota probe later confirmed RESOURCE_EXHAUSTED with GenerateRequestsPerDayPerProjectPerModel-FreeTier, limit 20, for gemini-3.6-flash. Its generic RetryInfo hint was 23 seconds, which does not replenish the full daily quota. A read-only model lookup confirmed gemini-3.7-flash supports generateContent through the same key; this model is also officially free-tier eligible. The four still-unfinished selections were released onto that model with all attempt counts and prior terminal outcomes preserved. No exhausted outcome was silently requeued. Evidence: gemini-quota-results.json, gemini-model-change-resume.json, gemini37-worker-settings.json. List-price-equivalent rates are the same $0.75/M input and $3.75/M output for both Gemini models through 2026-12-31; actual billing remains unavailable.',
        'A live quota-header probe returned 1,000 requests/day and 8,000 tokens/minute. Official published free-plan daily token quota is 200,000; actual remaining daily tokens were not exposed. [Groq rate limits](https://console.groq.com/docs/rate-limits).','',
        '## Pair accounting and rankings','',
        f"Accepted {run['accepted_count']} CVs; rejected {run['rejected_count']}. Expected {accounting['expected_pairs']}; stored {accounting['actual_pairs']}; terminal {accounting['terminal_pairs']}; selected {accounting['selected_pairs']}.",
        f"Reconciliation: `{accounting['reconciled']}`. Status counts: `{json.dumps(accounting['pair_statuses'],sort_keys=True)}`.",'',
        '| JD key | Status | Pairs | Stage 3 selected | Cap | Reconciled |',
        '|---|---|---:|---:|---:|---|']
    for jd in accounting['jds']:
        lines.append(f"| {jd['jd_key']} | {jd['status']} | {jd['pairs']} | {jd['selected']} | {jd['cap']} | {jd['reconciled']} |")
    lines.extend(['',f"Final ranking checks: `{json.dumps(run.get('ranking_checks',{}),sort_keys=True)}`.",
        f"Independent database check: {verification.get('unique_pair_ids','unavailable')} unique pair IDs; extracted-page/OCR totals `{json.dumps(verification.get('extraction',{}),sort_keys=True)}`. It verifies every JD has ranks 1–1,000, descending score/candidate-ID ordering and exact top-30 selection.",
        f"Actual shortlisted-candidate overlaps: `{json.dumps(verification.get('selected_candidate_overlap',{}),sort_keys=True)}`.",
        f"Actual shared candidates have separate stored evaluation records: `{json.dumps(verification.get('shared_selected_candidate_evaluations',[]),sort_keys=True)}`. The final SUCCESS-only lists may have no shared candidates even when shortlist/evaluation records overlap.",
        f"Review reasons (one result can have several): `{json.dumps(verification.get('review_reason_counts',{}),sort_keys=True)}`. Failure codes: `{json.dumps(verification.get('failure_codes',{}),sort_keys=True)}`. A terminal review/failure is accounted for but is not a successful ranking result.",
        f"Actual ranked-candidate overlaps: `{json.dumps(run.get('ranking_overlap_counts',{}),sort_keys=True)}`. Empty/unavailable rankings do not demonstrate actual overlap.",
        'A separate controlled live PostgreSQL/API fixture used two shared candidates with JD-A scores 90/70 and JD-B scores 60/80. Both candidates appeared in both lists, with reversed order, exactly four stored pairs, and no raw evidence in API responses. This tests ranking isolation, not model quality; zero provider calls. `live-overlap-results.json` contains exact inputs/outputs.',
        'Live API replay: reusing capacity-final-20260926-v4 returned the same campaign with created=false; re-uploading the same ZIP returned the saved intake report. Counts remained 1,000 CVs and 4,000 pairs. Exact inputs/outputs: `api-idempotency-results.json`.',
        f"Duplicate stored pair groups at latest sample: `{json.dumps(db.get('last_duplicates'))}`.",'',
        f"Independent stored-evaluation accounting: `{verification.get('unique_candidate_jd_pairs')}` unique candidate-JD pairs, `{verification.get('persisted_stage3_snapshots')}` Stage 3 snapshots and `{verification.get('success_or_review_without_snapshot')}` successful/review outcomes missing a snapshot. Legacy evaluation-table rows created during this campaign window: `{verification.get('legacy_evaluation_rows_in_campaign_window')}`; campaign evaluations are stored on their unique pair rows.",'',
        '## Runtime, queue waits and resource measurements','',
        f"Stage milestones (poll resolution ~30 seconds): `{json.dumps(run.get('milestones',{}),sort_keys=True)}`.",
        f"Peak sampled queue depths: `{json.dumps(db.get('peak_queue_depths',{}),sort_keys=True)}`; unacked deliveries: {db.get('peak_unacked_deliveries')}.",
        f"Total sampled container memory MiB: `{json.dumps(measured['total_container_memory_mib'],sort_keys=True)}`.",'',
        '| Container | CPU p95 / max (%) | Memory p95 / max (MiB) |', '|---|---:|---:|'])
    for name,metrics in measured['containers'].items():
        cpu,mem=metrics['cpu_percent'],metrics['memory_mib']
        lines.append(f"| {name} | {cpu['p95']} / {cpu['max']} | {mem['p95']} / {mem['max']} |")
    lines.extend(['','| Task | Queue wait p50 / p95 / max (ms) | Runtime p50 / p95 / max (ms) |','|---|---:|---:|'])
    for name,metrics in measured['tasks'].items():
        def show(key):
            value=metrics.get(key)
            return 'unavailable' if not value else f"{value['p50']} / {value['p95']} / {value['max']}"
        lines.append(f"| {name} | {show('queue_wait_ms')} | {show('runtime_ms')} |")
    lines.extend(['',f"Database telemetry window: {db.get('window_start')}–{db.get('window_end')}; peak connections {db.get('peak_connections')}.",
        f"Global counter deltas: `{json.dumps(db.get('counter_deltas',{}),sort_keys=True)}`. Rollbacks include read-only session closures; they are not a count of failed candidate evaluations.",'',
        '## Provider requests, throttling and cost','',
        f"Captured provider events: `{json.dumps(provider['counts'],sort_keys=True)}`.",
        f"Captured token usage: `{json.dumps(provider['tokens'],sort_keys=True)}`. Latency ms: `{json.dumps(provider['latency_ms'],sort_keys=True)}`.",
        f"Provider/model breakdown: `{json.dumps(provider.get('by_provider_model',[]),sort_keys=True)}`.",
        f"Provider-reported partial cost sum: `{provider.get('reported_partial_cost_sum')}`; a complete actual bill remains unavailable if any successful response omits cost. OpenRouter free responses can report zero; Groq does not expose actual billing here.",
        f"Provider-reported actual cost: `{provider['provider_reported_cost']}` (not available when null). Uncached list-price equivalent for captured usage: `{provider['uncached_list_price_estimate_usd']}` USD.",
        'The list-price estimate uses $0.15/M input and $0.60/M output; it is not billed free-plan spend. Missing usage on failed requests is not estimated. [Groq model pricing](https://console.groq.com/docs/model/openai/gpt-oss-120b).',
        'This provider window covers the full campaign Stage 3 events. Prior JD extraction, diagnostic/probe usage and other account consumers are not included; a total account bill is unavailable.',
        'A representative throttle probe confirmed HTTP 429 on tokens per day (TPD): limit 200,000, used 199,796, requested 3,658, Retry-After 1,493 seconds. Its TPM bucket was full at 8,000. A preceding tiny 97-token probe succeeded; this does not imply enough daily allowance for a full evaluation. Exact input/output: `provider-campaign-limit-results.json`, `provider-quota-limit-results.json`. Probe usage is separate from worker totals.',
        'OpenRouter subsequently confirmed HTTP 429 free-models-per-day with x-ratelimit-limit=50, remaining=0 and reset=1790467200000 (2026-09-27 00:00 UTC / 05:45 Nepal time). Fourteen selected pairs were unfinished when this limit was confirmed. Exact synthetic input/output: openrouter-daily-limit-results.json. An earlier tiny probe used 51 tokens at reported cost zero; both probes are separate from worker totals.',
        'The initial 6,000-token reservation allowed about one evaluation/minute; the tuned 4,000-token reservation permits two, with unchanged 8,000 TPM budget. Reservations estimate usage, so longer outputs can still cause provider 429s. At roughly 3,000+ tokens/request, 120 responses can exceed a 200,000-token daily free quota. No paid upgrade or spending was authorized.','',
        '## Recovery, authorization and changes validated','',
        'Live isolated retry/auth probe: missing/invalid token ->401; valid recruiter /auth/me ->200; cross-owner campaign ->404; other owner approved JDs ->empty. Injected 429 Retry-After=120 left the pair SHORTLISTED with 119.855 seconds remaining. Duplicate delivery ->LEASE_LOST. Simulated elapsed-cooldown retry ->SUCCESS; duplicate ->LEASE_LOST; one stored pair, two attempts/two injected calls. Fixture and limiter keys removed; zero real provider calls. Exact evidence: `live-fault-auth-results.json`.',
        'Fixed bottlenecks: CV readability no longer depends on a generic vocabulary or capitalization. Source audit reduced pages requiring OCR from 1,539 to 43. Corrupt/image-only text still triggers bounded OCR.',
        'Stage 0 recovery now excludes broker queued/unacked IDs and recent reservations, preventing repeated re-publication of healthy backlog. Terminal updates retain fenced leases and uniqueness constraints.',
        'Retrieval was increased from one process to three with bounded thread counts. An isolated 12-pair benchmark found eight threads 0.5951s, two 0.7510s, one 1.0964s with identical first score; the campaign improvement comes from process parallelism, not a claim that fewer threads alone are faster.',
        'Provider admission was reduced from the incorrect 120,000 TPM default to the measured free-plan 8,000 TPM. Safe task/provider events capture queue wait, runtime, status and usage without raw messages, credentials or CV text.',
        'At 2026-09-26 10:59:30 UTC the evaluation worker was gracefully recreated with Redis-backed delayed-wakeup coalescing. Measured control-queue bursts near 90 tasks came from per-pair cooldown timers. The fix publishes one timer per campaign/window and retains the 30-second beat recovery path; publication failure releases the coalescing key. Pair retry deadlines and attempt counts are unchanged.',
        'The deployed isolated retry/auth probe was repeated successfully after coalescing. A 120-notification burst against live Redis produced exactly one scheduled timer with a 60-second TTL; retry and duplicate-delivery results remained correct. Evidence: `live-fault-auth-coalescing-results.json`. Its first invocation immediately after recreation hit transient Docker DNS resolution failure; a service-DNS check and rerun passed. No campaign processing failure was observed from that probe failure.',
        'Privacy fixes already applied: repeated page-header names, phone extensions, and complete first-page contact lines; retrieval cache identity advanced to pii-mask-v4. Earlier trial derivatives with name/phone leaks were repaired and affected cached documents removed.',
        'At 2026-09-26 11:43:33 UTC the evaluation worker was recreated with a whole-request 180-second deadline, an 8,192-token OpenRouter completion cap, and resolved-model metadata. A prior HTTP 200 took 327.036 seconds because the old inactivity timeout did not bound wall time. Two interrupted leases were requeued after stopping the worker; existing results and attempt counts were preserved. Evidence: deadline-lease-resume.json. Thirty-eight relevant deadline/evidence/Stage 3 tests passed in 19.91 seconds; six deadline/measurement checks subsequently passed in 0.58 seconds.',
        'At 2026-09-26 11:49:01 UTC the live evaluation pool was grown from one to two processes without interrupting requests. The shared global inflight cap remains two, with unchanged token/request admission. Compose now exposes EVALUATION_WORKER_CONCURRENCY and docker/.env sets two for future recreations. Evidence: evaluation-pool-grow.json. The original one-process pool serialized slow free-router responses despite two claimed leases.',
        'At 2026-09-26 12:22:31 UTC the worker resumed with persisted retries for HTTP 500/502/503/504 and request timeouts/transport failures. These now park selections with PROVIDER_TRANSIENT_FAILURE and exhaust at five attempts with PROVIDER_TRANSIENT_RETRY_EXHAUSTED. HTTP 429 keeps its separate rate-limit codes. Two observed Gemini 503 failures were requeued after preserving their failed snapshots in gemini-transient-resume.json; earlier 106 terminal outcomes and all attempt counts were retained. Forty-six relevant checks passed in 12.04 seconds after updating the expected transient exception type.',
        'The deployed isolated 503/auth fixture returned TRANSIENT_RETRY, a 119.837-second persisted cooldown, then SUCCESS on the second injected call; duplicate deliveries returned LEASE_LOST both times. Exactly one pair was stored, 120 wakeups produced one timer, and all fixture records were removed. Zero real provider requests. Evidence: live-transient-auth-results.json. This fixes the earlier non-429 transient retry gap; malformed model output and non-retryable 4xx responses still become terminal failures.',
        'Final targeted validation of provider deadlines, durable retries/fencing, evidence handling and measurement accounting: 48 passed in 15.55 seconds, with one existing deprecation warning. Measurement accounting covers both Gemini model prices and excludes provider activity after observed completion. git diff --check passed.',
        'Regression evidence before wakeup coalescing: 258 passed, 11 skipped, one existing deprecation warning, 98.30 seconds. After coalescing, 10 relevant wakeup/Stage 3/measurement checks passed in 28.24 seconds. The duplicate-log measurement check also passed. Host tests overlapped portions of the campaign and may add CPU contention. No formatting errors from git diff --check.','',
        '## Superseded trials and remaining limits','',
        'Diagnostic 0dcf5e5a-2dcb-4042-9e1f-51288a1687ba ran with mid-flight fixes/restarts and original OCR failures; runtime 1,526.775s is not an accepted capacity baseline. It recorded real provider 429s.',
        'Trials 9b5edd3c-36d5-4d3a-b0c1-4dae0d9d61e1 (names), ec8bbc11-e38c-4d06-99ec-adeecf426c33 (phone extensions), and f6486035-18da-4f53-a4ab-26dd4290ee9a (contact locations) were deliberately closed before Stage 3. Each retained exactly 4,000 accounted terminal outcomes. Their timings must not be combined with the full run.',
        'Per the user instruction, deeper privacy/anonymization investigation is deferred. Historical source-location retention and residual indirect identifiers need follow-up. Do not interpret source-contact checks as complete anonymization or a privacy certification.',
        'API exposure is loopback-only. PostgreSQL currently publishes 5432 on all host interfaces; password authentication is configured, but firewall/network isolation was not tested. Tighten that binding before external deployment.',
        'One campaign on this host is measured; multi-campaign capacity, strict SLA, horizontal scale, OCR-heavy real-world archives, and sustained soak/restart failure rates are not established.',
        'Daily free-plan quota has been reached during the full campaign. A 1,493-second Retry-After replenishes enough allowance for that request, not necessarily the entire remaining pool. Completion of every selected evaluation may require waiting into the next day. Five-attempt exhaustion can otherwise leave explicit quota-failure outcomes; successful full-pool model evaluation is not accepted while pairs remain pending.',
        'The resumed phase uses a free auto-routing provider with different output/latency behavior. Initial OpenRouter responses took about 49–105 seconds, versus ~3–5 seconds for Groq. Actual routed model identifiers are not captured by the current event schema. The HTTP timeout is an inactivity timeout, so request wall time can exceed the configured 45 seconds; an overall provider deadline remains a follow-up. Interpret the total as a mixed-provider measured campaign, not a clean single-provider SLA.',
        'Peaks are sampled and may miss spikes. Queue wait includes countdown/redelivery time. DB metrics are global and include API/observer probes. Admission reservations are conservative and do not refund actual usage; daily account quota is not centrally tracked.',
        'Provider latency/429s/invalid or unsupported output may dominate turnaround and produce review/failure outcomes. Rankings contain SUCCESS only; REVIEW_REQUIRED and failures remain visible as separate outcomes.',
        'Campaign Stage 3 passes max_provider_attempts=1: HTTP 429 receives persisted delayed retries, but non-429 transport/server failures (including the observed OpenRouter HTTP 504) become terminal PROVIDER_ERROR outcomes. Invalid JSON/schema output becomes INVALID_LLM_OUTPUT. General provider retries are therefore not demonstrated by the live 429 fixture; broader transient-error queue retry is a remaining resilience gap.',
        'OpenRouter daily-limit responses expose a millisecond x-ratelimit-reset header without Retry-After. Current retries honor Retry-After when present and otherwise use exponential backoff; they do not interpret that reset header or open a shared daily-quota circuit breaker. Repeated daily-limit retries can therefore consume the five-attempt policy without new model inference. Waiting for reset or switching to another authorized free provider is required for additional successful responses.',
        'Evidence: `full-campaign.json`, `.intake.json`, `.rankings.json` when finalized; `full-telemetry.jsonl`; `full-worker-events.jsonl` and `full-evaluation-tuned-events.jsonl`; `full-measurements.json`; live fixture JSON. Artifacts may contain protected candidate identifiers and should remain access-controlled.',''])
    lines = [
        ('The resumed phase uses a free auto-routing provider with different output and latency behavior. '
         'Resolved models are captured only from the deadline deployment onward. Requests now have a 180-second whole-request deadline; '
         'timeout, invalid output and completion-cap failures remain explicit outcomes. '
         'Interpret the total as a mixed-provider measured campaign, not a clean single-provider SLA.')
        if line.startswith('The resumed phase uses a free auto-routing provider') else line
        for line in lines
    ]
    lines = [line.replace('Groq free plan only,','Initial provider: Groq free plan,')
             for line in lines if not line.startswith('Campaign Stage 3 passes max_provider_attempts=1:')]
    if complete:
        lines = [
            ('The campaign closed with all 4,000 pairs terminal. Free quotas and provider 503s prevented a fully successful '
             '120-selection inference run; failed outcomes are explicit in the accounting. Groq daily tokens, OpenRouter daily requests '
             'and Gemini per-model free requests all constrained this run. This establishes accounting, caps, recovery and sampled host behavior, '
             'but does not qualify a clean single-provider turnaround SLA or a full successful evaluation capacity at these free limits.')
            if line.startswith('Daily free-plan quota has been reached') else line
            for line in lines
        ]
    Path('PHASE6_CAPACITY_REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({'report':'PHASE6_CAPACITY_REPORT.md','campaign_id':run['campaign_id'],'status':status,'completion_accepted':complete,'terminal_pairs':accounting['terminal_pairs']}))


if __name__=='__main__':
    main()
