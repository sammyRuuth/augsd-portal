"""Application configuration using Pydantic BaseSettings"""

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with environment variable support"""

    # Application
    app_name: str = "AUGSD Portal"
    debug: bool = False

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost/portal_global"

    # Security
    secret_key: str = "your-secret-key-change-this-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24  # 24 hours

    # Writable directories. Hosts that provide a single persistent volume (Railway,
    # Fly, Render) should point all four at paths under that one mount.
    upload_dir: str = "uploads"
    export_dir: str = "exports"
    log_dir: str = "logs"
    backup_dir: str = "backups"
    max_upload_size: int = 50 * 1024 * 1024  # 50MB

    @field_validator("database_url")
    @classmethod
    def _require_async_driver(cls, value: str) -> str:
        """
        Force the asyncpg driver.

        Managed Postgres add-ons hand out a plain ``postgresql://`` URL, which
        SQLAlchemy would try to open with psycopg2 and fail on, since the engine is
        async. Rewriting here means the platform's URL can be used as-is.
        """
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+asyncpg://", 1)
        return value

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance"""
    return Settings()
