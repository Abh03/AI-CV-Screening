"""Read-only campaign/database/broker probe; run inside a deployment container."""
import asyncio
import json
import sys
from datetime import datetime, timezone

from redis.asyncio import Redis
from sqlalchemy import text
from app.config import settings
from app.models.database import AsyncSessionLocal


async def main():
    campaign_id = sys.argv[1]
    redis = Redis.from_url(settings.REDIS_URL)
    try:
        while True:
            async with AsyncSessionLocal() as db:
                queries = {
                    "database": "SELECT numbackends, xact_commit, xact_rollback, blks_read, blks_hit, deadlocks, temp_bytes FROM pg_stat_database WHERE datname=current_database()",
                    "activity": "SELECT state, wait_event_type, count(*) AS connections FROM pg_stat_activity WHERE datname=current_database() GROUP BY state,wait_event_type",
                    "pairs": "SELECT status,count(*) AS count,sum(attempt_count) AS retrieval_attempts,sum(stage3_attempt_count) AS stage3_attempts FROM campaign_pairs WHERE campaign_id=:id GROUP BY status",
                    "failures": "SELECT failure_code,count(*) AS count FROM campaign_pairs WHERE campaign_id=:id AND failure_code IS NOT NULL GROUP BY failure_code",
                    "privacy": "SELECT stage0_status,count(*) AS count,count(encrypted_pdf) AS retained_raw_pdfs FROM campaign_cvs WHERE campaign_id=:id GROUP BY stage0_status",
                    "duplicates": "SELECT count(*) AS duplicate_groups FROM (SELECT jd_id,cv_id FROM campaign_pairs WHERE campaign_id=:id GROUP BY jd_id,cv_id HAVING count(*)>1) d",
                    "campaign": "SELECT status,created_at,completed_at FROM campaigns WHERE id=:id",
                }
                result = {"at": datetime.now(timezone.utc).isoformat()}
                for name, query in queries.items():
                    rows = await db.execute(text(query), {"id": campaign_id})
                    result[name] = [dict(row) for row in rows.mappings()]
                result["queue_depths"] = {q: sum([await redis.llen(q + suffix) for suffix in ("", "\x06\x163", "\x06\x166", "\x06\x169")]) for q in ("ocr", "retrieval", "evaluation", "control", "screening")}
                result["unacked"] = await redis.hlen("unacked")
                print(json.dumps(result, default=str), flush=True)
                if result["campaign"][0]["status"] in ("COMPLETED", "FAILED"):
                    break
            await asyncio.sleep(20)
    finally:
        await redis.aclose()


asyncio.run(main())
