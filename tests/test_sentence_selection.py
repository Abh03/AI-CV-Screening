import json
import pytest
from app.stage3_evaluation.evidence import build_evidence_registry, sentence_registry, resolve_sentence_selection, verify_evidence
from app.stage3_evaluation.schemas import SentenceSelectionOutput
from app.stage3_evaluation.context import bounded_evaluation_prompt
from app.stage3_evaluation.budget import selection_schema, request_budget


def refs(owner='a'):
    return sentence_registry(build_evidence_registry(owner, {'evidence_by_category':{
        category:[{'text':'No\nKafka experience. Basic Python for 2.33 years. Built SQL services.', 'chunk_id':category}]
        for category in ('SKILLS','EXPERIENCE','PROJECTS','EDUCATION')}}))


def selection(registry):
    return SentenceSelectionOutput.model_validate(dict(
        {cat.lower():{'score':80,'rationale':'Provisional fit based on supplied evidence.',
                      'citations':[ref.evidence_id for ref in registry.values() if ref.category==cat]}
         for cat in ('SKILLS','EXPERIENCE','PROJECTS','EDUCATION')}, flags=[],
        executive_summary='Provisional category suitability.'))


def test_sentence_facts_preserve_wrapped_negation_qualifiers_and_decimals():
    registry=refs()
    assert len(registry)==12
    output=resolve_sentence_selection(selection(registry),registry,'a')
    assert [c.claim for c in output.skills.claims]==['No\nKafka experience.',' Basic Python for 2.33 years.',' Built SQL services.']
    assert all(c.claim==c.quote for c in output.skills.claims)
    assert not verify_evidence(output,registry,'a',require_claim_support=True).review_reasons
    with pytest.raises(ValueError):
        resolve_sentence_selection(selection(registry),registry,'b')


@pytest.mark.parametrize('bad',['foreign','category','invented','position'])
def test_sentence_invalid_selections_still_require_review(bad):
    registry=refs()
    selected=selection(registry)
    value={'foreign':refs('b')['SKILLS:1'].evidence_id,'category':registry['PROJECTS:1'].evidence_id,
           'invented':'ev_fake','position':'SKILLS:1'}[bad]
    selected.skills.citations=[value]
    checked=verify_evidence(resolve_sentence_selection(selected,registry,'a'),registry,'a',require_claim_support=True)
    assert 'INVALID_CITATIONS:skills' in checked.review_reasons
    assert 'UNSUPPORTED_CLAIM:skills' in checked.review_reasons


def test_request_schema_is_local_and_category_constrained():
    payload={'evidence_by_category':{c:[{'text':'No Kafka. Used Python.'}] for c in ('SKILLS','EXPERIENCE','PROJECTS','EDUCATION')}}
    prepared,registry,prompt=bounded_evaluation_prompt('a',{},payload,sentence_selections=True)
    schema=selection_schema(prompt)
    data=selection(registry).model_dump()
    for category in ('skills','experience','projects','education'):
        allowed=schema['properties'][category]['properties']['citations']['items']['enum']
        assert set(data[category]['citations']) <= set(allowed)
        assert registry['EXPERIENCE:1'].evidence_id not in schema['properties']['skills']['properties']['citations']['items']['enum']
    assert 'claims' not in schema['properties']['skills']['properties']
    assert schema['properties']['skills']['additionalProperties'] is False
    data['skills']['claims']=[{'claim':'Expert','citation':'ev_fake'}]
    with pytest.raises(ValueError):
        SentenceSelectionOutput.model_validate(data)
    assert '$ref' in selection_schema()['properties']['skills']
    budget=prepared['context_metadata']['token_budget']
    assert budget['system']>0 and budget['schema']>0 and budget['completion']>0
    assert budget['total']<=budget['limit']


def test_budget_omits_whole_context_and_reports_it(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings,'GROQ_CONTEXT_TOKENS',8000)
    monkeypatch.setattr(settings,'LLM_PROVIDER','groq')
    payload={'candidate_cv_text':'PROJECTS\n'+'Built Python APIs. '*500,'evidence_by_category':{}}
    prepared,registry,prompt=bounded_evaluation_prompt('a',{},payload,sentence_selections=True)
    assert not prepared['context_metadata']['complete']
    assert prepared['context_metadata']['omitted_passage_ids']
    assert request_budget(prompt,'groq')['total']<=8000
    assert not registry
