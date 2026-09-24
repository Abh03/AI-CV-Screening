"""PostgreSQL retrieval store for redacted, versioned CV evidence."""
import hashlib
import json
import math
import asyncio
from datetime import datetime, timezone

from sqlalchemy import text, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import (CandidateModel, DocumentVersionModel, SourceChunkModel,
                                 ChunkEmbeddingModel, JobQueryEmbeddingModel)
from app.stage2_retrieval.embeddings import (EMBEDDING_MODEL_NAME, EMBEDDING_MODEL_VERSION,
                                             generate_embeddings, generate_single_embedding)
from app.stage2_retrieval.hybrid_search import compute_rrf_score

CHUNKING_VERSION = "structural-v2"
REDACTION_VERSION = "pii-mask-v1"


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def vector_literal(values):
    if len(values) != 384 or any(not isinstance(x, (int, float)) or not math.isfinite(x) for x in values):
        raise ValueError("Embedding must contain 384 numeric dimensions")
    return "[" + ",".join(str(float(x)) for x in values) + "]"


class PostgresRetrievalRepository:
    def __init__(self, session: AsyncSession):
        if session.bind.dialect.name != "postgresql":
            raise ValueError("PostgreSQL is required for production retrieval")
        self.session = session

    async def prepare_document(self, candidate_id, redacted_text, chunks, source_pages=None,
                               redaction_version=REDACTION_VERSION):
        source_hash = digest(json.dumps({"text": redacted_text, "pages": source_pages},
                                        sort_keys=True, ensure_ascii=False))
        document_id = "doc:" + digest("\0".join((candidate_id, source_hash,
            redaction_version, CHUNKING_VERSION)))
        await self.session.execute(pg_insert(CandidateModel).values(id=candidate_id,
            created_at=datetime.now(timezone.utc))
            .on_conflict_do_nothing(index_elements=["id"]))
        await self.session.execute(pg_insert(DocumentVersionModel).values(
            id=document_id, candidate_id=candidate_id, content_hash=source_hash,
            redaction_version=redaction_version, chunking_version=CHUNKING_VERSION,
            created_at=datetime.now(timezone.utc))
            .on_conflict_do_nothing(index_elements=["id"]))
        for chunk in chunks:
            chunk_id = "chunk:" + digest("\0".join((document_id,
                str(chunk["global_chunk_id"]), chunk["text"])))
            chunk["candidate_id"] = candidate_id
            chunk["document_id"] = document_id
            chunk["chunk_id"] = chunk_id
            chunk.setdefault("source_location", {"section": chunk["section"],
                                                 "chunk_index": chunk["chunk_index"]})
            await self.session.execute(pg_insert(SourceChunkModel).values(
                id=chunk_id, document_id=document_id, candidate_id=candidate_id,
                category=chunk["category"], section=chunk["section"],
                chunk_index=chunk["chunk_index"], text=chunk["text"],
                content_hash=digest(chunk["text"]), source_location=chunk["source_location"])
                .on_conflict_do_nothing(index_elements=["id"]))
        keys = [(c["chunk_id"], EMBEDDING_MODEL_NAME, EMBEDDING_MODEL_VERSION) for c in chunks]
        missing = [c for c, key in zip(chunks, keys)
                   if await self.session.get(ChunkEmbeddingModel, key) is None]
        uncached = []
        for chunk in missing:
            reused = (await self.session.execute(
                select(ChunkEmbeddingModel.vector)
                .join(SourceChunkModel, SourceChunkModel.id == ChunkEmbeddingModel.chunk_id)
                .join(DocumentVersionModel, DocumentVersionModel.id == SourceChunkModel.document_id)
                .where(SourceChunkModel.candidate_id == candidate_id,
                       SourceChunkModel.content_hash == digest(chunk["text"]),
                       DocumentVersionModel.redaction_version == redaction_version,
                       DocumentVersionModel.chunking_version == CHUNKING_VERSION,
                       ChunkEmbeddingModel.model_name == EMBEDDING_MODEL_NAME,
                       ChunkEmbeddingModel.model_version == EMBEDDING_MODEL_VERSION)
                .limit(1))).scalar_one_or_none()
            if reused is None:
                uncached.append(chunk)
            else:
                await self.session.execute(pg_insert(ChunkEmbeddingModel).values(
                    chunk_id=chunk["chunk_id"], model_name=EMBEDDING_MODEL_NAME,
                    model_version=EMBEDDING_MODEL_VERSION, vector=reused)
                    .on_conflict_do_nothing(index_elements=["chunk_id", "model_name", "model_version"]))
        if uncached:
            # The embedding model can be slow; release the database transaction first.
            await self.session.commit()
            vectors = await asyncio.to_thread(generate_embeddings, [c["text"] for c in uncached])
            for chunk, vector in zip(uncached, vectors):
                vector_literal(vector)
                await self.session.execute(pg_insert(ChunkEmbeddingModel).values(
                    chunk_id=chunk["chunk_id"], model_name=EMBEDDING_MODEL_NAME,
                    model_version=EMBEDDING_MODEL_VERSION, vector=vector)
                    .on_conflict_do_nothing(index_elements=["chunk_id", "model_name", "model_version"]))
        await self.session.commit()
        return document_id

    async def query_vector(self, job_id, category, query):
        query_hash = digest(query)
        key = (job_id, category, query_hash, EMBEDDING_MODEL_NAME, EMBEDDING_MODEL_VERSION)
        cached = await self.session.get(JobQueryEmbeddingModel, key)
        if cached is not None:
            vector = list(cached.vector)
            await self.session.commit()
            return vector
        await self.session.commit()
        vector = await asyncio.to_thread(generate_single_embedding, query)
        vector_literal(vector)
        await self.session.execute(pg_insert(JobQueryEmbeddingModel).values(
            job_id=job_id, category=category, query_hash=query_hash,
            model_name=EMBEDDING_MODEL_NAME, model_version=EMBEDDING_MODEL_VERSION,
            vector=vector).on_conflict_do_nothing(index_elements=[
                "job_id", "category", "query_hash", "model_name", "model_version"]))
        await self.session.commit()
        return vector

    async def search(self, candidate_id, document_id, category, query, query_vector,
                     top_k=10, fallback_to_experience=False):
        if not query or top_k < 1:
            return []
        scope = {"candidate": candidate_id, "document": document_id,
                 "category": category, "limit": top_k,
                 "model": EMBEDDING_MODEL_NAME, "version": EMBEDDING_MODEL_VERSION}
        if fallback_to_experience:
            exists = (await self.session.execute(text("""
                SELECT 1 FROM source_chunks WHERE candidate_id=:candidate
                AND document_id=:document AND category=:category LIMIT 1
            """), scope)).first()
            if exists is None:
                fallback = (await self.session.execute(text("""
                    SELECT fallback_category FROM category_metadata
                    WHERE category=:category AND policy_version='category-v1'
                """), scope)).scalar_one_or_none()
                if fallback is not None:
                    scope["category"] = fallback
        base = """SELECT c.id AS chunk_id, c.document_id, c.candidate_id,
                    c.category, c.section, c.chunk_index, c.text, c.source_location
                  FROM source_chunks c """
        where = """ WHERE c.candidate_id=:candidate AND c.document_id=:document
                    AND c.category=:category """
        dense_sql = text(base + """JOIN chunk_embeddings e ON e.chunk_id=c.id
                    AND e.model_name=:model AND e.model_version=:version """ + where +
                    " ORDER BY e.vector <=> CAST(:vector AS vector), c.id LIMIT :limit")
        sparse_sql = text(base + where + """ AND c.tsv_content @@ websearch_to_tsquery('english', :query)
                    ORDER BY ts_rank_cd(c.tsv_content, websearch_to_tsquery('english', :query)) DESC,
                    c.id LIMIT :limit""")
        dense = (await self.session.execute(dense_sql,
            dict(scope, vector=vector_literal(query_vector)))).mappings().all()
        sparse = (await self.session.execute(sparse_sql, dict(scope, query=query))).mappings().all()
        return compute_rrf_score([dict(row) for row in dense],
                                 [dict(row) for row in sparse], top_n=top_k)
