from typing import List, Dict, Any
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
    weights: Dict[str, float] = DEFAULT_CATEGORY_WEIGHTS
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

    chunks = generate_cv_chunks(redacted_cv_text)
    if not chunks:
        return {"candidate_id": candidate_id, "composite_score": 0.0, "evidence_by_category": {}, "status": "NO_CHUNKS"}

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
    evidence_map = candidate_payload.get("evidence_by_category", {})
    lines = [f'<candidate_evidence candidate_id="{candidate_payload.get("candidate_id")}">']

    for category in ["SKILLS", "EXPERIENCE", "PROJECTS", "EDUCATION"]:
        chunks = evidence_map.get(category, [])
        lines.append(f'  <category name="{category}">')
        if not chunks:
            lines.append("    <snippet>No relevant evidence extracted.</snippet>")
        else:
            for idx, c in enumerate(chunks, start=1):
                score = c.get("rerank_score", 0.0)
                text = c.get("text", "")
                lines.append(f'    <snippet index="{idx}" relevance="{score}">\n      {text}\n    </snippet>')
        lines.append("  </category>")

    lines.append("</candidate_evidence>")
    return "\n".join(lines)