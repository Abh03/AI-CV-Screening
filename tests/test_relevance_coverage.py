import pytest
from pydantic import ValidationError
from app.stage1_rules.relevance import RelevanceContract
from app.stage2_retrieval.coverage import resolve_targets, score_coverage, SCORING_VERSION
from app.stage2_retrieval.evidence_extractor import DEFAULT_CATEGORY_WEIGHTS, rank_and_filter_candidate_batch


def target(identifier="payments", category="EXPERIENCE", **overrides):
    return dict(target_id=identifier, category=category, kind="responsibility",
                text="Develop payment APIs in Java", source_quote="Develop payment APIs in Java",
                importance=1, treatment="requirement",
                evidence_terms=[["payment API", "payment APIs"], ["Java"]], origin="approved", **overrides)


def chunk(text, category="EXPERIENCE", identifier="work", score=-12):
    return dict(chunk_id=identifier, category=category, text=text, rerank_score=score)


def test_negative_logits_do_not_zero_supported_work_and_projects():
    targets = [target()]
    evidence = {"EXPERIENCE": [chunk("Built payment APIs in Java", "PROJECTS")]}
    result = score_coverage(targets, evidence, DEFAULT_CATEGORY_WEIGHTS)
    assert result["composite_score"] == 1
    assert result["category_scores"]["EXPERIENCE"] == 1
    assert result["category_scores"]["EDUCATION"] is None
    assert result["active_category_weights"] == {"EXPERIENCE": 1}
    assessment = result["target_assessments"][0]
    assert assessment["status"] == "DIRECT" and assessment["citations"] == ["work"]
    assert assessment["supporting_text"] == "Built payment APIs in Java"
    assert result["shortlist_eligible"]


def test_skill_list_cannot_substitute_for_applied_delivery():
    targets, minimum = resolve_targets({}, [{"canonical": ".NET", "aliases": ["dotnet"]}])
    result = score_coverage(targets, {"SKILLS": [chunk(".NET", "SKILLS")],
        "EXPERIENCE": [chunk("Skills: dotnet", "SKILLS")]}, DEFAULT_CATEGORY_WEIGHTS, minimum)
    assert result["category_scores"]["SKILLS"] == 1
    assert result["category_scores"]["EXPERIENCE"] == 0
    assert not result["shortlist_eligible"]
    assert result["relevance_review_required"]


def test_missing_evidence_keeps_weight_while_absent_requirement_is_not_applicable():
    targets = [target(), target("degree", "EDUCATION") | {"kind": "education", "evidence_terms": [["Bachelor"]]}]
    result = score_coverage(targets, {"EXPERIENCE": [chunk("Built payment APIs in Java")]}, DEFAULT_CATEGORY_WEIGHTS)
    assert result["category_scores"]["EDUCATION"] == 0
    assert result["category_scores"]["PROJECTS"] is None
    assert result["composite_score"] == pytest.approx(.4 / .55, abs=1e-6)
    assert result["target_assessments"][1]["status"] == "MISSING_INFORMATION"


def test_domain_preferences_are_bounded_and_never_qualify_alone():
    domain = target("banking") | {"kind": "domain", "treatment": "preference", "evidence_terms": [["banking"]], "importance": 5}
    result = score_coverage([target(), domain], {"EXPERIENCE": [chunk("Digital banking") ]}, DEFAULT_CATEGORY_WEIGHTS)
    assert result["composite_score"] == .1
    assert not result["shortlist_eligible"]


def test_negated_claims_and_unrelated_sentences_do_not_create_a_direct_match():
    targets = [target()]
    result = score_coverage(targets, {"EXPERIENCE": [chunk("Never built payment APIs in Java")]}, DEFAULT_CATEGORY_WEIGHTS)
    assert result["composite_score"] == 0
    split = score_coverage(targets, {"EXPERIENCE": [chunk("Built payment APIs. Java certification.")]}, DEFAULT_CATEGORY_WEIGHTS)
    assert split["target_assessments"][0]["status"] == "PARTIAL"
    assert split["target_assessments"][0]["coverage"] == .5
    assert not split["shortlist_eligible"]


def test_dotnet_aliases_and_accepted_substitutes_are_distinct():
    targets, _ = resolve_targets({}, [{"canonical": ".NET", "substitutes": ["Java"]}])
    evidence = {"SKILLS": [chunk("dotnet", "SKILLS")], "EXPERIENCE": [chunk("Built services using Java")]}
    result = score_coverage(targets, evidence, DEFAULT_CATEGORY_WEIGHTS)
    assert result["category_scores"]["SKILLS"] == 1
    assert result["category_scores"]["EXPERIENCE"] == .5


def test_cap_is_15_with_evidence_ties_and_no_zero_padding():
    rows = [dict(candidate_id=f"{i:03}", scoring_version=SCORING_VERSION, shortlist_eligible=True,
                 composite_score=.5, responsibility_coverage=i % 2) for i in range(40)]
    rows += [dict(candidate_id="zero", composite_score=0, scoring_version=SCORING_VERSION,
                  shortlist_eligible=False)]
    selected = rank_and_filter_candidate_batch(rows)
    assert len(selected) == 15
    assert all(int(row["candidate_id"]) % 2 for row in selected)
    assert rank_and_filter_candidate_batch(rows[-1:]) == []
    assert rank_and_filter_candidate_batch([dict(candidate_id="legacy", composite_score=100)]) == []
    with pytest.raises(ValueError):
        rank_and_filter_candidate_batch(rows, 30)


def test_contract_rejects_duplicate_ids_and_domain_hardening():
    entry = target()
    entry.pop("origin")
    with pytest.raises(ValidationError):
        RelevanceContract(targets=[entry, entry])
    with pytest.raises(ValidationError):
        RelevanceContract(targets=[entry | {"kind": "domain"}])


def test_no_requirements_and_no_targets_go_to_review():
    targets, minimum = resolve_targets({"PROJECTS": "No explicit requirement", "EXPERIENCE": "Minimum 3 years"})
    result = score_coverage(targets, {}, DEFAULT_CATEGORY_WEIGHTS, minimum)
    assert result["relevance_reason"] == "NO_RELEVANCE_TARGETS"
    assert not result["shortlist_eligible"]


def test_production_requires_reviewed_source_context(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    targets, _ = resolve_targets({}, [{"canonical": "Java"}])
    result = score_coverage(targets, {"SKILLS": [chunk("Java", "SKILLS")],
        "EXPERIENCE": [chunk("Built Java services")]}, DEFAULT_CATEGORY_WEIGHTS)
    assert result["composite_score"] == 1
    assert not result["shortlist_eligible"]
    assert result["relevance_reason"] == "SOURCE_CONTEXT_REVIEW_REQUIRED"


def test_api_and_configuration_enforce_15_as_a_maximum():
    from app.api.schemas import ScreeningRequestSchema, PDFScreeningRequestSchema
    from app.config import Settings
    job = dict(job_id="job", title="Role", jd_category_queries={})
    assert ScreeningRequestSchema(job_profile=job, candidates=[]).top_n_stage2_cutoff == 15
    with pytest.raises(ValidationError):
        ScreeningRequestSchema(job_profile=job, candidates=[], top_n_stage2_cutoff=16)
    with pytest.raises(ValidationError):
        PDFScreeningRequestSchema(job_profile=job, candidate_id="candidate", pdf_base64="pdf",
                                 top_n_stage2_cutoff=16)
    with pytest.raises(ValidationError):
        Settings(DEFAULT_STAGE2_CUTOFF=30)


def test_reviewed_wording_equivalents_reach_sparse_retrieval():
    from app.stage2_retrieval.evidence_extractor import target_sparse_plan
    entry = target() | {"evidence_terms": [["payment APIs", "payment endpoints"], ["Java"]]}
    plan = target_sparse_plan(entry["text"], "EXPERIENCE", [entry])
    assert any(concept.matches("payment endpoints") and concept.priority == "required" for concept in plan.concepts)


@pytest.mark.asyncio
async def test_memory_and_postgres_share_coverage_and_keep_optional_education_context(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock
    import app.stage2_retrieval.evidence_extractor as extractor
    import app.stage2_retrieval.repository as repository
    import app.models.database as database
    from app.stage2_retrieval.chunker import generate_cv_chunks
    cv = "PROJECTS\nBuilt payment APIs using dotnet.\nEDUCATION\nBachelor of Science"
    contract = {"targets": [target() | {"evidence_terms": [["payment APIs"], [".NET"]]}]}
    contract["targets"][0].pop("origin")
    queries = {"EXPERIENCE": "Minimum 3 years", "PROJECTS": "No explicit requirement",
               "EDUCATION": "No explicit requirement"}
    monkeypatch.setattr(extractor, "generate_embeddings", lambda texts: [[0] * 384 for _ in texts])
    monkeypatch.setattr(extractor, "generate_single_embedding", lambda query: [0] * 384)
    monkeypatch.setattr(extractor, "execute_category_hybrid_search", lambda **kwargs: kwargs["category_chunks"])
    monkeypatch.setattr(extractor, "rerank_category_chunks",
                        lambda query, chunks, top_n: [dict(chunk, rerank_score=-8) for chunk in chunks[:top_n]])
    memory = extractor.extract_candidate_category_evidence("candidate", cv, queries,
        required_skills=[{"canonical": ".NET"}], relevance_contract=contract)
    session = AsyncMock()
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(database, "AsyncSessionLocal", lambda: context)
    chunks = [dict(chunk, chunk_id=f"chunk-{i}", candidate_id="candidate", document_id="document")
              for i, chunk in enumerate(generate_cv_chunks(cv))]
    repo = MagicMock()
    repo.prepare_document = AsyncMock(return_value="document")
    repo.query_vector = AsyncMock(return_value=[0] * 384)
    async def search(candidate, document, category, query, vector, **kwargs):
        return [chunk for chunk in chunks if chunk["category"] == category]
    repo.search = AsyncMock(side_effect=search)
    monkeypatch.setattr(repository, "PostgresRetrievalRepository", lambda _: repo)
    postgres = await extractor.extract_candidate_category_evidence_postgres("candidate", cv, queries, "job",
        required_skills=[{"canonical": ".NET"}], relevance_contract=contract)
    for result in (memory, postgres):
        assert result["composite_score"] == 1
        assert result["shortlist_eligible"]
        assert result["category_scores"]["EDUCATION"] is None
        assert result["evidence_by_category"]["EDUCATION"][0]["text"].endswith("Bachelor of Science")
        assert result["target_assessments"][0]["status"] == "DIRECT"
    assert memory["category_scores"] == postgres["category_scores"]
