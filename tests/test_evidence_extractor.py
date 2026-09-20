from app.stage2_retrieval.evidence_extractor import (
    extract_candidate_evidence,
    format_evidence_for_prompt
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

    jd_query = "FastAPI Backend Engineer with Kubernetes and PostgreSQL experience"

    payload = extract_candidate_evidence(
        redacted_cv_text=candidate_cv,
        jd_query_text=jd_query,
        top_rrf_k=10,
        top_evidence_n=3
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

    formatted_xml = format_evidence_for_prompt(mock_payload)

    assert "<retrieved_evidence>" in formatted_xml
    assert "</retrieved_evidence>" in formatted_xml
    assert 'section="EXPERIENCE"' in formatted_xml
    assert "Built FastAPI backend." in formatted_xml