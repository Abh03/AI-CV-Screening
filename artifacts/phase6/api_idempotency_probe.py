import json
from pathlib import Path
import httpx
from dotenv import dotenv_values

tokens=json.loads(dotenv_values('docker/.env')['API_TOKENS_JSON'])
token=next(iter(tokens))
if isinstance(tokens,list):
    token=tokens[0]['token']
campaign='c2f3123a-ebec-4e69-a26b-ad123c82fb98'
body={'approved_jd_ids':json.loads(Path('artifacts/phase6/jobs.json').read_text()),'idempotency_key':'capacity-final-20260926-v4'}
with httpx.Client(base_url='http://127.0.0.1:8000',headers={'Authorization':'Bearer '+token},timeout=120) as client:
    before=client.get(f'/api/v1/campaigns/{campaign}').json()
    create=client.post('/api/v1/campaigns',json=body)
    create.raise_for_status()
    assert create.json()['campaign_id']==campaign and create.json()['created'] is False
    with Path(r'C:\Abhyudit_Files\Infinite\CV-Benchmark-Dataset-Generator\output\cvs\CV_1000.zip').open('rb') as archive:
        upload=client.post(f'/api/v1/campaigns/{campaign}/archive',content=archive,headers={'Content-Type':'application/zip'})
    upload.raise_for_status()
    after=client.get(f'/api/v1/campaigns/{campaign}').json()
    assert after['counts']['cvs']==before['counts']['cvs']==1000
    assert sum(after['counts']['pairs'].values())==sum(before['counts']['pairs'].values())==4000
    result={'input':body,'output':{'create_status':create.status_code,'create':create.json(),'archive_status':upload.status_code,'archive_accepted_count':upload.json()['accepted_count'],'before_cv_count':1000,'after_cv_count':1000,'before_pairs':4000,'after_pairs':4000},'checks':{'idempotent_campaign':True,'idempotent_archive':True}}
    Path('artifacts/phase6/api-idempotency-results.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
