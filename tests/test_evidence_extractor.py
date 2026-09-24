from app.stage2_retrieval.evidence_extractor import (
    extract_candidate_category_evidence,
    format_category_evidence_for_prompt
)


def test_full_stage2_evidence_extraction():
    candidate_cv = (
        "PROFESSIONAL SUMMARY\n"
        "Senior Cloud Engineer specialized in Python, FastAPI, and Kubernetes.\n\n"
        "WORK EXPERIENCE\n"
        "Lead DevOps Engineer at CloudCorp (2021 - Present).\n"
        "Architected Kubernetes microservices clusters and deployed FastAPI backends on AWS.\n"
        "Implemented PostgreSQL connection pooling reducing database latency by 35%.\n\n"
        "TECHNICAL SKILLS\n"
        "Python, FastAPI, Docker, Kubernetes, AWS, PostgreSQL, Redis\n\n"
        "EDUCATION\n"
        "Bachelor of Engineering in Computer Science - TU, 2020\n"
    )

    jd_category_queries={
            "SKILLS": "Python FastAPI PostgreSQL",
            "EXPERIENCE": "Backend API engineer"
        }
    payload = extract_candidate_category_evidence(
        candidate_id="cand_evidence",
        redacted_cv_text=candidate_cv,
        jd_category_queries= jd_category_queries,
    )

    assert payload["status"] == "SUCCESS"
    chunks = [chunk for group in payload["evidence_by_category"].values() for chunk in group]
    assert chunks
    assert all(len(group) <= 2 for group in payload["evidence_by_category"].values())
    assert all("rerank_score" in chunk for chunk in chunks)
    assert any("FastAPI" in chunk["text"] for chunk in chunks)



def test_format_evidence_for_prompt():
    mock_payload = {
        "status": "SUCCESS",
        "evidence_by_category": {"EXPERIENCE": [
            {
                "section": "EXPERIENCE",
                "text": "[Section: EXPERIENCE] Built FastAPI backend.",
                "rerank_score": 0.895
            }
        ]}
    }

    formatted_xml = format_category_evidence_for_prompt(mock_payload)

    assert "</candidate_evidence>" in formatted_xml
    assert "Built FastAPI backend." in formatted_xml
