"""Read only safe Groq quota counters with a tiny non-CV probe."""
import json
from pathlib import Path
import httpx
from dotenv import dotenv_values

config=dotenv_values('docker/.env')
try:
    response=httpx.post('https://api.groq.com/openai/v1/chat/completions',
        headers={'Authorization':'Bearer '+config['GROQ_API_KEY']},
        json={'model':config.get('GROQ_MODEL','openai/gpt-oss-120b'),
              'messages':[{'role':'user','content':'Reply OK.'}],
              'max_completion_tokens':64,'reasoning_effort':'low'},timeout=45)
    counters={name:response.headers[name] for name in
        ['x-ratelimit-limit-requests','x-ratelimit-limit-tokens','x-ratelimit-remaining-requests',
         'x-ratelimit-remaining-tokens','x-ratelimit-reset-requests','x-ratelimit-reset-tokens','retry-after']
        if name in response.headers}
    safe={'status':response.status_code,'rate_headers':counters}
    if response.status_code==200:
        safe['usage']=response.json().get('usage',{})
    Path('artifacts/pilot36/latest-key-quota-probe.json').write_text(json.dumps(safe,indent=2),encoding='utf-8')
    print(json.dumps(safe))
except Exception as error:
    print(json.dumps({'error_type':type(error).__name__}))
