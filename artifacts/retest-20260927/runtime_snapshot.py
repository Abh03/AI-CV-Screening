import json
from app.config import settings
fields=('LLM_PROVIDER','GROQ_MODEL','GROQ_MAX_COMPLETION_TOKENS','GEMINI_MODEL',
        'STAGE2_BACKEND','STAGE3_CONTEXT_MAX_CHARS','CAMPAIGN_STAGE3_GLOBAL_INFLIGHT',
        'PROVIDER_TOKENS_PER_REQUEST','PROVIDER_TOKENS_PER_MINUTE','PROVIDER_REQUESTS_PER_MINUTE')
print(json.dumps({key:getattr(settings,key) for key in fields},indent=2))
