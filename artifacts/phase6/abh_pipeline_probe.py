"""Read-only pipeline diagnostics; no provider calls or database mutations."""
import asyncio
import json
from collections import Counter, defaultdict
from sqlalchemy import text
from app.models.database import AsyncSessionLocal, engine

CAMPAIGN = 'c2f3123a-ebec-4e69-a26b-ad123c82fb98'

async def main():
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(text('SELECT j.jd_key,c.source_filename,p.stage1_details,p.stage2_rank,p.status,p.result_snapshot FROM campaign_pairs p JOIN campaign_jds j ON j.id=p.jd_id JOIN campaign_cvs c ON c.id=p.cv_id WHERE p.campaign_id=:id'), {'id': CAMPAIGN})).mappings().all()
        jobs = (await db.execute(text('SELECT jd_key,job_snapshot,policy_snapshot FROM campaign_jds WHERE campaign_id=:id'), {'id': CAMPAIGN})).mappings().all()
        queries = []
        for job in jobs:
            for category, query in job['job_snapshot']['jd_category_queries'].items():
                q = (await db.execute(text("SELECT websearch_to_tsquery('english', :q)::text AS parsed_query, (SELECT count(*) FROM source_chunks WHERE tsv_content @@ websearch_to_tsquery('english', :q) AND category=:category AND candidate_id IN (SELECT candidate_id FROM campaign_cvs WHERE campaign_id=:id)) AS matching_chunks"), {'q': query, 'category': category, 'id': CAMPAIGN})).mappings().one()
                queries.append({'jd_key': job['jd_key'], 'category': category, 'input': query, **dict(q)})
    out = {'jobs': [dict(j) for j in jobs], 'sparse_queries': queries, 'by_jd': {}, 'stage1_codes': dict(Counter(check['code'] for row in rows for check in (row['stage1_details'] or {}).get('checks', [])))}
    groups = defaultdict(list)
    for row in rows:
        groups[row['jd_key']].append(row)
    for key, group in groups.items():
        evidence = [c for row in group for cs in (row['result_snapshot'] or {}).get('stage2_evidence', {}).get('evidence_by_category', {}).values() for c in cs]
        selected = [row for row in group if row['stage2_rank'] <= 30]
        sample = []
        for row in selected:
            snapshot = row['result_snapshot'] or {}
            evaluation = snapshot.get('stage3_evaluation', {})
            sample.append({'filename': row['source_filename'], 'stage2_rank': row['stage2_rank'], 'status': row['status'], 'stage2_category_scores': snapshot.get('stage2_evidence', {}).get('category_scores'), 'stage3_policy': snapshot.get('stage3_policy'), 'review_reasons': evaluation.get('review_reasons', []), 'flags': [{'type': f['type'], 'severity': f['severity'], 'description': f['description']} for f in (evaluation.get('llm_raw_output') or {}).get('flags', [])], 'snippet_lengths': {cat: [len(c['text']) for c in cs] for cat, cs in snapshot.get('stage2_evidence', {}).get('evidence_by_category', {}).items()}, 'snippet_sources': {cat: [c['section'] for c in cs] for cat, cs in snapshot.get('stage2_evidence', {}).get('evidence_by_category', {}).items()}})
        out['by_jd'][key] = {'stage2_evidence_chunks': len(evidence), 'with_sparse_rank': sum(c.get('sparse_rank') is not None for c in evidence), 'zero_category_counts': dict(Counter(cat for row in group for cat, score in (row['result_snapshot'] or {}).get('stage2_evidence', {}).get('category_scores', {}).items() if score == 0)), 'selected': sorted(sample, key=lambda r: r['stage2_rank'])}
    print(json.dumps(out))
    await engine.dispose()

asyncio.run(main())
