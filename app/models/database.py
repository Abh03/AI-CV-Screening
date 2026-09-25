from datetime import datetime, timezone
from typing import AsyncGenerator
from sqlalchemy import String, Float, DateTime, JSON, ForeignKey, ForeignKeyConstraint, Integer, Text, Index, UniqueConstraint, CheckConstraint, LargeBinary
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


class CampaignModel(Base):
    __tablename__ = "campaigns"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="INTAKE")
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    archive_hash: Mapped[str | None] = mapped_column(String(64))
    intake_report: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("owner_id", "idempotency_key", name="uq_campaign_owner_key"),
        Index("ix_campaigns_owner_created", "owner_id", "created_at"),
        CheckConstraint("status IN ('INTAKE','RUNNING','COMPLETED','FAILED')", name="ck_campaign_status"),
    )


class CampaignJDModel(Base):
    __tablename__ = "campaign_jds"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    jd_key: Mapped[str] = mapped_column(String(64), nullable=False)
    job_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    policy_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    stage3_cap: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        UniqueConstraint("campaign_id", "id", name="uq_campaign_jd_scope"),
        UniqueConstraint("campaign_id", "jd_key", name="uq_campaign_jd_key"),
        Index("ix_campaign_jds_campaign_status", "campaign_id", "status"),
        CheckConstraint("stage3_cap > 0", name="ck_campaign_jd_cap"),
        CheckConstraint("status IN ('PENDING','PROCESSING','SHORTLISTED','COMPLETED','FAILED')", name="ck_campaign_jd_status"),
    )


class CampaignCVModel(Base):
    __tablename__ = "campaign_cvs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    candidate_id: Mapped[str] = mapped_column(String(64), nullable=False)
    document_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    stage0_status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING")
    redacted_text: Mapped[str | None] = mapped_column(Text)
    source_locations: Mapped[list | None] = mapped_column(JSON)
    extraction_error_code: Mapped[str | None] = mapped_column(String(64))
    encrypted_pdf: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        UniqueConstraint("campaign_id", "id", name="uq_campaign_cv_scope"),
        UniqueConstraint("campaign_id", "candidate_id", name="uq_campaign_candidate"),
        Index("ix_campaign_cvs_campaign_stage0", "campaign_id", "stage0_status"),
        CheckConstraint("document_version > 0", name="ck_campaign_cv_version"),
        CheckConstraint("stage0_status IN ('PENDING','RUNNING','SUCCEEDED','FAILED')", name="ck_campaign_cv_stage0"),
        CheckConstraint("stage0_status NOT IN ('SUCCEEDED','FAILED') OR encrypted_pdf IS NULL", name="ck_campaign_cv_purge_raw"),
    )


class CampaignPairModel(Base):
    __tablename__ = "campaign_pairs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    jd_id: Mapped[str] = mapped_column(String(64), nullable=False)
    cv_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    stage1_decision: Mapped[str | None] = mapped_column(String(16))
    stage1_details: Mapped[dict | None] = mapped_column(JSON)
    stage2_score: Mapped[float | None] = mapped_column(Float)
    stage2_rank: Mapped[int | None] = mapped_column(Integer)
    stage3_status: Mapped[str | None] = mapped_column(String(32))
    composite_score: Mapped[float | None] = mapped_column(Float)
    tier: Mapped[str | None] = mapped_column(String(16))
    verification_required: Mapped[bool] = mapped_column(nullable=False, default=False)
    verification_reasons: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    result_snapshot: Mapped[dict | None] = mapped_column(JSON)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stage3_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    failure_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["campaign_id", "jd_id"], ["campaign_jds.campaign_id", "campaign_jds.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["campaign_id", "cv_id"], ["campaign_cvs.campaign_id", "campaign_cvs.id"], ondelete="CASCADE"),
        UniqueConstraint("jd_id", "cv_id", name="uq_campaign_pair_jd_cv"),
        Index("ix_campaign_pairs_jd_status", "jd_id", "status"),
        Index("ix_campaign_pairs_jd_rank", "jd_id", "status", "composite_score", "cv_id"),
        Index("ix_campaign_pairs_campaign_status", "campaign_id", "status"),
        Index("ix_campaign_pairs_dispatch", "campaign_id", "status", "lease_until"),
        CheckConstraint("attempt_count >= 0", name="ck_campaign_pair_attempts"),
        CheckConstraint("stage3_attempt_count >= 0", name="ck_campaign_pair_stage3_attempts"),
        CheckConstraint("stage2_rank IS NULL OR stage2_rank > 0", name="ck_campaign_pair_stage2_rank"),
        CheckConstraint("status IN ('PENDING','RUNNING','STAGE2_READY','EXTRACTION_FAILED','FILTER_REJECTED','PROCESSING_FAILED','CUTOFF_EXCLUDED','SHORTLISTED','STAGE3_RUNNING','SUCCESS','REVIEW_REQUIRED','EVALUATION_FAILED')", name="ck_campaign_pair_status"),
    )
