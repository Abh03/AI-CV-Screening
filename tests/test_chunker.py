from app.stage2_retrieval.chunker import parse_cv_sections, generate_cv_chunks
from app.stage2_retrieval.embeddings import generate_embeddings, generate_single_embedding


def test_cv_section_parsing_and_canonical_normalization():
    sample_cv = (
        "PROFESSIONAL SUMMARY\n"
        "Senior Backend Engineer with 5 years experience.\n\n"
        "EMPLOYMENT HISTORY\n"
        "Lead Developer at Tech Corp (2021 - Present).\n"
        "Built microservices in FastAPI and PostgreSQL.\n\n"
        "CORE COMPETENCIES\n"
        "Python, Docker, Kubernetes, Redis, SQL\n\n"
        "KEY PROJECTS\n"
        "Distributed search engine with pgvector.\n\n"
        "ACADEMIC BACKGROUND\n"
        "Bachelor in Computer Science - TU, 2020\n\n"
        "LICENSES & CERTIFICATIONS\n"
        "AWS Certified Solutions Architect\n"
    )

    sections = parse_cv_sections(sample_cv)

    # Verify all non-standard section headers map cleanly to canonical keys
    assert "SUMMARY" in sections
    assert "EXPERIENCE" in sections
    assert "SKILLS" in sections
    assert "PROJECTS" in sections
    assert "EDUCATION" in sections
    assert "CERTIFICATIONS" in sections

    assert "FastAPI" in sections["EXPERIENCE"]
    assert "AWS" in sections["CERTIFICATIONS"]


def test_context_aware_chunk_generation():
    sample_cv = (
        "WORK EXPERIENCE\n"
        "Architected enterprise search engine using PostgreSQL pgvector and FastAPI.\n"
        "Optimized query performance reducing latency by 40 percent.\n\n"
        "TECHNICAL SKILLS\n"
        "Python, FastAPI, PostgreSQL, Docker, Kubernetes\n"
    )

    chunks = generate_cv_chunks(sample_cv)

    assert len(chunks) >= 2
    assert chunks[0]["text"].startswith("[Section: EXPERIENCE]")
    assert "global_chunk_id" in chunks[0]


def test_oversized_source_block_keeps_bounds_and_location():
    from app.stage2_retrieval.chunker import chunk_section_structurally

    pieces = chunk_section_structurally("EXPERIENCE", "Python " * 400)
    assert len(pieces) > 1
    assert all(len(piece["text"]) <= 600 for piece in pieces)
    assert [piece["chunk_index"] for piece in pieces] == list(range(len(pieces)))
    pages = [{"page_number": 2, "blocks": [
        {"block_number": 3, "bbox": [1, 2, 3, 4], "text": "TECHNICAL SKILLS"},
        {"block_number": 4, "bbox": [1, 5, 3, 8], "text": "Python " * 400}]}]
    chunks = generate_cv_chunks("", source_pages=pages)
    assert chunks and all(chunk["category"] == "SKILLS" for chunk in chunks)
    assert all(chunk["source_location"]["page_number"] == 2 and
               chunk["source_location"]["block_index"] == 4 for chunk in chunks)


def test_embedding_generation_vector_dimension():
    sample_texts = [
        "[Section: SKILLS] Python, FastAPI, Docker",
        "[Section: EXPERIENCE] Built distributed task queues with Celery"
    ]

    vectors = generate_embeddings(sample_texts)

    assert len(vectors) == 2
    assert len(vectors[0]) == 384
    assert isinstance(vectors[0][0], float)

    single_vector = generate_single_embedding("Python FastAPI Developer")
    assert len(single_vector) == 384
