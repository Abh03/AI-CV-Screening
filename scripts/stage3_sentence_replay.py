"""Replay frozen assessments and independently measure v13 context; never mutate history."""
import argparse
import asyncio
from collections import Counter
import hashlib
import json
from pathlib import Path
from app.stage3_evaluation.context import bounded_evaluation_prompt
from app.stage3_evaluation.evidence import verify_evidence, resolve_sentence_selection
from app.stage3_evaluation.schemas import SentenceSelectionOutput
from app.stage3_evaluation.schemas import FinalCandidateEvaluation, LLMEvaluationOutput, EvidenceReference
from app.stage3_evaluation.scoring import score_evaluation


def rows(source):
    return [r for r in json.loads(source.read_text(encoding='utf-8-sig'))['comparisons']
            if r['stage3']['output'] and r['stage3']['output'].get('llm_raw_output')]


def prepare(row):
    payload = dict(row['stage2']['output'], candidate_cv_text=row['stage0']['output'])
    return payload, row['stage2']['input']['job_profile']


def replay(source):
    pairs = []
    failures = Counter()
    reasons = Counter()
    construction_failures = Counter()
    for row in rows(source):
        saved = row['stage3']['output']
        cid = saved['candidate_id']
        registry = {k: EvidenceReference.model_validate(v) for k,v in saved['evidence_verification']['registry'].items()}
        output = LLMEvaluationOutput.model_validate(saved['llm_raw_output'])
        metadata = saved['evidence_verification'].get('context_metadata', {})
        checked = verify_evidence(output, registry, cid,
            saved['evidence_verification'].get('injection_signals', []), require_claim_support=True,
            context_complete=metadata.get('complete', True))
        checked.context_metadata = metadata
        result = score_evaluation(cid, output, checked)
        failures.update(c.reason for c in checked.checks if not c.valid)
        reasons.update(result.review_reasons)
        payload, jd = prepare(row)
        prepared, new_registry, prompt = bounded_evaluation_prompt(cid, jd, payload, sentence_selections=True)
        # Exercise deterministic construction for every selectable sentence.
        # This is a protocol check, not a migrated assessment or new ranking.
        selected = SentenceSelectionOutput.model_validate(dict({
            category.lower(): {'score': 0, 'rationale': 'Protocol construction check.',
                'citations': [ref.evidence_id for ref in new_registry.values() if ref.category == category]}
            for category in ('SKILLS', 'EXPERIENCE', 'PROJECTS', 'EDUCATION')},
            flags=[], executive_summary='Protocol construction check.'))
        constructed = verify_evidence(resolve_sentence_selection(selected, new_registry, cid),
            new_registry, cid, require_claim_support=True)
        construction_failures.update(check.reason for check in constructed.checks if not check.valid)
        pairs.append({'cv':row['cv'], 'jd':row['jd'], 'candidate_id':cid,
            'historical_score':saved['composite_score'], 'replay_score':result.composite_score,
            'replay_status':result.evaluation_status.value, 'review_reasons':result.review_reasons,
            'v13_context':prepared['context_metadata'], 'v13_selectable_sentences':len(new_registry),
            'v13_prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest()})
    return {'mode':'offline_unchanged_historical_responses',
        'limitations':['Legacy free-form claims and invalid citations are intentionally not repaired or relabelled.',
                      'New sentence protocol needs fresh provider responses; context rebuild alone cannot validate old rationales.'],
        'summary':{'sentence_construction_check_failures':dict(construction_failures),'pairs':len(pairs),'failures':dict(failures),'review_reasons':dict(reasons),
            'statuses':dict(Counter(p['replay_status'] for p in pairs)),
            'v13_complete_context':sum(p['v13_context']['complete'] for p in pairs),
            'all_scores_preserved':all(p['historical_score']==p['replay_score'] for p in pairs)},'pairs':pairs}


async def live(source, output):
    from app.config import settings
    from app.stage3_evaluation.llm_client import evaluate_single_candidate_async, llm_client
    if settings.LLM_PROVIDER.lower() != 'groq' or llm_client.provider != 'groq':
        raise ValueError('Live validation requires configured Groq provider')
    cases = rows(source)
    selected = []
    # One large-context case, one material-flag case, and one previously complete case.
    selectors = [lambda r: len(r['stage0']['output']),
                 lambda r: bool(r['stage3']['output']['has_critical_flags']),
                 lambda r: 'EVIDENCE_CONTEXT_TRUNCATED' not in r['stage3']['output']['review_reasons']]
    remaining = list(cases)
    for index, key in enumerate(selectors):
        pool = ([r for r in remaining if r['jd'] != selected[0]['jd']] or remaining) if index == 2 else remaining
        row = max(pool, key=key)
        remaining.remove(row)
        selected.append(row)
    results = []
    try:
        for row in selected:
            payload, jd = prepare(row)
            result = await evaluate_single_candidate_async(payload, jd, max_retries=0, max_provider_attempts=1)
            results.append({'cv':row['cv'],'jd':row['jd'],'result':result.model_dump(mode='json')})
            output.write_text(json.dumps({'mode':'three_case_live_validation','provider':'groq',
                'model':settings.GROQ_MODEL,'results':results},indent=2),encoding='utf-8')
            print(json.dumps({'cv':row['cv'],'jd':row['jd'],'status':result.evaluation_status.value,
                'review_reasons':result.review_reasons,'error_code':result.error_code}),flush=True)
            if result.error_code == 'PROVIDER_RATE_LIMITED':
                break
    finally:
        await llm_client.aclose()


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('source',type=Path)
    parser.add_argument('output',type=Path)
    parser.add_argument('--live',action='store_true')
    args=parser.parse_args()
    if args.output.exists():
        raise SystemExit('Output already exists; choose a new path to preserve history')
    if args.live:
        asyncio.run(live(args.source,args.output))
    else:
        result=replay(args.source)
        args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps(result['summary'],indent=2))
