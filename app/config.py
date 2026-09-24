from typing import Optional, Literal
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "Automated CV Screening & Ranking Engine"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    
    # Environment & Logging
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"
    
    # Database Configuration
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_DB: str = "cv_engine"
    POSTGRES_HOST: str = "127.0.0.1"
    POSTGRES_PORT: int = 5432
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/cv_engine"
    
    # Redis Configuration
    REDIS_URL: str = "redis://127.0.0.1:6379/0"
    CELERY_VISIBILITY_TIMEOUT_SECONDS: int = Field(default=3600, ge=60)
    QUEUE_RECOVERY_SECONDS: int = Field(default=300, ge=30)
    RUN_TIMEOUT_SECONDS: int = Field(default=1800, ge=30)
    RUN_MAX_ATTEMPTS: int = Field(default=3, ge=1)
    OCR_CONCURRENCY_LIMIT: int = Field(default=1, ge=1)
    EMBEDDING_CONCURRENCY_LIMIT: int = Field(default=1, ge=1)
    RERANK_CONCURRENCY_LIMIT: int = Field(default=1, ge=1)
    PROVIDER_TIMEOUT_SECONDS: int = Field(default=45, ge=1)
    
    # Security & Encryption
    ENCRYPTION_SECRET_KEY: Optional[str] = None
    API_TOKENS_JSON: Optional[str] = None
    PDF_MAX_BYTES: int = Field(default=10 * 1024 * 1024, ge=1)
    PDF_MAX_PAGES: int = Field(default=20, ge=1)
    PDF_MAX_PAGE_PIXELS: int = Field(default=4_000_000, ge=1)
    PDF_OCR_TIMEOUT_SECONDS: int = Field(default=15, ge=1)
    PDF_OCR_MAX_PAGES: int = Field(default=5, ge=0)
    PDF_OCR_LANGUAGE: Literal["eng"] = "eng"
    
    # Pipeline Defaults & Concurrency Limits
    DEFAULT_STAGE2_CUTOFF: int = 30
    STAGE2_BACKEND: Literal["memory", "postgres"] = "memory"
    LLM_CONCURRENCY_LIMIT: int = 5
    
    # LLM API Keys & Provider Config
    LLM_PROVIDER: str = "mock"
    GEMINI_API_KEY: Optional[str] = None
    OPENAI_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None
    GROQ_API_KEY: Optional[str] = None
    OPENROUTER_API_KEY: Optional[str] = None

    def validate_production(self):
        if self.ENVIRONMENT.lower() != "production":
            return
        from sqlalchemy.engine import make_url
        from app.core.auth import configured_principals
        from app.core.security import validate_encryption_configuration
        if make_url(self.DATABASE_URL).get_backend_name() != "postgresql" or self.STAGE2_BACKEND != "postgres":
            raise ValueError("Production requires PostgreSQL and postgres retrieval")
        if (self.DATABASE_URL == "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/cv_engine"
                or self.REDIS_URL == "redis://127.0.0.1:6379/0"):
            raise ValueError("Production requires explicit database and Redis configuration")
        if self.LLM_PROVIDER.lower() not in {"gemini", "groq", "openrouter"}:
            raise ValueError("Production requires a supported live LLM provider")
        key = {"gemini": self.GEMINI_API_KEY, "groq": self.GROQ_API_KEY,
               "openrouter": self.OPENROUTER_API_KEY}[self.LLM_PROVIDER.lower()]
        if not key:
            raise ValueError("Production LLM provider key is missing")
        if not configured_principals(self.API_TOKENS_JSON):
            raise ValueError("Production API_TOKENS_JSON is missing or empty")
        validate_encryption_configuration()

    GROQ_MODEL: str = "openai/gpt-oss-120b"
    OPENROUTER_MODEL: str = "meta-llama/llama-3.3-70b-instruct:free"

    @model_validator(mode="after")
    def validate_worker_timeouts(self):
        if self.CELERY_VISIBILITY_TIMEOUT_SECONDS <= self.RUN_TIMEOUT_SECONDS + 35:
            raise ValueError("CELERY_VISIBILITY_TIMEOUT_SECONDS must exceed the run timeout and lease")
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
