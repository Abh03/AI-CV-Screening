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
        redacted_cv_text=candidate_cv,
        jd_category_queries= jd_category_queries,
    )

    assert payload["status"] == "SUCCESS"
    assert payload["total_chunks_processed"] > 0
    assert len(payload["evidence_chunks"]) <= 3

    # Top chunk should capture experience/skills mentioning FastAPI/Kubernetes/PostgreSQL
    top_chunk = payload["evidence_chunks"][0]
    assert "rerank_score" in top_chunk
    assert any(term in top_chunk["text"] for term in ["FastAPI", "Kubernetes", "PostgreSQL"])


def test_format_evidence_for_prompt():
    mock_payload = {
        "status": "SUCCESS",
        "evidence_chunks": [
            {
                "section": "EXPERIENCE",
                "text": "[Section: EXPERIENCE] Built FastAPI backend.",
                "rerank_score": 0.895
            }
        ]
    }

    formatted_xml = format_category_evidence_for_prompt(mock_payload)

    assert "</candidate_evidence>" in formatted_xml