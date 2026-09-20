import math
import re
from typing import List, Dict, Any, Optional


def cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot_product / (norm_a * norm_b)


def compute_rrf_score(
    dense_results: List[Dict[str, Any]],
    sparse_results: List[Dict[str, Any]],
    k: int = 60,
    top_n: Optional[int] = None
) -> List[Dict[str, Any]]:
    scores: Dict[str, float] = {}
    chunks_metadata: Dict[str, Dict[str, Any]] = {}
    dense_ranks: Dict[str, int] = {}
    sparse_ranks: Dict[str, int] = {}

    for rank, item in enumerate(dense_results, start=1):
        chunk_id = str(item["chunk_id"])
        dense_ranks[chunk_id] = rank
        scores[chunk_id] = scores.get(chunk_id, 0.0) + (1.0 / (k + rank))
        if chunk_id not in chunks_metadata:
            chunks_metadata[chunk_id] = item

    for rank, item in enumerate(sparse_results, start=1):
        chunk_id = str(item["chunk_id"])
        sparse_ranks[chunk_id] = rank
        scores[chunk_id] = scores.get(chunk_id, 0.0) + (1.0 / (k + rank))
        if chunk_id not in chunks_metadata:
            chunks_metadata[chunk_id] = item

    fused_results = []
    for chunk_id, rrf_score in scores.items():
        meta = chunks_metadata[chunk_id].copy()
        meta["rrf_score"] = round(rrf_score, 6)
        meta["dense_rank"] = dense_ranks.get(chunk_id, None)
        meta["sparse_rank"] = sparse_ranks.get(chunk_id, None)
        fused_results.append(meta)

    fused_results.sort(key=lambda x: x["rrf_score"], reverse=True)
    if top_n is not None:
        return fused_results[:top_n]
    return fused_results


def execute_category_hybrid_search(
    query_text: str,
    query_vector: List[float],
    category_chunks: List[Dict[str, Any]],
    top_k: int = 10,
    k_rrf: int = 60
) -> List[Dict[str, Any]]:
    """
    Runs dense and sparse retrieval strictly over chunks belonging to a specific category.
    """
    if not category_chunks or not query_text:
        return []

    # 1. Dense Search
    dense_scored = []
    for chunk in category_chunks:
        emb = chunk.get("embedding")
        if emb:
            sim = cosine_similarity(query_vector, emb)
            c = chunk.copy()
            c["dense_score"] = round(sim, 4)
            dense_scored.append(c)
    dense_scored.sort(key=lambda x: x["dense_score"], reverse=True)
    dense_hits = dense_scored[:top_k]

    # 2. Sparse Search
    query_terms = [t.lower() for t in re.findall(r"\w+", query_text) if len(t) > 2]
    sparse_scored = []
    for chunk in category_chunks:
        text_lower = chunk.get("text", "").lower()
        score = 0.0
        for term in query_terms:
            score += len(re.findall(r"\b" + re.escape(term) + r"\b", text_lower)) * 1.0
        if score > 0.0:
            c = chunk.copy()
            c["sparse_score"] = score
            sparse_scored.append(c)
    sparse_scored.sort(key=lambda x: x["sparse_score"], reverse=True)
    sparse_hits = sparse_scored[:top_k]

    # 3. Reciprocal Rank Fusion
    return compute_rrf_score(dense_hits, sparse_hits, k=k_rrf, top_n=top_k)