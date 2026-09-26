import asyncio
import json
from sqlalchemy import text
from app.models.database import AsyncSessionLocal, engine

async def main():
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(text("SELECT j.jd_key,c.source_filename,p.stage1_decision,p.stage2_rank,p.stage2_score,p.status,p.stage3_attempt_count,p.failure_code,p.composite_score FROM campaign_pairs p JOIN campaign_jds j ON p.jd_id=j.id JOIN campaign_cvs c ON p.cv_id=c.id WHERE p.campaign_id=:id ORDER BY j.jd_key,p.stage2_rank"), {'id': 'c2f3123a-ebec-4e69-a26b-ad123c82fb98'})).mappings().all()
    print(json.dumps([dict(row) for row in rows]))
    await engine.dispose()

asyncio.run(main())
