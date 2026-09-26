import asyncio,json
from sqlalchemy import select
from app.models.database import AsyncSessionLocal,CampaignPairModel,CampaignCVModel,CampaignJDModel,engine
async def main():
    async with AsyncSessionLocal() as db:
        pair,cv,jd=(await db.execute(select(CampaignPairModel,CampaignCVModel,CampaignJDModel).join(CampaignCVModel,CampaignCVModel.id==CampaignPairModel.cv_id).join(CampaignJDModel,CampaignJDModel.id==CampaignPairModel.jd_id).where(CampaignPairModel.campaign_id=='c2f3123a-ebec-4e69-a26b-ad123c82fb98',CampaignPairModel.status.in_(('SUCCESS','REVIEW_REQUIRED'))).order_by(CampaignPairModel.updated_at).limit(1))).one()
        evidence=(pair.result_snapshot or {}).get('stage2_evidence',{})
        evaluation=dict((pair.result_snapshot or {}).get('stage3_evaluation',{}))
        verification=dict(evaluation.get('evidence_verification',{}))
        verification.pop('registry',None)
        evaluation['evidence_verification']=verification
        print(json.dumps({'input':{'campaign_id':pair.campaign_id,'source_file':cv.source_filename,'candidate_id':cv.candidate_id,'jd':jd.job_snapshot},'output':{'stage0':{'status':cv.stage0_status,'first_600_redacted_characters':cv.redacted_text[:600],'raw_pdf_retained':cv.encrypted_pdf is not None},'stage1':{'decision':pair.stage1_decision,'details':pair.stage1_details},'stage2':{'score':pair.stage2_score,'rank':pair.stage2_rank,'first_snippet_per_category':{key:value[:1] for key,value in evidence.get('evidence_by_category',{}).items()}},'stage3':evaluation}},indent=2))
    await engine.dispose()
asyncio.run(main())
