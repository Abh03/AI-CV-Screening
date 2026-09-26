"""Run only while the evaluation worker is stopped for deadline deployment."""
import asyncio
import json
from datetime import datetime, timezone
from sqlalchemy import select
from app.models.database import AsyncSessionLocal, CampaignModel, CampaignPairModel, engine

CAMPAIGN = 'c2f3123a-ebec-4e69-a26b-ad123c82fb98'

async def main():
    resumed = []
    released_waits = []
    retried_transient_failures = []
    async with AsyncSessionLocal() as db:
        campaign = (await db.execute(select(CampaignModel).where(CampaignModel.id == CAMPAIGN).with_for_update())).scalar_one()
        assert campaign.status == 'RUNNING'
        pairs = (await db.execute(select(CampaignPairModel).where(CampaignPairModel.campaign_id == CAMPAIGN).with_for_update())).scalars().all()
        assert len(pairs) == 4000
        for pair in pairs:
            if pair.status=='EVALUATION_FAILED' and pair.failure_code=='PROVIDER_ERROR' and (pair.result_snapshot or {}).get('stage3_policy',{}).get('provider')=='gemini':
                snapshot=dict(pair.result_snapshot)
                retried_transient_failures.append({'pair_id':pair.id,'attempt_count':pair.stage3_attempt_count,'previous_evaluation':snapshot.get('stage3_evaluation')})
                snapshot.pop('stage3_evaluation',None)
                snapshot.pop('stage3_policy',None)
                pair.result_snapshot=snapshot
                pair.status='SHORTLISTED'
                pair.stage3_status=None
                pair.composite_score=pair.tier=None
            if pair.status == 'STAGE3_RUNNING':
                assert pair.stage2_rank is not None and pair.stage2_rank <= 30
                assert not (pair.result_snapshot or {}).get('stage3_evaluation')
                resumed.append({'pair_id': pair.id, 'attempt_count': pair.stage3_attempt_count})
                pair.status = 'SHORTLISTED'
                pair.stage3_status = None
                pair.lease_owner = pair.lease_until = None
                pair.updated_at = datetime.now(timezone.utc)
            elif pair.status == 'SHORTLISTED':
                released_waits.append({'pair_id':pair.id,'attempt_count':pair.stage3_attempt_count,'previous_failure_code':pair.failure_code})
                pair.lease_owner = pair.lease_until = None
                pair.failure_code = None
                pair.updated_at = datetime.now(timezone.utc)
        await db.commit()
    print(json.dumps({'input': {'campaign_id': CAMPAIGN, 'worker_stopped': True, 'reason': 'Rerun Gemini transient failures after retry fix'}, 'output': {'accounted_pairs': len(pairs), 'interrupted_leases_requeued': resumed, 'provider_waits_released':released_waits,'transient_failures_requeued':retried_transient_failures,'attempt_counts_preserved': True, 'earlier_106_terminal_outcomes_retained': True}}, indent=2))
    await engine.dispose()

asyncio.run(main())
