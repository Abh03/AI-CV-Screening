import pytest
from app.stage3_evaluation.evidence import (
    build_evidence_registry, resolve_evidence_selection, verify_evidence, claim_is_source_excerpt,
)
from app.stage3_evaluation.schemas import EvidenceSelectionOutput

CATEGORIES = ("SKILLS", "EXPERIENCE", "PROJECTS", "EDUCATION")


def registry(candidate="a"):
    return build_evidence_registry(candidate, {"evidence_by_category": {
        c: [{"text": "Used Python for 2 years. No Kafka experience...", "chunk_id": c}]
        for c in CATEGORIES}})


def selection(refs):
    result = {"flags": [], "executive_summary": "Provisional suitability assessment."}
    for tag, ref in refs.items():
        result[ref.category.lower()] = {"score": 80, "rationale": "Relevant supplied context.",
            "citations": [ref.evidence_id],
            "claims": [{"claim": "Used Python for 2 years.", "citation": ref.evidence_id}]}
    return EvidenceSelectionOutput.model_validate(result)


def verify(selected, refs, complete=True):
    output = resolve_evidence_selection(selected, refs, "a")
    return output, verify_evidence(output, refs, "a", require_claim_support=True,
                                  context_complete=complete)


def test_original_quotes_are_attached_and_source_ellipsis_is_preserved():
    refs = registry()
    output, checked = verify(selection(refs), refs)
    assert not checked.review_reasons
    assert output.skills.claims[0].quote == refs["SKILLS:1"].text
    assert output.skills.claims[0].evidence_id == refs["SKILLS:1"].evidence_id


@pytest.mark.parametrize("kind", ["unknown", "foreign", "wrong_category", "old_tag"])
def test_bad_selection_requires_review(kind):
    refs = registry()
    selected = selection(refs)
    bad = {"unknown": "ev_missing", "foreign": registry("b")["SKILLS:1"].evidence_id,
           "wrong_category": refs["EXPERIENCE:1"].evidence_id, "old_tag": "SKILLS:1"}[kind]
    selected.skills.citations = [bad]
    selected.skills.claims[0].citation = bad
    _, checked = verify(selected, refs)
    assert "INVALID_CITATIONS:skills" in checked.review_reasons
    assert "UNSUPPORTED_CLAIM:skills" in checked.review_reasons


@pytest.mark.parametrize("claim", ["Used Python for 8 years.", "Has Kafka experience",
                                    "Expert in Python", "Used Python and Kafka", "Kafka experience..."])
def test_related_words_or_valid_id_do_not_establish_claim_support(claim):
    refs = registry()
    selected = selection(refs)
    selected.skills.claims[0].claim = claim
    _, checked = verify(selected, refs)
    assert "UNSUPPORTED_CLAIM:skills" in checked.review_reasons


def test_missing_claim_and_incomplete_context_require_review():
    refs = registry()
    selected = selection(refs)
    selected.skills.claims = []
    _, checked = verify(selected, refs, complete=False)
    assert "MISSING_CLAIM_SUPPORT:skills" in checked.review_reasons
    assert "EVIDENCE_CONTEXT_TRUNCATED" in checked.review_reasons


def test_ids_survive_order_changes_and_change_with_source_or_owner():
    chunks = [{"text": "Python", "chunk_id": "one"}, {"text": "SQL", "chunk_id": "two"}]
    def ids(items, candidate="a"):
        return {r.text: r.evidence_id for r in build_evidence_registry(candidate,
            {"evidence_by_category": {"SKILLS": items}}).values()}
    assert ids(chunks) == ids(list(reversed(chunks)))
    assert ids(chunks) != ids(chunks, "b")
    assert ids(chunks)["Python"] != ids([dict(chunks[0], text="Python expert")])["Python expert"]


def test_provider_cannot_supply_quotes():
    data = selection(registry()).model_dump()
    data["skills"]["claims"][0]["quote"] = "fabricated"
    with pytest.raises(ValueError):
        EvidenceSelectionOutput.model_validate(data)


@pytest.mark.parametrize('claim,source', [
    ('Python', '[Section: SKILLS] Python, SQL, Docker'),
    ('C#', 'C#, ASP.NET Core, SQL Server'),
    ('Used Python', 'Used Python for 2 years.'),
    ('SQL to transform source records', 'Used SQL to transform source records and publish curated tables.'),
    ('Python, SQL', '[Section: SKILLS] Python,\nSQL, Docker'),
    ('No Kafka experience', 'No\nKafka experience. Used Python for 2 years.'),
    ('Basic Python knowledge from coursework', 'Skills: Basic Python knowledge from coursework.'),
    ('Python for 2 years. No Kafka experience', 'Used Python for 2 years. No Kafka experience. Also used SQL.'),
])
def test_source_contained_excerpts(claim, source):
    assert claim_is_source_excerpt(claim, source)


@pytest.mark.parametrize('claim,source', [
    ('Java', 'JavaScript'),
    ('Kafka experience', 'No\nKafka experience.'),
    ('Python knowledge', 'Basic Python knowledge.'),
    ('Built Python APIs', 'Assisted colleagues who Built Python APIs.'),
    ('Python', 'Learning Python.'),
    ('Used Python for 8 years', 'Used Python for 2 years.'),
    ('Expert in Python', 'Used Python for 2 years.'),
    ('No Kafka experience. Python knowledge', 'No Kafka experience. Python knowledge is limited.'),
])
def test_source_excerpts_do_not_drop_limits_or_add_facts(claim, source):
    assert not claim_is_source_excerpt(claim, source)


def test_excerpt_support_is_applied_to_id_claims():
    refs = registry()
    selected = selection(refs)
    selected.skills.claims[0].claim = 'Used Python'
    _, checked = verify(selected, refs)
    assert 'UNSUPPORTED_CLAIM:skills' not in checked.review_reasons
    selected.skills.claims[0].claim = 'Kafka experience'
    _, checked = verify(selected, refs)
    assert 'UNSUPPORTED_CLAIM:skills' in checked.review_reasons
