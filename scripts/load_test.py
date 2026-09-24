"""Submit a bounded PDF batch and report latency; requires a running deployment."""
import argparse
import asyncio
import base64
import json
import os
import time
from pathlib import Path

import httpx


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--count", type=int, default=50)
    parser.add_argument("--concurrency", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.count <= 1000 or not 1 <= args.concurrency <= 20:
        parser.error("count must be 1-1000 and concurrency 1-20")
    document = base64.b64encode(args.pdf.read_bytes()).decode()
    job = json.loads(args.job.read_text(encoding="utf-8"))
    semaphore = asyncio.Semaphore(args.concurrency)
    latencies = []
    errors = 0
    completed = 0
    failed = 0
    run_ids = []
    start = time.perf_counter()
    headers = {"Authorization": "Bearer " + os.environ["API_TOKEN"]}
    async with httpx.AsyncClient(base_url=args.url, headers=headers, timeout=30) as client:
        async def submit(index):
            nonlocal errors
            async with semaphore:
                request = {"job_profile": job, "candidate_id": f"load-{index}", "pdf_base64": document,
                           "idempotency_key": f"load-{int(start)}-{index}"}
                before = time.perf_counter()
                try:
                    response = await client.post("/api/v1/screening/submit-pdf", json=request)
                    latencies.append(time.perf_counter() - before)
                    if response.status_code == 202:
                        run_ids.append(response.json()["run_id"])
                    else:
                        errors += 1
                except httpx.HTTPError:
                    errors += 1
        await asyncio.gather(*(submit(i) for i in range(args.count)))
        while run_ids and time.perf_counter() - start < 600:
            await asyncio.sleep(2)
            remaining = []
            for run_id in run_ids:
                try:
                    response = await client.get(f"/api/v1/screening/runs/{run_id}")
                    state = response.json()["status"] if response.status_code == 200 else None
                    if state == "COMPLETED":
                        completed += 1
                    elif state == "FAILED":
                        failed += 1
                    else:
                        remaining.append(run_id)
                except httpx.HTTPError:
                    remaining.append(run_id)
            run_ids = remaining
    ordered = sorted(latencies)
    p95 = ordered[max(0, int(len(ordered) * .95 + .9999) - 1)] if ordered else None
    print(json.dumps({"submitted": args.count, "request_errors": errors,
                      "completed": completed, "failed": failed, "unfinished": len(run_ids),
                      "submit_p95_seconds": p95, "elapsed_seconds": time.perf_counter() - start}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
