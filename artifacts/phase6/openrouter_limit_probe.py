"""One small synthetic request; keep this usage separate from the campaign."""
import asyncio,json,re
import httpx
from app.config import settings

async def main():
    payload={'model':settings.OPENROUTER_MODEL,'messages':[{'role':'user','content':'Reply OK'}],'max_tokens':32}
    async with httpx.AsyncClient(timeout=45) as client:
        response=await asyncio.wait_for(client.post('https://openrouter.ai/api/v1/chat/completions',headers={'Authorization':'Bearer '+settings.OPENROUTER_API_KEY},json=payload),timeout=45)
    data=response.json()
    error=data.get('error',{})
    print(json.dumps({'input':payload,'output':{'http_status':response.status_code,'rate_limit_headers':{k:v for k,v in response.headers.items() if k.startswith('x-ratelimit-') or k=='retry-after'},'error':{k:re.sub(r'org_[A-Za-z0-9]+','[ACCOUNT]',str(v)) for k,v in error.items() if k in {'code','type','message'}},'usage':data.get('usage'),'resolved_model':data.get('model')}},indent=2))

asyncio.run(main())
