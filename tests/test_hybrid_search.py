from app.stage2_retrieval.hybrid_search import (
    compute_rrf_score,
    execute_category_hybrid_search
)
from app.stage2_retrieval.embeddings import generate_single_embedding


def test_rrf_scoring_math_and_ranking():
    # Chunk A: Ranked #1 in Dense, #2 in Sparse -> RRF = 1/(60+1) + 1/(60+2) = 0.032522
    # Chunk B: Ranked #85 in Dense, #1 in Sparse -> RRF = 1/(60+85) + 1/(60+1) = 0.023282
    dense_results = [
        {"chunk_id": "chunk_A", "text": "FastAPI PostgreSQL microservice"},
        {"chunk_id": "chunk_C", "text": "Python developer"},
    ]

    sparse_results = [
        {"chunk_id": "chunk_B", "text": "PostgreSQL database admin"},
        {"chunk_id": "chunk_A", "text": "FastAPI PostgreSQL microservice"},
    ]

    fused = compute_rrf_score(dense_results, sparse_results, k=60)

    # Chunk A should win because it scored highly across BOTH dense and sparse retrieval
    assert fused[0]["chunk_id"] == "chunk_A"
    assert fused[0]["dense_rank"] == 1
    assert fused[0]["sparse_rank"] == 2
    assert fused[0]["rrf_score"] == 0.032522

    assert fused[1]["chunk_id"] == "chunk_B"
    assert fused[1]["dense_rank"] is None
    assert fused[1]["sparse_rank"] == 1


def test_rrf_disjoint_sets():
    dense_results = [{"chunk_id": "chunk_1", "text": "Vector match"}]
    sparse_results = [{"chunk_id": "chunk_2", "text": "Keyword match"}]

    fused = compute_rrf_score(dense_results, sparse_results, k=60)

    assert len(fused) == 2
    # Both ranked #1 in their respective lists -> tied RRF score 1/(60+1) = 0.016393
    assert fused[0]["rrf_score"] == fused[1]["rrf_score"]


def test_end_to_end_hybrid_search():
    query_text = "FastAPI PostgreSQL Developer"
    query_vector = generate_single_embedding(query_text)

    # Mock chunk collection with vectors
    chunks = [
        {
            "chunk_id": "c1",
            "text": "[Section: EXPERIENCE] Built REST APIs in FastAPI and tuned PostgreSQL queries.",
            "embedding": generate_single_embedding("Built REST APIs in FastAPI and tuned PostgreSQL queries.")
        },
        {
            "chunk_id": "c2",
            "text": "[Section: SKILLS] Java, Spring Boot, MySQL",
            "embedding": generate_single_embedding("Java Spring Boot MySQL")
        },
        {
            "chunk_id": "c3",
            "text": "[Section: EXPERIENCE] Wrote bash automation scripts for Linux servers.",
            "embedding": generate_single_embedding("Wrote bash automation scripts for Linux servers.")
        }
    ]

    results = execute_category_hybrid_search(
        query_text=query_text,
        query_vector=query_vector,
        category_chunks=chunks,
        top_k=2
    )

    assert len(results) > 0
    # Highest relevance chunk must be c1
    assert results[0]["chunk_id"] == "c1"
    assert "FastAPI" in results[0]["text"]