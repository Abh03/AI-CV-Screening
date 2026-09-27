"""Compare an exported campaign with generator truth, preserving stage artifacts."""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('export', type=Path)
    parser.add_argument('--system-prompt', type=Path, help='Exact system prompt used by this run')
    args = parser.parse_args()
    raw = args.export.read_bytes()
    # Windows PowerShell redirects native stdout as UTF-16 by default.
    data = json.loads(raw.decode('utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'))
    truth = json.loads(Path('C:/Abhyudit_Files/Infinite/CV-Benchmark-Dataset-Generator/output/ground_truth.json').read_text())
    source_jds = json.loads(Path('C:/Abhyudit_Files/Infinite/CV-Benchmark-Dataset-Generator/output/jds/jds.json').read_text())
    titles = {j['title']: j['jd_id'] for j in source_jds}
    expected = {(r['jd_id'], r['candidate_id']): r for r in truth}
    cvs = {r['id']: r for r in data['campaign_cvs']}
    rows = []
    summaries = []
    stage_io = []
    from app.stage3_evaluation.prompts import build_stage3_user_prompt, SYSTEM_PROMPT_STAGE3
    from app.stage3_evaluation.context import bounded_evaluation_prompt
    from app.stage3_evaluation.schemas import EvidenceReference
    system_prompt = args.system_prompt.read_text(encoding='utf-8') if args.system_prompt else SYSTEM_PROMPT_STAGE3
    system_hash = hashlib.sha256(system_prompt.encode()).hexdigest()
    if args.system_prompt:
        assert all(jd['policy_snapshot']['prompt_sha256'] == system_hash for jd in data['campaign_jds']), 'System prompt does not match the persisted run policy'
    for jd in data['campaign_jds']:
        benchmark_jd = titles[jd['job_snapshot']['title']]
        pairs = [p for p in data['campaign_pairs'] if p['jd_id'] == jd['id']]
        joined = []
        for pair in pairs:
            candidate = Path(cvs[pair['cv_id']]['source_filename']).stem
            label = expected[(benchmark_jd, candidate)]
            selected = pair['stage3_attempt_count'] > 0 or pair['status'] in {'SHORTLISTED', 'STAGE3_RUNNING'}
            row = {**pair, 'benchmark_jd': benchmark_jd, 'benchmark_candidate': candidate,
                   'expected': label, 'selected': selected}
            rows.append(row)
            joined.append(row)
            evidence = (pair['result_snapshot'] or {}).get('stage2_evidence')
            evaluation = (pair['result_snapshot'] or {}).get('stage3_evaluation') or {}
            verification = evaluation.get('evidence_verification') or {}
            registry = verification.get('registry')
            prompt = None
            if registry and evidence:
                prompt = build_stage3_user_prompt(cvs[pair['cv_id']]['candidate_id'],
                    jd['job_snapshot'], dict(evidence,
                        context_metadata=verification.get('context_metadata', {})),
                    registry={tag: EvidenceReference.model_validate(ref) for tag, ref in registry.items()},
                    compact=True, neutral_sources=jd['policy_snapshot'].get('prompt_version') == 'stage3-prompt-v10')
            elif evidence and pair['stage3_attempt_count']:
                _, _, prompt = bounded_evaluation_prompt(cvs[pair['cv_id']]['candidate_id'],
                    jd['job_snapshot'], dict(evidence, candidate_cv_text=cvs[pair['cv_id']]['redacted_text']),
                    max_bytes=jd['policy_snapshot'].get('stage3_prompt_max_bytes', 16000))
            stage_io.append({'jd': benchmark_jd, 'cv': candidate,
                'stage0': {'input': cvs[pair['cv_id']]['source_filename'],
                           'output': cvs[pair['cv_id']]['redacted_text'],
                           'status': cvs[pair['cv_id']]['stage0_status']},
                'stage1': {'input': {'cv_text': cvs[pair['cv_id']]['redacted_text'],
                                    'job_profile': jd['job_snapshot']}, 'output': pair['stage1_details']},
                'stage2': {'input': {'job_profile': jd['job_snapshot'],
                                    'source_locations': cvs[pair['cv_id']]['source_locations']},
                           'output': evidence, 'rank': pair['stage2_rank'],
                           'selected_for_stage3': selected},
                'stage3': {'attempts': pair['stage3_attempt_count'],
                           'input_reconstructed': prompt,
                           'input_basis': 'persisted verification registry' if registry else 'current prompt builder; exact failed request not persisted',
                           'output': (pair['result_snapshot'] or {}).get('stage3_evaluation'),
                           'status': pair['stage3_status'], 'failure': pair['failure_code']}})
        eligible = [r for r in joined if r['expected']['expected_stage1'] != 'FAIL']
        ideal = sorted(eligible, key=lambda r: (-r['expected']['relevance_score'], r['benchmark_candidate']))[:jd['stage3_cap']]
        summaries.append({'jd': benchmark_jd, 'title': jd['job_snapshot']['title'],
            'stage1_actual': dict(Counter(r['stage1_decision'] or 'PENDING' for r in joined)),
            'stage1_expected': dict(Counter(r['expected']['expected_stage1'] for r in joined)),
            'stage1_confusion': dict(Counter(r['expected']['expected_stage1'] + ' -> ' + (r['stage1_decision'] or 'PENDING') for r in joined)),
            'stage1_crossed_actual': sum(r['stage1_decision'] in {'PASS','REVIEW'} for r in joined),
            'stage1_crossed_expected': len(eligible),
            'stage2_selected_actual': sum(r['selected'] for r in joined),
            'stage2_selected_expected_cap': len(ideal),
            'stage2_selected_bands': dict(Counter(r['expected']['overall_relevance_band'] for r in joined if r['selected'])),
            'stage2_selected_expected_fail': sum(r['selected'] and r['expected']['expected_stage1']=='FAIL' for r in joined),
            'stage2_benchmark_top_overlap': len({r['benchmark_candidate'] for r in ideal} & {r['benchmark_candidate'] for r in joined if r['selected']}),
            'stage2_zero_scores': sum(r['stage2_score']==0 for r in joined),
            'stage2_review_without_llm': sum(r['status']=='REVIEW_REQUIRED' and not r['stage3_attempt_count'] for r in joined),
            'final_statuses': dict(Counter(r['status'] for r in joined))})
    report = {'campaign': {k:v for k,v in data['campaigns'][0].items() if k!='intake_report'},
              'stage0': dict(Counter(r['stage0_status'] for r in cvs.values())),
              'stage0_expected_success': len(cvs), 'jds': summaries,
              'limitations': ['Generator labels use latent facts; source evidence and system policy can differ.',
                              'No ground-truth Stage 3 SUCCESS/REVIEW labels supplied; relevance bands are proxies.',
                              'Benchmark top membership is tie-sensitive; high-band share is the primary shortlist metric.']}
    args.export.with_suffix('.comparison.json').write_text(json.dumps(report, indent=2))
    args.export.with_suffix('.stage-details.json').write_text(json.dumps(rows, indent=2))
    args.export.with_suffix('.stage-io.json').write_text(json.dumps({
        'stage3_system_prompt': system_prompt,
        'system_prompt_matches_run': all(jd['policy_snapshot']['prompt_sha256'] == system_hash for jd in data['campaign_jds']),
        'comparisons': stage_io}, indent=2))
    print(json.dumps(report, indent=2))

if __name__ == '__main__':
    main()
