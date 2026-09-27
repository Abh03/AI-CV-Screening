import asyncio
import json
from app.jd_intake import extract_profile
from app.config import settings
async def main():
    print(json.dumps({'provider': settings.LLM_PROVIDER, 'model': settings.GROQ_MODEL}))
    try:
        await extract_profile('Java Engineer. Java and SQL required. Minimum 3 years experience. Build payment APIs.')
        print('OK')
    except Exception as exc:
        print(type(exc).__name__, str(exc)[:1800])
asyncio.run(main())
