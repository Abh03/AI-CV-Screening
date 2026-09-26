"""Summarize sampled resource metrics and safe task/provider events."""
import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from datetime import datetime, timedelta


def read_text(path):
    raw = path.read_bytes() if path.exists() else b""
    return raw.decode("utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig", errors="replace")


def records(path):
    for line in read_text(path).splitlines():
        start = line.find("{")
        if start < 0:
            continue
        try:
            yield json.loads(line[start:])
        except json.JSONDecodeError:
            continue


def distribution(values):
    values = sorted(values)
    if not values:
        return None
    def percentile(p):
        offset = (len(values) - 1) * p
        low = int(offset)
        high = min(low + 1, len(values) - 1)
        return round(values[low] + (values[high] - values[low]) * (offset - low), 3)
    return {"count": len(values), "mean": round(sum(values) / len(values), 3),
            "p50": percentile(.5), "p95": percentile(.95), "max": round(values[-1], 3)}


def memory_mib(value):
    match = re.match(r"([\d.]+)([A-Za-z]+)", value.replace(" ", ""))
    if not match:
        return 0
    amount, unit = match.groups()
    return float(amount) * {"B": 1 / 1048576, "KiB": 1 / 1024, "MiB": 1,
                           "GiB": 1024, "kB": 1000 / 1048576,
                           "MB": 1000000 / 1048576, "GB": 1000000000 / 1048576}.get(unit, 0)


def summarize(report, telemetry, events):
    samples = report.get("samples", [])
    containers = defaultdict(lambda: {"cpu": [], "memory": []})
    total_memory = []
    for sample in samples:
        used = 0
        for row in sample.get("containers") or []:
            cpu = float(row["cpu"].strip("%"))
            memory = memory_mib(row["memory"].split("/")[0])
            containers[row["container"]]["cpu"].append(cpu)
            containers[row["container"]]["memory"].append(memory)
            used += memory
        if used:
            total_memory.append(used)
    tasks = defaultdict(lambda: {"queue_wait_ms": [], "runtime_ms": []})
    provider = Counter()
    latencies = []
    tokens = Counter()
    reported_costs = []
    models = set()
    provider_groups = defaultdict(lambda: {'counts':Counter(),'tokens':Counter(),'latencies':[],'costs':[],'resolved_models':Counter()})
    seen_events = set()
    duplicate_events = 0
    started = report["started_at"]
    elapsed = report.get('elapsed_seconds')
    end = ((datetime.fromisoformat(started)+timedelta(seconds=elapsed)).isoformat()
           if report.get('status') in {'COMPLETED','FAILED'} and elapsed is not None else None)
    if end:
        telemetry = [sample for sample in telemetry if started <= sample['at'] <= end]
    for event in events:
        if event.get("timestamp", "") < started:
            continue
        if end and event.get('timestamp','') > end:
            continue
        if event.get('event') not in {'task_started','task_finished','provider_request','provider_response'}:
            continue
        identity = json.dumps(event, sort_keys=True)
        if identity in seen_events:
            duplicate_events += 1
            continue
        seen_events.add(identity)
        stage = event.get("stage", "")
        group = provider_groups[(event.get('provider','').lower(),event.get('model'))] if event.get('event','').startswith('provider_') else None
        if event.get("event") == "task_started" and "queue_wait_ms" in event:
            tasks[stage]["queue_wait_ms"].append(event["queue_wait_ms"])
        elif event.get("event") == "task_finished" and "task_runtime_ms" in event:
            tasks[stage]["runtime_ms"].append(event["task_runtime_ms"])
        elif event.get("event") == "provider_request":
            provider["requests"] += 1
            group['counts']['requests'] += 1
        elif event.get("event") == "provider_response":
            models.add((event.get("provider", "").lower(), event.get("model")))
            provider[str(event["status"])] += 1
            group['counts'][str(event['status'])] += 1
            if event.get('resolved_model'):
                group['resolved_models'][event['resolved_model']] += 1
            latencies.append(event["latency_ms"])
            group['latencies'].append(event['latency_ms'])
            if "total_tokens" in event:
                provider["responses_with_usage"] += 1
                group['counts']['responses_with_usage'] += 1
            for field in ("input_tokens", "output_tokens", "total_tokens"):
                tokens[field] += event.get(field, 0)
                group['tokens'][field] += event.get(field,0)
            if "cost" in event:
                reported_costs.append(event["cost"])
                provider['responses_with_cost'] += 1
                group['counts']['responses_with_cost'] += 1
                group['costs'].append(event['cost'])
    group_outputs = []
    for (name,model),group in provider_groups.items():
        estimate = ((group['tokens']['input_tokens']*.15+group['tokens']['output_tokens']*.60)/1000000
                    if (name,model)==('groq','openai/gpt-oss-120b') else
                    (group['tokens']['input_tokens']*.75+group['tokens']['output_tokens']*3.75)/1000000
                    if name=='gemini' and model in {'gemini-3.6-flash','gemini-3.7-flash'} else
                    0 if name=='openrouter' and model=='openrouter/free' else None)
        if not group['counts'].get('responses_with_usage'):
            estimate = None
        group_outputs.append({'provider':name,'requested_model':model,'counts':dict(group['counts']),
                              'resolved_model_counts':dict(group['resolved_models']),
                              'tokens':dict(group['tokens']),'latency_ms':distribution(group['latencies']),
                              'reported_cost_sum':sum(group['costs']) if group['costs'] else None,
                              'uncached_list_price_estimate_usd':round(estimate,6) if estimate is not None else None})
    priced_groups = [row for row in group_outputs if row['counts'].get('responses_with_usage')]
    estimated_total = (sum(row['uncached_list_price_estimate_usd'] for row in priced_groups)
                       if priced_groups and all(row['uncached_list_price_estimate_usd'] is not None for row in priced_groups) else None)
    database = None
    if telemetry:
        first, last = telemetry[0], telemetry[-1]
        before, after = first["database"][0], last["database"][0]
        database = {"window_start": first["at"], "window_end": last["at"],
                    "counter_deltas": {key: after[key] - before[key] for key in before if key != "numbackends"},
                    "peak_connections": max(s["database"][0]["numbackends"] for s in telemetry),
                    "peak_queue_depths": {q: max(s["queue_depths"][q] for s in telemetry)
                                          for q in first["queue_depths"]},
                    "peak_unacked_deliveries": max(s["unacked"] for s in telemetry),
                    "last_privacy": last["privacy"], "last_duplicates": last["duplicates"]}
    return {"campaign_id": report["campaign_id"],
            "status": report.get("status", samples[-1]["status"] if samples else "UNKNOWN"),
            "elapsed_seconds": report.get("elapsed_seconds", samples[-1]["elapsed_seconds"] if samples else None),
            "accounting": report.get("accounting"), "milestones": report.get("milestones"),
            "duplicate_captured_events_ignored": duplicate_events,
            "containers": {name: {"cpu_percent": distribution(rows["cpu"]),
                                    "memory_mib": distribution(rows["memory"])} for name, rows in containers.items()},
            "total_container_memory_mib": distribution(total_memory),
            "tasks": {name: {metric: distribution(values) for metric, values in rows.items()}
                      for name, rows in tasks.items()},
            "provider": {"counts": dict(provider), "latency_ms": distribution(latencies),
                         "tokens": dict(tokens),
                         "by_provider_model": group_outputs,
                         "provider_reported_cost": sum(reported_costs) if reported_costs and provider['responses_with_cost']==provider['responses_with_usage'] else None,
                         "reported_partial_cost_sum":sum(reported_costs) if reported_costs else None,
                         "uncached_list_price_estimate_usd": round(estimated_total,6) if estimated_total is not None else None,
                         "price_source": "https://console.groq.com/docs/model/openai/gpt-oss-120b",
                         "additional_price_sources": ["https://ai.google.dev/gemini-api/docs/pricing", "https://openrouter.ai/openrouter/free"],
                         "cost_limits": "List-price equivalent for captured usage only; actual free-plan billing is not exposed."},
            "database_and_broker": database,
            "measurement_limits": ["Resource peaks are sampled; short spikes can be missed.",
                "Queue wait is publication-to-start, including scheduled countdowns and redeliveries, not only broker list residence.",
                "DB counters are global and cover the telemetry window; probes and API polls contribute load.",
                "Worker/provider events are scoped by run time; no other active evaluation campaigns should share this window."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--telemetry", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--extra-events", type=Path, action="append", default=[],
                        help="Additional event streams; identical captured events are deduplicated")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from itertools import chain
    events = chain.from_iterable(records(path) for path in [args.events, *args.extra_events])
    result = summarize(json.loads(read_text(args.report)), list(records(args.telemetry)), events)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key not in {"containers", "tasks"}}, indent=2))


if __name__ == "__main__":
    main()
