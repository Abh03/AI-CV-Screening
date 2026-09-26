import asyncio,json
from sqlalchemy import text
from app.models.database import AsyncSessionLocal,engine

async def main():
    async with AsyncSessionLocal() as db:
        rows=(await db.execute(text("SELECT status,failure_code,count(*) AS pairs,min(stage3_attempt_count) AS min_attempts,max(stage3_attempt_count) AS max_attempts,min(lease_until) AS earliest_deadline,max(lease_until) AS latest_deadline FROM campaign_pairs WHERE campaign_id='c2f3123a-ebec-4e69-a26b-ad123c82fb98' AND status IN ('SHORTLISTED','STAGE3_RUNNING','EVALUATION_FAILED') GROUP BY status,failure_code ORDER BY status,failure_code"))).mappings().all()
        print(json.dumps({'input':{'campaign_id':'c2f3123a-ebec-4e69-a26b-ad123c82fb98','action':'read retry deadlines and failure counts'},'output':[dict(row) for row in rows]},indent=2,default=str))
    await engine.dispose()

asyncio.run(main())
