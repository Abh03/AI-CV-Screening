import asyncio,json,httpx
from sqlalchemy import select
from app.models.database import AsyncSessionLocal,engine,CampaignPairModel,CampaignCVModel,CampaignJDModel
from app.stage3_evaluation.context import prepare_evaluation_context
from app.stage3_evaluation.prompts import build_stage3_user_prompt,SYSTEM_PROMPT_STAGE3
from app.stage3_evaluation.llm_client import LLMClientWrapper
from app.config import settings
async def main():
    async with AsyncSessionLocal() as db:
        p,c,j=(await db.execute(select(CampaignPairModel,CampaignCVModel,CampaignJDModel).join(CampaignCVModel,CampaignPairModel.cv_id==CampaignCVModel.id).join(CampaignJDModel,CampaignPairModel.jd_id==CampaignJDModel.id).where(CampaignPairModel.campaign_id=='9be091e0-b82d-4846-9682-adb98569eca7',CampaignPairModel.stage3_attempt_count>0))).first()
        e=dict(p.result_snapshot['stage2_evidence'],candidate_cv_text=c.redacted_text)
        e=prepare_evaluation_context(c.candidate_id,e)
        prompt=build_stage3_user_prompt(c.candidate_id,j.job_snapshot,e)
    async with httpx.AsyncClient(timeout=180) as client:
        r=await client.post('https://api.groq.com/openai/v1/chat/completions',headers={'Authorization':'Bearer '+settings.GROQ_API_KEY},json={'model':settings.GROQ_MODEL,'messages':[{'role':'system','content':SYSTEM_PROMPT_STAGE3},{'role':'user','content':prompt}],'temperature':0.1,'max_completion_tokens':4096,'reasoning_effort':'low','response_format':LLMClientWrapper()._get_structured_response_format()})
        print(json.dumps({'prompt_chars':len(prompt),'status':r.status_code,'error':r.json().get('error'),'usage':r.json().get('usage')},default=str))
    await engine.dispose()
asyncio.run(main())
