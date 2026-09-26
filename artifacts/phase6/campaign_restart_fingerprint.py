import asyncio,json,hashlib
from sqlalchemy import text
from app.models.database import AsyncSessionLocal,engine

async def main():
    async with AsyncSessionLocal() as db:
        rows=(await db.execute(text("SELECT id,jd_id,cv_id,status,stage2_rank,stage2_score,stage3_attempt_count,failure_code,composite_score,tier,result_snapshot FROM campaign_pairs WHERE campaign_id='c2f3123a-ebec-4e69-a26b-ad123c82fb98' ORDER BY id"))).mappings().all()
    assert len(rows)==4000
    encoded=json.dumps([dict(row) for row in rows],sort_keys=True,separators=(',',':')).encode()
    print(json.dumps({'input':{'campaign_id':'c2f3123a-ebec-4e69-a26b-ad123c82fb98','fields':'pair identity, outcomes, ranks, scores, attempts and complete snapshots'},'output':{'pairs':len(rows),'sha256':hashlib.sha256(encoded).hexdigest()}}))
    await engine.dispose()

asyncio.run(main())
