import json
from xml.etree import ElementTree as ET

import pytest

from app.stage1_rules.experience import extract_experience
from app.stage2_retrieval.chunker import generate_cv_chunks
from app.stage3_evaluation.context import prepare_evaluation_context
from app.stage3_evaluation.evidence import build_evidence_registry, verify_evidence
from app.stage3_evaluation.llm_client import LLMClientWrapper, evaluate_single_candidate_async
from app.stage3_evaluation.prompts import build_stage3_user_prompt
from app.stage3_evaluation.schemas import LLMEvaluationOutput, SupportedClaim


CV = ("PROFILE\nPractitioner with 2.33 years\nof industry experience.\n\n"
      "PROFESSIONAL EXPERIENCE\nJunior ETL Developer | Example\n2024 - 2026\n"
      "Maintained Airflow retries and freshness alerts.\n\n"
      "PROJECTS\nBuilt a Python pipeline with SQL and operational monitoring.\n\n"
      "TECHNICAL SKILLS\nPython, SQL, Airflow\n\nEDUCATION\nBSc Computer Science")


def data():
    return {"candidate_id": "a", "candidate_cv_text": CV, "evidence_by_category": {
        "PROJECTS": [{"text": "[Section: EXPERIENCE] 2024 - 2026", "category": "EXPERIENCE"}]}}


def test_complete_cv_is_citable_with_exact_source_offsets_and_stable_identity():
    prepared = prepare_evaluation_context("a", data())
    registry = build_evidence_registry("a", prepared)
    assert prepared["context_metadata"]["complete"]
    assert prepared["context_metadata"]["included_context_chars"] == len(CV)
    refs = [ref for ref in registry.values() if ref.document_id]
    for ref in refs:
        assert ref.text == CV[ref.source_location.char_start:ref.source_location.char_end]
    assert any("freshness alerts" in ref.text for ref in refs if ref.category == "EXPERIENCE")
    assert any("operational monitoring" in ref.text for ref in refs if ref.category == "PROJECTS")
    assert registry == build_evidence_registry("a", data())
    root = ET.fromstring(build_stage3_user_prompt("a", {}, data()))
    assert root.find("candidate_cv_text") is None  # Context is supplied once, through citations.
    assert json.loads(root.find("context_metadata").text)["complete"]
    assert len(root.findall(".//snippet")) == len(registry)


def test_large_context_has_bounded_evidence_and_explicit_review():
    payload = dict(data(), candidate_cv_text=CV + "\nPROJECTS\n" + "Long project detail.\n" * 1000)
    prepared = prepare_evaluation_context("a", payload, max_chars=4000)
    assert prepared["context_metadata"]["evidence_chars"] <= 4000
    assert prepared["context_metadata"]["omitted_passage_ids"]
    result = verify_evidence(LLMEvaluationOutput.model_validate(LLMClientWrapper._call_mock("a")),
        build_evidence_registry("a", prepared), "a", context_complete=False)
    assert "EVIDENCE_CONTEXT_TRUNCATED" in result.review_reasons


def test_headerless_cv_has_citable_context_for_every_category():
    payload = {"candidate_cv_text": "Python engineer. BSc Computer Science. Built a SQL pipeline."}
    registry = build_evidence_registry("a", payload)
    assert {ref.category for ref in registry.values()} == {"SKILLS", "EXPERIENCE", "PROJECTS", "EDUCATION"}
    assert {ref.source_category for ref in registry.values()} == {"CONTEXT"}
    assert all(ref.text == payload["candidate_cv_text"] for ref in registry.values())


@pytest.mark.parametrize("claim,quote,reason", [
    ("Has 8 years of industry experience", "2.33 years of industry experience", "NUMERIC_CLAIM_NOT_IN_QUOTE"),
    ("Built Kafka pipelines", "Built Kafka pipelines", "QUOTE_NOT_IN_SOURCE"),
])
def test_existing_citation_cannot_validate_fabricated_quote_or_numeric_claim(claim, quote, reason):
    registry = build_evidence_registry("a", data())
    tag = next(tag for tag, ref in registry.items() if ref.category == "EXPERIENCE" and "2.33" in ref.text)
    output = LLMEvaluationOutput.model_validate(LLMClientWrapper._call_mock("a"))
    output.experience.citations = [tag]
    output.experience.claims = [SupportedClaim(claim=claim, citation=tag, quote=quote)]
    result = verify_evidence(output, registry, "a", require_claim_support=True)
    assert any(check.reason == reason for check in result.checks)
    assert "UNSUPPORTED_CLAIM:experience" in result.review_reasons


def test_cross_block_role_and_dates_survive_continuation_chunks():
    blocks = ["PROFESSIONAL EXPERIENCE", "Junior ETL Developer | Example", "2024 - 2026",
              "- Maintained Airflow retries and freshness alerts. " * 25,
              "Senior Engineer | Other", "2026 - Present", "Built SQL pipelines."]
    pages = [{"page_number": 1, "blocks": [{"block_number": i, "bbox": [0, i, 100, i+1], "text": text}
                                             for i, text in enumerate(blocks)]}]
    chunks = generate_cv_chunks("", source_pages=pages)
    junior = [chunk for chunk in chunks if "Junior ETL" in chunk["text"]]
    assert len(junior) > 1
    assert all("2024 - 2026" in chunk["text"] for chunk in junior)
    assert all(len(chunk["text"]) <= 600 for chunk in chunks)
    assert all([loc["block_index"] for loc in chunk["source_location"]["blocks"]] == [1, 2, 3]
               for chunk in junior)
    assert not any("Other" in chunk["text"] for chunk in junior)


@pytest.mark.parametrize("text,years", [
    ("Practitioner with 2.33 years\nof industry experience.", 2.33),
    ("Has 5 years of experience with Python", None),
    ("Over 3 years of industry experience", None),
    ("3+ years of industry experience", None),
    ("2 years of industry experience\nOverall experience: 4 years", None),
])
def test_explicit_experience_and_ambiguity(text, years):
    result = extract_experience(text)
    assert (result["years"] if result else None) == years


@pytest.mark.asyncio
async def test_provider_receives_citable_full_context_and_support_is_verified():
    class Provider:
        async def generate_structured_evaluation(self, system_prompt, user_prompt):
            root = ET.fromstring(user_prompt)
            result = LLMClientWrapper._call_mock("a")
            for category in ("skills", "experience", "projects", "education"):
                snippets = root.findall(f'.//category[@name="{category.upper()}"]/snippet')
                snippet = snippets[-1]
                result[category]["citations"] = [snippet.attrib["tag"]]
                result[category]["claims"] = [{"claim": "The CV supplies this context.",
                    "citation": snippet.attrib["tag"], "quote": snippet.text}]
            assert "Built a Python pipeline" in user_prompt
            return json.dumps(result)

    result = await evaluate_single_candidate_async(data(), {}, Provider())
    assert result.evidence_verification.context_metadata["complete"]
    assert not result.invalid_citations
    assert not result.review_reasons
