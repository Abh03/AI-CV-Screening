"""Three frozen-case requests with complete evidence and no context shrinking."""
import asyncio
import argparse
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
import httpx
from scripts.stage3_sentence_replay import rows, prepare
from app.config import Settings, settings
from app.stage3_evaluation.context import prepare_evaluation_context, unique_evidence_chars
from app.stage3_evaluation.evidence import build_evidence_registry, sentence_registry, resolve_sentence_selection
from app.stage3_evaluation.prompts import build_stage3_user_prompt, SYSTEM_PROMPT_STAGE3
from app.stage3_evaluation.budget import request_budget
from app.stage3_evaluation.schemas import CompactSentenceSelectionOutput
from app.stage3_evaluation.compact import resolve_compact_selection
from app.stage3_evaluation.llm_client import LLMClientWrapper, ProviderRateLimited
from app.stage3_evaluation.evaluator import compute_deterministic_tier
from app.stage0_extraction.injection_guard import scan_for_injection_anomalies


async def run(output):
    configured=Settings(_env_file='docker/.env')
    for name in ('LLM_PROVIDER','GROQ_API_KEY','GROQ_MODEL','PROVIDER_TOKENS_PER_MINUTE',
                 'PROVIDER_REQUESTS_PER_MINUTE','GROQ_CONTEXT_TOKENS','GROQ_MAX_COMPLETION_TOKENS'):
        setattr(settings,name,getattr(configured,name))
    if output.exists():
        raise ValueError('Choose a new artifact path; do not overwrite historical results')
    cases=rows(Path('artifacts/pilot36/run3-export.stage-io.json'))
    # Exactly the same representative cases as the previous three-case validation.
    cases=[next(row for row in cases if row['cv']==cv and row['jd']==jd)
           for cv,jd in [('CV0347','JD02'),('CV0363','JD02'),('CV0981','JD04')]]
    report={'mode':'complete_context_no_shrink_three_case_validation','protocol':'sentence-handles-v1',
            'created_at':datetime.now(timezone.utc).isoformat(),'provider':'groq','model':settings.GROQ_MODEL,
            'token_limit_per_minute':settings.PROVIDER_TOKENS_PER_MINUTE,
            'completion_allowance':settings.GROQ_MAX_COMPLETION_TOKENS,
            'results':[],'metrics':[]}
    class SafeMetrics(logging.Handler):
        def emit(self,record):
            if getattr(record,'event',None)=='provider_response':
                report['metrics'].append({key:getattr(record,key) for key in
                    ('status','error_code','input_tokens','output_tokens','total_tokens','count')
                    if hasattr(record,key)})
    logger=logging.getLogger('cv_screening')
    handler=SafeMetrics()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    wrapper=LLMClientWrapper()
    def save():
        output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    try:
        for index,row in enumerate(cases):
            if index:
                # Keep waits short and preserve one minute spacing between requests.
                await asyncio.sleep(31)
                await asyncio.sleep(30)
            payload,jd=prepare(row)
            cid=payload['candidate_id']
            full=prepare_evaluation_context(cid,payload,
                max_chars=max(settings.STAGE3_CONTEXT_MAX_CHARS,
                    len(payload['candidate_cv_text'])+unique_evidence_chars(payload['evidence_by_category'])+1),
                complete_units=True)
            if not full['context_metadata']['complete']:
                raise ValueError('Complete evidence was not supplied; no lossy validation allowed')
            registry=sentence_registry(build_evidence_registry(cid,full))
            prompt=build_stage3_user_prompt(cid,jd,full,registry=registry,compact=True,neutral_sources=True,selection_handles=True)
            budget=request_budget(prompt,'groq')
            item={'cv':row['cv'],'jd':row['jd'],'candidate_id':cid,
                  'context_complete':True,'selectable_sentences':len(registry),
                  'token_budget':budget,'prompt_bytes':len(prompt.encode()),'shrink_fallback':False}
            report['results'].append(item)
            save()
            try:
                data=await wrapper.generate_evaluation(system_prompt=SYSTEM_PROMPT_STAGE3,
                    user_prompt=prompt,candidate_id=cid,max_provider_attempts=1)
                resolved=resolve_compact_selection(CompactSentenceSelectionOutput.model_validate(data),registry,cid)
                jd_text='\n'.join([jd.get('title',''),*jd.get('jd_category_queries',{}).values()])
                signals=['JD_INJECTION_SIGNAL'] if scan_for_injection_anomalies(jd_text)['is_flagged'] else []
                if scan_for_injection_anomalies(cid)['is_flagged']:
                    signals.append('IDENTIFIER_INJECTION_SIGNAL')
                result=compute_deterministic_tier(cid,resolved,full,registry=registry,injection_signals=signals)
                item.update(status=result.evaluation_status.value,result=result.model_dump(mode='json'))
                item['verification_failures']=sum(not check.valid for check in result.evidence_verification.checks)
            except httpx.HTTPStatusError as error:
                item.update(status='PROVIDER_REJECTED',http_status=error.response.status_code,
                            error_code='PROVIDER_CONTEXT_TOO_LARGE' if error.response.status_code==413 else 'PROVIDER_ERROR')
                # Preserve only numeric limit diagnostics, never raw provider error bodies.
                try:
                    message=str(error.response.json().get('error',{}).get('message',''))
                    for label,pattern in [('provider_requested_tokens',r'requested\s*[:=]?\s*(\d+)'),
                                          ('provider_limit_tokens',r'limit\s*[:=]?\s*(\d+)')]:
                        match=re.search(pattern,message,re.I)
                        if match:
                            item[label]=int(match.group(1))
                except (ValueError,TypeError,AttributeError):
                    pass
            except ProviderRateLimited as error:
                item.update(status='PROVIDER_REJECTED',error_code='PROVIDER_RATE_LIMITED',retry_after=error.retry_after)
            except Exception as error:
                item.update(status='VALIDATION_FAILED',error_type=type(error).__name__)
            save()
            print(json.dumps({key:item[key] for key in ('cv','jd','status','http_status','error_code',
                'provider_requested_tokens','provider_limit_tokens','verification_failures') if key in item}),flush=True)
            if item.get('error_code')=='PROVIDER_RATE_LIMITED':
                break
    finally:
        logger.removeHandler(handler)
        await wrapper.aclose()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    asyncio.run(run(args.output))
