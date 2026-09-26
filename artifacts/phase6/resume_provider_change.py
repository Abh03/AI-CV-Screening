"""Resume only the unfinished selections after an authorized provider change."""
import asyncio,json
from datetime import datetime,timezone
from sqlalchemy import select
from app.models.database import AsyncSessionLocal,CampaignModel,CampaignPairModel,engine

CAMPAIGN='c2f3123a-ebec-4e69-a26b-ad123c82fb98'
async def main():
    saved_failures=[]
    async with AsyncSessionLocal() as db:
        campaign=(await db.execute(select(CampaignModel).where(CampaignModel.id==CAMPAIGN).with_for_update())).scalar_one()
        assert campaign.status=='RUNNING'
        pairs=(await db.execute(select(CampaignPairModel).where(CampaignPairModel.campaign_id==CAMPAIGN).with_for_update())).scalars().all()
        assert len(pairs)==4000
        cleared=0
        for pair in pairs:
            if pair.status=='EVALUATION_FAILED' and pair.failure_code=='PROVIDER_ERROR' and (pair.result_snapshot or {}).get('stage3_policy',{}).get('provider')=='openrouter':
                saved_failures.append({'pair_id':pair.id,'attempts':pair.stage3_attempt_count,'failed_snapshot':pair.result_snapshot.get('stage3_evaluation')})
                pair.status='SHORTLISTED'
                pair.stage3_status=None
                pair.composite_score=pair.tier=None
                snapshot=dict(pair.result_snapshot)
                snapshot.pop('stage3_evaluation',None)
                snapshot.pop('stage3_policy',None)
                pair.result_snapshot=snapshot
            if pair.status=='SHORTLISTED':
                pair.lease_until=pair.lease_owner=None
                pair.failure_code=None
                pair.updated_at=datetime.now(timezone.utc)
                cleared+=1
        await db.commit()
    print(json.dumps({'input':{'campaign_id':CAMPAIGN,'provider':'openrouter','model':'openrouter/free','reason':'Authorized provider change; release Groq cooldowns and retry model-forwarding failures'},'output':{'accounted_pairs':len(pairs),'released_waiting_selections':cleared,'model_forwarding_failures_requeued':len(saved_failures),'attempt_counts_preserved':True,'failed_attempt_evidence':saved_failures,'completed_results_retained':True}},indent=2))
    await engine.dispose()
asyncio.run(main())
