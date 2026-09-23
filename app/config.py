from typing import Optional
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
    
    # Security & Encryption
    ENCRYPTION_SECRET_KEY: Optional[str] = None
    
    # Pipeline Defaults & Concurrency Limits
    DEFAULT_STAGE2_CUTOFF: int = 30
    LLM_CONCURRENCY_LIMIT: int = 5
    
    # LLM API Keys & Provider Config
    LLM_PROVIDER: str = "mock"
    GEMINI_API_KEY: Optional[str] = None
    OPENAI_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None
    GROQ_API_KEY: Optional[str] = None
    OPENROUTER_API_KEY: Optional[str] = None

    GROQ_MODEL: str = "openai/gpt-oss-120b"
    OPENROUTER_MODEL: str = "meta-llama/llama-3.3-70b-instruct:free"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()