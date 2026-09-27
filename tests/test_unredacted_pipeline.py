from xml.etree import ElementTree as ET

import fitz
import pytest

from app import orchestrator
from app.config import settings
from app.stage0_extraction import pipeline
from app.stage3_evaluation.prompts import build_stage3_user_prompt
from app.stage3_evaluation.scoring import failed_evaluation


CV = ("Jane Doe\njane@example.com\nKathmandu\n"
      "Java | Python | SQL | C# | .NET | AWS | Azure | Spring Boot | Selenium | Apache Spark\n"
      "PROFILE\nJava developer building Spring Boot services.\n"
      "SKILLS\nJava, Python, Apache Spark\nEDUCATION\nBachelor degree 2018\n")


@pytest.mark.parametrize("scan", [False, True])
def test_pdf_and_ocr_bypass_every_masker(monkeypatch, scan):
    def forbidden(*args):
        raise AssertionError("Screening must not mask CV text")

    for name in ("mask_header_zone", "mask_body_zone", "mask_contact_lines"):
        monkeypatch.setattr(pipeline, name, forbidden)
    monkeypatch.setattr(pipeline, "assess_readable_extraction_integrity",
                        lambda text: {"requires_ocr": not bool(text), "passed": bool(text)})
    monkeypatch.setattr(pipeline, "_ocr_page", lambda page: CV)
    with fitz.open() as doc:
        page = doc.new_page()
        if not scan:
            page.insert_text((40, 40), CV, fontsize=8)
        result = pipeline.ingest_pdf(doc.tobytes())
    assert result.status == "success"
    for term in ("Jane Doe", "jane@example.com", "Kathmandu", "Java", "Apache Spark", "2018"):
        assert term in result.redacted_text
    assert result.redacted_text == "\n\n".join(
        block["text"] for page in result.pages for block in page["blocks"])
    assert "[REDACTED" not in result.redacted_text


@pytest.mark.asyncio
async def test_full_cv_reaches_rules_retrieval_and_llm_prompt(monkeypatch):
    monkeypatch.setattr(settings, "STAGE2_BACKEND", "memory")
    seen = []
    original_rules = orchestrator.evaluate_stage1_hard_filters

    def rules(**kwargs):
        assert kwargs["candidate_cv_text"] == CV
        result = original_rules(**kwargs)
        assert not any(check["code"] == "REQUIRED_SKILL_UNCERTAIN" for check in result["checks"])
        seen.append("rules")
        return result

    def retrieve(**kwargs):
        assert kwargs["redacted_cv_text"] == CV
        seen.append("retrieval")
        return {"candidate_id": "candidate", "status": "SUCCESS", "composite_score": 1,
                "scoring_version": "stage2-coverage-v1", "shortlist_eligible": True, "evidence_by_category": {"SKILLS": [{"text": "Java, Python, Apache Spark"}]}}

    async def evaluate(candidate_payloads, jd_profile, **kwargs):
        payload = candidate_payloads[0]
        prompt = ET.fromstring(build_stage3_user_prompt("candidate", jd_profile, payload))
        snippets = [node.text for node in prompt.findall(".//snippet")]
        for term in ("Jane Doe", "jane@example.com", "Kathmandu", "Spring Boot", "Bachelor degree 2018"):
            assert any(term in text for text in snippets)
        assert prompt.find("context_metadata") is not None
        assert "Java" in prompt.find("candidate_evidence/category/snippet").text
        seen.append("prompt")
        return [failed_evaluation("candidate", "PROVIDER_ERROR", "test")]

    monkeypatch.setattr(orchestrator, "evaluate_stage1_hard_filters", rules)
    monkeypatch.setattr(orchestrator, "extract_candidate_category_evidence", retrieve)
    monkeypatch.setattr(orchestrator, "evaluate_candidate_batch_async", evaluate)
    result = await orchestrator.run_end_to_end_screening_pipeline(
        [{"candidate_id": "candidate", "raw_cv_text": CV,
          "recruiter_overrides": {"work_authorized": "eligible"}}],
        {"job_id": "java", "title": "Java Developer", "jd_category_queries": {"SKILLS": "Java"},
         "must_have_skills": [{"canonical": "Java", "aliases": [], "substitutes": []}]})
    assert seen == ["rules", "retrieval", "prompt"]
    assert result["outcomes"][0]["input_snapshot"]["redacted_cv_text"] == CV
