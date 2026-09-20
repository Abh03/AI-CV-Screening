import math
import re
from typing import List, Dict, Any, Optional


def cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
    """Computes the cosine similarity between two dense vectors."""
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
    """
    Calculates Reciprocal Rank Fusion (RRF) scores across dense and sparse rank lists.
    RRF(d) = sum_{m in M} (1 / (k + rank_m(d)))
    """
    scores: Dict[str, float] = {}
    chunks_metadata: Dict[str, Dict[str, Any]] = {}
    dense_ranks: Dict[str, int] = {}
    sparse_ranks: Dict[str, int] = {}

    # Process Dense Search Ranks
    for rank, item in enumerate(dense_results, start=1):
        chunk_id = str(item["chunk_id"])
        dense_ranks[chunk_id] = rank
        scores[chunk_id] = scores.get(chunk_id, 0.0) + (1.0 / (k + rank))
        if chunk_id not in chunks_metadata:
            chunks_metadata[chunk_id] = item

    # Process Sparse Search Ranks
    for rank, item in enumerate(sparse_results, start=1):
        chunk_id = str(item["chunk_id"])
        sparse_ranks[chunk_id] = rank
        scores[chunk_id] = scores.get(chunk_id, 0.0) + (1.0 / (k + rank))
        if chunk_id not in chunks_metadata:
            chunks_metadata[chunk_id] = item

    # Construct fused result list
    fused_results = []
    for chunk_id, rrf_score in scores.items():
        meta = chunks_metadata[chunk_id].copy()
        meta["rrf_score"] = round(rrf_score, 6)
        meta["dense_rank"] = dense_ranks.get(chunk_id, None)
        meta["sparse_rank"] = sparse_ranks.get(chunk_id, None)
        fused_results.append(meta)

    # Sort by fused RRF score descending
    fused_results.sort(key=lambda x: x["rrf_score"], reverse=True)

    if top_n is not None:
        return fused_results[:top_n]

    return fused_results


def in_memory_dense_search(
    query_vector: List[float],
    chunks: List[Dict[str, Any]],
    top_k: int = 20
) -> List[Dict[str, Any]]:
    """Simulates dense vector search using cosine similarity for test/standalone environments."""
    scored_chunks = []
    for chunk in chunks:
        vector = chunk.get("embedding")
        if vector:
            sim = cosine_similarity(query_vector, vector)
            chunk_copy = chunk.copy()
            chunk_copy["score"] = round(sim, 4)
            scored_chunks.append(chunk_copy)

    scored_chunks.sort(key=lambda x: x["score"], reverse=True)
    return scored_chunks[:top_k]


def in_memory_sparse_search(
    query_text: str,
    chunks: List[Dict[str, Any]],
    top_k: int = 20
) -> List[Dict[str, Any]]:
    """Simulates lexical full-text search by term match frequency for test/standalone environments."""
    query_terms = [t.lower() for t in re.findall(r"\w+", query_text) if len(t) > 2]
    scored_chunks = []

    for chunk in chunks:
        text_lower = chunk.get("text", "").lower()
        score = 0.0
        for term in query_terms:
            matches = len(re.findall(r"\b" + re.escape(term) + r"\b", text_lower))
            score += matches * 1.0

        if score > 0.0:
            chunk_copy = chunk.copy()
            chunk_copy["score"] = score
            scored_chunks.append(chunk_copy)

    scored_chunks.sort(key=lambda x: x["score"], reverse=True)
    return scored_chunks[:top_k]


def execute_hybrid_search(
    query_text: str,
    query_vector: List[float],
    chunks: List[Dict[str, Any]],
    top_k_dense: int = 20,
    top_k_sparse: int = 20,
    k_rrf: int = 60,
    top_n: int = 10
) -> List[Dict[str, Any]]:
    """
    Main Stage 2 retrieval function: runs parallel dense and sparse retrieval
    and merges the ranked outputs via Reciprocal Rank Fusion (RRF).
    """
    dense_hits = in_memory_dense_search(query_vector, chunks, top_k=top_k_dense)
    sparse_hits = in_memory_sparse_search(query_text, chunks, top_k=top_k_sparse)

    return compute_rrf_score(
        dense_results=dense_hits,
        sparse_results=sparse_hits,
        k=k_rrf,
        top_n=top_n
    )