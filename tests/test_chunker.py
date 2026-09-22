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