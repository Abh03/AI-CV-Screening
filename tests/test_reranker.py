from app.stage2_retrieval.reranker import rerank_category_chunks


def test_cross_encoder_reranking_precision():
    query_text = "Senior Python Developer with FastAPI and PostgreSQL microservices experience"

    # Chunks retrieved from Step 4.2 RRF
    rrf_chunks = [
        {
            "chunk_id": "c1",
            "section": "SUMMARY",
            "text": "[Section: SUMMARY] Experienced software developer proficient in Web development and MySQL.",
            "rrf_score": 0.032
        },
        {
            "chunk_id": "c2",
            "section": "EXPERIENCE",
            "text": "[Section: EXPERIENCE] Lead Python Architect: Designed high-throughput FastAPI microservices backed by PostgreSQL and Redis.",
            "rrf_score": 0.030
        },
        {
            "chunk_id": "c3",
            "section": "EXPERIENCE",
            "text": "[Section: EXPERIENCE] Maintained legacy Django applications and wrote basic SQL queries.",
            "rrf_score": 0.028
        }
    ]

    # Run Cross-Encoder Re-ranking
    reranked = rerank_category_chunks(category_query=query_text, chunks=rrf_chunks, top_n=2)

    assert len(reranked) == 2
    
    # c2 must be re-ranked to position #1 due to exact joint requirement alignment
    assert reranked[0]["chunk_id"] == "c2"
    assert "rerank_score" in reranked[0]
    assert reranked[0]["rerank_score"] > reranked[1]["rerank_score"]


def test_empty_reranker_inputs():
    assert rerank_category_chunks("", [{"text": "Sample text"}]) == []
    assert rerank_category_chunks("Python Query", []) == []