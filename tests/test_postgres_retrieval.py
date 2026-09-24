"""Run with --run-infrastructure against PostgreSQL 16 + pgvector."""
import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.engine import make_url
import psycopg2

from app.config import settings
from app.stage2_retrieval.chunker import generate_cv_chunks
from app.stage2_retrieval.repository import PostgresRetrievalRepository

pytestmark = [pytest.mark.infrastructure, pytest.mark.asyncio]


@pytest.fixture
def migrated_database(monkeypatch):
    """Provision a fresh database through the documented Alembic path."""
    name = "stage2_test_" + uuid.uuid4().hex[:16]
    source = make_url(settings.DATABASE_URL)
    admin_dsn = source.set(drivername="postgresql", database="postgres").render_as_string(hide_password=False)
    conn = psycopg2.connect(admin_dsn)
    conn.autocommit = True
    with conn.cursor() as cursor:
        cursor.execute(f'CREATE DATABASE "{name}"')
    test_url = source.set(database=name)
    monkeypatch.setattr(settings, "DATABASE_URL", test_url.render_as_string(hide_password=False))
    try:
        command.upgrade(Config("alembic.ini"), "head")
        yield test_url.render_as_string(hide_password=False)
    finally:
        with conn.cursor() as cursor:
            cursor.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        conn.close()


async def test_migrations_scoped_retrieval_and_cache(migrated_database, monkeypatch):
    from app.stage2_retrieval import repository as module

    calls = []
    def embed(texts):
        calls.extend(texts)
        return [[1.0] + [0.0] * 383 if "Python" in t else [0.0, 1.0] + [0.0] * 382
                for t in texts]
    monkeypatch.setattr(module, "generate_embeddings", embed)
    monkeypatch.setattr(module, "generate_single_embedding", lambda _: [1.0] + [0.0] * 383)
    engine = create_async_engine(migrated_database)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            assert 160000 <= int((await session.execute(text("SHOW server_version_num"))).scalar_one()) < 170000
            assert (await session.execute(text("SELECT extversion FROM pg_extension WHERE extname='vector'"))).scalar_one()
            assert (await session.execute(text("SELECT version_num FROM alembic_version"))).scalar_one() == "92a1c4d06e9f"
            repo = PostgresRetrievalRepository(session)
            first = generate_cv_chunks("SKILLS\nPython FastAPI PostgreSQL\n\nEXPERIENCE\nBuilt Python services")
            other = generate_cv_chunks("SKILLS\nPython and Kubernetes")
            doc_a = await repo.prepare_document("candidate-a", "SKILLS\nPython FastAPI PostgreSQL\n\nEXPERIENCE\nBuilt Python services", first)
            doc_b = await repo.prepare_document("candidate-b", "SKILLS\nPython and Kubernetes", other)
            vector = await repo.query_vector("job-a", "SKILLS", "Python")
            hits = await repo.search("candidate-a", doc_a, "SKILLS", "Python", vector)
            assert hits and all(h["candidate_id"] == "candidate-a" and h["category"] == "SKILLS" for h in hits)
            assert all(h["document_id"] == doc_a and h["source_location"] for h in hits)
            assert any(h["sparse_rank"] is not None and h["dense_rank"] is not None for h in hits)
            assert not await repo.search("candidate-b", doc_a, "SKILLS", "Python", vector)
            assert not await repo.search("candidate-a", doc_b, "SKILLS", "Python", vector)
            assert not await repo.search("candidate-a", doc_a, "EDUCATION", "Python", vector)
            assert await repo.search("candidate-a", doc_a, "PROJECTS", "Python", vector,
                                     fallback_to_experience=True)
            assert not await repo.search("candidate-a", doc_a, "PROJECTS", "Python", vector)
            assert (await session.execute(text("SELECT fallback_category FROM category_metadata "
                                               "WHERE category='EXPERIENCE'"))).scalar_one_or_none() is None
            count = len(calls)
            await repo.prepare_document("candidate-a", "SKILLS\nPython FastAPI PostgreSQL\n\nEXPERIENCE\nBuilt Python services", first)
            assert len(calls) == count
            moved = generate_cv_chunks("SKILLS\nPython FastAPI PostgreSQL\n\nEXPERIENCE\nBuilt Python services")
            moved_doc = await repo.prepare_document("candidate-a",
                "SKILLS\nPython FastAPI PostgreSQL\n\nEXPERIENCE\nBuilt Python services",
                moved, source_pages=[{"page_number": 2}])
            assert moved_doc != doc_a and len(calls) == count
            assert await repo.query_vector("job-a", "SKILLS", "Python") == vector
            assert (await session.execute(text("SELECT count(*) FROM job_query_embeddings"))).scalar_one() == 1
            changed = generate_cv_chunks("SKILLS\nPython FastAPI PostgreSQL Redis")
            new_doc = await repo.prepare_document("candidate-a", "SKILLS\nPython FastAPI PostgreSQL Redis", changed)
            assert new_doc != doc_a and len(calls) > count
            assert all(h["document_id"] == new_doc for h in
                       await repo.search("candidate-a", new_doc, "SKILLS", "Python", vector))
            monkeypatch.setattr(module, "EMBEDDING_MODEL_VERSION", "fixture-v2")
            count = len(calls)
            await repo.prepare_document("candidate-a", "SKILLS\nPython FastAPI PostgreSQL Redis", changed)
            assert len(calls) > count
            with pytest.raises(Exception):
                async with session.begin_nested():
                    await session.execute(text("""INSERT INTO chunk_embeddings
                        (chunk_id, model_name, model_version, vector)
                        VALUES (:id, 'bad', 'bad', CAST('[1,2,3]' AS vector))"""),
                        {"id": changed[0]["chunk_id"]})
            await session.commit()
        async with engine.connect() as connection:
            await connection.execute(text("SET enable_seqscan=off"))
            plan = " ".join(row[0] for row in (await connection.execute(text("""
                EXPLAIN SELECT id FROM source_chunks WHERE candidate_id='candidate-a'
                AND document_id=:document AND category='SKILLS'
            """), {"document": doc_a})).all())
            assert "ix_source_chunks_scope" in plan
            fts_plan = " ".join(row[0] for row in (await connection.execute(text("""
                EXPLAIN SELECT id FROM source_chunks
                WHERE tsv_content @@ websearch_to_tsquery('english', 'Python')
            """))).all())
            assert "ix_source_chunks_fts" in fts_plan
    finally:
        await engine.dispose()


async def test_production_extractor_keeps_rrf_rerank_and_cutoff(migrated_database, monkeypatch):
    from app.models import database
    from app.stage2_retrieval import repository, evidence_extractor

    engine = create_async_engine(migrated_database)
    monkeypatch.setattr(database, "AsyncSessionLocal", async_sessionmaker(engine, expire_on_commit=False))
    counts = {"document": 0, "query": 0, "rerank": 0}

    def embed(texts):
        counts["document"] += len(texts)
        return [[1.0] + [0.0] * 383 for _ in texts]

    def query_embed(_):
        counts["query"] += 1
        return [1.0] + [0.0] * 383

    def rerank(query, chunks, top_n=2):
        counts["rerank"] += 1
        return [dict(chunk, rerank_score=5.0) for chunk in chunks[:top_n]]

    monkeypatch.setattr(repository, "generate_embeddings", embed)
    monkeypatch.setattr(repository, "generate_single_embedding", query_embed)
    monkeypatch.setattr(evidence_extractor, "rerank_category_chunks", rerank)
    cv = "SKILLS\nPython FastAPI PostgreSQL\n\nEXPERIENCE\nBuilt Python services"
    queries = {"SKILLS": "Python", "EXPERIENCE": "Python", "PROJECTS": "Python"}
    try:
        first = await evidence_extractor.extract_candidate_category_evidence_postgres(
            "candidate-a", cv, queries, "job-a")
        assert first["composite_score"] > 0
        assert first["evidence_by_category"]["SKILLS"][0]["dense_rank"] == 1
        assert first["evidence_by_category"]["SKILLS"][0]["sparse_rank"] == 1
        assert first["evidence_by_category"]["PROJECTS"][0]["category"] == "EXPERIENCE"
        assert counts["rerank"] == 3
        document_count, query_count = counts["document"], counts["query"]
        second = await evidence_extractor.extract_candidate_category_evidence_postgres(
            "candidate-a", cv, queries, "job-a")
        assert second["composite_score"] == first["composite_score"]
        assert counts["document"] == document_count and counts["query"] == query_count
        assert evidence_extractor.rank_and_filter_candidate_batch(
            [dict(candidate_id="weak", composite_score=0), second], top_n_llm=1)[0]["candidate_id"] == "candidate-a"
    finally:
        await engine.dispose()
