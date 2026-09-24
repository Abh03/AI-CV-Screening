from datetime import datetime, timezone
from typing import AsyncGenerator
from sqlalchemy import String, Float, DateTime, JSON, ForeignKey, Integer, Text, Index, UniqueConstraint
from pgvector.sqlalchemy import Vector
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.config import settings

DATABASE_URL = settings.DATABASE_URL

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


class JobProfileModel(Base):
    __tablename__ = "job_profiles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False, default="local")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    category_queries: Mapped[dict] = mapped_column(JSON, nullable=False)
    hard_filter_rules: Mapped[dict] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )

    evaluations: Mapped[list["EvaluationResultModel"]] = relationship(back_populates="job_profile")


class EvaluationResultModel(Base):
    __tablename__ = "evaluation_results"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("screening_runs.id"), nullable=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("job_profiles.id"), nullable=False)
    candidate_id: Mapped[str] = mapped_column(String(64), nullable=False)
    composite_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    tier: Mapped[str | None] = mapped_column(String(16), nullable=True)
    evaluation_status: Mapped[str] = mapped_column(String(32), nullable=False)
    scoring_policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    category_scores: Mapped[dict] = mapped_column(JSON, nullable=False)
    verified_citations: Mapped[list] = mapped_column(JSON, nullable=False)
    invalid_citations: Mapped[list] = mapped_column(JSON, nullable=False)
    has_critical_flags: Mapped[bool] = mapped_column(nullable=False)
    llm_raw_output: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )

    job_profile: Mapped["JobProfileModel"] = relationship(back_populates="evaluations")


class CandidateModel(Base):
    __tablename__ = "candidates"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class DocumentVersionModel(Base):
    __tablename__ = "document_versions"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    redaction_version: Mapped[str] = mapped_column(String(32), nullable=False)
    chunking_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    __table_args__ = (Index("ix_document_versions_candidate", "candidate_id", "created_at"),)


class SourceChunkModel(Base):
    __tablename__ = "source_chunks"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    section: Mapped[str] = mapped_column(String(32), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_location: Mapped[dict] = mapped_column(JSON, nullable=False)
    __table_args__ = (Index("ix_source_chunks_scope", "candidate_id", "document_id", "category"),)


class ChunkEmbeddingModel(Base):
    __tablename__ = "chunk_embeddings"
    chunk_id: Mapped[str] = mapped_column(ForeignKey("source_chunks.id", ondelete="CASCADE"), primary_key=True)
    model_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    model_version: Mapped[str] = mapped_column(String(64), primary_key=True)
    vector: Mapped[list] = mapped_column(Vector(384), nullable=False)


class CategoryMetadataModel(Base):
    __tablename__ = "category_metadata"
    category: Mapped[str] = mapped_column(String(16), primary_key=True)
    fallback_category: Mapped[str | None] = mapped_column(String(16), nullable=True)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)


class JobQueryEmbeddingModel(Base):
    __tablename__ = "job_query_embeddings"
    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    category: Mapped[str] = mapped_column(String(16), primary_key=True)
    query_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    model_version: Mapped[str] = mapped_column(String(64), primary_key=True)
    vector: Mapped[list] = mapped_column(Vector(384), nullable=False)


class ScreeningRunModel(Base):
    __tablename__ = "screening_runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False, default="local")
    job_id: Mapped[str] = mapped_column(ForeignKey("job_profiles.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="legacy")
    job_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    policy_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    response_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    request_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class CandidateOutcomeModel(Base):
    __tablename__ = "candidate_outcomes"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("screening_runs.id"), nullable=False)
    candidate_id: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    input_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    stage_history: Mapped[list] = mapped_column(JSON, nullable=False)
    evidence_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    citation_mapping: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    validated_output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scoring_policy_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    __table_args__ = (UniqueConstraint("run_id", "candidate_id", name="uq_candidate_outcome_run_candidate"),)
