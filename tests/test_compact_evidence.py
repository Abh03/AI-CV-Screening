import pytest
from app.stage3_evaluation.context import prepare_evaluation_context, bounded_evaluation_prompt
from app.stage3_evaluation.evidence import build_evidence_registry, sentence_registry, verify_evidence
from app.stage3_evaluation.compact import evidence_manifest, resolve_compact_selection, SelectionScopeMismatch
from app.stage3_evaluation.schemas import CompactSentenceSelectionOutput
from app.stage3_evaluation.budget import selection_schema
from app.stage3_evaluation.prompts import build_stage3_user_prompt
from xml.etree import ElementTree as ET

CV='EXPERIENCE\nEngineer A 2020-2022. No Kafka experience. Basic Python for 2.33 years.\nEngineer B 2023-2025. No Kafka experience.\nPROJECTS\nBuilt SQL services.\nEDUCATION\nBSc Computer Science.'


def prepared(owner='a', retrieved='[Section: EXPERIENCE] No Kafka experience.'):
    payload={'candidate_id':owner,'candidate_cv_text':CV,'evidence_by_category':{
        'EXPERIENCE':[{'text':retrieved,'category':'EXPERIENCE','chunk_id':'retrieved'}]}}
    full=prepare_evaluation_context(owner,payload,complete_units=True)
    return full,sentence_registry(build_evidence_registry(owner,full))


def selected(registry,owner='a'):
    manifest=evidence_manifest(registry,owner)
    return CompactSentenceSelectionOutput.model_validate(dict({
        cat.lower():{'score':80,'rationale':'Provisional fit.',
                      'citations':[handle for handle,ref in manifest.handles.items() if ref.category==cat]}
        for cat in ('SKILLS','EXPERIENCE','PROJECTS','EDUCATION')},
        flags=[],executive_summary='Provisional fit.',evidence_scope=manifest.scope))


def test_dedup_preserves_full_cv_occurrences_provenance_and_source_limits():
    full,registry=prepared()
    manifest=evidence_manifest(registry,'a')
    assert manifest.deduplicated_ids
    context=[ref for ref,_ in manifest.rows if str(ref.document_id).startswith('context:')]
    assert ' '.join(' '.join(ref.text for ref in context).split())==' '.join(CV.split())
    assert sum(ref.text.strip()=='No Kafka experience.' for ref in context)==2
    assert any('Basic Python for 2.33 years.' in ref.text for ref in context)
    assert len(manifest.rows)<len(registry)
    assert not verify_evidence(resolve_compact_selection(selected(registry),registry,'a'),
        registry,'a',require_claim_support=True).review_reasons
    # Deduplication changes delivery, not the immutable verification registry.
    assert any(ref.chunk_id=='retrieved' for ref in registry.values())
    assert full['context_metadata']['complete']


def test_nonduplicate_and_partial_word_evidence_are_retained():
    _,registry=prepared(retrieved='Used Kafka in production.')
    assert not evidence_manifest(registry,'a').deduplicated_ids
    assert any(ref.text=='Used Kafka in production.' for ref,_ in evidence_manifest(registry).rows)
    payload={'candidate_cv_text':'SKILLS\nJavaScript.','evidence_by_category':{
        'SKILLS':[{'text':'Java','category':'SKILLS'}]}}
    full=prepare_evaluation_context('a',payload,complete_units=True)
    registry=sentence_registry(build_evidence_registry('a',full))
    assert not evidence_manifest(registry,'a').deduplicated_ids


@pytest.mark.parametrize('kind',['owner','registry','reorder'])
def test_scope_prevents_cross_candidate_and_changed_request_reuse(kind):
    _,registry=prepared()
    selection=selected(registry)
    if kind=='owner':
        _,other=prepared('b')
        owner='b'
    elif kind=='registry':
        other=dict(list(registry.items())[1:])
        owner='a'
    else:
        # A reordered retrieval request can change short handles while stable
        # evidence IDs remain the same. Old selections must not be reused.
        registry=sentence_registry(build_evidence_registry('a',{'evidence_by_category':{
            'SKILLS':[{'text':'Used Python.','chunk_id':'one'},
                      {'text':'Used SQL.','chunk_id':'two'}]}}))
        selection=selected(registry)
        other=dict(reversed(list(registry.items())))
        owner='a'
    with pytest.raises(SelectionScopeMismatch):
        resolve_compact_selection(selection,other,owner)


@pytest.mark.parametrize('kind',['unknown','wrong_category','legacy_tag'])
def test_handle_validation_does_not_weaken_citation_review(kind):
    _,registry=prepared()
    selection=selected(registry)
    manifest=evidence_manifest(registry)
    wrong=next(handle for handle,ref in manifest.handles.items() if ref.category=='EDUCATION')
    selection.skills.citations=[{'unknown':'S99999','wrong_category':wrong,'legacy_tag':'SKILLS:1'}[kind]]
    checked=verify_evidence(resolve_compact_selection(selection,registry,'a'),registry,'a',require_claim_support=True)
    assert 'INVALID_CITATIONS:skills' in checked.review_reasons
    assert 'UNSUPPORTED_CLAIM:skills' in checked.review_reasons


def test_schema_enumerates_short_handles_and_exact_ownership_scope():
    full,registry=prepared()
    prompt=build_stage3_user_prompt('a',{},full,registry=registry,selection_handles=True)
    schema=selection_schema(prompt)
    manifest=evidence_manifest(registry,'a')
    assert schema['properties']['evidence_scope']['enum']==[manifest.scope]
    assert set(schema['properties']['skills']['properties']['citations']['items']['enum'])=={
        handle for handle,ref in manifest.handles.items() if ref.category=='SKILLS'}
    assert 'ev_' not in prompt
    assert all(ref.text in [node.text for node in ET.fromstring(prompt).findall('.//e')]
               for ref,_ in manifest.rows)
    assert 'claims' not in schema['properties']['skills']['properties']


def test_empty_registry_still_binds_candidate_scope():
    assert evidence_manifest({},'a').scope!=evidence_manifest({},'b').scope


def test_groq_input_admission_and_completion_context_are_separate(monkeypatch):
    from types import SimpleNamespace
    from app.config import settings
    from app.stage3_evaluation import budget as module
    class Counter:
        def encode(self,*args,**kwargs):
            return SimpleNamespace(ids=[0]*2300)
    monkeypatch.setattr(module,'groq_tokenizer',lambda:Counter())
    monkeypatch.setattr(settings,'GROQ_MODEL','openai/gpt-oss-120b')
    monkeypatch.setattr(settings,'PROVIDER_TOKENS_PER_MINUTE',8000)
    monkeypatch.setattr(settings,'GROQ_CONTEXT_TOKENS',16000)
    monkeypatch.setattr(settings,'GROQ_MAX_COMPLETION_TOKENS',4096)
    result=module.request_budget('<evaluation_request />','groq')
    assert result['input']<8000<result['total']<16000
    assert result['fits'] and result['minute_reservation']==8000
    monkeypatch.setattr(settings,'GROQ_CONTEXT_TOKENS',8000)
    assert not module.request_budget('<evaluation_request />','groq')['fits']


def test_untrusted_tokenizer_is_rejected(tmp_path):
    from app.stage3_evaluation.budget import load_groq_tokenizer
    path=tmp_path/'tokenizer.json'
    path.write_text('{}')
    with pytest.raises(ValueError,match='Unexpected Groq tokenizer'):
        load_groq_tokenizer(str(path))
