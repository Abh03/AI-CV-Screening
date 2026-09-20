from typing import List, Dict, Any, Optional
from sentence_transformers import CrossEncoder

# Pre-trained Cross-Encoder optimized for passage re-ranking
_MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"
_reranker_instance: Optional[CrossEncoder] = None


def get_reranker_model() -> CrossEncoder:
    """Lazy initializer for CrossEncoder model."""
    global _reranker_instance
    if _reranker_instance is None:
        _reranker_instance = CrossEncoder(_MODEL_NAME)
    return _reranker_instance


def rerank_chunks(
    query_text: str,
    chunks: List[Dict[str, Any]],
    top_n: int = 5
) -> List[Dict[str, Any]]:
    """
    Re-ranks retrieved RRF chunks using full cross-attention over query-chunk pairs.
    Returns the top_n highest scoring chunks enriched with 'rerank_score'.
    """
    if not query_text or not chunks:
        return []

    model = get_reranker_model()

    # Form pairs: [ [query_text, chunk_text_1], [query_text, chunk_text_2], ... ]
    pairs = [[query_text, chunk.get("text", "")] for chunk in chunks]

    # Predict cross-attention relevance scores
    scores = model.predict(pairs)

    reranked_chunks = []
    for chunk, score in zip(chunks, scores):
        chunk_copy = chunk.copy()
        chunk_copy["rerank_score"] = round(float(score), 4)
        reranked_chunks.append(chunk_copy)

    # Sort descending by Cross-Encoder score
    reranked_chunks.sort(key=lambda x: x["rerank_score"], reverse=True)

    return reranked_chunks[:top_n]