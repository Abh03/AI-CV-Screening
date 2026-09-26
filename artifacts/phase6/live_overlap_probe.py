"""Controlled live ranking fixture: shared candidates, independent JD scores; no model calls."""
import asyncio, json
from uuid import uuid4
import httpx
from sqlalchemy import select, delete
from app.core.auth import hash_password, issue_access_token
from app.models.database import AsyncSessionLocal, CampaignModel, CampaignJDModel, CampaignCVModel, CampaignPairModel, RecruiterUserModel, engine
from app.campaigns.persistence import reserve_campaign, reserve_cv

async def main():
    owner = 'overlap-probe-'+uuid4().hex[:12]
    campaign_id = None
    try:
        async with AsyncSessionLocal() as db:
            user = RecruiterUserModel(id=owner,username=owner,email=owner+'@example.invalid',role='recruiter',is_active=True,password_hash=hash_password(uuid4().hex),token_version=0,failed_attempts=0)
            db.add(user)
            await db.commit()
            token,_ = issue_access_token(user)
            _,campaign = await reserve_campaign(db,owner_id=owner,request_hash=uuid4().hex,
                job_snapshots=[{'job_id':key,'title':key} for key in ('fixture-A','fixture-B')],policy_snapshots=[{},{}])
            campaign_id=campaign.id
            for candidate in ('shared-1','shared-2'):
                await reserve_cv(db,campaign_id=campaign_id,owner_id=owner,candidate_id=candidate,source_filename=candidate+'.pdf',content_hash=uuid4().hex)
            rows = (await db.execute(select(CampaignPairModel,CampaignJDModel.jd_key,CampaignCVModel.candidate_id).join(CampaignJDModel,CampaignJDModel.id==CampaignPairModel.jd_id).join(CampaignCVModel,CampaignCVModel.id==CampaignPairModel.cv_id).where(CampaignPairModel.campaign_id==campaign_id))).all()
            scores={('fixture-A','shared-1'):90,('fixture-A','shared-2'):70,('fixture-B','shared-1'):60,('fixture-B','shared-2'):80}
            for pair,jd,candidate in rows:
                pair.status=pair.stage3_status='SUCCESS'
                pair.composite_score=scores[(jd,candidate)]
                pair.stage2_rank=1 if scores[(jd,candidate)]>=80 else 2
                pair.tier='TIER_1'
                pair.result_snapshot={'stage3_evaluation':{'executive_summary':'Controlled ranking fixture','evidence_verification':{'registry':{'SKILLS:1':{'text':'SENSITIVE_FIXTURE_TEXT'}}}}}
            campaign.status='COMPLETED'
            for jd in (await db.execute(select(CampaignJDModel).where(CampaignJDModel.campaign_id==campaign_id))).scalars():
                jd.status='COMPLETED'
            await db.commit()
        outputs={}
        async with httpx.AsyncClient(base_url='http://127.0.0.1:8000',headers={'Authorization':'Bearer '+token}) as client:
            for key in ('fixture-A','fixture-B'):
                response=await client.get(f'/api/v1/campaigns/{campaign_id}/jds/{key}/rankings')
                assert response.status_code==200 and 'SENSITIVE_FIXTURE_TEXT' not in response.text
                outputs[key]=response.json()
        orders={key:[row['candidate_id'] for row in value['results']] for key,value in outputs.items()}
        assert orders=={'fixture-A':['shared-1','shared-2'],'fixture-B':['shared-2','shared-1']}
        assert len(rows)==4
        print(json.dumps({'input':{'controlled_scores':[{'jd':jd,'candidate':candidate,'score':score} for (jd,candidate),score in scores.items()]},'output':outputs,'checks':{'independent_reverse_order':True,'overlapping_candidates':2,'stored_pairs':4,'raw_evidence_hidden':True},'provider_calls':0,'fixture_only':True},indent=2))
    finally:
        async with AsyncSessionLocal() as db:
            if campaign_id:
                await db.execute(delete(CampaignModel).where(CampaignModel.id==campaign_id))
            await db.execute(delete(RecruiterUserModel).where(RecruiterUserModel.id==owner))
            await db.commit()
        await engine.dispose()
asyncio.run(main())
