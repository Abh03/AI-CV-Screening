from app.stage2_retrieval.chunker import parse_cv_sections, generate_cv_chunks
from app.stage2_retrieval.embeddings import generate_embeddings, generate_single_embedding
import pytest


@pytest.mark.parametrize("heading", [
    "SELECTED PROJECTS", "KEY PROJECTS", "ACADEMIC PROJECTS", "PERSONAL PROJECTS",
    "MAJOR PROJECTS", "NOTABLE PROJECTS", "RELEVANT PROJECTS", "TECHNICAL PROJECTS",
    "PROFESSIONAL PROJECTS", "RESEARCH PROJECTS", "PROJECT EXPERIENCE",
    "PROJECT HIGHLIGHTS", "PROJECT PORTFOLIO", "Selected Projects:",
])
def test_project_heading_aliases_preserve_category_and_provenance(heading):
    project = "Built a Python payments API with PostgreSQL and automated tests."
    text = f"SUMMARY\nBackend developer.\n{heading}\n{project}\nEDUCATION\nComputer Science degree."
    sections = parse_cv_sections(text)
    assert sections["PROJECTS"] == project
    assert project not in sections["SUMMARY"]
    chunks = generate_cv_chunks(text)
    projects = [chunk for chunk in chunks if chunk["category"] == "PROJECTS"]
    assert projects and all(chunk["section"] == "PROJECTS" for chunk in projects)

    pages = [{"page_number": 1, "blocks": [
        {"block_number": 0, "bbox": [0, 0, 10, 10], "text": "SUMMARY\nBackend developer."},
        {"block_number": 1, "bbox": [0, 10, 10, 20], "text": heading},
    ]}, {"page_number": 2, "blocks": [
        {"block_number": 0, "bbox": [0, 0, 10, 10], "text": project},
        {"block_number": 1, "bbox": [0, 10, 10, 20], "text": "EDUCATION\nComputer Science degree."},
    ]}]
    projects = [chunk for chunk in generate_cv_chunks(text, source_pages=pages)
                if chunk["category"] == "PROJECTS"]
    assert len(projects) == 1
    assert projects[0]["source_location"] == {
        "page_number": 2, "block_index": 0, "bbox": [0, 0, 10, 10],
        "section": "PROJECTS", "chunk_index": 0,
    }


def test_embedded_project_heading_is_recognized_in_source_block():
    pages = [{"page_number": 1, "blocks": [{"block_number": 0,
        "bbox": [0, 0, 10, 10], "text":
        "SUMMARY\nBackend developer.\nSELECTED PROJECTS\nBuilt a Python API."}]}]
    chunks = generate_cv_chunks("", source_pages=pages)
    assert [chunk["category"] for chunk in chunks] == ["EXPERIENCE", "PROJECTS"]
    assert chunks[1]["source_location"]["section"] == "PROJECTS"


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
