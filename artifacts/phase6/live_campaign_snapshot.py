import asyncio,json
from sqlalchemy import text
from app.models.database import AsyncSessionLocal,engine
async def main():
    async with AsyncSessionLocal() as db:
        rows=(await db.execute(text("SELECT j.jd_key,p.status,count(*) AS pairs,sum(p.stage3_attempt_count) AS stage3_attempts FROM campaign_pairs p JOIN campaign_jds j ON p.jd_id=j.id WHERE p.campaign_id='c2f3123a-ebec-4e69-a26b-ad123c82fb98' GROUP BY j.jd_key,p.status ORDER BY j.jd_key,p.status"))).mappings().all()
        print(json.dumps({'campaign_id':'c2f3123a-ebec-4e69-a26b-ad123c82fb98','jds':[dict(row) for row in rows]},indent=2))
    await engine.dispose()
asyncio.run(main())
