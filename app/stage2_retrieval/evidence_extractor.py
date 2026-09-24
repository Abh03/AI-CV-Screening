from typing import List, Dict, Any
import asyncio
from app.stage2_retrieval.chunker import generate_cv_chunks
from app.stage2_retrieval.embeddings import generate_embeddings, generate_single_embedding
from app.stage2_retrieval.hybrid_search import execute_category_hybrid_search
from app.stage2_retrieval.reranker import rerank_category_chunks

DEFAULT_CATEGORY_WEIGHTS: Dict[str, float] = {
    "EXPERIENCE": 0.40,
    "SKILLS": 0.30,
    "PROJECTS": 0.15,
    "EDUCATION": 0.15
}


def extract_candidate_category_evidence(
    candidate_id: str,
    redacted_cv_text: str,
    jd_category_queries: Dict[str, str],
    weights: Dict[str, float] = DEFAULT_CATEGORY_WEIGHTS,
    source_pages: list[dict] | None = None
) -> Dict[str, Any]:
    """
    Processes a single candidate CV:
    1. Structure-aware chunking.
    2. Category-isolated Hybrid Retrieval (Dense + Sparse RRF).
    3. Category Cross-Encoder re-ranking.
    4. Composite candidate score calculation S_cand.
    """
    if not redacted_cv_text:
        return {"candidate_id": candidate_id, "composite_score": 0.0, "evidence_by_category": {}, "status": "EMPTY_CV"}

    chunks = generate_cv_chunks(redacted_cv_text, source_pages=source_pages)
    if not chunks:
        return {"candidate_id": candidate_id, "composite_score": 0.0, "evidence_by_category": {}, "status": "NO_CHUNKS"}

    # Stable content-derived identities until database document/chunk IDs arrive.
    from app.stage3_evaluation.evidence import stable_identity
    document_id = "redacted:" + stable_identity(candidate_id, redacted_cv_text)
    for chunk in chunks:
        chunk["candidate_id"] = candidate_id
        chunk["document_id"] = document_id
        chunk["chunk_id"] = "chunk:" + stable_identity(document_id, chunk["global_chunk_id"], chunk["text"])
        chunk.setdefault("source_location", {"section": chunk["section"], "chunk_index": chunk["chunk_index"]})

    # Generate Embeddings
    embeddings = generate_embeddings([c["text"] for c in chunks])
    for chunk, emb in zip(chunks, embeddings):
        chunk["embedding"] = emb

    evidence_by_category: Dict[str, List[Dict[str, Any]]] = {}
    category_scores: Dict[str, float] = {}

    for category in ["SKILLS", "EXPERIENCE", "PROJECTS", "EDUCATION"]:
        cat_query = jd_category_queries.get(category, "")
        cat_chunks = [c for c in chunks if c.get("category") == category]

        # Fallback to general chunks if candidate lacks specific category header
        if not cat_chunks and category in ["PROJECTS", "SKILLS"]:
            cat_chunks = [c for c in chunks if c.get("category") == "EXPERIENCE"]

        if not cat_query or not cat_chunks:
            evidence_by_category[category] = []
            category_scores[category] = 0.0
            continue

        query_vector = generate_single_embedding(cat_query)
        rrf_hits = execute_category_hybrid_search(
            query_text=cat_query,
            query_vector=query_vector,
            category_chunks=cat_chunks,
            top_k=10
        )

        # Re-rank Top 2 evidence chunks for this category
        top_ranked = rerank_category_chunks(
            category_query=cat_query,
            chunks=rrf_hits,
            top_n=2
        )

        for item in top_ranked:
            item.pop("embedding", None)

        evidence_by_category[category] = top_ranked

        if top_ranked:
            avg_cat_score = sum(c["rerank_score"] for c in top_ranked) / len(top_ranked)
            category_scores[category] = max(0.0, avg_cat_score)
        else:
            category_scores[category] = 0.0

    # Calculate Composite Candidate Score S_cand
    composite_score = sum(
        weights.get(cat, 0.25) * category_scores.get(cat, 0.0)
        for cat in ["EXPERIENCE", "SKILLS", "PROJECTS", "EDUCATION"]
    )

    return {
        "candidate_id": candidate_id,
        "composite_score": round(composite_score, 4),
        "category_scores": category_scores,
        "evidence_by_category": evidence_by_category,
        "status": "SUCCESS"
    }


async def extract_candidate_category_evidence_postgres(
    candidate_id: str, redacted_cv_text: str, jd_category_queries: Dict[str, str],
    job_id: str, weights: Dict[str, float] = DEFAULT_CATEGORY_WEIGHTS,
    source_pages: list[dict] | None = None
) -> Dict[str, Any]:
    """Persist redacted chunks and retrieve both branches in PostgreSQL."""
    from app.models.database import AsyncSessionLocal
    from app.stage2_retrieval.repository import PostgresRetrievalRepository

    if not redacted_cv_text:
        return {"candidate_id": candidate_id, "composite_score": 0.0,
                "evidence_by_category": {}, "status": "EMPTY_CV"}
    chunks = generate_cv_chunks(redacted_cv_text, source_pages=source_pages)
    if not chunks:
        return {"candidate_id": candidate_id, "composite_score": 0.0,
                "evidence_by_category": {}, "status": "NO_CHUNKS"}
    evidence_by_category = {}
    category_scores = {}
    async with AsyncSessionLocal() as session:
        repo = PostgresRetrievalRepository(session)
        document_id = await repo.prepare_document(candidate_id, redacted_cv_text,
                                                  chunks, source_pages)
        for category in ["SKILLS", "EXPERIENCE", "PROJECTS", "EDUCATION"]:
            query = jd_category_queries.get(category, "")
            if not query:
                evidence_by_category[category] = []
                category_scores[category] = 0.0
                continue
            query_vector = await repo.query_vector(job_id, category, query)
            hits = await repo.search(candidate_id, document_id, category, query,
                                     query_vector, fallback_to_experience=category in ("SKILLS", "PROJECTS"))
            await session.commit()
            ranked = await asyncio.to_thread(rerank_category_chunks, query, hits, top_n=2)
            evidence_by_category[category] = ranked
            category_scores[category] = (max(0.0, sum(c["rerank_score"] for c in ranked) / len(ranked))
                                         if ranked else 0.0)
        await session.commit()
    score = sum(weights.get(category, 0.25) * category_scores[category]
                for category in ["EXPERIENCE", "SKILLS", "PROJECTS", "EDUCATION"])
    return {"candidate_id": candidate_id, "composite_score": round(score, 4),
            "category_scores": category_scores, "evidence_by_category": evidence_by_category,
            "status": "SUCCESS"}


def rank_and_filter_candidate_batch(
    candidate_payloads: List[Dict[str, Any]],
    top_n_llm: int = 40
) -> List[Dict[str, Any]]:
    """
    Ranks all ~300 Stage 1 survivors globally by S_cand and selects Top 30-50 for LLM evaluation.
    """
    sorted_candidates = sorted(
        candidate_payloads,
        key=lambda x: x.get("composite_score", 0.0),
        reverse=True
    )
    return sorted_candidates[:top_n_llm]


def format_category_evidence_for_prompt(candidate_payload: Dict[str, Any]) -> str:
    """
    Formats category-isolated evidence chunks into XML structure for Stage 3 LLM prompts.
    """
    from xml.etree import ElementTree as ET
    from app.stage3_evaluation.prompts import build_stage3_user_prompt
    prompt = build_stage3_user_prompt(candidate_payload.get("candidate_id", "UNKNOWN"), {}, candidate_payload)
    evidence = ET.fromstring(prompt).find("candidate_evidence")
    evidence.set("candidate_id", candidate_payload.get("candidate_id", "UNKNOWN"))
    return ET.tostring(evidence, encoding="unicode")
