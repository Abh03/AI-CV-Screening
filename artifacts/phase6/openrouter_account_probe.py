import asyncio,json
import httpx
from app.config import settings

async def main():
    async with httpx.AsyncClient(timeout=30) as client:
        response=await client.get('https://openrouter.ai/api/v1/key',headers={'Authorization':'Bearer '+settings.OPENROUTER_API_KEY})
    value=response.json().get('data',{})
    print(json.dumps({'input':{'endpoint':'GET /api/v1/key','provider':settings.LLM_PROVIDER,'configured_model':settings.OPENROUTER_MODEL},'output':{'http_status':response.status_code,'account':{key:value.get(key) for key in ('is_free_tier','limit','limit_remaining','usage_daily','usage','rate_limit') if key in value}},'model_requests':0},indent=2))
asyncio.run(main())
