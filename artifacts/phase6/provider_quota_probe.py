import asyncio
import json
import httpx
import re
from app.config import settings


async def main():
    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.post("https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": "Bearer " + settings.GROQ_API_KEY},
            json={"model": settings.GROQ_MODEL,
                  "messages": [{"role": "user", "content": "Reply OK"}],
                  "max_completion_tokens": 32, "reasoning_effort": "low"})
        print(json.dumps({"input": {"model": settings.GROQ_MODEL, "message": "Reply OK", "max_completion_tokens": 32},
              "http_status": response.status_code,
              "rate_limit_headers": {k: v for k, v in response.headers.items()
                                     if k.startswith("x-ratelimit-") or k == "retry-after"},
              "error": {key: re.sub(r'org_[A-Za-z0-9]+', '[ACCOUNT]', str(value))
                        for key, value in response.json().get('error', {}).items()
                        if key in {'code', 'type', 'message'}},
              "usage": response.json().get("usage")}, indent=2))


asyncio.run(main())
