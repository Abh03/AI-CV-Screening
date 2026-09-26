"""Run and measure a real campaign through the authenticated HTTP API.

Use an archive of representative PDFs. The report contains aggregate metrics only.
Optional DATABASE_URL and REDIS_URL enable database attempt and queue metrics.
"""
import argparse
import asyncio
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx


TERMINAL = {"EXTRACTION_FAILED", "FILTER_REJECTED", "PROCESSING_FAILED",
            "CUTOFF_EXCLUDED", "SUCCESS", "REVIEW_REQUIRED", "EVALUATION_FAILED"}
SELECTED = {"SHORTLISTED", "STAGE3_RUNNING", "SUCCESS", "REVIEW_REQUIRED",
            "EVALUATION_FAILED"}
QUEUES = ("ocr", "retrieval", "evaluation", "control", "screening")


def reconcile(status, jds):
    counts = status["counts"]
    cvs = counts["cvs"]
    expected = cvs * counts["jds"]
    pairs = counts["pairs"]
    selected = sum(pairs.get(state, 0) for state in SELECTED)
    jd_checks = []
    for jd in jds["jds"]:
        states = jd["counts"]
        jd_selected = sum(states.get(state, 0) for state in SELECTED)
        jd_checks.append({"jd_key": jd["jd_key"], "status": jd["status"],
                          "pairs": sum(states.values()), "selected": jd_selected,
                          "cap": jd["stage3_cap"],
                          "reconciled": sum(states.values()) == cvs and jd_selected <= jd["stage3_cap"]})
    return {"expected_pairs": expected, "actual_pairs": sum(pairs.values()),
            "terminal_pairs": counts["terminal_pairs"], "selected_pairs": selected,
            "pair_statuses": pairs, "jds": jd_checks,
            "reconciled": (sum(pairs.values()) == expected
                           and all(jd["reconciled"] for jd in jd_checks)
                           and sum(jd["selected"] for jd in jd_checks) == selected)}


async def db_metrics(url, campaign_id):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(url, pool_size=1, max_overflow=0)
    try:
        async with engine.connect() as db:
            rows = (await db.execute(text("""
                SELECT COALESCE(SUM(attempt_count),0),
                       COALESCE(SUM(stage3_attempt_count),0),
                       COALESCE(SUM(CASE WHEN failure_code IN
                           ('PROVIDER_RATE_LIMITED','PROVIDER_RATE_LIMIT_EXHAUSTED')
                           THEN 1 ELSE 0 END),0)
                FROM campaign_pairs WHERE campaign_id = :id
            """), {"id": campaign_id})).one()
            load = (await db.execute(text("""
                SELECT numbackends, xact_commit, xact_rollback, blks_read, blks_hit
                FROM pg_stat_database WHERE datname = current_database()
            """))).one_or_none()
            return {"retrieval_attempts": rows[0], "stage3_attempts": rows[1],
                    "provider_rate_limit_pairs_current": rows[2],
                    "database_counters": dict(zip(
                        ("connections", "commits", "rollbacks", "blocks_read", "blocks_hit"), load)) if load else None}
    finally:
        await engine.dispose()


async def queue_depths(url):
    from redis.asyncio import Redis

    redis = Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
    try:
        return {queue: await redis.llen(queue) for queue in QUEUES}
    finally:
        await redis.aclose()


async def docker_resources():
    proc = await asyncio.create_subprocess_exec(
        "docker", "stats", "--no-stream", "--format", "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    stdout, _ = await proc.communicate()
    if proc.returncode:
        return None
    return [dict(zip(("container", "cpu", "memory"), line.split("|", 2)))
            for line in stdout.decode(errors="replace").splitlines() if line.count("|") == 2]


async def run(args):
    jobs = json.loads(args.jobs.read_text(encoding="utf-8"))
    if not isinstance(jobs, list) or not jobs or any(not isinstance(job, str) or not job for job in jobs):
        raise ValueError("jobs file must contain a nonempty JSON array of approved JD IDs")
    if not args.archive.is_file():
        raise ValueError("archive does not exist")
    token = os.environ.get("API_TOKEN")
    if not token and args.env_file:
        from dotenv import dotenv_values
        configured = dotenv_values(args.env_file).get("API_TOKENS_JSON")
        if configured:
            token = json.loads(configured)[0]["token"]
    if not token:
        raise ValueError("API_TOKEN is required")
    started = time.monotonic()
    report = {"started_at": datetime.now(timezone.utc).isoformat(),
              "archive_bytes": args.archive.stat().st_size, "jd_count": len(jobs),
              "samples": [], "limitations": [
                  "Stage boundary times are sampled at the polling interval; individual queue wait is not persisted.",
                  "Provider request, token, and billing usage are not exposed by campaign records; Stage 3 attempts are worker attempts and may differ from provider requests."]}
    headers = {"Authorization": f"Bearer {token}"}
    timeout = httpx.Timeout(args.request_timeout, connect=10)
    async with httpx.AsyncClient(base_url=args.url, headers=headers, timeout=timeout) as client:
        ready = await client.get("/ready")
        ready.raise_for_status()
        key = args.idempotency_key or f"capacity-{uuid4()}"
        created = await client.post("/api/v1/campaigns", json={
            "approved_jd_ids": jobs, "idempotency_key": key})
        created.raise_for_status()
        campaign_id = created.json()["campaign_id"]
        report["campaign_id"] = campaign_id
        before_upload = time.monotonic()
        async def archive_chunks():
            with args.archive.open("rb") as archive:
                while chunk := archive.read(1024 * 1024):
                    yield chunk

        uploaded = await client.post(f"/api/v1/campaigns/{campaign_id}/archive",
                                     content=archive_chunks(),
                                     headers={"Content-Type": "application/zip"})
        uploaded.raise_for_status()
        intake = uploaded.json()
        report["intake_seconds"] = round(time.monotonic() - before_upload, 3)
        report["accepted_count"] = intake["accepted_count"]
        report["rejected_count"] = intake["rejected_count"]
        report["rejection_codes"] = dict(Counter(item["code"] for item in intake["rejected"]))
        if not intake["accepted_count"]:
            raise RuntimeError("Archive accepted no PDFs")
        milestones = {}
        last_status = None
        while True:
            status_response, jds_response = await asyncio.gather(
                client.get(f"/api/v1/campaigns/{campaign_id}"),
                client.get(f"/api/v1/campaigns/{campaign_id}/jds"))
            status_response.raise_for_status()
            jds_response.raise_for_status()
            status, jds = status_response.json(), jds_response.json()
            last_status = status["status"]
            elapsed = round(time.monotonic() - started, 3)
            accounting = reconcile(status, jds)
            if (not accounting["reconciled"] or accounting["expected_pairs"]
                    != intake["accepted_count"] * len(jobs)):
                raise RuntimeError("Campaign pair counts or JD shortlist caps do not reconcile")
            if status["stage0"].get("PENDING", 0) + status["stage0"].get("RUNNING", 0) == 0:
                milestones.setdefault("stage0_terminal_seconds", elapsed)
            if all(jd["status"] in ("SHORTLISTED", "COMPLETED", "FAILED") for jd in jds["jds"]):
                milestones.setdefault("all_jds_shortlisted_seconds", elapsed)
            sample = {"elapsed_seconds": elapsed, "status": last_status,
                      "stage0": status["stage0"], "pairs": accounting["pair_statuses"]}
            if args.redis_url:
                try:
                    sample["queue_depths"] = await queue_depths(args.redis_url)
                except Exception:
                    report["limitations"].append("Redis queue depth sampling failed")
                    args.redis_url = None
            if args.docker_stats:
                sample["containers"] = await docker_resources()
            report["samples"].append(sample)
            if last_status in ("COMPLETED", "FAILED") or elapsed >= args.timeout:
                break
            await asyncio.sleep(args.poll)
        report["elapsed_seconds"] = elapsed
        report["status"] = last_status
        report["milestones"] = milestones
        report["accounting"] = accounting
        if args.database_url:
            try:
                report["database"] = await db_metrics(args.database_url, campaign_id)
            except Exception:
                report["limitations"].append("Database attempt sampling failed")
        if last_status == "COMPLETED":
            rankings = {}
            for jd in jds["jds"]:
                response = await client.get(f"/api/v1/campaigns/{campaign_id}/jds/{jd['jd_key']}/rankings")
                response.raise_for_status()
                rankings[jd["jd_key"]] = response.json()["total"]
            report["ranking_counts"] = rankings
        if report["samples"]:
            depths = [sample["queue_depths"] for sample in report["samples"] if "queue_depths" in sample]
            if depths:
                report["peak_queue_depths"] = {queue: max(sample[queue] for sample in depths) for queue in QUEUES}
        report["samples"] = report["samples"][::max(1, args.report_sample_stride)]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "samples"}, indent=2))
    if last_status != "COMPLETED" or accounting["terminal_pairs"] != accounting["expected_pairs"]:
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--jobs", type=Path, required=True, help="JSON array of approved JD version IDs")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--idempotency-key")
    parser.add_argument("--env-file", type=Path, help="Read the first API token from a local Compose env file")
    parser.add_argument("--report", type=Path, default=Path("campaign-load-report.json"))
    parser.add_argument("--poll", type=float, default=5)
    parser.add_argument("--timeout", type=float, default=7200)
    parser.add_argument("--request-timeout", type=float, default=300)
    parser.add_argument("--report-sample-stride", type=int, default=1)
    parser.add_argument("--redis-url", default=os.environ.get("REDIS_URL"))
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--docker-stats", action="store_true")
    args = parser.parse_args()
    if args.poll <= 0 or args.timeout <= 0 or args.report_sample_stride < 1:
        parser.error("poll, timeout, and report sample stride must be positive")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
