"""Application Configuration via Pydantic Settings."""

import json
from typing import Any, List, Union
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for Ledgerly backend."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ENVIRONMENT: str = "development"
    DEBUG: bool = True

    # Server Binding
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    CORS_ORIGINS: Union[List[str], str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    # Database URLs
    # Administrative Connection (for migrations and DDL)
    ADMIN_DATABASE_URL: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/ledgerly_dev"
    )
    ADMIN_TEST_DATABASE_URL: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/ledgerly_test"
    )

    # Runtime Connection (non-superuser role, RLS enforced)
    DATABASE_URL: str = (
        "postgresql+asyncpg://ledgerly_app:ledgerly_secret@localhost:5432/ledgerly_dev"
    )
    TEST_DATABASE_URL: str = (
        "postgresql+asyncpg://ledgerly_app:ledgerly_secret@localhost:5432/ledgerly_test"
    )

    # Connection Pool Settings
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 5
    DB_POOL_TIMEOUT: int = 30
    DB_POOL_RECYCLE: int = 1800

    # Security
    SECRET_KEY: str = "change-this-in-production-to-a-secure-random-key"
    SESSION_COOKIE_NAME: str = "ledgerly_session"
    SESSION_LIFETIME_SECONDS: int = 86400

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str) and not v.startswith("["):
            return [i.strip() for i in v.split(",")]
        elif isinstance(v, str) and v.startswith("["):
            return json.loads(v)
        return v


settings = Settings()
