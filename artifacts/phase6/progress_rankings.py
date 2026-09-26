import json
from pathlib import Path
from dotenv import dotenv_values
import httpx

campaign='c2f3123a-ebec-4e69-a26b-ad123c82fb98'
token=json.loads(dotenv_values('docker/.env')['API_TOKENS_JSON'])[0]['token']
jobs=json.loads(Path('artifacts/phase6/jobs.json').read_text())
outputs={}
with httpx.Client(base_url='http://127.0.0.1:8000',headers={'Authorization':'Bearer '+token},timeout=30) as client:
    for job in jobs:
        response=client.get(f'/api/v1/campaigns/{campaign}/jds/{job}/rankings')
        response.raise_for_status()
        value=response.json()
        rows=value['results']
        assert len({row['candidate_id'] for row in rows})==len(rows)
        assert all(row['status']=='SUCCESS' for row in rows)
        assert [row['rank'] for row in rows]==list(range(1,len(rows)+1))
        assert [row['score'] for row in rows]==sorted((row['score'] for row in rows),reverse=True)
        outputs[job]=value
Path('artifacts/phase6/progress-rankings.json').write_text(json.dumps(outputs,indent=2)+'\n')
print(json.dumps({'input':{'campaign_id':campaign,'jd_keys':jobs},'output':{key:{'total':value['total'],'first_results':[{'rank':row['rank'],'file':row['source_filename'],'score':row['score'],'provisional':row['provisional']} for row in value['results'][:3]]} for key,value in outputs.items()},'successful_ranking_checks_passed':True},indent=2))
