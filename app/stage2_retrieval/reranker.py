from typing import List, Dict, Any, Optional
from sentence_transformers import CrossEncoder
from threading import BoundedSemaphore
from app.config import settings

_MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"
_reranker_instance: Optional[CrossEncoder] = None
_rerank_slots = BoundedSemaphore(settings.RERANK_CONCURRENCY_LIMIT)


def get_reranker_model() -> CrossEncoder:
    global _reranker_instance
    if _reranker_instance is None:
        _reranker_instance = CrossEncoder(_MODEL_NAME)
    return _reranker_instance


def rerank_category_chunks(
    category_query: str,
    chunks: List[Dict[str, Any]],
    top_n: int = 2
) -> List[Dict[str, Any]]:
    if not category_query or not chunks:
        return []

    with _rerank_slots:
        model = get_reranker_model()
        pairs = [[category_query, chunk.get("text", "")] for chunk in chunks]
        scores = model.predict(pairs)

    reranked = []
    for chunk, score in zip(chunks, scores):
        c = chunk.copy()
        c["rerank_score"] = round(float(score), 4)
        reranked.append(c)

    reranked.sort(key=lambda x: x["rerank_score"], reverse=True)
    return reranked[:top_n]
