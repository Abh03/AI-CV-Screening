import json
from pathlib import Path
data=json.loads(Path('artifacts/retest-20260927/pilot-export.json').read_text(encoding='utf-8-sig'))
cvs={c['id']:c for c in data['campaign_cvs']}
for p in data['campaign_pairs']:
    if p['stage1_decision'] == 'FAIL':
        name=Path(cvs[p['cv_id']]['source_filename']).stem
        if name in {'CV0001','CV0002','CV0526','CV0888'}:
            print(name,p['jd_id'],p['stage1_details']['failed_reasons'])
    if p['stage2_score'] is not None:
        e=(p['result_snapshot'] or {}).get('stage2_evidence',{})
        print(json.dumps({'cv':cvs[p['cv_id']]['source_filename'], 'jd':p['jd_id'],
                         'status':p['status'],'failure':p['failure_code'],
                         'score':p['stage2_score'],
                         'scores':{k:v for k,v in e.items() if k not in {'evidence_by_category','retrieval_by_category','relevance_targets'}},
                         'categories':{k:len(v) for k,v in e.get('evidence_by_category',{}).items()}},default=str))
