from datetime import datetime, timezone
from typing import AsyncGenerator
from sqlalchemy import String, Float, DateTime, JSON, ForeignKey
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