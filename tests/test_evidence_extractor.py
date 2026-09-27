from app.stage2_retrieval.evidence_extractor import (
    extract_candidate_category_evidence,
    format_category_evidence_for_prompt
)
import pytest


@pytest.mark.parametrize("query", [None, "", "   ", "No explicit requirement", " no EXPLICIT requirement. "])
def test_project_query_fallback_uses_mandatory_skills(query):
    from app.stage2_retrieval.evidence_extractor import resolve_retrieval_queries
    from app.stage1_rules.jd_profiler import SkillCluster
    queries = {"SKILLS": "Python SQL"}
    if query is not None:
        queries["PROJECTS"] = query
    original = dict(queries)
    resolved = resolve_retrieval_queries(queries, [
        {"canonical": "Python", "aliases": ["python3"], "substitutes": ["Java"]},
        SkillCluster(canonical="SQL"), {"canonical": "Python"},
    ])
    assert resolved["PROJECTS"] == "Projects demonstrating hands-on experience with Python, SQL"
    assert resolved["SKILLS"] == "Python SQL"
    assert queries == original


def test_project_query_preserves_explicit_target_and_handles_no_skills():
    from app.stage2_retrieval.evidence_extractor import resolve_retrieval_queries
    assert resolve_retrieval_queries({"PROJECTS": "Payment gateways"}, []) == {
        "PROJECTS": "Payment gateways"}
    assert resolve_retrieval_queries({}, [])["PROJECTS"] == "Projects demonstrating hands-on experience"


def test_synthetic_project_query_reaches_retrieval_and_reranking(monkeypatch):
    import app.stage2_retrieval.evidence_extractor as extractor
    seen = []
    monkeypatch.setattr(extractor, "generate_embeddings", lambda texts: [[0.0] * 384 for _ in texts])
    monkeypatch.setattr(extractor, "generate_single_embedding", lambda query: [0.0] * 384)

    def search(**kwargs):
        seen.append(kwargs["query_text"])
        assert all(chunk["category"] == "PROJECTS" for chunk in kwargs["category_chunks"])
        return kwargs["category_chunks"]

    def rerank(category_query, chunks, top_n):
        seen.append(category_query)
        return [dict(chunk, rerank_score=0.8) for chunk in chunks[:top_n]]

    monkeypatch.setattr(extractor, "execute_category_hybrid_search", search)
    monkeypatch.setattr(extractor, "rerank_category_chunks", rerank)
    payload = extractor.extract_candidate_category_evidence(
        "candidate", "SELECTED PROJECTS\nBuilt a Python API.",
        {"PROJECTS": "No explicit requirement"}, required_skills=[{"canonical": "Python"}])
    assert seen == ["Projects demonstrating hands-on experience with Python"] * 2
    assert payload["evidence_by_category"]["PROJECTS"][0]["section"] == "PROJECTS"


@pytest.mark.asyncio
async def test_postgres_uses_synthetic_project_target(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock
    import app.models.database as database
    import app.stage2_retrieval.repository as repository
    import app.stage2_retrieval.evidence_extractor as extractor

    session = AsyncMock()
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(database, "AsyncSessionLocal", lambda: context)
    repo = MagicMock()
    repo.prepare_document = AsyncMock(return_value="document")
    repo.query_vector = AsyncMock(return_value=[0.0] * 384)
    repo.search = AsyncMock(return_value=[])
    monkeypatch.setattr(repository, "PostgresRetrievalRepository", lambda _: repo)
    monkeypatch.setattr(extractor, "rerank_category_chunks", lambda *args, **kwargs: [])
    await extractor.extract_candidate_category_evidence_postgres(
        "candidate", "SELECTED PROJECTS\nBuilt a SQL data pipeline.",
        {"PROJECTS": "No explicit requirement"}, "job",
        required_skills=[{"canonical": "SQL"}])
    query = "Projects demonstrating hands-on experience with SQL"
    repo.query_vector.assert_awaited_once_with("job", "PROJECTS", query)
    repo.search.assert_awaited_once_with("candidate", "document", "PROJECTS", query,
                                        [0.0] * 384, fallback_to_experience=True,
                                        sparse_plan=extractor.build_sparse_plan(query, "PROJECTS", [{"canonical": "SQL"}]))
    chunks = repo.prepare_document.call_args.args[2]
    assert chunks[0]["category"] == "PROJECTS"


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
