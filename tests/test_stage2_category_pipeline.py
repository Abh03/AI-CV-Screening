from app.stage2_retrieval.chunker import generate_cv_chunks
from app.stage2_retrieval.evidence_extractor import (
    extract_candidate_category_evidence,
    rank_and_filter_candidate_batch,
    format_category_evidence_for_prompt
)


def test_structure_aware_chunking_and_category_tagging():
    sample_cv = (
        "PROFESSIONAL SUMMARY\n"
        "Backend engineer with expertise in distributed systems.\n\n"
        "WORK EXPERIENCE\n"
        "• Built microservices in Python FastAPI and PostgreSQL.\n"
        "• Reduced query latency by 45 percent using Redis caching clusters.\n"
        "• Managed Kubernetes deployments on AWS.\n\n"
        "TECHNICAL SKILLS\n"
        "Python, FastAPI, PostgreSQL, Docker, Kubernetes, AWS, Redis\n\n"
        "KEY PROJECTS\n"
        "• Distributed Payment Gateway: Processed 10k transactions per second.\n\n"
        "EDUCATION\n"
        "Bachelor in Computer Engineering - TU, 2020\n"
    )

    chunks = generate_cv_chunks(sample_cv)
    assert len(chunks) >= 4

    categories = {c["category"] for c in chunks}
    assert "EXPERIENCE" in categories
    assert "SKILLS" in categories
    assert "PROJECTS" in categories
    assert "EDUCATION" in categories


def test_category_aware_pipeline_and_batch_ranking():
    jd_category_queries = {
        "SKILLS": "Python FastAPI PostgreSQL Kubernetes AWS Redis",
        "EXPERIENCE": "Backend Engineer building high-throughput microservices",
        "PROJECTS": "Distributed payment systems and API gateways",
        "EDUCATION": "Bachelor degree in Computer Science or Software Engineering"
    }

    cv_strong = (
        "WORK EXPERIENCE\n"
        "Senior Backend Engineer developing FastAPI microservices with PostgreSQL.\n\n"
        "TECHNICAL SKILLS\n"
        "Python, FastAPI, PostgreSQL, Kubernetes, AWS\n\n"
        "PROJECTS\n"
        "Built distributed payment system processing high traffic.\n\n"
        "EDUCATION\n"
        "Bachelor in Computer Engineering - TU, 2020\n"
    )

    cv_weak = (
        "WORK EXPERIENCE\n"
        "Junior IT Support Assistant fixing desktop printers and office routers.\n\n"
        "TECHNICAL SKILLS\n"
        "Windows 10, MS Office, Hardware Maintenance\n\n"
        "EDUCATION\n"
        "High School Diploma in Humanities, 2018\n"
    )

    payload_strong = extract_candidate_category_evidence("cand_101", cv_strong, jd_category_queries)
    payload_weak = extract_candidate_category_evidence("cand_102", cv_weak, jd_category_queries)

    assert payload_strong["composite_score"] > payload_weak["composite_score"]

    batch = [payload_weak, payload_strong]
    top_candidates = rank_and_filter_candidate_batch(batch, top_n_llm=1)

    assert len(top_candidates) == 1
    assert top_candidates[0]["candidate_id"] == "cand_101"

    formatted_xml = format_category_evidence_for_prompt(payload_strong)
    assert '<category name="SKILLS">' in formatted_xml
    assert '<category name="EXPERIENCE">' in formatted_xml
    assert "cand_101" in formatted_xml