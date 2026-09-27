"""Review extracted JD profiles against source-only JD metadata and approve them."""
import asyncio
import difflib
import json
from pathlib import Path
import httpx
from dotenv import dotenv_values
from scripts.benchmark_retest_prepare import ROOT, SOURCE
from app.jd_intake import ExtractedJD


async def main():
    jobs = json.loads((SOURCE / 'jds/jds.json').read_text())
    token = json.loads(dotenv_values('docker/.env')['API_TOKENS_JSON'])[0]['token']
    approved = []
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8000',
            headers={'Authorization': 'Bearer ' + token}, timeout=180) as client:
        for job in jobs:
            draft = json.loads((ROOT / (job['jd_id'] + '-draft.json')).read_text())
            assert draft['status'] == 'REVIEW', job['jd_id']
            profile = draft['profile']
            source = ' '.join(' '.join(b['text'].split()) for p in draft['pages'] for b in p['blocks'])
            profile['title'] = job['title']
            # Validate PDF-backed JD facts; never use candidate ground truth to build requirements.
            for group in job['required_skill_groups']:
                assert all(term in source for term in group)
            profile['must_have_skills'] = [dict(canonical=g[0], aliases=[], substitutes=g[1:]) for g in job['required_skill_groups']]
            profile['nice_to_have_skills'] = [dict(canonical=s, aliases=[], substitutes=[]) for s in job['preferred_skills']]
            profile['hard_filter_rules'] = dict(min_years_experience=job['min_years'], degree_requirement=None, require_work_authorization=False)
            targets = []
            for i, group in enumerate(job['required_skill_groups']):
                quote = ' or '.join(group)
                assert quote in source
                targets.append(dict(target_id=f'required_{i}', category='SKILLS', kind='skill',
                    text=quote, source_quote=quote, importance=5, treatment='requirement', evidence_terms=[group]))
            for i, skill in enumerate(job['preferred_skills']):
                targets.append(dict(target_id=f'preferred_{i}', category='SKILLS', kind='skill',
                    text=skill, source_quote=skill, importance=1, treatment='preference', evidence_terms=[[skill]]))
            for target in profile['relevance_contract']['targets']:
                if target['kind'] not in {'responsibility', 'domain'}:
                    continue
                target = dict(target)
                quote = ' '.join(target['source_quote'].split())
                if quote not in source:
                    quote = quote.replace('\u2011', '-').replace('\u2010', '-')
                if quote not in source:
                    candidates = job['responsibilities'] + [job['context']]
                    quote = max(candidates, key=lambda x: difflib.SequenceMatcher(None, quote, x).ratio())
                assert quote in source, quote
                target['source_quote'] = quote
                targets.append(target)
            assert any(t['kind']=='responsibility' for t in targets)
            profile['relevance_contract']['targets'] = targets
            profile['jd_category_queries']['SKILLS'] = ', '.join(' or '.join(g) for g in job['required_skill_groups'])
            profile['jd_category_queries']['EXPERIENCE'] = job['experience_requirement'] + ' ' + ' '.join(job['responsibilities'])
            profile['jd_category_queries']['PROJECTS'] = 'No explicit requirement'
            profile['jd_category_queries']['EDUCATION'] = 'No explicit requirement'
            profile = ExtractedJD.model_validate(profile).model_dump(mode='json')
            (ROOT / (job['jd_id'] + '-reviewed.json')).write_text(json.dumps(profile, indent=2))
            response = await client.post(f"/api/v1/jds/drafts/{draft['draft_id']}/approve", json=profile)
            response.raise_for_status()
            result = response.json()
            approved.append(result['approved_jd_id'])
            (ROOT / (job['jd_id'] + '-approved.json')).write_text(json.dumps(result, indent=2))
            print(json.dumps({'jd': job['jd_id'], 'approved': result['approved_jd_id']}), flush=True)
    (ROOT / 'jobs.json').write_text(json.dumps(approved, indent=2))

if __name__ == '__main__':
    asyncio.run(main())
