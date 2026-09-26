"""Read-only verification of all stage ranks, overlaps and persisted evaluation snapshots."""
import asyncio,json
from collections import Counter,defaultdict
from itertools import combinations
from sqlalchemy import text
from app.models.database import AsyncSessionLocal,engine

CAMPAIGN='c2f3123a-ebec-4e69-a26b-ad123c82fb98'
SELECTED={'SHORTLISTED','STAGE3_RUNNING','SUCCESS','REVIEW_REQUIRED','EVALUATION_FAILED'}
TERMINAL={'SUCCESS','REVIEW_REQUIRED','EVALUATION_FAILED','CUTOFF_EXCLUDED','PROCESSING_FAILED','FILTER_REJECTED','EXTRACTION_FAILED'}

async def main():
    async with AsyncSessionLocal() as db:
        rows=(await db.execute(text("SELECT p.id,j.jd_key,c.candidate_id,p.status,p.stage2_score,p.stage2_rank,p.stage3_attempt_count,p.failure_code,p.composite_score,p.result_snapshot FROM campaign_pairs p JOIN campaign_jds j ON p.jd_id=j.id JOIN campaign_cvs c ON p.cv_id=c.id WHERE p.campaign_id=:id"),{'id':CAMPAIGN})).mappings().all()
        pages=(await db.execute(text("SELECT count(*) AS extracted_pages,count(*) FILTER (WHERE (page->>'ocr_used')::boolean) AS ocr_pages FROM campaign_cvs CROSS JOIN LATERAL json_array_elements(source_locations) page WHERE campaign_id=:id"),{'id':CAMPAIGN})).mappings().one()
        campaign=(await db.execute(text("SELECT status,created_at,completed_at FROM campaigns WHERE id=:id"),{'id':CAMPAIGN})).mappings().one()
        legacy_rows=(await db.execute(text("SELECT count(*) FROM evaluation_results WHERE created_at >= (SELECT created_at FROM campaigns WHERE id=:id) AND created_at <= COALESCE((SELECT completed_at FROM campaigns WHERE id=:id),now())"),{'id':CAMPAIGN})).scalar_one()
    groups=defaultdict(list)
    for row in rows:
        groups[row['jd_key']].append(row)
    selected={key:{row['candidate_id'] for row in value if row['status'] in SELECTED} for key,value in groups.items()}
    successes={key:{row['candidate_id'] for row in value if row['status']=='SUCCESS'} for key,value in groups.items()}
    shared=defaultdict(list)
    for row in rows:
        if row['status'] in SELECTED:
            shared[row['candidate_id']].append({'jd_key':row['jd_key'],'pair_id':row['id'],'status':row['status'],
                'score':row['composite_score'],'attempts':row['stage3_attempt_count'],
                'has_stage3_snapshot':'stage3_evaluation' in (row['result_snapshot'] or {})})
    checks={}
    for key,value in groups.items():
        ranked=sorted(value,key=lambda row:(-row['stage2_score'],row['candidate_id']))
        checks[key]={'pairs':len(value),'selected':len(selected[key]),'selected_cap_pass':len(selected[key])<=30,
                     'unique_candidate_pairs':len({row['candidate_id'] for row in value})==len(value),
                     'complete_stage2_ranks':sorted(row['stage2_rank'] for row in value)==list(range(1,1001)),
                     'deterministic_stage2_order':all(row['stage2_rank']==index+1 for index,row in enumerate(ranked)),
                     'selected_exact_top30':selected[key]=={row['candidate_id'] for row in ranked[:30]},
                     'statuses':dict(Counter(row['status'] for row in value)),
                     'stage3_attempts':sum(row['stage3_attempt_count'] for row in value)}
    result={'input':{'campaign_id':CAMPAIGN},'output':{'campaign':dict(campaign),'pairs':len(rows),'terminal_pairs':sum(row['status'] in TERMINAL for row in rows),'unique_pair_ids':len({row['id'] for row in rows}),
             'unique_candidate_jd_pairs':len({(row['jd_key'],row['candidate_id']) for row in rows}),
             'legacy_evaluation_rows_in_campaign_window':legacy_rows,
             'extraction':dict(pages),'jds':checks,
             'selected_candidate_overlap':{a+'|'+b:len(selected[a]&selected[b]) for a,b in combinations(groups,2)},
             'successful_candidate_overlap':{a+'|'+b:len(successes[a]&successes[b]) for a,b in combinations(groups,2)},
             'shared_selected_candidate_evaluations':[{ 'candidate_id':candidate,'evaluations':values } for candidate,values in shared.items() if len(values)>1],
             'persisted_stage3_snapshots':sum('stage3_evaluation' in (row['result_snapshot'] or {}) for row in rows),
             'success_or_review_without_snapshot':sum(row['status'] in {'SUCCESS','REVIEW_REQUIRED'} and 'stage3_evaluation' not in (row['result_snapshot'] or {}) for row in rows),
             'failure_codes':dict(Counter(row['failure_code'] for row in rows if row['status']=='EVALUATION_FAILED')),
             'review_reason_counts':dict(Counter(reason for row in rows if row['status']=='REVIEW_REQUIRED' for reason in (row['result_snapshot'] or {}).get('stage3_evaluation',{}).get('review_reasons',[]))),
             'failure_codes_in_snapshots':dict(Counter((row['result_snapshot'] or {}).get('stage3_evaluation',{}).get('error_code') for row in rows if row['status']=='EVALUATION_FAILED'))}}
    print(json.dumps(result,indent=2,default=str))
    assert len(rows)==4000 and len(groups)==4
    assert result['output']['unique_candidate_jd_pairs']==4000
    assert all(all(value[field] for field in ('selected_cap_pass','unique_candidate_pairs','complete_stage2_ranks','deterministic_stage2_order','selected_exact_top30')) for value in checks.values())
    assert result['output']['success_or_review_without_snapshot']==0
    await engine.dispose()
asyncio.run(main())
