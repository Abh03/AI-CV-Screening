"""One representative request to classify provider throttling; no evaluation is persisted."""
import asyncio,json,re
import httpx
from sqlalchemy import select
from app.config import settings
from app.models.database import AsyncSessionLocal,CampaignPairModel,CampaignJDModel,engine
from app.stage3_evaluation.prompts import SYSTEM_PROMPT_STAGE3,build_stage3_user_prompt
from app.stage3_evaluation.schemas import LLMEvaluationOutput

async def main():
    async with AsyncSessionLocal() as db:
        pair,jd=(await db.execute(select(CampaignPairModel,CampaignJDModel).join(CampaignJDModel,CampaignJDModel.id==CampaignPairModel.jd_id).where(CampaignPairModel.campaign_id=='c2f3123a-ebec-4e69-a26b-ad123c82fb98',CampaignPairModel.failure_code=='PROVIDER_RATE_LIMITED').order_by(CampaignPairModel.updated_at.desc()).limit(1))).one()
        evidence=pair.result_snapshot['stage2_evidence']
        payload={'model':settings.GROQ_MODEL,'messages':[{'role':'system','content':SYSTEM_PROMPT_STAGE3},{'role':'user','content':build_stage3_user_prompt(evidence['candidate_id'],jd.job_snapshot,evidence)}],
                 'temperature':0.1,'response_format':{'type':'json_schema','json_schema':{'name':'llm_evaluation_output','strict':True,'schema':LLMEvaluationOutput.model_json_schema()}}}
    async with httpx.AsyncClient(timeout=45) as client:
        response=await client.post('https://api.groq.com/openai/v1/chat/completions',headers={'Authorization':'Bearer '+settings.GROQ_API_KEY},json=payload)
    body=response.json()
    print(json.dumps({'input':{'campaign_id':pair.campaign_id,'pair_id':pair.id,'jd_key':jd.jd_key,'model':settings.GROQ_MODEL,'completion_limit':'provider default','stored_evidence_used':True},'output':{'http_status':response.status_code,'rate_limit_headers':{key:value for key,value in response.headers.items() if key.startswith('x-ratelimit-') or key=='retry-after'},'error':{key:re.sub(r'org_[A-Za-z0-9]+','[ACCOUNT]',str(value)) for key,value in body.get('error',{}).items() if key in {'code','type','message'}},'usage':body.get('usage')},'provider_requests':1,'evaluation_persisted':False},indent=2))
    await engine.dispose()
asyncio.run(main())
