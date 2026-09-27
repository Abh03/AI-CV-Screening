from datetime import date
from xml.etree import ElementTree as ET
import json

import httpx
import pytest

from app.stage1_rules.experience import extract_experience
from app.stage2_retrieval.coverage import resolve_targets, score_coverage
from app.stage2_retrieval.evidence_extractor import DEFAULT_CATEGORY_WEIGHTS
from app.stage3_evaluation.context import bounded_evaluation_prompt
from app.stage3_evaluation.evidence import build_evidence_registry, verify_evidence
from app.stage3_evaluation.llm_client import LLMClientWrapper, evaluate_single_candidate_async
from app.stage3_evaluation.schemas import LLMEvaluationOutput, SupportedClaim


@pytest.mark.parametrize("history,months", [
    ("Engineer | A\nJan 2020 - Jan 2022\nEngineer | B\n01/2021 - 01/2023", 36),
    ("Engineer | A\n01/2020 - 01/2021\nEngineer | B\n01/2022 - 01/2023", 24),
    ("Engineer | A\n12/2025 - Present", 9),
    ("Engineer | A\n2020-01 to 2021-01", 12),
    ("Engineer | A\n2020 - 2023", None),
    ("Engineer | A\nFeb 2023 - Jan 2022", None),
    ("Engineer | A\n13/2020 - 01/2022", None),
    ("Engineer | A\nJan 2020 - Jan 2030", None),
    ("Engineer | A\nJan 2020 - Jan 2022\nEngineer | B\nUndated role", None),
])
def test_work_history_union_and_uncertainty(history, months):
    result = extract_experience("WORK EXPERIENCE\n" + history + "\nEDUCATION\n2010 - 2014",
                                as_of=date(2026, 9, 27))
    assert (result["months"] if result else None) == months


def test_explicit_claim_takes_precedence_and_projects_do_not_count():
    assert extract_experience("Overall experience: 5 years\nWORK EXPERIENCE\nJan 2020 - Jan 2021")["years"] == 5
    assert extract_experience("PROJECTS\nJan 2020 - Jan 2025") is None
    assert extract_experience("Over 3 years of industry experience\nWORK EXPERIENCE\nJan 2020 - Jan 2021") is None


@pytest.mark.parametrize("dates,code", [
    ("Jan 2020 - Dec 2022", "EXPERIENCE_DATE_PRECISION"),
    ("Jan 2020 - Nov 2022", "INSUFFICIENT_EXPERIENCE"),
    ("Jan 2020 - Jan 2023", "EXPERIENCE_UNVERIFIED"),
])
def test_month_precision_does_not_create_a_borderline_rejection(dates, code):
    from app.stage1_rules.rules_engine import evaluate_stage1_hard_filters
    result = evaluate_stage1_hard_filters(None, "WORK EXPERIENCE\nEngineer | A\n" + dates, None,
        {"min_years_experience": 3, "require_work_authorization": False, "must_have_skills": []})
    assert next(check for check in result["checks"] if check["rule"] == "experience")["code"] == code


def responsibility(groups, **kwargs):
    return dict(target_id="r", category="EXPERIENCE", kind="responsibility",
                text="Deliver services", source_quote="Deliver services", importance=1,
                treatment="requirement", evidence_terms=groups, **kwargs)


def test_bundled_responsibility_can_be_partial_without_becoming_direct():
    targets, minimum = resolve_targets({}, relevance_contract={"targets": [
        responsibility([["lineage"], ["retention"], ["access controls"], ["batch workloads"]])]})
    result = score_coverage(targets, {"EXPERIENCE": [{"chunk_id": "work", "category": "EXPERIENCE",
        "text": "Documented lineage and retention policies."}]}, DEFAULT_CATEGORY_WEIGHTS, minimum)
    assert result["shortlist_eligible"]
    assert result["target_assessments"][0]["status"] == "PARTIAL"
    assert result["target_assessments"][0]["coverage"] == .5


def test_required_technology_delivery_qualifies_without_domain_phrase():
    contract = {"targets": [responsibility([["claims workflow"], ["API"], ["background processing"]])]}
    targets, minimum = resolve_targets({}, required_skills=[{"canonical": "Entity Framework"}],
                                       relevance_contract=contract)
    result = score_coverage(targets, {"EXPERIENCE": [{"chunk_id": "work", "category": "EXPERIENCE",
        "text": "Investigated Entity Framework query behaviour."}]}, DEFAULT_CATEGORY_WEIGHTS, minimum)
    assert result["shortlist_eligible"]
    result = score_coverage(targets, {"EXPERIENCE": [{"chunk_id": "list", "category": "EXPERIENCE",
        "text": "Environment: Entity Framework"}]}, DEFAULT_CATEGORY_WEIGHTS, minimum)
    assert not result["shortlist_eligible"]


def test_preference_only_category_does_not_dilute_required_coverage():
    main = responsibility([["Java"]])
    optional = dict(main, target_id="domain", category="PROJECTS", kind="domain", treatment="preference",
                    evidence_terms=[["retail"]])
    targets, _ = resolve_targets({}, relevance_contract={"targets": [main, optional]})
    result = score_coverage(targets, {"EXPERIENCE": [{"chunk_id": "work", "category": "EXPERIENCE",
        "text": "Built Java services"}]}, DEFAULT_CATEGORY_WEIGHTS)
    assert result["composite_score"] == 1
    assert result["category_scores"]["PROJECTS"] is None
    missing_optional = dict(optional, category="EXPERIENCE")
    targets, _ = resolve_targets({}, relevance_contract={"targets": [main, missing_optional]})
    result = score_coverage(targets, {"EXPERIENCE": [{"chunk_id": "work", "category": "EXPERIENCE",
        "text": "Built Java services"}]}, DEFAULT_CATEGORY_WEIGHTS)
    assert result["composite_score"] == 1


def test_complete_shared_context_is_sent_once_with_category_citations():
    text = "WORK EXPERIENCE\nBuilt Python services.\nPROJECTS\nBuilt a SQL pipeline."
    prepared, registry, prompt = bounded_evaluation_prompt("a", {}, {"candidate_cv_text": text})
    root = ET.fromstring(prompt)
    assert prepared["context_metadata"]["complete"]
    assert prompt.count("Built Python services.") == 1
    assert root.findall(".//snippet[@source_tag]")
    for snippet in root.findall(".//snippet[@source_tag]"):
        source = root.find(f'.//snippet[@tag="{snippet.attrib["source_tag"]}"]')
        assert source.text == registry[snippet.attrib["tag"]].text


@pytest.mark.parametrize("quote", ["Built ... services", "Built … services"])
def test_ellipses_are_rejected_even_when_literal_source_contains_them(quote):
    registry = build_evidence_registry("a", {"evidence_by_category": {"EXPERIENCE": [{"text": quote}]}})
    output = LLMEvaluationOutput.model_validate(LLMClientWrapper._call_mock("a"))
    output.experience.claims = [SupportedClaim(claim="Built services", quote=quote, citation="EXPERIENCE:1")]
    result = verify_evidence(output, registry, "a", require_claim_support=True)
    assert any(check.reason == "ELLIPSIS_IN_QUOTE" for check in result.checks)


def large_payload():
    return {"candidate_id": "a", "evidence_by_category": {cat: [
        {"chunk_id": f"{cat}-{i}", "text": "Built <services> & tested 数据. " * 30}
        for i in range(4)] for cat in ("SKILLS", "EXPERIENCE", "PROJECTS", "EDUCATION")}}


def test_serialized_unicode_xml_is_bounded_and_matches_registry():
    original = large_payload()
    prepared, registry, prompt = bounded_evaluation_prompt("a", {}, original, max_bytes=6000)
    assert len(prompt.encode("utf-8")) <= 6000
    assert prepared["context_metadata"]["complete"] is False
    assert len(original["evidence_by_category"]["SKILLS"]) == 4
    for snippet in ET.fromstring(prompt).findall(".//snippet"):
        if snippet.attrib["tag"] != "NONE":
            source = ET.fromstring(prompt).find(f'.//snippet[@tag="{snippet.attrib["source_tag"]}"]') if "source_tag" in snippet.attrib else snippet
            assert registry[snippet.attrib["tag"]].text == source.text


@pytest.mark.asyncio
async def test_413_retries_smaller_context_and_marks_review():
    class Provider:
        def __init__(self):
            self.sizes = []

        async def generate_structured_evaluation(self, system_prompt, user_prompt):
            self.sizes.append(len(user_prompt.encode("utf-8")))
            if len(self.sizes) == 1:
                response = httpx.Response(413, request=httpx.Request("POST", "https://example.test"))
                raise httpx.HTTPStatusError("Too large", request=response.request, response=response)
            return json.dumps(LLMClientWrapper._call_mock("a"))

    provider = Provider()
    result = await evaluate_single_candidate_async(large_payload(), {}, provider)
    assert len(provider.sizes) == 2
    assert provider.sizes[1] <= provider.sizes[0] // 2
    assert "EVIDENCE_CONTEXT_TRUNCATED" in result.evidence_verification.review_reasons
