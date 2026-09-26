import asyncio,json,re
from google import genai
from google.genai import types
from app.config import settings

async def main():
    client=genai.Client(api_key=settings.GEMINI_API_KEY,http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1)))
    result={'input':{'model':settings.GEMINI_MODEL,'contents':'Reply OK','max_output_tokens':32},'output':{}}
    try:
        try:
            response=await asyncio.wait_for(client.aio.models.generate_content(model=settings.GEMINI_MODEL,contents='Reply OK',config=types.GenerateContentConfig(max_output_tokens=32)),timeout=45)
            usage=getattr(response,'usage_metadata',None)
            result['output']={'status':200,'total_tokens':getattr(usage,'total_token_count',None)}
        except Exception as exc:
            details=getattr(exc,'details',{})
            error=details.get('error',{}) if isinstance(details,dict) else {}
            items=error.get('details',[])
            result['output']={'status':getattr(exc,'code',None),'error_status':getattr(exc,'status',None),
                'quota_limits_in_message':re.findall(r'limit:\s*(\d+)',error.get('message','')),
                'quota_violations':[{k:v for k,v in violation.items() if k in {'quotaId','quotaMetric'}} for item in items for violation in item.get('violations',[])],
                'retry_delays':[item['retryDelay'] for item in items if 'retryDelay' in item]}
        try:
            model=await client.aio.models.get(model='gemini-3.7-flash')
            result['alternative_model']={'input':'GET models/gemini-3.7-flash','output':{'name':model.name,'supported_actions':model.supported_actions},'inference_requests':0}
        except Exception as exc:
            result['alternative_model']={'input':'GET models/gemini-3.7-flash','output':{'status':getattr(exc,'code',None),'exception_type':type(exc).__name__},'inference_requests':0}
        print(json.dumps(result,indent=2))
    finally:
        await client.aio.aclose()
        client.close()

asyncio.run(main())
