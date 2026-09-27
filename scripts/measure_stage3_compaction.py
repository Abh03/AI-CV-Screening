"""Offline before/after prompt measurements over frozen pilot inputs; no provider calls."""
import argparse
import json
from pathlib import Path
from statistics import mean
from tokenizers import Tokenizer
from scripts.stage3_sentence_replay import rows, prepare
from app.config import Settings, settings
from app.stage3_evaluation.context import prepare_evaluation_context, bounded_evaluation_prompt
from app.stage3_evaluation.evidence import build_evidence_registry, sentence_registry, verify_evidence
from app.stage3_evaluation.compact import evidence_manifest, resolve_compact_selection
from app.stage3_evaluation.schemas import CompactSentenceSelectionOutput
from app.stage3_evaluation.prompts import build_stage3_user_prompt, SYSTEM_PROMPT_STAGE3
from app.stage3_evaluation.budget import request_budget


def measure(source, baseline, output):
    if output.exists():
        raise ValueError('Choose a new output path; preserve previous measurements')
    config=Settings(_env_file='docker/.env')
    for name in ('LLM_PROVIDER','GROQ_MODEL','PROVIDER_TOKENS_PER_MINUTE','GROQ_CONTEXT_TOKENS','GROQ_MAX_COMPLETION_TOKENS'):
        setattr(settings,name,getattr(config,name))
    before=json.loads(baseline.read_text(encoding='utf-8'))
    count=lambda text: len(tokenizer.encode(text,add_special_tokens=False).ids)
    tokenizer=Tokenizer.from_file('models/gpt-oss/tokenizer.json')
    items=[]
    for row,old in zip(rows(source),before['cases']):
        if (row['cv'],row['jd'])!=(old['cv'],old['jd']):
            raise ValueError('Frozen case ordering mismatch')
        payload,jd=prepare(row)
        cid=payload['candidate_id']
        full=prepare_evaluation_context(cid,payload,max_chars=100000,complete_units=True)
        registry=sentence_registry(build_evidence_registry(cid,full))
        prompt=build_stage3_user_prompt(cid,jd,full,registry=registry,selection_handles=True)
        manifest=evidence_manifest(registry,cid)
        budget=request_budget(prompt,'groq')
        selected=CompactSentenceSelectionOutput.model_validate(dict({
            category.lower():{'score':0,'rationale':'Protocol construction check.',
                'citations':[handle for handle,ref in manifest.handles.items() if ref.category==category]}
            for category in ('SKILLS','EXPERIENCE','PROJECTS','EDUCATION')},
            flags=[],executive_summary='Protocol construction check.',evidence_scope=manifest.scope))
        checked=verify_evidence(resolve_compact_selection(selected,registry,cid),registry,cid,require_claim_support=True)
        bounded,_,_=bounded_evaluation_prompt(cid,jd,payload,sentence_selections=True)
        old_input=count(before['system_prompt'])+count(old['prompt'])+count(json.dumps(old['schema'],separators=(',',':')))
        items.append({'cv':row['cv'],'jd':row['jd'],'v13_input_token_estimate':old_input,
            'v14_input_token_estimate':budget['input'],'v14_token_budget':budget,
            'reduction_percent':round(100*(old_input-budget['input'])/old_input,1),
            'original_sentence_refs':len(registry),'transport_sentence_rows':len(manifest.rows),
            'transport_handles':len(manifest.handles),'deduplicated_retrieval_citations':len(manifest.deduplicated_ids),
            'full_cv_chars':len(payload['candidate_cv_text']),
            'full_context_preserved':full['context_metadata']['complete'],
            'bounded_context_complete':bounded['context_metadata']['complete'],
            'construction_failures':sum(not check.valid for check in checked.checks)})
    report={'mode':'offline_lossless_compaction_measurement','protocol':'sentence-handles-v1',
        'limits':['No provider calls or new scoring judgments.',
                  'Input estimates use the pinned GPT-OSS tokenizer plus 128 tokens for provider framing/schema rendering.',
                  'Actual completion usage is unknown; recent pre-compaction responses used 713-1029 output tokens.',
                  'Registry/provenance remain unchanged; duplicate retrieval text is omitted only from transport.'],
        'summary':{'pairs':len(items),
            'before_input_range':[min(p['v13_input_token_estimate'] for p in items),max(p['v13_input_token_estimate'] for p in items)],
            'after_input_range':[min(p['v14_input_token_estimate'] for p in items),max(p['v14_input_token_estimate'] for p in items)],
            'mean_after_input':round(mean(p['v14_input_token_estimate'] for p in items)),
            'mean_reduction_percent':round(mean(p['reduction_percent'] for p in items),1),
            'complete_bounded_context':sum(p['bounded_context_complete'] for p in items),
            'construction_failures':sum(p['construction_failures'] for p in items)},'pairs':items}
    output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report['summary'],indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('source',type=Path)
    parser.add_argument('baseline',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    measure(args.source,args.baseline,args.output)
